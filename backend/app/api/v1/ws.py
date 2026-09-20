"""Endpoint WebSocket del hub (fase 12, ARCHITECTURE.md §8): ``/api/v1/ws``.

Protocolo de frames (JSON, uno por mensaje):
- cliente → servidor: ``{"type":"auth","token":<JWT>}`` (SIEMPRE el primero,
  §8.1), ``{"type":"subscribe","topics":[...],"since_id":<int opcional>}``,
  ``{"type":"unsubscribe","topics":[...]}`` y ``{"type":"pong"}``.
- servidor → cliente: ``{"type":"ready"}``, ``{"type":"subscribed","topics",
  "denied","replayed","cursor"}``, ``{"type":"event","event":{...}}``,
  ``{"type":"unsubscribed","topics"}``, ``{"type":"ping"}`` (latido, §8.2) y
  ``{"type":"error","code","detail"}``.

Autenticación SOLO por JWT de usuario en fase 12 (el token de dispositivo del
tpv-agent llega en la fase 13); la sesión de BD se abre y CIERRA para validar
el primer frame y para cada replay: una conexión WS jamás retiene una conexión
del pool toda su vida. Close codes: ``4401`` autenticación ausente/inválida/
revocada, ``4400`` primer frame ilegible; los temas sin permiso NO cortan la
conexión: se devuelven en ``subscribed.denied``.

El solape replay/vivo (un evento publicado mientras se suscribe puede llegar
dos veces) se resuelve en el consumidor por ``id`` monotónico (§8.2:
idempotencia); el servidor nunca pierde eventos, a lo sumo los repite.
"""

import asyncio
import contextlib
import json

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import Settings
from app.core.errors import AppError
from app.core.events import EventBus, Subscription
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.db.session import get_session_factory
from app.repos import events as events_repo
from app.services import events as events_service
from app.services.auth import Principal, load_principal

logger = get_logger("tpv.ws")

router = APIRouter()

WS_AUTH_TIMEOUT = 4401
WS_BAD_FRAME = 4400

# Cola de salida por conexión: frames ya serializables a la espera de envío.
_OUTBOX_MAXSIZE = 1024


def _dump(frame: dict) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"), default=str)


async def _send(websocket: WebSocket, frame: dict) -> None:
    await websocket.send_text(_dump(frame))


def _emit(outbox: asyncio.Queue, frame: dict) -> None:
    """Añade un frame a la cola de salida; si el cliente no drena (socket
    muerto), se descarta y el cierre de conexión hará el resto."""
    try:
        outbox.put_nowait(frame)
    except asyncio.QueueFull:  # pragma: no cover - socket atascado
        logger.warning("ws.outbox_dropped")


def _topics_of(frame: dict) -> list[str] | None:
    topics = frame.get("topics")
    if not isinstance(topics, list) or not topics:
        return None
    if not all(isinstance(item, str) and item for item in topics):
        return None
    return topics


# ---------------------------------------------------------------------------
# Autenticación (primer frame, §8.1)
# ---------------------------------------------------------------------------
async def _authenticate(websocket: WebSocket, settings: Settings) -> Principal | None:
    """Valida el primer frame y devuelve el Principal (o cierra y devuelve None).

    Verificación en dos pasos como en HTTP: firma/expiración del JWT sin BD y,
    después, sesión viva + usuario activo + permisos con una sesión corta
    (revocación inmediata también aquí).
    """
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=settings.ws_auth_timeout_seconds
        )
    except asyncio.TimeoutError:
        await websocket.close(code=WS_AUTH_TIMEOUT)
        return None
    except WebSocketDisconnect:
        return None

    try:
        frame = json.loads(raw)
        if not isinstance(frame, dict) or frame.get("type") != "auth":
            raise ValueError("el primer frame debe ser auth")
        token = frame["token"]
        if not isinstance(token, str):
            raise ValueError("token ausente")
    except Exception:
        await websocket.close(code=WS_BAD_FRAME)
        return None

    try:
        claims = decode_access_token(settings, token)
    except jwt.PyJWTError:
        await websocket.close(code=WS_AUTH_TIMEOUT)
        return None

    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            return await load_principal(session, claims=claims)
        except AppError:  # sesión revocada/expirada, usuario inactivo…
            await websocket.close(code=WS_AUTH_TIMEOUT)
            return None


# ---------------------------------------------------------------------------
# Suscripción con replay (§8.2) — sesión de BD corta, se cierra al terminar
# ---------------------------------------------------------------------------
async def _handle_subscribe(
    sub: Subscription,
    outbox: asyncio.Queue,
    principal: Principal,
    settings: Settings,
    frame: dict,
) -> None:
    topics = _topics_of(frame)
    if topics is None:
        _emit(outbox, {"type": "error", "code": "WS_BAD_FRAME", "detail": "topics debe ser una lista de cadenas"})
        return
    since_id = frame.get("since_id")
    if since_id is not None and (
        isinstance(since_id, bool) or not isinstance(since_id, int) or since_id < 0
    ):
        _emit(outbox, {"type": "error", "code": "WS_BAD_FRAME", "detail": "since_id debe ser un entero >= 0"})
        return

    granted, denied = events_service.authorize_topics(topics, principal)
    if not granted:
        _emit(outbox, {"type": "subscribed", "topics": [], "denied": denied, "replayed": 0, "cursor": since_id})
        return

    # 1) Los vivos empiezan YA (los duplicados del solape los deduplica el
    # cliente por id); 2) después, el replay desde event_log.
    sub.topics.update(granted)
    replayed, cursor = 0, since_id
    if since_id is not None:
        factory = get_session_factory(settings)
        async with factory() as session:
            rows = await events_repo.since(
                session, topics=set(granted), after_id=since_id, limit=settings.ws_replay_limit
            )
        replayed = len(rows)
        for row in rows:
            _emit(outbox, {"type": "event", "event": events_service.to_envelope(row).frame()})
        cursor = rows[-1].id if rows else since_id
    _emit(
        outbox,
        {
            "type": "subscribed",
            "topics": granted,
            "denied": denied,
            "replayed": replayed,
            "cursor": cursor,
        },
    )


# ---------------------------------------------------------------------------
# Tareas por conexión: lector, bomba (bus → outbox) y escritor (latido)
# ---------------------------------------------------------------------------
async def _reader(
    websocket: WebSocket,
    sub: Subscription,
    outbox: asyncio.Queue,
    principal: Principal,
    settings: Settings,
) -> None:
    while True:
        raw = await websocket.receive_text()  # WebSocketDisconnect propaga
        try:
            frame = json.loads(raw)
            if not isinstance(frame, dict):
                raise ValueError("no es un objeto")
        except Exception:
            _emit(outbox, {"type": "error", "code": "WS_BAD_FRAME", "detail": "Frame ilegible: se esperaba un objeto JSON"})
            continue
        kind = frame.get("type")
        if kind == "pong":
            continue
        if kind == "subscribe":
            await _handle_subscribe(sub, outbox, principal, settings, frame)
        elif kind == "unsubscribe":
            topics = _topics_of(frame) or []
            removed = [topic for topic in topics if topic in sub.topics]
            sub.topics.difference_update(topics)
            _emit(outbox, {"type": "unsubscribed", "topics": removed})
        else:
            _emit(outbox, {"type": "error", "code": "WS_UNKNOWN_FRAME", "detail": f"Tipo de frame no soportado: {kind!r}"})


async def _pump(sub: Subscription, outbox: asyncio.Queue) -> None:
    """Traslada los sobres del bus a la cola de salida (get sin timeout:
    nunca se cancela, así no se pierde ningún evento del bus)."""
    while True:
        envelope = await sub.queue.get()
        _emit(outbox, {"type": "event", "event": envelope.frame()})


async def _writer(websocket: WebSocket, outbox: asyncio.Queue, settings: Settings) -> None:
    """Único emisor del socket: frames de la cola y latido ping (§8.2)."""
    while True:
        try:
            frame = await asyncio.wait_for(outbox.get(), timeout=settings.ws_heartbeat_seconds)
        except asyncio.TimeoutError:
            # get() cancelado por timeout: drenar por si un frame entró a la vez
            frame = outbox.get_nowait() if outbox.qsize() else {"type": "ping"}
        await _send(websocket, frame)


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    bus: EventBus | None = getattr(websocket.app.state, "event_bus", None)
    await websocket.accept()
    if bus is None:  # pragma: no cover - create_app siempre registra el bus
        await websocket.close(code=1011)
        return

    principal = await _authenticate(websocket, settings)
    if principal is None:
        return
    logger.info("ws.connected", user=str(principal.user_id))
    await _send(websocket, {"type": "ready"})

    sub = bus.subscribe()
    outbox: asyncio.Queue = asyncio.Queue(maxsize=_OUTBOX_MAXSIZE)
    pump = asyncio.create_task(_pump(sub, outbox))
    writer = asyncio.create_task(_writer(websocket, outbox, settings))
    try:
        await _reader(websocket, sub, outbox, principal, settings)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(sub)
        for task in (pump, writer):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(pump, writer, return_exceptions=True)
    logger.info("ws.disconnected", user=str(principal.user_id))

"""Bus de eventos en memoria + difusión pos-commit (fase 12, ARCHITECTURE.md §8).

Diseño (§8.3: proceso único, pub/sub en memoria; §8.2: replay por ``event_log``):

1. Los servicios llaman ``services.events.record()`` DENTRO de la transacción
   del cambio: el evento se INSERTA en ``event_log`` y se confirma con el MISMO
   commit (si el cambio revierte, el evento nunca existió).
2. El sobre (``Envelope``) queda en espera en ``session.info`` ("staging").
3. Un listener ``after_commit`` (instalado a nivel de clase ``Session``, una
   sola vez al importar) publica los sobres en espera en el ``EventBus`` de la
   app; ``after_rollback`` los descarta. Nadie recibe un evento que no está
   confirmado en la BD y el replay por ``id`` nunca miente.

Publicar NUNCA bloquea ni revienta al negocio (§8.2: el WS acelera, no frena):
suscriptor lento → cola llena → el evento se descarta PARA ESE cliente y se
registra; el cliente se cura con ``since_id`` (replay) o re-sincronizando por
REST. Lo crítico (venta, caja) nunca espera al WS.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session as SyncSession

from app.core.logging import get_logger

logger = get_logger("tpv.events")

# Claves en session.info (mismo dict que lee el listener sobre el sync Session).
BUS_KEY = "tpv_event_bus"
PENDING_KEY = "tpv_pending_events"

# Cola por conexión: holgada para un local, pequeña a propósito — si se llena,
# el cliente va demasiado lento y se le descarta (se cura con replay/REST).
QUEUE_MAXSIZE = 256


@dataclass(frozen=True, slots=True)
class Envelope:
    """Sobre de evento: lo que se persiste (``event_log``) y lo que viaja."""

    id: int  # cursor de replay: bigserial de event_log
    event_id: UUID
    topic: str
    type: str
    payload: dict | None
    actor_user_id: UUID | None
    occurred_at: datetime

    def frame(self) -> dict:
        """Payload del frame WS ``{"type": "event"}`` (dinero string, §3)."""
        return {
            "id": self.id,
            "event_id": str(self.event_id),
            "topic": self.topic,
            "type": self.type,
            "payload": self.payload,
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(slots=True, eq=False)
class Subscription:
    """Una conexión suscrita: temas vigentes + cola de frames pendientes.

    ``eq=False``: la identidad es el propio objeto — el ``EventBus`` las guarda
    en un ``set`` y cada conexión es distinta aunque sus temas coincidan.
    """

    topics: set[str] = field(default_factory=set)
    loop: asyncio.AbstractEventLoop | None = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAXSIZE))


class EventBus:
    """Pub/sub en memoria (suficiente para decenas de conexiones, §8.3).

    La interfaz es la que un día implementará Redis pub/sub sin tocar
    servicios: ``subscribe``/``unsubscribe``/``publish`` y nada más.
    """

    def __init__(self, *, queue_maxsize: int = QUEUE_MAXSIZE) -> None:
        self._queue_maxsize = queue_maxsize
        self._subs: set[Subscription] = set()

    def subscribe(self, topics: Iterable[str] = ()) -> Subscription:
        sub = Subscription(topics=set(topics), loop=None, queue=asyncio.Queue(maxsize=self._queue_maxsize))
        try:
            sub.loop = asyncio.get_running_loop()
        except RuntimeError:
            sub.loop = None  # fuera de un loop (tests unitarios): cola directa
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def publish(self, envelopes: Iterable[Envelope]) -> None:
        """Reparte sobres a los suscriptores de cada tema. Nunca lanza.

        La cola del WS vive en SU loop: si el commit ocurre en otro hilo/loop
        (TestClient usa un loop por petición), se despierta al consumidor con
        ``call_soon_threadsafe``; en producción (un solo loop de uvicorn) la
        ruta es directa. Cualquier anomalía (loop cerrado, cola llena) se
        registra y se descarta para ese cliente: nunca rompe el commit.
        """
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for sub in tuple(self._subs):
            for env in envelopes:
                if env.topic not in sub.topics:
                    continue
                try:
                    put = sub.queue.put_nowait
                    if sub.loop is not None and sub.loop is not current:
                        sub.loop.call_soon_threadsafe(put, env)
                    else:
                        put(env)
                except asyncio.QueueFull:
                    logger.warning(
                        "events.slow_consumer_dropped",
                        topic=env.topic,
                        event_type=env.type,
                        event_id=str(env.event_id),
                    )
                except RuntimeError:  # loop del suscriptor ya cerrado
                    logger.info("events.subscriber_gone", event_id=str(env.event_id))
                    self._subs.discard(sub)
                    break


# ---------------------------------------------------------------------------
# Staging en session.info + hooks de ciclo de vida (nivel de clase, una vez)
# ---------------------------------------------------------------------------
def attach_bus(session, bus: EventBus | None) -> None:
    """Registra el bus de la app en la sesión (``get_db`` lo inyecta)."""
    session.info[BUS_KEY] = bus


def stage(session, envelope: Envelope) -> None:
    """Deja el sobre a la espera del commit (``services.events.record``)."""
    session.info.setdefault(PENDING_KEY, []).append(envelope)


def deliver(session) -> None:
    """``after_commit``: publica los sobres confirmados en la BD."""
    pending = session.info.pop(PENDING_KEY, None)
    bus = session.info.get(BUS_KEY)
    if pending and bus is not None:
        bus.publish(pending)


def discard(session) -> None:
    """``after_rollback``: los sobres de una transacción revertida no existen."""
    session.info.pop(PENDING_KEY, None)


sa_event.listen(SyncSession, "after_commit", deliver)
sa_event.listen(SyncSession, "after_rollback", discard)

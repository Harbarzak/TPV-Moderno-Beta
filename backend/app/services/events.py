"""Registro y autorización de eventos del hub WebSocket (fase 12, §8).

``record()`` lo llaman los servicios DENTRO de su transacción: inserta en
``event_log`` (mismo commit que el cambio) y deja el sobre en espera; la
difusión real ocurre tras el commit en :mod:`app.core.events`. Nunca comitea
ni lanza por fallo de publicación: si el hub no está (app sin bus, tests),
el negocio sigue.

La autorización de temas también vive aquí (lógica pura, testeable sin BD):
los servicios no importan de ``api`` y la API de WS solo traduce.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import BUS_KEY, Envelope, stage
from app.core.logging import get_logger
from app.db.models.system import EventLog
from app.repos import events as repo
from app.services.auth import Principal

logger = get_logger("tpv.events")


async def record(
    session: AsyncSession,
    *,
    topic: str,
    type: str,
    payload: dict | None = None,
    actor_user_id: UUID | None = None,
) -> None:
    """Registra un evento ligado a la transacción en curso.

    El sobre se genera 100% en Python (uuid4 + reloj, UNA vez: el ``event_id``
    y ``occurred_at`` persistidos son exactamente los que viajan) para no
    depender de atributos ORM tras el commit; el ``id`` (cursor) lo asigna el
    INSERT. Sin bus registrado no hay INSERT ni sobre: registrar eventos no
    puede convertir al hub en una dependencia del negocio.
    """
    if session.info.get(BUS_KEY) is None:
        return
    event_id = uuid4()
    occurred_at = datetime.now(UTC)
    envelope = Envelope(
        id=await repo.append(
            session,
            topic=topic,
            type=type,
            payload=payload,
            actor_user_id=actor_user_id,
            event_id=event_id,
            occurred_at=occurred_at,
        ),
        event_id=event_id,
        topic=topic,
        type=type,
        payload=payload,
        actor_user_id=actor_user_id,
        occurred_at=occurred_at,
    )
    stage(session, envelope)
    logger.debug("events.recorded", topic=topic, type=type, cursor=envelope.id)


def to_envelope(row: EventLog) -> Envelope:
    """Reconstruye el sobre desde ``event_log`` (replay)."""
    return Envelope(
        id=row.id,
        event_id=row.event_id,
        topic=row.topic,
        type=row.type,
        payload=row.payload,
        actor_user_id=row.actor_user_id,
        occurred_at=row.occurred_at,
    )


# ---------------------------------------------------------------------------
# Autorización de temas (§8.1): qué permiso exige cada suscripción
# ---------------------------------------------------------------------------
# Temas fijos: permiso exigido, o None = cualquier usuario autenticado.
_TOPIC_PERMISSIONS: dict[str, str | None] = {
    "sales": "sales.sell",       # ventas y cobros
    "catalog": "products.view",  # quien vende puede ver el catálogo cambiar
    "cash": "cash.open",         # sesiones y movimientos de caja
    "kds": "kds.operate",         # comandas de cocina (fase 32: permiso propio)
    "restaurant": "restaurant.operate",  # plano de mesas (fase 30): sala en vivo
    "system": None,              # avisos del sistema: cualquier autenticado
}

# ``agent:{uuid}`` queda RESERVADO al tpv-agent (fase 13): en fase 12 ningún
# usuario JWT se suscribe. Temas desconocidos: denegados.
_RESERVED = object()


def _required_permission(topic: str) -> str | None | object:
    if topic in _TOPIC_PERMISSIONS:
        return _TOPIC_PERMISSIONS[topic]
    prefix, _, rest = topic.partition(":")
    if prefix == "terminal" and rest:
        try:
            UUID(rest)
        except ValueError:
            return _RESERVED
        return "cash.open"  # impresión/turnos del propio TPV (§8.1)
    return _RESERVED


def authorize_topics(
    requested: list[str], principal: Principal
) -> tuple[list[str], list[str]]:
    """Separa los temas pedidos en (concedidos, denegados), en orden pedido."""

    granted: list[str] = []
    denied: list[str] = []
    for topic in requested:
        required = _required_permission(topic)
        if required is _RESERVED:
            denied.append(topic)
        elif required is None or required in principal.permissions:
            granted.append(topic)
        else:
            denied.append(topic)
    return granted, denied

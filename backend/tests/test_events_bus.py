"""Tests unitarios del bus de eventos y su autorización (fase 12, §8).

Sin BD: el sobre (``Envelope``), el reparto del ``EventBus``, el staging en
``session.info`` con commit/rollback simulados y la autorización de temas son
lógica pura. La integración completa (WS + ``event_log``) vive en
``test_ws_api.py``.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.core.events import (
    BUS_KEY,
    PENDING_KEY,
    Envelope,
    EventBus,
    attach_bus,
    deliver,
    discard,
    stage,
)
from app.services.auth import Principal
from app.services.events import authorize_topics


def _envelope(*, topic="sales", type="sales.created", payload=None) -> Envelope:
    return Envelope(
        id=1,
        event_id=uuid4(),
        topic=topic,
        type=type,
        payload=payload,
        actor_user_id=None,
        occurred_at=datetime.now(UTC),
    )


def _principal(permissions) -> Principal:
    return Principal(
        user_id=uuid4(),
        username="cajero",
        full_name="Usuario de Prueba",
        role_code="cajero",
        permissions=frozenset(permissions),
        scope="pos",
    )


# ---------------------------------------------------------------------------
# Envelope: lo que viaja por el socket
# ---------------------------------------------------------------------------
def test_el_frame_del_sobre_es_json_seguro():
    event_id = uuid4()
    actor = uuid4()
    occurred = datetime(2026, 9, 12, 10, 30, 0, tzinfo=UTC)
    env = Envelope(
        id=42,
        event_id=event_id,
        topic="cash",
        type="cash.opened",
        payload={"cash_session_id": "abc"},
        actor_user_id=actor,
        occurred_at=occurred,
    )
    frame = env.frame()
    assert frame == {
        "id": 42,
        "event_id": str(event_id),
        "topic": "cash",
        "type": "cash.opened",
        "payload": {"cash_session_id": "abc"},
        "actor_user_id": str(actor),
        "occurred_at": "2026-09-12T10:30:00+00:00",
    }
    # Todo el frame es serializable tal cual (UUID/datetime ya convertidos).
    assert all(not isinstance(v, (UUID, datetime)) for v in frame.values())


# ---------------------------------------------------------------------------
# EventBus: subscribe / publish / unsubscribe
# ---------------------------------------------------------------------------
def test_publish_encola_en_los_suscriptores_del_tema():
    async def scenario():
        bus = EventBus()
        sales = bus.subscribe(topics={"sales"})
        cash = bus.subscribe(topics={"cash"})
        env = _envelope(topic="sales")
        bus.publish([env])
        assert sales.queue.qsize() == 1
        assert cash.queue.qsize() == 0  # filtro por tema
        assert await sales.queue.get() is env

        bus.unsubscribe(sales)
        bus.publish([_envelope(topic="sales")])
        assert sales.queue.qsize() == 0  # dado de baja: no recibe más

    asyncio.run(scenario())


def test_suscriptor_lento_se_descarta_sin_reventar_el_commit():
    async def scenario():
        bus = EventBus(queue_maxsize=1)
        sub = bus.subscribe(topics={"sales"})
        bus.publish([_envelope(topic="sales"), _envelope(topic="sales", type="sales.closed")])
        assert sub.queue.qsize() == 1  # la segunda se descarta para ESTE cliente

    asyncio.run(scenario())


def test_publish_sin_suscriptores_es_un_no_op():
    bus = EventBus()
    bus.publish([_envelope()])  # ni lanza ni guarda nada
    assert not bus._subs


# ---------------------------------------------------------------------------
# Staging en session.info + commit/rollback
# ---------------------------------------------------------------------------
def _fake_session(bus) -> SimpleNamespace:
    session = SimpleNamespace(info={})
    attach_bus(session, bus)
    return session


def test_deliver_publica_los_sobres_confirmados_y_discard_los_tira():
    async def scenario():
        bus = EventBus()
        sub = bus.subscribe(topics={"sales"})
        session = _fake_session(bus)

        env = _envelope()
        stage(session, env)
        assert session.info[PENDING_KEY] == [env]

        deliver(session)  # tras el commit
        assert PENDING_KEY not in session.info
        assert await sub.queue.get() is env

        stage(session, env)
        discard(session)  # tras el rollback
        assert PENDING_KEY not in session.info
        assert sub.queue.qsize() == 0

        # Sin bus (tests, scripts): deliver/discard no explotan.
        orphan = SimpleNamespace(info={PENDING_KEY: [env]})
        deliver(orphan)
        assert PENDING_KEY not in orphan.info

    asyncio.run(scenario())


def test_attach_bus_registra_el_bus_en_la_sesion():
    bus = EventBus()
    session = _fake_session(bus)
    assert session.info[BUS_KEY] is bus
    attach_bus(session, None)  # app sin hub: record() se desactiva
    assert session.info[BUS_KEY] is None


# ---------------------------------------------------------------------------
# Autorización de temas (§8.1)
# ---------------------------------------------------------------------------
def test_temas_fijos_exigen_su_permiso():
    # Camarero real (semilla): kds.operate desde fase 32 — el tablero es suyo.
    vendedor = _principal({"products.view", "sales.sell", "cash.open", "kds.operate"})
    granted, denied = authorize_topics(
        ["sales", "catalog", "cash", "kds", "system"], vendedor
    )
    assert granted == ["sales", "catalog", "cash", "kds", "system"]
    assert denied == []

    sin_caja = _principal({"products.view", "sales.sell"})
    granted, denied = authorize_topics(["sales", "cash"], sin_caja)
    assert granted == ["sales"]
    assert denied == ["cash"]

    # El tema KDS ya no se hereda de vender (fase 32): exige kds.operate.
    sin_kds = _principal({"sales.sell"})
    granted, denied = authorize_topics(["sales", "kds"], sin_kds)
    assert granted == ["sales"]
    assert denied == ["kds"]

    sin_nada = _principal(set())
    granted, denied = authorize_topics(["sales", "catalog"], sin_nada)
    assert granted == []
    assert denied == ["sales", "catalog"]


def test_terminal_exige_cash_open_y_lo_desconocido_se_deniega():
    cajero = _principal({"cash.open"})
    terminal = f"terminal:{uuid4()}"
    agent = f"agent:{uuid4()}"
    granted, denied = authorize_topics([terminal, agent, "voladores"], cajero)
    assert granted == [terminal]
    assert denied == [agent, "voladores"]

    # terminal con UUID malformado: denegado, no 500.
    granted, denied = authorize_topics(["terminal:no-es-uuid"], cajero)
    assert granted == [] and denied == ["terminal:no-es-uuid"]


def test_el_orden_de_la_respuesta_es_el_pedido():
    jefe = _principal({"cash.open"})
    terminal = f"terminal:{uuid4()}"
    requested = ["cash", "desconocido", "sales", terminal]
    granted, denied = authorize_topics(requested, jefe)
    assert granted == ["cash", terminal]  # en el orden pedido
    assert denied == ["desconocido", "sales"]

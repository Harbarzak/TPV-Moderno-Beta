"""Tests de la máquina de estados KDS (fase 32): lógica pura, sin BD.

Reglas bajo prueba (``app/domain/kitchen.py``): avance libre incluido el
salto NUEVO→LISTO, un único paso atrás, terminales SERVIDO/CANCELADA y la
derivación del estado de la comanda a partir de sus líneas.
"""

from app.db.enums import KitchenStatus
from app.domain import kitchen as domain


def test_avance_lineal_por_los_tres_estados():
    assert domain.can_transition_line(KitchenStatus.pending, KitchenStatus.preparing)
    assert domain.can_transition_line(KitchenStatus.preparing, KitchenStatus.ready)
    assert domain.can_transition_line(KitchenStatus.ready, KitchenStatus.served)


def test_salto_nuevo_a_listo_legal_y_nuevo_a_servido_ilegal():
    # Un plato que sale acabado sin pasar marcado por PREPARANDO: caso real.
    assert domain.can_transition_line(KitchenStatus.pending, KitchenStatus.ready)
    assert not domain.can_transition_line(KitchenStatus.pending, KitchenStatus.served)
    # La cabecera respeta las mismas reglas (CHECK served_at → ready_at).
    assert domain.can_transition_ticket(KitchenStatus.pending, KitchenStatus.ready)
    assert not domain.can_transition_ticket(KitchenStatus.pending, KitchenStatus.served)


def test_solo_hay_un_paso_atras():
    assert domain.can_transition_line(KitchenStatus.ready, KitchenStatus.preparing)
    assert domain.can_transition_line(KitchenStatus.preparing, KitchenStatus.pending)
    # Dos pasos atrás no: de LISTO no se salta hasta NUEVO.
    assert not domain.can_transition_line(KitchenStatus.ready, KitchenStatus.pending)
    # Quedarse quieto no es una transición (el servicio lo trata como no-op).
    assert not domain.can_transition_line(KitchenStatus.pending, KitchenStatus.pending)


def test_cancelar_se_puede_desde_cualquier_estado_activo():
    for status in (KitchenStatus.pending, KitchenStatus.preparing, KitchenStatus.ready):
        assert domain.can_transition_line(status, KitchenStatus.cancelled)


def test_terminales_sin_salida():
    for status in (KitchenStatus.served, KitchenStatus.cancelled):
        assert domain.is_terminal(status)
        assert domain.LINE_TRANSITIONS[status] == frozenset()
        assert domain.NEXT_LINE_STATUS[status] is None
        assert not domain.can_transition_line(status, KitchenStatus.pending)
    assert not domain.is_terminal(KitchenStatus.ready)


def test_next_line_status_avanza_un_paso():
    assert domain.NEXT_LINE_STATUS[KitchenStatus.pending] is KitchenStatus.preparing
    assert domain.NEXT_LINE_STATUS[KitchenStatus.preparing] is KitchenStatus.ready
    assert domain.NEXT_LINE_STATUS[KitchenStatus.ready] is KitchenStatus.served


def test_comanda_y_linea_comparten_reglas():
    for status, targets in domain.LINE_TRANSITIONS.items():
        assert domain.TICKET_TRANSITIONS[status] == targets


def test_derivacion_sin_lineas_activas_o_todas_servidas():
    # Sin líneas activas la derivación pura devuelve SERVIDO; es el SERVICIO
    # quien decide antes de derivar: borrar la comanda o cancelarla (nunca
    # «servida» por vaciado).
    assert domain.derive_ticket_status([]) is KitchenStatus.served
    assert domain.derive_ticket_status([KitchenStatus.cancelled]) is KitchenStatus.served
    assert domain.derive_ticket_status([KitchenStatus.served]) is KitchenStatus.served
    assert domain.derive_ticket_status(
        [KitchenStatus.served, KitchenStatus.served]
    ) is KitchenStatus.served


def test_derivacion_por_tramos():
    # Todas NUEVO.
    assert domain.derive_ticket_status([KitchenStatus.pending]) is KitchenStatus.pending
    # Las canceladas no cuentan para la cabecera.
    assert domain.derive_ticket_status(
        [KitchenStatus.pending, KitchenStatus.cancelled]
    ) is KitchenStatus.pending
    # Algún trabajo empezado (o alguna lista): PREPARANDO.
    assert domain.derive_ticket_status(
        [KitchenStatus.pending, KitchenStatus.preparing]
    ) is KitchenStatus.preparing
    assert domain.derive_ticket_status(
        [KitchenStatus.pending, KitchenStatus.ready]
    ) is KitchenStatus.preparing
    # Todo listo (una ya servida vale): LISTO.
    assert domain.derive_ticket_status([KitchenStatus.ready]) is KitchenStatus.ready
    assert domain.derive_ticket_status(
        [KitchenStatus.ready, KitchenStatus.served]
    ) is KitchenStatus.ready
    # Mezcla con NUEVO pendiente: no se adelanta a lo trabajado.
    assert domain.derive_ticket_status(
        [KitchenStatus.pending, KitchenStatus.ready, KitchenStatus.served]
    ) is KitchenStatus.preparing

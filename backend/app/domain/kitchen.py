"""Máquina de estados KDS (fase 32): pura, sin BD ni ORM — testeable sola.

Estados (``KitchenStatus``, existente desde el esquema inicial):
``pending`` (NUEVO) → ``preparing`` (PREPARANDO) → ``ready`` (LISTO) →
``served`` (SERVIDO), más ``cancelled`` (terminal; vía anulación o retirada de
línea ya empezada).

Reglas:
- El paso adelante es libre, incluido el salto NUEVO→LISTO (un plato que sale
  acabado sin pasar marcado por PREPARANDO es un caso real de cocina).
- Hay UN paso atrás por línea: LISTO→PREPARANDO y PREPARANDO→NUEVO (se marcó
  mal / falta un ingrediente). SERVIDO y CANCELADA son terminales.
- El estado de la comanda (cabecera) se DERIVA de sus líneas: nunca se marca
  a mano — el tablero es la verdad de las líneas.
- La cabecera nunca puede adelantarse a sus líneas: «todo listo» exige que
  todas las líneas activas lo estén (validación en ``services.kitchen``).
"""

from app.db.enums import KitchenStatus

Pending = KitchenStatus.pending
Preparing = KitchenStatus.preparing
Ready = KitchenStatus.ready
Served = KitchenStatus.served
Cancelled = KitchenStatus.cancelled

#: Transiciones legales de una LÍNEA de comanda.
LINE_TRANSITIONS: dict[KitchenStatus, frozenset[KitchenStatus]] = {
    Pending: frozenset({Preparing, Ready, Cancelled}),
    Preparing: frozenset({Pending, Ready, Cancelled}),
    Ready: frozenset({Preparing, Served, Cancelled}),
    Served: frozenset(),
    Cancelled: frozenset(),
}

#: Transiciones legales de la COMANDA (cabecera). La derivación respeta el
#: CheckConstraint ``ck_kitchen_orders_flow``: llegar a SERVIDO exige pasar
#: por LISTO (``served_at`` no puede existir sin ``ready_at``).
TICKET_TRANSITIONS: dict[KitchenStatus, frozenset[KitchenStatus]] = {
    Pending: frozenset({Preparing, Ready, Cancelled}),
    Preparing: frozenset({Pending, Ready, Cancelled}),
    Ready: frozenset({Preparing, Served, Cancelled}),
    Served: frozenset(),
    Cancelled: frozenset(),
}

#: Siguiente estado al tocar una línea (avance de un paso); None = terminal.
NEXT_LINE_STATUS: dict[KitchenStatus, KitchenStatus | None] = {
    Pending: Preparing,
    Preparing: Ready,
    Ready: Served,
    Served: None,
    Cancelled: None,
}

_TERMINAL = frozenset({Served, Cancelled})


def can_transition_line(current: KitchenStatus, target: KitchenStatus) -> bool:
    return target in LINE_TRANSITIONS[current]


def can_transition_ticket(current: KitchenStatus, target: KitchenStatus) -> bool:
    return target in TICKET_TRANSITIONS[current]


def is_terminal(status: KitchenStatus) -> bool:
    return status in _TERMINAL


def derive_ticket_status(line_statuses: list[KitchenStatus]) -> KitchenStatus:
    """Estado de la comanda derivado de sus líneas activas (no canceladas).

    - todas SERVIDO (o sin líneas activas)  → SERVIDO
    - todas LISTO o SERVIDO                 → LISTO
    - alguna PREPARANDO/LISTO/SERVIDO       → PREPARANDO (trabajo empezado)
    - resto (todas NUEVO)                   → NUEVO
    """
    active = [status for status in line_statuses if status is not Cancelled]
    if not active or all(status is Served for status in active):
        return Served
    if all(status in (Ready, Served) for status in active):
        return Ready
    if any(status in (Preparing, Ready, Served) for status in active):
        return Preparing
    return Pending

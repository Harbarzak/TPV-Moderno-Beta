"""Matemática pura de los informes (fase 15 · Informes).

Sin E/S ni BD: los derivados del periodo (venta neta, ticket medio) se calculan
en céntimos enteros (§3) con las mismas reglas half-up del motor de ventas; la
capa de repos agrega en SQL y la de servicio convierte desde/hacia Decimal con
:mod:`app.domain.sales`. La acotación de rangos también vive aquí: los listados
NUNCA se sirven sin límite temporal (§13 — no cargar millones de filas).
"""

from datetime import datetime, timedelta

from app.domain.sales import round_half_up

__all__ = [
    "MAX_RANGE_DAYS",
    "bounded_range",
    "average_ticket_cents",
    "net_cents",
]

# Un año de histórico por consulta: con límite de página ≤ 200 filas es lo más
# que un terminal debe pedir de golpe (el resto, paginar o acotar más).
MAX_RANGE_DAYS = 366

# Margen de reloj: «hasta» y «desde» coinciden en el mismo instante sigue siendo
# un rango válido (un único cierre puntual).
ZERO = timedelta(0)


def bounded_range(
    since: datetime, until: datetime, *, max_days: int = MAX_RANGE_DAYS
) -> tuple[datetime, datetime]:
    """Valida el rango de un informe: ``until`` no puede ser anterior a ``since``
    ni el intervalo superar ``max_days``. Lanza ``ValueError``; la capa de
    servicio lo traduce a un 422 problem+json."""

    if until < since:
        raise ValueError("La fecha final no puede ser anterior a la inicial")
    if until - since > timedelta(days=max_days):
        raise ValueError(f"El rango del informe no puede superar {max_days} días")
    return since, until


def net_cents(sales_cents: int, refunds_cents: int) -> int:
    """Venta neta del periodo: ventas − devoluciones (ambos en céntimos ≥ 0)."""

    if sales_cents < 0 or refunds_cents < 0:
        raise ValueError("ventas y devoluciones son importes absolutos en céntimos")
    return sales_cents - refunds_cents


def average_ticket_cents(total_cents: int, count: int) -> int:
    """Ticket medio: total / nº de operaciones, half-up (§3); sin operaciones → 0."""

    if count < 0:
        raise ValueError("el número de operaciones no puede ser negativo")
    if count == 0:
        return 0
    return round_half_up(total_cents, count)

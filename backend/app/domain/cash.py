"""Matemática pura de la caja (fase 08 · Caja).

Sin E/S ni BD: el efectivo **esperado** de una sesión es el fondo inicial más
las ventas en efectivo, menos las devoluciones en efectivo, más las entradas y
menos las salidas de caja (ARCHITECTURE.md §5); la **diferencia** del arqueo es
contado − esperado. Todo en céntimos enteros (§3): la capa servicio convierte
desde/hacia Decimal con :mod:`app.domain.sales`.
"""

__all__ = ["count_total_cents", "difference_cents", "expected_cash_cents"]


def count_total_cents(lines: list[tuple[int, int]]) -> int:
    """Importe contado: Σ denominación (céntimos) × cantidad de piezas."""

    return sum(denomination * quantity for denomination, quantity in lines)


def expected_cash_cents(
    *,
    opening_cents: int,
    cash_sales_cents: int = 0,
    cash_refunds_cents: int = 0,
    cash_in_cents: int = 0,
    cash_out_cents: int = 0,
) -> int:
    """Efectivo que DEBE haber en la sesión según el sistema."""

    return (
        opening_cents
        + cash_sales_cents
        - cash_refunds_cents
        + cash_in_cents
        - cash_out_cents
    )


def difference_cents(counted_cents: int, expected_cents: int) -> int:
    """Desajuste del arqueo: contado − esperado (negativo = falta efectivo)."""

    return counted_cents - expected_cents

"""Motor de cálculo de ventas (fase 06 · Motor de ventas).

Lógica PURA: nada de I/O, sesiones ni modelos. El dinero viaja en **céntimos
enteros** y las cantidades en **mili-unidades** (1 ud = 1000), nunca en coma
flotante (§3). Convenios de redondeo (half-up):

1. Bruto de línea: ``round(price_cents * |qty_milli| / 1000)``.
2. Descuento % sobre el bruto: ``round(bruto * (10000 - pct_bp) / 10000)``.
3. Base (IVA incluido en el PVP) por cociente ppm:
   ``base = round(total * 1e6 / (1e6 + rate_bp * 100))``; IVA = total - base.

Una cantidad negativa (devolución) calcula sobre el valor absoluto y aplica el
signo al final: la línea de devolución es exactamente el negativo de la positiva.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MILI = 1_000  # mili-unidades por unidad
PPM = 1_000_000  # partes por millón (cociente de IVA incluido)
BP = 10_000  # puntos básicos (porcentaje con 2 decimales)


def round_half_up(numerator: int, denominator: int) -> int:
    """División entera con redondeo half-up (``denominator > 0``)."""

    if denominator <= 0:
        raise ValueError("denominator debe ser positivo")
    return (numerator * 2 + denominator) // (denominator * 2)


# ---------------------------------------------------------------------------
# Conversión Decimal (BD/API) ↔ enteros exactos (dominio)
# ---------------------------------------------------------------------------
def money_to_cents(value: Decimal) -> int:
    """Decimal con 2 decimales (validado en la API) → céntimos."""

    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def qty_to_milli(value: Decimal) -> int:
    """Cantidad con hasta 3 decimales (validado en la API) → mili-unidades."""

    return int((value * MILI).to_integral_value(rounding=ROUND_HALF_UP))


def milli_to_qty(value_milli: int) -> Decimal:
    """Mili-unidades → Decimal con 3 decimales (columna ``order_lines.quantity``)."""

    return (Decimal(value_milli) / MILI).quantize(Decimal("0.001"))


def pct_to_bp(value: Decimal) -> int:
    """Porcentaje con 2 decimales → puntos básicos (12.50 % → 1250 bp)."""

    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class LineAmounts:
    """Importes de una línea, en céntimos (negativos en devoluciones)."""

    base: int
    tax: int
    total: int


def line_amounts(
    *, price_cents: int, qty_milli: int, discount_bp: int = 0, tax_rate_bp: int
) -> LineAmounts:
    """Cálculo de una línea: bruto → descuento → desglose de IVA."""

    if qty_milli == 0:
        raise ValueError("La cantidad no puede ser cero")
    if not 0 <= discount_bp <= BP:
        raise ValueError("Descuento fuera de rango (0-100 %)")

    # 1) Bruto (PVP con IVA) por cantidad, sobre el valor absoluto…
    gross = round_half_up(price_cents * abs(qty_milli), MILI)
    # 2) …descuento %…
    total = round_half_up(gross * (BP - discount_bp), BP)
    # 3) …y base por cociente ppm (el PVP lleva el IVA dentro).
    denominator = PPM + tax_rate_bp * 100
    base = round_half_up(total * PPM, denominator)

    sign = -1 if qty_milli < 0 else 1
    return LineAmounts(base=sign * base, tax=sign * (total - base), total=sign * total)


@dataclass(frozen=True, slots=True)
class TaxSlice:
    """Tramo de IVA del pedido (un tramo por tipo impositivo)."""

    rate_bp: int
    base: int
    tax: int
    total: int


@dataclass(frozen=True, slots=True)
class OrderTotals:
    """Totales del pedido: sumas exactas de sus líneas + desglose por tramo."""

    base: int
    tax: int
    total: int
    slices: tuple[TaxSlice, ...]


def order_totals(lines: Iterable[tuple[int, LineAmounts]]) -> OrderTotals:
    """Totales de un pedido a partir de pares ``(tax_rate_bp, LineAmounts)``.

    Los tramos agrupan por tipo; ``total`` del tramo = base + tax del tramo, así
    la suma de tramos reproduce el total del pedido sin céntimos perdidos.
    """

    base_by_rate: dict[int, int] = defaultdict(int)
    tax_by_rate: dict[int, int] = defaultdict(int)
    for rate_bp, amounts in lines:
        base_by_rate[rate_bp] += amounts.base
        tax_by_rate[rate_bp] += amounts.tax

    slices = tuple(
        TaxSlice(
            rate_bp=rate_bp,
            base=base_by_rate[rate_bp],
            tax=tax_by_rate[rate_bp],
            total=base_by_rate[rate_bp] + tax_by_rate[rate_bp],
        )
        for rate_bp in sorted(base_by_rate)
    )
    base = sum(s.base for s in slices)
    tax = sum(s.tax for s in slices)
    return OrderTotals(base=base, tax=tax, total=base + tax, slices=slices)

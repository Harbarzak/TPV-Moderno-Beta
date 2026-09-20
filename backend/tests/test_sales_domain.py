"""Pruebas exhaustivas del cálculo de ventas (fase 06).

El dominio trabaja con enteros (céntimos, mili-unidades, puntos básicos); el
oráculo independiente usa ``Decimal`` con ``ROUND_HALF_UP``. Se cruzan los dos
en una rejilla completa de precios × cantidades × descuentos × tipos de IVA,
más los invariantes estructurales (aditividad de tramos, simetría de
devoluciones, límites de descuento).
"""

from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.domain.sales import (
    LineAmounts,
    cents_to_decimal,
    line_amounts,
    money_to_cents,
    order_totals,
    pct_to_bp,
    qty_to_milli,
    round_half_up,
)

CENT = Decimal("0.01")


def oracle(price: Decimal, qty: Decimal, pct: Decimal, rate: Decimal) -> tuple[int, int, int]:
    """Implementación de referencia con Decimal (independiente de la del dominio)."""

    gross = (price * qty).quantize(CENT, rounding=ROUND_HALF_UP)
    total = (gross * (1 - pct / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    base = (total / (1 + rate / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return int(base * 100), int((total - base) * 100), int(total * 100)


def impl(price: Decimal, qty: Decimal, pct: Decimal, rate: Decimal) -> LineAmounts:
    return line_amounts(
        price_cents=money_to_cents(price),
        qty_milli=qty_to_milli(qty),
        discount_bp=pct_to_bp(pct),
        tax_rate_bp=pct_to_bp(rate),
    )


PRICES = ["0.01", "0.05", "0.10", "0.13", "0.37", "1.00", "2.50", "9.99", "10.00", "123.45", "9999.99"]
QTYS = ["0.001", "0.010", "0.125", "0.350", "0.500", "1", "2", "3", "10", "123.456"]
DISCOUNTS = ["0", "5", "10", "12.50", "33.33", "50", "66.67", "100"]
RATES = ["0", "4", "10", "21"]


def test_rejilla_exhaustiva_con_oraculo_decimal() -> None:
    """Todos los cruces precio × cantidad × descuento × IVA coinciden con el oráculo."""

    for price in PRICES:
        for qty in QTYS:
            for pct in DISCOUNTS:
                for rate in RATES:
                    amounts = impl(Decimal(price), Decimal(qty), Decimal(pct), Decimal(rate))
                    base, tax, total = oracle(
                        Decimal(price), Decimal(qty), Decimal(pct), Decimal(rate)
                    )
                    assert (amounts.base, amounts.tax, amounts.total) == (base, tax, total), (
                        f"{price} × {qty} con {pct} % dto. e IVA {rate} %"
                    )


def test_redondeo_half_up() -> None:
    assert round_half_up(500, 1000) == 1  # 0.005 → 0.01 (hacia arriba)
    assert round_half_up(499, 1000) == 0  # 0.00499 → 0.00
    assert round_half_up(1500, 1000) == 2  # exacto .5 → arriba
    assert round_half_up(0, 1000) == 0
    assert round_half_up(13_000_000, 1_040_000) == 13  # 12.5 exacto en cociente IVA 4 %
    with pytest.raises(ValueError):
        round_half_up(1, 0)


def test_conversiones_exactas() -> None:
    assert money_to_cents(Decimal("10.00")) == 1000
    assert money_to_cents(Decimal("0.01")) == 1
    assert cents_to_decimal(1000) == Decimal("10.00")
    assert cents_to_decimal(1) == Decimal("0.01")
    assert qty_to_milli(Decimal("1")) == 1000
    assert qty_to_milli(Decimal("0.35")) == 350
    assert qty_to_milli(Decimal("123.456")) == 123456
    assert pct_to_bp(Decimal("21.00")) == 2100
    assert pct_to_bp(Decimal("12.50")) == 1250


def test_cantidad_cero_rechazada() -> None:
    with pytest.raises(ValueError):
        line_amounts(price_cents=100, qty_milli=0, tax_rate_bp=2100)


def test_descuento_fuera_de_rango_rechazado() -> None:
    with pytest.raises(ValueError):
        line_amounts(price_cents=100, qty_milli=1000, discount_bp=-1, tax_rate_bp=2100)
    with pytest.raises(ValueError):
        line_amounts(price_cents=100, qty_milli=1000, discount_bp=10_001, tax_rate_bp=2100)


def test_descuento_100_anula_el_importe_pero_no_la_base_de_otros() -> None:
    amounts = line_amounts(price_cents=1000, qty_milli=1000, discount_bp=10_000, tax_rate_bp=2100)
    assert amounts.total == 0
    assert amounts.base == 0
    assert amounts.tax == 0


def test_devolucion_es_el_negativo_exacto() -> None:
    """La línea de devolución (qty negativa) = -línea positiva, céntimo a céntimo."""

    for price in PRICES:
        for qty in QTYS:
            for pct in ["0", "10", "50"]:
                for rate in RATES:
                    sale = impl(Decimal(price), Decimal(qty), Decimal(pct), Decimal(rate))
                    refund = impl(Decimal(price), Decimal(qty), Decimal(pct), Decimal(rate))
                    negative = line_amounts(
                        price_cents=money_to_cents(Decimal(price)),
                        qty_milli=-qty_to_milli(Decimal(qty)),
                        discount_bp=pct_to_bp(Decimal(pct)),
                        tax_rate_bp=pct_to_bp(Decimal(rate)),
                    )
                    assert (negative.base, negative.tax, negative.total) == (
                        -sale.base,
                        -sale.tax,
                        -sale.total,
                    ), f"devolución de {price} × {qty}"
                    assert refund == sale  # sanidad del propio helper


def test_totales_aditivos_con_varios_tipos() -> None:
    lines = [
        (2100, impl(Decimal("10.00"), Decimal("2"), Decimal("0"), Decimal("21"))),
        (1000, impl(Decimal("5.00"), Decimal("1"), Decimal("10"), Decimal("10"))),
        (400, impl(Decimal("2.00"), Decimal("3"), Decimal("0"), Decimal("4"))),
        (0, impl(Decimal("1.00"), Decimal("1"), Decimal("0"), Decimal("0"))),
    ]
    totals = order_totals(lines)

    assert totals.total == sum(a.total for _, a in lines)
    assert totals.base == sum(a.base for _, a in lines)
    assert totals.tax == totals.total - totals.base
    assert [s.rate_bp for s in totals.slices] == [0, 400, 1000, 2100]  # ordenado
    for rate_bp, amounts in lines:
        slice_ = next(s for s in totals.slices if s.rate_bp == rate_bp)
        assert slice_.base + slice_.tax == slice_.total
    assert sum(s.base for s in totals.slices) == totals.base
    assert sum(s.tax for s in totals.slices) == totals.tax


def test_totales_con_devoluciones_se_compensan() -> None:
    sale = impl(Decimal("10.00"), Decimal("2"), Decimal("0"), Decimal("21"))
    refund = line_amounts(
        price_cents=1000, qty_milli=-2000, discount_bp=0, tax_rate_bp=2100
    )
    assert (refund.base, refund.tax, refund.total) == (-sale.base, -sale.tax, -sale.total)

    totals = order_totals([(2100, sale), (2100, refund)])
    assert (totals.base, totals.tax, totals.total) == (0, 0, 0)
    assert len(totals.slices) == 1  # un solo tramo, a cero

"""Tests de la matemática pura del cobro (fase 07 · Pagos).

Sin BD ni E/S: ``settle_payments`` exige que la suma de pagos cubra el total
EXACTAMENTE, admite «importe entregado» solo en efectivo (y devuelve cambio)
y rechaza cobros parciales y excesos sin efectivo que los absorba. El backend,
no el frontend, decide cuándo una venta está pagada.
"""

import pytest

from app.domain.payments import PaymentInput, SettleError, settle_payments


def _cash(amount: int, tendered: int | None = None) -> PaymentInput:
    return PaymentInput(amount_cents=amount, tendered_cents=tendered, is_cash=True)


def _card(amount: int) -> PaymentInput:
    return PaymentInput(amount_cents=amount, tendered_cents=None, is_cash=False)


def test_pago_exacto():
    settled, change = settle_payments(450, [_cash(450)])
    assert [(s.amount_cents, s.change_cents) for s in settled] == [(450, 0)]
    assert change == 0


def test_pago_superior_entrega_cambio():
    # Entrega 10.00 por una venta de 3.00: el pago aplica 3.00 y sobra cambio.
    settled, change = settle_payments(300, [_cash(300, 1000)])
    assert (settled[0].amount_cents, settled[0].change_cents) == (300, 700)
    assert change == 700


def test_pago_mixto():
    # 4.50 = 3.00 en efectivo (entrega 5.00) + 1.50 con tarjeta.
    settled, change = settle_payments(450, [_cash(300, 500), _card(150)])
    assert [(s.amount_cents, s.change_cents) for s in settled] == [(300, 200), (150, 0)]
    assert change == 200


def test_varios_efectivos_suman_sus_cambios():
    settled, change = settle_payments(500, [_cash(200, 300), _cash(300, 400)])
    assert [s.change_cents for s in settled] == [100, 100]
    assert change == 200


def test_pago_parcial_insuficiente():
    with pytest.raises(SettleError) as exc:
        settle_payments(450, [_cash(300), _card(100)])
    assert exc.value.reason == "insufficient"


def test_exceso_sin_efectivo():
    # La suma supera el total y el exceso no es «entregado» de efectivo.
    with pytest.raises(SettleError) as exc:
        settle_payments(450, [_card(300), _card(300)])
    assert exc.value.reason == "excess"


def test_entregado_solo_en_efectivo():
    with pytest.raises(SettleError) as exc:
        settle_payments(450, [PaymentInput(450, 500, is_cash=False)])
    assert exc.value.reason == "tendered_on_non_cash"


def test_entregado_menor_que_importe():
    with pytest.raises(SettleError) as exc:
        settle_payments(450, [_cash(450, 400)])
    assert exc.value.reason == "tendered_short"


def test_importe_no_positivo():
    with pytest.raises(SettleError) as exc:
        settle_payments(450, [_cash(0)])
    assert exc.value.reason == "positive"


def test_total_no_positivo():
    with pytest.raises(SettleError) as exc:
        settle_payments(0, [_cash(100)])
    assert exc.value.reason == "total"


def test_sin_pagos():
    with pytest.raises(SettleError) as exc:
        settle_payments(450, [])
    assert exc.value.reason == "insufficient"

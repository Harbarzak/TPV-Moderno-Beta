"""Tests unitarios de la matemática de caja (fase 08 · Caja).

Puros y sin BD: total contado por denominaciones, efectivo esperado
(fondo + ventas en efectivo − devoluciones + entradas − salidas) y diferencia
(contado − esperado). Todo en céntimos enteros (§3), half-up implícito porque
no hay división.
"""

from app.domain.cash import count_total_cents, difference_cents, expected_cash_cents


# ---------------------------------------------------------------------------
# Contado: Σ denominación × cantidad
# ---------------------------------------------------------------------------
def test_contado_suma_denominaciones():
    # 50 € + 5 € + 3×1 € + 0,50 €
    assert count_total_cents([(5000, 1), (500, 1), (100, 3), (50, 1)]) == 5850


def test_contado_vacio_y_cantidad_cero():
    assert count_total_cents([]) == 0
    assert count_total_cents([(100, 0), (200, 0)]) == 0


# ---------------------------------------------------------------------------
# Esperado: fondo inicial + ventas − devoluciones + entradas − salidas
# ---------------------------------------------------------------------------
def test_esperado_solo_fondo_inicial():
    assert expected_cash_cents(opening_cents=5000) == 5000


def test_esperado_ciclo_completo():
    # Fondo 50 + venta 3 − devolución 1,50 + entrada 10 − salida 5 = 56,50
    assert expected_cash_cents(
        opening_cents=5000,
        cash_sales_cents=300,
        cash_refunds_cents=150,
        cash_in_cents=1000,
        cash_out_cents=500,
    ) == 5650


def test_esperado_con_devolucion_mayor_que_la_venta():
    # La caja puede quedar por debajo del fondo si se devuelve más efectivo.
    assert expected_cash_cents(
        opening_cents=5000, cash_sales_cents=100, cash_refunds_cents=500
    ) == 4600


# ---------------------------------------------------------------------------
# Diferencia: contado − esperado
# ---------------------------------------------------------------------------
def test_diferencia_positiva_negativa_y_cero():
    assert difference_cents(counted_cents=5850, expected_cents=5650) == 200
    assert difference_cents(counted_cents=5450, expected_cents=5650) == -200
    assert difference_cents(counted_cents=5650, expected_cents=5650) == 0

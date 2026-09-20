"""Tests unitarios del dominio de informes (fase 15) — sin BD.

La matemática de los informes vive en ``app/domain/reports.py``: acotación de
rangos (§13 — ningún informe sin límite temporal), venta neta y ticket medio
en céntimos enteros con las reglas half-up del motor de ventas (§3).
"""

from datetime import datetime, timedelta

import pytest

from app.domain.reports import (
    MAX_RANGE_DAYS,
    average_ticket_cents,
    bounded_range,
    net_cents,
)

SINCE = datetime(2026, 3, 1, 8, 0, 0)


class TestBoundedRange:
    def test_rango_valido_se_devuelve_intacto(self):
        until = SINCE + timedelta(days=7)
        assert bounded_range(SINCE, until) == (SINCE, until)

    def test_instante_unico_es_valido(self):
        # «hasta» == «desde»: un único cierre puntual, no un error.
        assert bounded_range(SINCE, SINCE) == (SINCE, SINCE)

    def test_hasta_anterior_a_desde_es_error(self):
        with pytest.raises(ValueError, match="anterior"):
            bounded_range(SINCE, SINCE - timedelta(minutes=1))

    def test_techo_de_max_days(self):
        limite = SINCE + timedelta(days=MAX_RANGE_DAYS)
        assert bounded_range(SINCE, limite) == (SINCE, limite)
        with pytest.raises(ValueError, match=str(MAX_RANGE_DAYS)):
            bounded_range(SINCE, limite + timedelta(seconds=1))

    def test_techo_personalizado(self):
        with pytest.raises(ValueError, match="7 días"):
            bounded_range(SINCE, SINCE + timedelta(days=8), max_days=7)


class TestNetCents:
    def test_ventas_menos_devoluciones(self):
        assert net_cents(440, 110) == 330

    def test_sin_devoluciones(self):
        assert net_cents(440, 0) == 440

    def test_importes_negativos_son_error(self):
        with pytest.raises(ValueError):
            net_cents(-1, 0)
        with pytest.raises(ValueError):
            net_cents(0, -1)


class TestAverageTicket:
    def test_division_exacta(self):
        assert average_ticket_cents(440, 2) == 220

    def test_redondeo_half_up(self):
        # 100/3 = 33,33… → 33; 5/2 = 2,5 → 3 (half-up, igual que el motor).
        assert average_ticket_cents(100, 3) == 33
        assert average_ticket_cents(5, 2) == 3

    def test_sin_operaciones_el_ticket_medio_es_cero(self):
        assert average_ticket_cents(0, 0) == 0

    def test_operaciones_negativas_son_error(self):
        with pytest.raises(ValueError):
            average_ticket_cents(100, -1)

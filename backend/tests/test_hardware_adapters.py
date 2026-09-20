"""Tests unitarios de los adaptadores de hardware (fase 11) — sin base de datos.

Cubren FASE_11: los cinco Protocolos ``runtime_checkable`` (cajón, escáner,
pinpad, CashDro, display de cliente), el error ``HardwareError`` con ``code``
estable, los sustitutos honestos (``Null*`` para dispositivos inofensivos,
``Simulated*`` —SIEMPRE marcados como simulados— para los que mueven dinero)
y el registro ``HardwareAdapters.defaults()``.

No hay drivers de fabricante aquí: comprobar que estos objetos satisfacen sus
Protocolos con ``isinstance`` es exactamente la prueba de que una integración
real será solo OTRA implementación de la misma interfaz.
"""

import asyncio

import pytest

from app.adapters.hardware import (
    BarcodeScannerAdapter,
    CashDrawerAdapter,
    CashDroAdapter,
    CashDropResult,
    CustomerDisplayAdapter,
    FailingCashDrawerAdapter,
    HardwareAdapters,
    HardwareError,
    NullBarcodeScannerAdapter,
    NullCashDrawerAdapter,
    NullCustomerDisplayAdapter,
    PaymentTerminalAdapter,
    SimulatedCashDroAdapter,
    SimulatedPaymentTerminalAdapter,
    TerminalChargeResult,
)


def _run(coro):
    return asyncio.run(coro)


def _tid() -> "uuid.UUID":
    import uuid
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# 1 · Los Protocolos son runtime_checkable: defaults() implementa los cinco
# ---------------------------------------------------------------------------
def test_defaults_implementa_los_cinco_protocolos():
    hw = HardwareAdapters.defaults()
    assert isinstance(hw.drawer, CashDrawerAdapter)
    assert isinstance(hw.scanner, BarcodeScannerAdapter)
    assert isinstance(hw.payment_terminal, PaymentTerminalAdapter)
    assert isinstance(hw.cashdro, CashDroAdapter)
    assert isinstance(hw.customer_display, CustomerDisplayAdapter)


def test_los_sustitutos_satisfacen_su_protocolo():
    assert isinstance(NullCashDrawerAdapter(), CashDrawerAdapter)
    assert isinstance(NullBarcodeScannerAdapter(), BarcodeScannerAdapter)
    assert isinstance(SimulatedPaymentTerminalAdapter(), PaymentTerminalAdapter)
    assert isinstance(SimulatedCashDroAdapter(), CashDroAdapter)
    assert isinstance(NullCustomerDisplayAdapter(), CustomerDisplayAdapter)


# ---------------------------------------------------------------------------
# 2 · HardwareError: code estable para trazabilidad
# ---------------------------------------------------------------------------
def test_hardware_error_trae_code_por_defecto():
    err = HardwareError("El cajón no responde")
    assert err.code == "HARDWARE_UNAVAILABLE"
    assert HardwareError("PIN pad offline", code="PINPAD_TIMEOUT").code == "PINPAD_TIMEOUT"


# ---------------------------------------------------------------------------
# 3 · Cajón: Null registra, Failing lanza (la venta debe sobrevivirle)
# ---------------------------------------------------------------------------
def test_null_cash_drawer_registra_las_aperturas():
    drawer = NullCashDrawerAdapter()
    tid = _tid()
    _run(drawer.open_drawer(terminal_id=tid))
    assert drawer.opens == [tid]
    assert drawer.name == "null"


def test_failing_cash_drawer_lanza_hardware_error():
    drawer = FailingCashDrawerAdapter()
    with pytest.raises(HardwareError):
        _run(drawer.open_drawer(terminal_id=_tid()))


# ---------------------------------------------------------------------------
# 4 · Escáner: devuelve lo encolado y None si no llega nada
# ---------------------------------------------------------------------------
def test_null_scanner_encola_y_vacia():
    scanner = NullBarcodeScannerAdapter()
    scanner.queue("8412345678901")
    assert _run(scanner.next_scan()) == "8412345678901"
    assert _run(scanner.next_scan(timeout_seconds=0.1)) is None


# ---------------------------------------------------------------------------
# 5 · Pinpad simulado: aprueba siempre, marcado SIMULADO, deja traza
# ---------------------------------------------------------------------------
def test_simulated_pinpad_aprueba_y_registra():
    pinpad = SimulatedPaymentTerminalAdapter()
    tid = _tid()
    result = _run(pinpad.charge(terminal_id=tid, amount_cents=350, reference="ord-1"))
    assert isinstance(result, TerminalChargeResult)
    assert result.approved is True
    assert result.auth_code is not None and result.auth_code.startswith("SIM-")
    assert result.detail is not None and "SIMULADO" in result.detail
    assert pinpad.charges == [{"terminal_id": tid, "amount_cents": 350, "reference": "ord-1"}]


# ---------------------------------------------------------------------------
# 6 · CashDro simulado: entrega exacta (solo interfaz en la fase)
# ---------------------------------------------------------------------------
def test_simulated_cashdro_entrega_lo_pedido():
    cashdro = SimulatedCashDroAdapter()
    tid = _tid()
    result = _run(cashdro.dispense(terminal_id=tid, amount_cents=1250))
    assert isinstance(result, CashDropResult)
    assert result.delivered_cents == 1250
    assert result.detail is not None and "SIMULAD" in result.detail
    assert cashdro.dispensations[0]["amount_cents"] == 1250


# ---------------------------------------------------------------------------
# 7 · Display de cliente: recuerda lo mostrado y los clears
# ---------------------------------------------------------------------------
def test_null_display_recuerda_lineas_y_clears():
    display = NullCustomerDisplayAdapter()
    tid = _tid()
    _run(display.show(terminal_id=tid, lines=["TOTAL 3,50 €", "GRACIAS"]))
    _run(display.clear(terminal_id=tid))
    assert display.last_lines[tid] == ["TOTAL 3,50 €", "GRACIAS"]
    assert display.cleared == [tid]

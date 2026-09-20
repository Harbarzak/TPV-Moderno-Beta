"""Adaptadores de hardware (fase 11 · Hardware): interfaces, cero fabricantes.

El dominio solo conoce estos Protocolos (inyectados en ``app.state.hardware``);
los drivers reales —cajón por impresora/USB, escáner serie/HID, pinpad,
CashDro, display de cliente— llegarán con el tpv-agent (fases 13-14) y serán
solo OTRA implementación de estas mismas interfaces: sustituir una integración
no toca el motor de ventas.

Regla de oro: el hardware nunca está en el camino crítico de una venta. Los
efectos físicos (abrir cajón, cobrar con pinpad, entregar efectivo) ocurren
DESPUÉS de que la transacción ha hecho commit y su fallo se registra pero no
revierte nada: los drivers solo deben lanzar :class:`HardwareError`.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4


class HardwareError(Exception):
    """Fallo de un dispositivo físico. ``code`` estable para trazabilidad."""

    def __init__(self, message: str, *, code: str = "HARDWARE_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Cajón portamonedas
# ---------------------------------------------------------------------------
@runtime_checkable
class CashDrawerAdapter(Protocol):
    """Abre el cajón del terminal (kick). El flag ``opens_drawer`` de la
    forma de pago decide CUÁNDO; el adaptador decide CÓMO."""

    name: str

    async def open_drawer(self, *, terminal_id: UUID) -> None: ...


class NullCashDrawerAdapter:
    """Sin cajón conectado: registra las aperturas solicitadas."""

    name = "null"

    def __init__(self) -> None:
        self.opens: list[UUID] = []

    async def open_drawer(self, *, terminal_id: UUID) -> None:
        self.opens.append(terminal_id)


class FailingCashDrawerAdapter(NullCashDrawerAdapter):
    """Cajón atascado: lanza siempre (para probar que la venta sobrevive)."""

    name = "failing"

    def __init__(self, message: str = "El cajón no responde") -> None:
        super().__init__()
        self.message = message

    async def open_drawer(self, *, terminal_id: UUID) -> None:
        raise HardwareError(self.message)


# ---------------------------------------------------------------------------
# Lector de códigos de barras conectado al SERVIDOR (vía tpv-agent). El wedge
# del cliente (fase 07) no pasa por aquí.
# ---------------------------------------------------------------------------
@runtime_checkable
class BarcodeScannerAdapter(Protocol):
    name: str

    async def next_scan(self, *, timeout_seconds: float = 5.0) -> str | None:
        """Próximo código leído, o None si no llega nada en el timeout."""
        ...


class NullBarcodeScannerAdapter:
    """Sin escáner conectado: solo devuelve lo que un test le encole."""

    name = "null"

    def __init__(self) -> None:
        self.pending: deque[str] = deque()

    def queue(self, code: str) -> None:
        self.pending.append(code)

    async def next_scan(self, *, timeout_seconds: float = 5.0) -> str | None:
        return self.pending.popleft() if self.pending else None


# ---------------------------------------------------------------------------
# Terminal de pago (pinpad)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TerminalChargeResult:
    """Respuesta del pinpad: el pago lo confirmará el backend igual que un
    efectivo (el terminal es solo el periférico que lo captura)."""

    approved: bool
    auth_code: str | None = None
    card_last4: str | None = None
    ticket: str | None = None      # comprobante impreso por el terminal
    detail: str | None = None


@runtime_checkable
class PaymentTerminalAdapter(Protocol):
    name: str

    async def charge(
        self, *, terminal_id: UUID, amount_cents: int, reference: str | None = None
    ) -> TerminalChargeResult: ...


class SimulatedPaymentTerminalAdapter:
    """Sin pinpad físico: aprueba SIEMPRE y lo marca como simulado (auth_code
    ``SIM-…``). Solo para desarrollo; producción exige un driver real."""

    name = "simulated"

    def __init__(self) -> None:
        self.charges: list[dict] = []

    async def charge(
        self, *, terminal_id: UUID, amount_cents: int, reference: str | None = None
    ) -> TerminalChargeResult:
        self.charges.append(
            {"terminal_id": terminal_id, "amount_cents": amount_cents, "reference": reference}
        )
        return TerminalChargeResult(
            approved=True,
            auth_code=f"SIM-{uuid4().hex[:8].upper()}",
            card_last4="0000",
            detail="Cobro SIMULADO sin pinpad físico",
        )


# ---------------------------------------------------------------------------
# CashDro (reciclador de efectivo) — SOLO interfaz, fuera de alcance su
# implementación real (ARCHITECTURE.md §5).
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CashDropResult:
    delivered_cents: int
    detail: str | None = None


@runtime_checkable
class CashDroAdapter(Protocol):
    name: str

    async def dispense(
        self, *, terminal_id: UUID, amount_cents: int, reference: str | None = None
    ) -> CashDropResult: ...


class SimulatedCashDroAdapter:
    """Sin CashDro físico: confirma la entrega completa y lo marca como
    simulado. Solo para desarrollo."""

    name = "simulated"

    def __init__(self) -> None:
        self.dispensations: list[dict] = []

    async def dispense(
        self, *, terminal_id: UUID, amount_cents: int, reference: str | None = None
    ) -> CashDropResult:
        self.dispensations.append(
            {"terminal_id": terminal_id, "amount_cents": amount_cents, "reference": reference}
        )
        return CashDropResult(
            delivered_cents=amount_cents,
            detail="Entrega SIMULADA sin CashDro físico",
        )


# ---------------------------------------------------------------------------
# Display de cliente
# ---------------------------------------------------------------------------
@runtime_checkable
class CustomerDisplayAdapter(Protocol):
    name: str

    async def show(self, *, terminal_id: UUID, lines: Sequence[str]) -> None: ...

    async def clear(self, *, terminal_id: UUID) -> None: ...


class NullCustomerDisplayAdapter:
    """Sin display conectado: recuerda lo último que se le habría mostrado."""

    name = "null"

    def __init__(self) -> None:
        self.last_lines: dict[UUID, list[str]] = {}
        self.cleared: list[UUID] = []

    async def show(self, *, terminal_id: UUID, lines: Sequence[str]) -> None:
        self.last_lines[terminal_id] = list(lines)

    async def clear(self, *, terminal_id: UUID) -> None:
        self.cleared.append(terminal_id)


# ---------------------------------------------------------------------------
# Registro de la aplicación: una sola instancia en ``app.state.hardware``
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class HardwareAdapters:
    """Los cinco periféricos que la app puede tocar. Cada campo es
    sustituible a posteriori (tests, drivers de las fases 13-14) sin tocar
    a quien lo consume."""

    drawer: CashDrawerAdapter
    scanner: BarcodeScannerAdapter
    payment_terminal: PaymentTerminalAdapter
    cashdro: CashDroAdapter
    customer_display: CustomerDisplayAdapter

    @classmethod
    def defaults(cls) -> "HardwareAdapters":
        """Juego sin hardware físico: cajón/escáner/display nulos y pinpad/
        CashDro SIMULADOS (todo queda marcado como «SIMULADO» en su
        resultado). Producción exigirá drivers reales."""

        return cls(
            drawer=NullCashDrawerAdapter(),
            scanner=NullBarcodeScannerAdapter(),
            payment_terminal=SimulatedPaymentTerminalAdapter(),
            cashdro=SimulatedCashDroAdapter(),
            customer_display=NullCustomerDisplayAdapter(),
        )

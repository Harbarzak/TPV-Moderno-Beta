"""Matemática pura del cobro (fase 07 · Pagos).

Sin BD ni E/S: el servicio de ventas valida aquí que la suma de pagos cubra
EXACTAMENTE el total de la venta (el backend decide cuándo una venta está
pagada, nunca el frontend). El exceso solo se admite como «importe entregado»
sobre efectivo y se devuelve como cambio; no altera lo que aporta cada pago
al total. Dinero en céntimos enteros (§3).
"""

from collections.abc import Sequence
from dataclasses import dataclass


class SettleError(ValueError):
    """Cobro imposible: ``reason`` permite al servicio mapearlo a ErrorCode."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PaymentInput:
    """Pago de un cierre: lo que aporta al total y, en efectivo, lo entregado."""

    amount_cents: int            # > 0: parte del total que cubre este pago
    tendered_cents: int | None   # solo efectivo: lo que entrega el cliente
    is_cash: bool                # la forma de pago es efectivo


@dataclass(frozen=True, slots=True)
class SettledPayment:
    """Pago liquidado: importe aplicado y cambio a devolver al cliente."""

    amount_cents: int
    change_cents: int  # tendered − amount (0 si no hay importe entregado)


def settle_payments(
    total_cents: int, payments: Sequence[PaymentInput]
) -> tuple[tuple[SettledPayment, ...], int]:
    """Liquidación del cobro: (pagos liquidados, cambio total).

    Reglas:
    - cada pago aporta ``amount > 0``;
    - ``tendered`` solo en efectivo y nunca menor que el importe aplicado;
    - la suma de importes debe ser igual al total: menor es cobro parcial
      insuficiente, mayor es exceso (el cambio sale del «entregado», no de
      inflar importes).
    """

    if total_cents <= 0:
        raise SettleError("total", "El total a cobrar debe ser positivo")

    settled: list[SettledPayment] = []
    applied = 0
    for item in payments:
        if item.amount_cents <= 0:
            raise SettleError("positive", "El importe de cada pago debe ser positivo")
        change = 0
        if item.tendered_cents is not None:
            if not item.is_cash:
                raise SettleError(
                    "tendered_on_non_cash",
                    "Solo el efectivo admite importe entregado (cambio)",
                )
            if item.tendered_cents < item.amount_cents:
                raise SettleError(
                    "tendered_short",
                    "El importe entregado no puede ser menor que el pago",
                )
            change = item.tendered_cents - item.amount_cents
        applied += item.amount_cents
        settled.append(SettledPayment(amount_cents=item.amount_cents, change_cents=change))

    if not settled:
        raise SettleError("insufficient", "El cobro requiere al menos un pago")
    if applied < total_cents:
        raise SettleError(
            "insufficient",
            f"La suma de pagos ({applied}) no cubre el total ({total_cents})",
        )
    if applied > total_cents:
        raise SettleError(
            "excess",
            f"La suma de pagos ({applied}) excede el total ({total_cents}); "
            "el exceso debe entregarse como importe entregado en efectivo",
        )
    return tuple(settled), sum(s.change_cents for s in settled)

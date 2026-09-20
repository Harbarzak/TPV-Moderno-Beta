"""Dominio puro del plugin fiscal (fase 34): sin BD, sin I/O, sin API.

Dos responsabilidades:

1. Transformar el evento genérico de venta (``sale_events.payload``, fase 06)
   en el snapshot que viajará al régimen fiscal (:func:`build_payload`). El
   snapshot es una copia CURADA: solo lo fiscalmente relevante, dinero SIEMPRE
   string (§3), y el régimen nunca lee las tablas de venta.
2. El ciclo de vida del documento fiscal (:data:`TRANSITIONS` /
   :func:`ensure_transition`): máquina de estados pequeña y verificable, la
   misma en schema.sql, modelos y servicio.

Este módulo no sabe NADA de Veri*Factu ni TicketBAI: el motor de ventas
emite eventos genéricos y el plugin fiscal los interpreta (ADR-010).
"""

from dataclasses import dataclass
from datetime import datetime

from app.db.enums import FiscalDocumentStatus, FiscalDocumentType

#: ``sale_events.event_type`` → tipo de documento fiscal.
DOC_TYPES: dict[str, FiscalDocumentType] = {
    "sale_closed": FiscalDocumentType.sale,
    "sale_voided": FiscalDocumentType.void,
    "refund_issued": FiscalDocumentType.refund,
}


class FiscalDomainError(ValueError):
    """Regla fiscal violada (evento desconocido, transición imposible…)."""


def doc_type_for(event_type: str) -> FiscalDocumentType:
    """Tipo de documento fiscal para un evento genérico de venta."""
    doc_type = DOC_TYPES.get(event_type)
    if doc_type is None:
        raise FiscalDomainError(f"Evento de venta no fiscalizable: {event_type!r}")
    return doc_type


@dataclass(frozen=True, slots=True)
class FiscalPayload:
    """Snapshot fiscal listo para persistir en ``fiscal_documents.payload``."""

    doc_type: FiscalDocumentType
    data: dict


def build_payload(
    event_type: str, payload: dict, *, occurred_at: datetime
) -> FiscalPayload:
    """Copia curada del evento genérico de venta hacia el snapshot fiscal.

    El evento ya trae totales, líneas y pagos como dinero string (§3): se
    pasan tal cual — el plugin fiscal NUNCA recalcula importes, solo los
    certifica. Se añade el contexto propio de cada tipo de documento.
    """

    doc_type = doc_type_for(event_type)
    data: dict = {
        "doc_type": doc_type.value,
        "order_id": payload.get("order_id"),
        "occurred_at": occurred_at.isoformat(),
        "totals": payload.get("totals"),
        "lines": payload.get("lines"),
        "payments": payload.get("payments"),
    }
    if event_type == "sale_closed":
        data["change_total"] = payload.get("change_total")
        data["cash_session_id"] = payload.get("cash_session_id")
    elif event_type == "sale_voided":
        data["reason"] = payload.get("reason")
        data["previous_status"] = payload.get("previous_status")
    elif event_type == "refund_issued":
        data["original_order_id"] = payload.get("original_order_id")
        data["reason"] = payload.get("reason")
        data["cash_session_id"] = payload.get("cash_session_id")
    return FiscalPayload(doc_type=doc_type, data=data)


#: Máquina de estados del documento fiscal. ``accepted`` y ``cancelled`` son
#: terminales (un documento aceptado por el régimen jamás se retoca: se
#: rectifica con un documento nuevo).
TRANSITIONS: dict[FiscalDocumentStatus, frozenset[FiscalDocumentStatus]] = {
    FiscalDocumentStatus.pending: frozenset(
        {FiscalDocumentStatus.sent, FiscalDocumentStatus.cancelled}
    ),
    FiscalDocumentStatus.sent: frozenset(
        {FiscalDocumentStatus.accepted, FiscalDocumentStatus.rejected}
    ),
    FiscalDocumentStatus.rejected: frozenset(
        {FiscalDocumentStatus.pending, FiscalDocumentStatus.cancelled}
    ),
    FiscalDocumentStatus.accepted: frozenset(),
    FiscalDocumentStatus.cancelled: frozenset(),
}


def ensure_transition(
    current: FiscalDocumentStatus, target: FiscalDocumentStatus
) -> None:
    """Valida la transición de estado; :class:`FiscalDomainError` si es imposible."""
    if target not in TRANSITIONS[current]:
        raise FiscalDomainError(
            f"Transición fiscal imposible: {current.value} → {target.value}"
        )

"""Tests del dominio y adaptadores del plugin fiscal (fase 34). Sin BD, sin I/O.

Cubre el contrato puro que el resto del plugin asume: el mapeo evento→documento,
el snapshot curado (dinero string intacto, §3), la máquina de estados del
documento y la selección de adaptador (``none`` + esqueletos seleccionables
que fallan con ``FISCAL_NOT_IMPLEMENTED`` hasta que haya desarrollo legal).
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.adapters.fiscal import (
    FiscalError,
    FiscalResult,
    FiscalSubmission,
    NullFiscalAdapter,
    PROVIDERS,
    TicketBaiAdapter,
    VeriFactuAdapter,
    build_fiscal_adapter,
)
from app.db.enums import FiscalDocumentStatus, FiscalDocumentType
from app.domain import fiscal as domain

_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


def _totals():
    # Dinero SIEMPRE string (§3): el plugin fiscal no lo toca.
    return {
        "base": "10.00",
        "tax": "2.10",
        "total": "12.10",
        "slices": [{"rate_bp": 2100, "base": "10.00", "tax": "2.10", "total": "12.10"}],
    }


def _sale_closed_payload():
    return {
        "order_id": str(uuid4()),
        "terminal_id": str(uuid4()),
        "user_id": str(uuid4()),
        "cash_session_id": str(uuid4()),
        "paid_at": "2026-09-14T11:59:00+00:00",
        "totals": _totals(),
        "lines": [{"name": "Café", "quantity": "2.000", "line_total": "3.00"}],
        "payments": [{"kind": "cash", "amount": "5.00"}],
        "change_total": "1.90",
    }


# ---------------------------------------------------------------------------
# Mapeo evento genérico → tipo de documento
# ---------------------------------------------------------------------------
class TestDocTypes:
    def test_mapping_from_sale_events(self):
        assert domain.DOC_TYPES == {
            "sale_closed": FiscalDocumentType.sale,
            "sale_voided": FiscalDocumentType.void,
            "refund_issued": FiscalDocumentType.refund,
        }

    @pytest.mark.parametrize("event_type,doc_type", [
        ("sale_closed", FiscalDocumentType.sale),
        ("sale_voided", FiscalDocumentType.void),
        ("refund_issued", FiscalDocumentType.refund),
    ])
    def test_doc_type_for(self, event_type, doc_type):
        assert domain.doc_type_for(event_type) is doc_type

    def test_unknown_event_rejected(self):
        with pytest.raises(domain.FiscalDomainError):
            domain.doc_type_for("order_opened")


# ---------------------------------------------------------------------------
# Snapshot fiscal curado
# ---------------------------------------------------------------------------
class TestBuildPayload:
    def test_sale_closed_snapshot(self):
        payload = _sale_closed_payload()
        snapshot = domain.build_payload("sale_closed", payload, occurred_at=_NOW)
        assert snapshot.doc_type is FiscalDocumentType.sale
        data = snapshot.data
        assert data["doc_type"] == "sale"
        assert data["order_id"] == payload["order_id"]
        assert data["occurred_at"] == _NOW.isoformat()
        assert data["totals"] == payload["totals"]          # dinero string intacto
        assert data["lines"] == payload["lines"]
        assert data["payments"] == payload["payments"]
        assert data["change_total"] == "1.90"               # propio de sale_closed
        assert data["cash_session_id"] == payload["cash_session_id"]
        # Nada del payload original se cuela sin control: llaves exactas.
        assert set(data) == {
            "doc_type", "order_id", "occurred_at", "totals", "lines",
            "payments", "change_total", "cash_session_id",
        }

    def test_sale_voided_snapshot(self):
        payload = {
            "order_id": str(uuid4()),
            "previous_status": "paid",
            "reason": "error del cajero",
            "voided_by": str(uuid4()),
            "totals": None,
        }
        snapshot = domain.build_payload("sale_voided", payload, occurred_at=_NOW)
        assert snapshot.doc_type is FiscalDocumentType.void
        data = snapshot.data
        assert data["doc_type"] == "void"
        assert data["reason"] == "error del cajero"
        assert data["previous_status"] == "paid"
        # Campos propios de otros tipos: fuera. totals/lines/payments siguen en
        # la base (None en el borrador anulado), como el evento los trajo.
        assert "change_total" not in data and "cash_session_id" not in data
        assert data["totals"] is None and data["payments"] is None

    def test_refund_snapshot_keeps_original(self):
        original = str(uuid4())
        payload = {
            "original_order_id": original,
            "refund_order_id": str(uuid4()),
            "reason": "devolución parcial",
            "cash_session_id": str(uuid4()),
            "totals": _totals(),
            "lines": [],
            "payments": [{"kind": "cash", "amount": "-5.00"}],
        }
        snapshot = domain.build_payload("refund_issued", payload, occurred_at=_NOW)
        assert snapshot.doc_type is FiscalDocumentType.refund
        data = snapshot.data
        assert data["doc_type"] == "refund"
        assert data["original_order_id"] == original
        assert data["reason"] == "devolución parcial"
        assert data["payments"] == payload["payments"]      # importes negativos intactos


# ---------------------------------------------------------------------------
# Máquina de estados del documento fiscal
# ---------------------------------------------------------------------------
class TestTransitions:
    @pytest.mark.parametrize("current,target", [
        (FiscalDocumentStatus.pending, FiscalDocumentStatus.sent),
        (FiscalDocumentStatus.pending, FiscalDocumentStatus.cancelled),
        (FiscalDocumentStatus.sent, FiscalDocumentStatus.accepted),
        (FiscalDocumentStatus.sent, FiscalDocumentStatus.rejected),
        (FiscalDocumentStatus.rejected, FiscalDocumentStatus.pending),   # reencolar
        (FiscalDocumentStatus.rejected, FiscalDocumentStatus.cancelled),
    ])
    def test_valid(self, current, target):
        domain.ensure_transition(current, target)  # no explota

    @pytest.mark.parametrize("current,target", [
        (FiscalDocumentStatus.pending, FiscalDocumentStatus.accepted),   # sin enviar
        (FiscalDocumentStatus.sent, FiscalDocumentStatus.pending),
        (FiscalDocumentStatus.pending, FiscalDocumentStatus.rejected),
        (FiscalDocumentStatus.accepted, FiscalDocumentStatus.rejected),  # terminal
        (FiscalDocumentStatus.accepted, FiscalDocumentStatus.pending),
        (FiscalDocumentStatus.cancelled, FiscalDocumentStatus.pending),  # terminal
    ])
    def test_invalid(self, current, target):
        with pytest.raises(domain.FiscalDomainError):
            domain.ensure_transition(current, target)


# ---------------------------------------------------------------------------
# Selección de adaptador (TPV_FISCAL_PROVIDER)
# ---------------------------------------------------------------------------
def _submission() -> FiscalSubmission:
    return FiscalSubmission(
        document_id=uuid4(),
        order_id=uuid4(),
        doc_type="sale",
        payload={"doc_type": "sale", "totals": _totals()},
        attempts=0,
    )


class TestAdapterFactory:
    @pytest.mark.parametrize("provider,cls", [
        ("none", NullFiscalAdapter),
        ("verifactu", VeriFactuAdapter),
        ("ticketbai", TicketBaiAdapter),
    ])
    def test_build_known(self, provider, cls):
        adapter = build_fiscal_adapter(provider)
        assert isinstance(adapter, cls)
        assert adapter.name == provider

    def test_unknown_provider_fails_fast(self):
        with pytest.raises(ValueError, match="TPV_FISCAL_PROVIDER"):
            build_fiscal_adapter("sii")

    def test_providers_catalog(self):
        assert PROVIDERS == ("none", "verifactu", "ticketbai")


class TestAdapterBehavior:
    def test_none_consumes_without_documents(self):
        adapter = build_fiscal_adapter("none")
        result = asyncio.run(adapter.submit(_submission()))
        assert isinstance(result, FiscalResult)
        assert result.accepted is True
        assert result.external_ref is None
        assert "sin régimen" in result.detail["noop"]

    @pytest.mark.parametrize("provider", ["verifactu", "ticketbai"])
    def test_skeletons_are_selectable_but_fail_loud(self, provider):
        adapter = build_fiscal_adapter(provider)
        with pytest.raises(FiscalError) as excinfo:
            asyncio.run(adapter.submit(_submission()))
        assert excinfo.value.code == "FISCAL_NOT_IMPLEMENTED"

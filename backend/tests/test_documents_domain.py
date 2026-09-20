"""Tests de dominio de documentos (fase 09): matemática y renders.

Puros y sin BD: formato de números de serie, fusión e inversión de desgloses
de IVA, cabecera con/sin logo y payloads congelados de ticket y factura. El
dinero viaja como string (§3) y las sumas son ``Decimal`` exactas.
"""

import base64
from decimal import Decimal

from app.domain import documents as docs


def _slice(rate_bp: int, base: str, tax: str) -> dict:
    total = format(Decimal(base) + Decimal(tax), "f")
    return {"rate_bp": rate_bp, "base": base, "tax": tax, "total": total}


def _summary(base: str, tax: str, slices: list[dict]) -> dict:
    return {
        "base": base,
        "tax": tax,
        "total": format(Decimal(base) + Decimal(tax), "f"),
        "slices": slices,
    }


# ---------------------------------------------------------------------------
# Numeración
# ---------------------------------------------------------------------------
def test_formato_de_numero_de_ticket():
    assert docs.format_ticket_number("A", 42) == "A-000042"
    assert docs.format_ticket_number("Z", 1) == "Z-000001"
    # Sin truncar series largas: seis dígitos son el MÍNIMO.
    assert docs.format_ticket_number("A", 1234567) == "A-1234567"


def test_formato_de_numero_de_factura():
    assert docs.format_invoice_number("FAC", 2026, 7) == "FAC 2026/000007"
    assert docs.format_invoice_number("R", 2026, 123456) == "R 2026/123456"


def test_extension_de_logo_admite_solo_png_y_jpeg():
    assert docs.logo_extension("image/png") == "png"
    assert docs.logo_extension("image/jpeg") == "jpg"
    assert docs.logo_extension("IMAGE/PNG") == "png"  # insensible a mayúsculas
    assert docs.logo_extension("image/gif") is None
    assert docs.logo_extension("application/pdf") is None


def test_data_uri_del_logo():
    raw = b"\x89PNG\r\n\x1a\n"
    uri = docs.build_logo_data_uri("image/png", raw)
    assert uri == "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    # El embebido se decodifica de vuelta al fichero original.
    assert base64.b64decode(uri.split(",", 1)[1]) == raw


# ---------------------------------------------------------------------------
# Desgloses de IVA: fusión (factura de N ventas) e inversión (rectificativa)
# ---------------------------------------------------------------------------
def test_merge_de_desgloses_de_varias_ordenes():
    s1 = _summary("10.00", "2.10", [_slice(2100, "10.00", "2.10")])
    s2 = _summary("5.00", "0.50", [_slice(1000, "5.00", "0.50")])
    s3 = _summary("20.00", "4.20", [_slice(2100, "20.00", "4.20")])

    merged = docs.merge_tax_summaries([s1, s2, s3])

    assert merged["base"] == "35.00"
    assert merged["tax"] == "6.80"
    assert merged["total"] == "41.80"
    # Un solo tramo por tipo, ordenado por rate_bp.
    assert merged["slices"] == [
        _slice(1000, "5.00", "0.50"),
        _slice(2100, "30.00", "6.30"),
    ]


def test_merge_con_lista_vacia_o_desgloses_vacios():
    empty = docs.merge_tax_summaries([])
    assert (empty["base"], empty["tax"], empty["total"]) == ("0", "0", "0")
    assert empty["slices"] == []

    # Desgloses vacíos (órdenes sin resumen) no rompen la fusión.
    assert docs.merge_tax_summaries([{}, None])["total"] == "0"


def test_negate_de_desglose_para_rectificativa_total():
    summary = _summary("35.00", "6.80", [_slice(1000, "5.00", "0.50"),
                                         _slice(2100, "30.00", "6.30")])

    negated = docs.negate_tax_summary(summary)

    assert negated["base"] == "-35.00"
    assert negated["tax"] == "-6.80"
    assert negated["total"] == "-41.80"
    # Invierte importes, NO el tipo de IVA.
    assert [s["rate_bp"] for s in negated["slices"]] == [1000, 2100]
    assert negated["slices"][1] == _slice(2100, "-30.00", "-6.30")


# ---------------------------------------------------------------------------
# Cabecera de negocio (datos fiscales + logo opcional)
# ---------------------------------------------------------------------------
def test_cabecera_vacia_sin_datos_ni_logo():
    assert docs.build_header({}, None) == {"business": None, "logo": None}


def test_cabecera_filtra_campos_vacios():
    header = docs.build_header({"name": "Bar Ejemplo SL", "tax_id": "", "phone": None}, None)
    assert header == {"business": {"name": "Bar Ejemplo SL"}, "logo": None}


def test_cabecera_con_logo():
    uri = docs.build_logo_data_uri("image/jpeg", b"jpg")
    header = docs.build_header({"name": "Bar", "tax_id": "B12345678"}, uri)
    assert header["logo"] == uri
    assert header["business"] == {"name": "Bar", "tax_id": "B12345678"}


# ---------------------------------------------------------------------------
# Payloads congelados
# ---------------------------------------------------------------------------
def test_payload_de_ticket():
    payload = docs.build_ticket_payload(
        doc_number="A-000001",
        series="A",
        number=1,
        issued_at="2026-09-11T12:00:00+00:00",
        header={"business": None, "logo": None},
        order={"id": "o-1", "type": "sale", "original_order_id": None,
               "original_doc_number": None, "customer_id": None},
        lines=[{"name": "Café solo", "quantity": "2", "unit_price": "1.50",
                "tax_rate": "21.00", "discount_pct": "0", "base": "2.48",
                "total": "3.00", "extra": "se descarta"}],
        totals={"base": "2.48", "tax": "0.52", "total": "3.00", "slices": []},
        payments=[{"code": "CASH", "kind": "cash", "amount": "3.00", "secret": "x"}],
        change_total="0.00",
    )

    assert payload["kind"] == "ticket"
    assert payload["doc_number"] == "A-000001"
    assert payload["order"]["type"] == "sale"
    # El render de línea se congela SIN claves ajenas al documento impreso.
    assert payload["lines"] == [{"name": "Café solo", "quantity": "2",
                                 "unit_price": "1.50", "tax_rate": "21.00",
                                 "discount_pct": "0", "base": "2.48", "total": "3.00"}]
    # Pagos: solo lo imprimible (código, tipo e importe).
    assert payload["payments"] == [{"code": "CASH", "kind": "cash", "amount": "3.00"}]
    assert payload["change_total"] == "0.00"


def test_payload_de_factura_con_y_sin_rectificativa():
    common = dict(
        doc_number="FAC 2026/000001", series="FAC", year=2026, number=1,
        issue_date="2026-09-11", header={"business": None, "logo": None},
        customer={"id": "c-1", "name": "Cliente SL", "tax_id": "B12345678"},
        orders=[{"order_id": "o-1", "doc_number": "A-000001", "total": "3.00"}],
        totals={"base": "2.48", "tax": "0.52", "total": "3.00", "slices": []},
    )

    invoice = docs.build_invoice_payload(**common)
    assert invoice["kind"] == "invoice"
    assert invoice["customer"]["tax_id"] == "B12345678"
    assert invoice["rectifies"] is None

    rectified = docs.build_invoice_payload(
        **common,
        rectifies={"invoice_id": "f-1", "doc_number": "FAC 2026/000001",
                   "mode": docs.RECTIFICATION_PARTIAL, "reason": "Devolución"},
    )
    assert rectified["rectifies"]["mode"] == "partial"
    assert rectified["rectifies"]["doc_number"] == "FAC 2026/000001"


def test_constantes_de_rectificacion():
    assert docs.RECTIFICATION_TOTAL == "total"
    assert docs.RECTIFICATION_PARTIAL == "partial"
    assert docs.LOGO_MIME_EXTENSIONS == {"image/png": "png", "image/jpeg": "jpg"}

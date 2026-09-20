"""Matemática y formato de documentos comerciales (fase 09 · Tickets y facturas).

Puro y sin BD: formato de números de serie, fusión de desgloses de IVA y
construcción del render congelado (payload JSONB) de tickets, facturas y
rectificativas. El dinero viaja como string (§3) y las sumas se hacen con
``Decimal`` — sin división, así que no hay redondeo nuevo: los importes ya
llegan redondeados half-up desde el motor de ventas (:mod:`app.domain.sales`).

Nada específico de Veri*Factu/TicketBAI: esa integración es un plugin futuro.
"""

from decimal import Decimal
from typing import Any

# Formato de render: seis dígitos, ceros a la izquierda ("A-000042").
_NUMBER_WIDTH = 6

# Modos de rectificación de una factura.
RECTIFICATION_TOTAL = "total"      # anula la factura completa (importes invertidos)
RECTIFICATION_PARTIAL = "partial"  # rectifica una devolución (importes de la orden negativa)

# Mime types admitidos para el logo de cabecera (extensión del fichero en volumen).
LOGO_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
}


def format_ticket_number(series: str, number: int) -> str:
    """Número comercial de ticket: ``A-000042`` (serie por terminal)."""

    return f"{series}-{number:0{_NUMBER_WIDTH}d}"


def format_invoice_number(series: str, year: int, number: int) -> str:
    """Número legal de factura: ``FAC 2026/000042`` (serie por año)."""

    return f"{series} {year}/{number:0{_NUMBER_WIDTH}d}"


def logo_extension(mime: str) -> str | None:
    """Extensión de fichero para un mime de logo admitido (None si no lo es)."""

    return LOGO_MIME_EXTENSIONS.get(mime.lower())


def build_logo_data_uri(mime: str, raw: bytes) -> str:
    """Data URI del logo para embeber en el payload (snapshot histórico)."""

    import base64

    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


# ---------------------------------------------------------------------------
# Desgloses de IVA (formato ``_totals_dict`` del motor de ventas)
# ---------------------------------------------------------------------------
def _money_sum(values: list[str]) -> str:
    total = sum((Decimal(v) for v in values), Decimal(0))
    return format(total, "f")


def merge_tax_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Fusiona desgloses de IVA de varias órdenes en uno (factura de N ventas).

    Cada desglose tiene la forma ``{"base", "tax", "total",
    "slices": [{"rate_bp", "base", "tax", "total"}]}`` con dinero string.
    """

    by_rate: dict[int, dict[str, Decimal]] = {}
    for summary in summaries:
        for slice_ in (summary or {}).get("slices", []):
            slot = by_rate.setdefault(
                int(slice_["rate_bp"]),
                {"base": Decimal(0), "tax": Decimal(0), "total": Decimal(0)},
            )
            slot["base"] += Decimal(slice_["base"])
            slot["tax"] += Decimal(slice_["tax"])
            slot["total"] += Decimal(slice_["total"])

    return {
        "base": _money_sum([(s or {}).get("base", "0") for s in summaries]),
        "tax": _money_sum([(s or {}).get("tax", "0") for s in summaries]),
        "total": _money_sum([(s or {}).get("total", "0") for s in summaries]),
        "slices": [
            {
                "rate_bp": rate_bp,
                "base": format(slot["base"], "f"),
                "tax": format(slot["tax"], "f"),
                "total": format(slot["total"], "f"),
            }
            for rate_bp, slot in sorted(by_rate.items())
        ],
    }


def negate_tax_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Invierte el signo de un desglose (rectificativa total de una factura)."""

    def _neg(value: str) -> str:
        return format(-Decimal(value), "f")

    return {
        "base": _neg(summary["base"]),
        "tax": _neg(summary["tax"]),
        "total": _neg(summary["total"]),
        "slices": [
            {
                "rate_bp": int(slice_["rate_bp"]),
                "base": _neg(slice_["base"]),
                "tax": _neg(slice_["tax"]),
                "total": _neg(slice_["total"]),
            }
            for slice_ in summary.get("slices", [])
        ],
    }


# ---------------------------------------------------------------------------
# Cabecera de negocio (nombre fiscal + logo opcional)
# ---------------------------------------------------------------------------
def build_header(business: dict[str, str] | None, logo_uri: str | None) -> dict[str, Any]:
    """Cabecera impresa: datos fiscales del negocio y logo embebido si existe.

    Sin logo configurado (o fichero ausente) el documento se genera sin él;
    sin datos fiscales, la cabecera queda vacía. El logo viaja como data URI
    DENTRO del payload: cambiar el logo no altera los documentos ya emitidos.
    """

    fields = {k: v for k, v in (business or {}).items() if v}
    return {
        "business": fields or None,
        "logo": logo_uri,
    }


# ---------------------------------------------------------------------------
# Renders congelados (payload JSONB)
# ---------------------------------------------------------------------------
def _render_line(line: dict[str, Any]) -> dict[str, Any]:
    """Snapshot imprimible de una línea de venta (importes ya congelados)."""

    return {
        "name": line["name"],
        "quantity": line["quantity"],
        "unit_price": line["unit_price"],
        "tax_rate": line["tax_rate"],
        "discount_pct": line["discount_pct"],
        "base": line["base"],
        "total": line["total"],
    }


def build_ticket_payload(
    *,
    doc_number: str,
    series: str,
    number: int,
    issued_at: str,
    header: dict[str, Any],
    order: dict[str, Any],
    lines: list[dict[str, Any]],
    totals: dict[str, Any],
    payments: list[dict[str, Any]],
    change_total: str,
) -> dict[str, Any]:
    """Payload del ticket (comercial). ``order`` describe la venta impresa:

    ``{"id", "type": "sale"|"refund", "original_order_id", "original_doc_number",
    "customer_id"}`` — en un ticket de devolución la orden es negativa y
    referencia a la venta original.
    """

    return {
        "kind": "ticket",
        "doc_number": doc_number,
        "series": series,
        "number": number,
        "issued_at": issued_at,
        "header": header,
        "order": order,
        "lines": [_render_line(line) for line in lines],
        "totals": totals,
        "payments": [
            {"code": p["code"], "kind": p["kind"], "amount": p["amount"]}
            for p in payments
        ],
        "change_total": change_total,
    }


def build_invoice_payload(
    *,
    doc_number: str,
    series: str,
    year: int,
    number: int,
    issue_date: str,
    header: dict[str, Any],
    customer: dict[str, Any],
    orders: list[dict[str, Any]],
    totals: dict[str, Any],
    rectifies: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Payload de factura (documento fiscal básico, sin Veri*Factu/TicketBAI).

    ``orders`` embebe por cada venta facturada su referencia de ticket, líneas
    y pagos. ``rectifies`` (rectificativas): ``{"invoice_id", "doc_number",
    "mode", "reason"}``.
    """

    return {
        "kind": "invoice",
        "doc_number": doc_number,
        "series": series,
        "year": year,
        "number": number,
        "issue_date": issue_date,
        "header": header,
        "customer": customer,
        "orders": orders,
        "totals": totals,
        "rectifies": rectifies,
    }

"""Matemática y política de impresión (fase 10 · Impresión).

Puro y sin BD: dimensionado del logo de cabecera al ancho real de la
impresora térmica, política de reintentos de la cola y máquina de estados de
un ``PrintJob``. La lógica de VENTAS nunca imprime: quien emite un documento
entrega aquí su payload congelado y punto (``services.documents`` →
``services.printing``).
"""

import base64
import binascii
from datetime import datetime
from typing import Any

from app.db.enums import PrinterKind, PrintJobKind, PrintJobStatus

# Reintentos controlados: como máximo MAX_ATTEMPTS envíos automáticos por
# trabajo; agotados, el job queda ``failed`` hasta que un operador lo
# recupera (reintento manual, auditado).
MAX_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 2       # tras el 1er fallo: 2 s, 6 s, 18 s… (2·3^(n-1))
_RETRY_CAP_SECONDS = 300

# Papel térmico (203 dpi ≈ 8 px/mm): columnas → ancho en píxeles para la
# imagen de cabecera (58 mm ≈ 384 px, 80 mm ≈ 512 px, 112 mm ≈ 576 px).
THERMAL_WIDTH_PX = {32: 384, 42: 512, 48: 576}


def thermal_width_px(width_chars: int) -> int:
    """Ancho de imagen útil para una impresora de ``width_chars`` columnas."""

    return THERMAL_WIDTH_PX.get(width_chars, 512)


# ---------------------------------------------------------------------------
# Reintentos (backoff): la política vive aquí, no en el hardware
# ---------------------------------------------------------------------------
def retry_delay_seconds(attempts: int) -> int:
    """Espera antes del siguiente reintento automático.

    ``attempts`` = intentos YA hechos; 2·3^(n-1) con tope de 5 minutos.
    """

    if attempts <= 0:
        return 0
    return min(_RETRY_BASE_SECONDS * 3 ** (attempts - 1), _RETRY_CAP_SECONDS)


def retry_due(attempts: int, last_attempt_at: datetime | None, now: datetime) -> bool:
    """¿Toca reintentar? Inmediato si nunca se envió; si no, cuando el
    backoff del número de intentos está vencido."""

    if attempts <= 0 or last_attempt_at is None:
        return True
    return (now - last_attempt_at).total_seconds() >= retry_delay_seconds(attempts)


# ---------------------------------------------------------------------------
# Logo de cabecera: data URI → imagen escalada al ancho de la impresora
# ---------------------------------------------------------------------------
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def split_data_uri(uri: str) -> tuple[str, bytes] | None:
    """``data:<mime>;base64,<datos>`` → (mime, bytes); None si no es válida."""

    if not uri.startswith("data:") or ";base64," not in uri:
        return None
    mime, _, data = uri[5:].partition(";base64,")
    if not mime:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    return mime, raw


def _png_dimensions(raw: bytes) -> tuple[int, int] | None:
    """El primer chunk de un PNG es siempre IHDR: dimensiones en bytes 16-24."""

    if len(raw) < 24 or not raw.startswith(_PNG_SIGNATURE):
        return None
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def _jpeg_dimensions(raw: bytes) -> tuple[int, int] | None:
    """Recorre los marcadores JPEG hasta el primer SOF (dimensiones)."""

    if len(raw) < 4 or raw[0] != 0xFF or raw[1] != 0xD8:
        return None
    i, n = 2, len(raw)
    while i + 4 <= n:
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # sin longitud
            i += 2
            continue
        if marker == 0xD9:  # EOI: no hubo SOF
            return None
        segment_length = int.from_bytes(raw[i + 2 : i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOF
            if i + 9 > n:
                return None
            height = int.from_bytes(raw[i + 5 : i + 7], "big")
            width = int.from_bytes(raw[i + 7 : i + 9], "big")
            return width, height
        if segment_length < 2:
            return None
        i += 2 + segment_length
    return None


def image_dimensions(raw: bytes) -> tuple[int, int] | None:
    """(ancho, alto) de un png/jpeg; None si el formato no se reconoce."""

    return _png_dimensions(raw) or _jpeg_dimensions(raw)


def fit_to_width(width: int, height: int, max_px: int) -> tuple[int, int]:
    """Escala al ancho de la impresora respetando la proporción.

    Nunca amplía: si la imagen ya cabe, se deja como está (el driver de
    impresión decide el centrado). Altura mínima de 1 píxel.
    """

    if width <= 0 or height <= 0 or max_px <= 0:
        return 0, 0
    if width <= max_px:
        return width, height
    # Half-up como en el resto del proyecto (round() de Python es «banker's»).
    return max_px, max(1, int(height * max_px / width + 0.5))


def header_logo_for_printer(payload: dict[str, Any], width_chars: int) -> dict[str, Any] | None:
    """Logo de la cabecera del documento, listo para la impresora.

    Devuelve ``{"mime", "data" (base64), "width", "height"}`` con el ancho
    ajustado a las columnas de la impresora térmica, o None si el documento
    no lleva logo o la imagen no se puede interpretar: se imprime sin él
    antes que romper la cola (el payload congelado lo conserva intacto).
    """

    uri = ((payload or {}).get("header") or {}).get("logo")
    if not isinstance(uri, str):
        return None
    parsed = split_data_uri(uri)
    if parsed is None:
        return None
    mime, raw = parsed
    dimensions = image_dimensions(raw)
    if dimensions is None:
        return None
    width, height = fit_to_width(dimensions[0], dimensions[1], thermal_width_px(width_chars))
    return {
        "mime": mime,
        "data": base64.b64encode(raw).decode("ascii"),
        "width": width,
        "height": height,
    }


# ---------------------------------------------------------------------------
# Encolado: qué impresora imprime cada tipo de job
# ---------------------------------------------------------------------------
# «Cocina» y «copia» no son adaptadores: la cocina es un printer_kind y la
# copia un job nuevo con el payload congelado. None = cualquier impresora.
PRINTER_KIND_FOR_JOB: dict[PrintJobKind, PrinterKind | None] = {
    PrintJobKind.ticket: PrinterKind.receipt,
    PrintJobKind.invoice: PrinterKind.invoice,
    PrintJobKind.kitchen: PrinterKind.kitchen,
    PrintJobKind.report_x: PrinterKind.receipt,
    PrintJobKind.report_z: PrinterKind.receipt,
    PrintJobKind.test: None,
}


def printer_kind_for_job(kind: PrintJobKind) -> PrinterKind | None:
    """Tipo de impresora que atiende un job (None: cualquiera)."""

    return PRINTER_KIND_FOR_JOB[kind]


# ---------------------------------------------------------------------------
# Máquina de estados de la cola (ARCHITECTURE.md §9.1)
# ---------------------------------------------------------------------------
# ``sent`` = entregado al adaptador, a la espera de confirmación de impresión.
# ``failed`` es terminal salvo reintento (manual o automático con backoff).
_VALID_TRANSITIONS: dict[PrintJobStatus, set[PrintJobStatus]] = {
    PrintJobStatus.queued: {
        PrintJobStatus.sent,
        PrintJobStatus.failed,
        PrintJobStatus.cancelled,
    },
    PrintJobStatus.sent: {
        PrintJobStatus.printed,
        PrintJobStatus.failed,
        PrintJobStatus.cancelled,
    },
    PrintJobStatus.failed: {PrintJobStatus.queued, PrintJobStatus.cancelled},
    PrintJobStatus.printed: set(),
    PrintJobStatus.cancelled: set(),
}


def can_transition(current: PrintJobStatus, target: PrintJobStatus) -> bool:
    """¿Es válido pasar el job de ``current`` a ``target``?"""

    return target in _VALID_TRANSITIONS[current]

"""Dominio de impresión (fase 10): puro, sin BD.

Logo térmico (dimensiones PNG/JPEG, ajuste al ancho de la impresora),
política de reintentos (backoff) y máquina de estados de la cola.
"""

import base64
from datetime import datetime, timedelta, timezone

from app.db.enums import PrinterKind, PrintJobKind, PrintJobStatus
from app.domain import printing as pr

# ---------------------------------------------------------------------------
# Fábricas de imágenes mínimas (solo cabecera: no hace falta decodificar)
# ---------------------------------------------------------------------------
def _png_bytes(width: int, height: int) -> bytes:
    header = (
        b"\x00\x00\x00\rIHDR"  # longitud + tipo del primer chunk (siempre IHDR)
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"  # bit depth, color RGBA, …
    )
    return pr._PNG_SIGNATURE + header


def _jpeg_bytes(width: int, height: int) -> bytes:
    # SOF0: marcador + longitud(2) + precisión(1) + alto(2) + ancho(2) + componentes
    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"


def _logo_uri(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


# ---------------------------------------------------------------------------
# Ancho térmico
# ---------------------------------------------------------------------------
def test_thermal_width_px_por_columnas():
    assert pr.thermal_width_px(32) == 384
    assert pr.thermal_width_px(42) == 512
    assert pr.thermal_width_px(48) == 576


def test_thermal_width_px_desconocido_usa_80mm():
    assert pr.thermal_width_px(999) == 512


# ---------------------------------------------------------------------------
# Reintentos (backoff 2·3^(n-1), tope 300 s)
# ---------------------------------------------------------------------------
def test_retry_delay_seconds_backoff_con_tope():
    assert pr.retry_delay_seconds(0) == 0
    assert pr.retry_delay_seconds(1) == 2
    assert pr.retry_delay_seconds(2) == 6
    assert pr.retry_delay_seconds(3) == 18
    assert pr.retry_delay_seconds(10) == 300  # nunca más de 5 minutos


def test_retry_due_inmediato_si_nunca_se_envio():
    now = datetime.now(timezone.utc)
    assert pr.retry_due(0, now, now)
    assert pr.retry_due(2, None, now)


def test_retry_due_respeta_el_backoff():
    now = datetime.now(timezone.utc)
    # 1 intento hace 1 s (delay 2 s): toca esperar; hace 2 s: toca reintentar.
    assert not pr.retry_due(1, now - timedelta(seconds=1), now)
    assert pr.retry_due(1, now - timedelta(seconds=2), now)
    # 2 intentos (delay 6 s).
    assert not pr.retry_due(2, now - timedelta(seconds=5), now)
    assert pr.retry_due(2, now - timedelta(seconds=6), now)


# ---------------------------------------------------------------------------
# data URI y dimensiones de imagen
# ---------------------------------------------------------------------------
def test_split_data_uri_valida_redondea_a_bytes():
    raw = b"logo-bytes"
    assert pr.split_data_uri(_logo_uri(raw)) == ("image/png", raw)


def test_split_data_uri_invalidas_devuelven_none():
    assert pr.split_data_uri("https://ejemplo.es/logo.png") is None  # no es data URI
    assert pr.split_data_uri("data:image/png,xx") is None  # sin base64
    assert pr.split_data_uri("data:;base64,aG9sYQ==") is None  # sin mime
    assert pr.split_data_uri("data:image/png;base64,!!!!") is None  # base64 roto


def test_image_dimensions_png():
    assert pr.image_dimensions(_png_bytes(384, 120)) == (384, 120)


def test_image_dimensions_jpeg():
    assert pr.image_dimensions(_jpeg_bytes(800, 300)) == (800, 300)


def test_image_dimensions_desconocido_o_truncado():
    assert pr.image_dimensions(b"GIF89a") is None
    assert pr.image_dimensions(_png_bytes(10, 10)[:12]) is None  # cortado antes del IHDR
    assert pr.image_dimensions(b"") is None


# ---------------------------------------------------------------------------
# Ajuste al ancho de la impresora (nunca amplía, alto proporcional)
# ---------------------------------------------------------------------------
def test_fit_to_width_no_amplia():
    assert pr.fit_to_width(384, 120, 512) == (384, 120)


def test_fit_to_width_reduce_respetando_proporcion():
    assert pr.fit_to_width(1024, 512, 512) == (512, 256)
    assert pr.fit_to_width(1024, 300, 384) == (384, 113)


def test_fit_to_width_altura_minima_un_pixel():
    assert pr.fit_to_width(2000, 1, 500) == (500, 1)


def test_fit_to_width_entradas_invalidas():
    assert pr.fit_to_width(0, 100, 512) == (0, 0)
    assert pr.fit_to_width(100, 100, 0) == (0, 0)


# ---------------------------------------------------------------------------
# Logo de cabecera listo para la impresora
# ---------------------------------------------------------------------------
def _payload_con_logo(raw: bytes) -> dict:
    return {"header": {"logo": _logo_uri(raw)}}


def test_header_logo_ajusta_al_ancho_de_cada_impresora():
    payload = _payload_con_logo(_png_bytes(1024, 256))
    wide = pr.header_logo_for_printer(payload, 48)
    assert wide == {"mime": "image/png", "data": wide["data"], "width": 576, "height": 144}
    assert base64.b64decode(wide["data"]) == _png_bytes(1024, 256)  # bytes intactos

    narrow = pr.header_logo_for_printer(payload, 32)
    assert narrow["width"] == 384
    assert narrow["height"] == 96


def test_header_logo_que_ya_cabe_no_se_toca():
    logo = pr.header_logo_for_printer(_payload_con_logo(_png_bytes(300, 90)), 42)
    assert logo["width"] == 300 and logo["height"] == 90


def test_header_logo_jpeg():
    logo = pr.header_logo_for_printer(
        {"header": {"logo": _logo_uri(_jpeg_bytes(600, 200), "image/jpeg")}}, 42
    )
    assert logo["mime"] == "image/jpeg"
    assert logo["width"] == 512 and logo["height"] == 171


def test_header_logo_sin_logo_o_roto_devuelve_none():
    assert pr.header_logo_for_printer({}, 42) is None
    assert pr.header_logo_for_printer({"header": {"logo": None}}, 42) is None
    assert pr.header_logo_for_printer({"header": {"logo": "data:image/png;base64,!!!!"}}, 42) is None
    # Formato no interpretado: se imprime sin logo, nunca rompe la cola.
    assert pr.header_logo_for_printer({"header": {"logo": _logo_uri(b"GIF89a...")}}, 42) is None
    assert pr.header_logo_for_printer(None, 42) is None


# ---------------------------------------------------------------------------
# Mapeo job → impresora y máquina de estados
# ---------------------------------------------------------------------------
def test_printer_kind_for_job():
    assert pr.printer_kind_for_job(PrintJobKind.ticket) is PrinterKind.receipt
    assert pr.printer_kind_for_job(PrintJobKind.invoice) is PrinterKind.invoice
    assert pr.printer_kind_for_job(PrintJobKind.kitchen) is PrinterKind.kitchen
    assert pr.printer_kind_for_job(PrintJobKind.report_x) is PrinterKind.receipt
    assert pr.printer_kind_for_job(PrintJobKind.report_z) is PrinterKind.receipt
    assert pr.printer_kind_for_job(PrintJobKind.test) is None  # cualquier impresora


def test_can_transition_flujo_normal():
    ok = pr.can_transition
    assert ok(PrintJobStatus.queued, PrintJobStatus.sent)
    assert ok(PrintJobStatus.sent, PrintJobStatus.printed)
    assert ok(PrintJobStatus.sent, PrintJobStatus.failed)
    assert ok(PrintJobStatus.failed, PrintJobStatus.queued)  # reintento


def test_can_transition_terminales_y_saltos_ilegales():
    ok = pr.can_transition
    assert not ok(PrintJobStatus.printed, PrintJobStatus.queued)  # printed es terminal
    assert not ok(PrintJobStatus.cancelled, PrintJobStatus.queued)
    assert not ok(PrintJobStatus.queued, PrintJobStatus.printed)  # sin pasar por sent
    assert not ok(PrintJobStatus.sent, PrintJobStatus.queued)


def test_max_attempts_y_politica_controlada():
    # 3 intentos automáticos como máximo; después, intervención del operador.
    assert pr.MAX_ATTEMPTS == 3

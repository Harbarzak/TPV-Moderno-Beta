"""Tests unitarios de idempotencia (fase 14 · Offline): sin base de datos.

Cubren las piezas puras — normalización de la cabecera (``parse_key``),
huella determinista (``fingerprint``), ventana de dedupe (``TTL``),
inmutabilidad de los contratos ``Replay``/``Idempotency`` y la traducción de
la cabecera HTTP a contexto (``app.api.idempotency.idempotency_of``, incluido
el cobro, §4.1, que EXIGE la clave). La mecánica de replay contra PostgreSQL
vive en ``test_idempotency_api.py`` (requiere ``TPV_TEST_DATABASE_URL``).
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.api.idempotency import idempotency_of
from app.core.errors import AppError, ErrorCode
from app.services.idempotency import (
    HEADER,
    TTL,
    Idempotency,
    Replay,
    fingerprint,
    parse_key,
)

CLOSE_PATH = "/api/v1/sales/orders/11111111-1111-1111-1111-111111111111/close"


# ---------------------------------------------------------------------------
# parse_key: la cabecera ausente, vacía, sucia o malformada
# ---------------------------------------------------------------------------
def test_clave_ausente():
    assert parse_key(None) is None


def test_clave_vacia_cuenta_como_ausente():
    assert parse_key("") is None
    assert parse_key("   ") is None


def test_clave_se_recorta():
    assert parse_key("  abc-123  ") == "abc-123"


def test_clave_demasiado_larga_es_422():
    with pytest.raises(AppError) as err:
        parse_key("k" * 201)
    assert err.value.status_code == 422
    assert err.value.code == ErrorCode.VALIDATION_ERROR
    assert HEADER in err.value.message


def test_clave_en_el_limite_es_valida():
    assert parse_key("k" * 200) == "k" * 200


# ---------------------------------------------------------------------------
# fingerprint: huella determinista de método + ruta + contenido validado
# ---------------------------------------------------------------------------
def test_huella_determinista():
    assert fingerprint("POST", CLOSE_PATH, '{"a":1}') == fingerprint("POST", CLOSE_PATH, '{"a":1}')


def test_huella_cambia_con_el_cuerpo():
    assert fingerprint("POST", CLOSE_PATH, '{"a":1}') != fingerprint("POST", CLOSE_PATH, '{"a":2}')


def test_huella_cambia_con_la_ruta():
    assert fingerprint("POST", CLOSE_PATH, None) != fingerprint("POST", CLOSE_PATH + "x", None)


def test_huella_cambia_con_el_metodo():
    assert fingerprint("POST", CLOSE_PATH, None) != fingerprint("PUT", CLOSE_PATH, None)


def test_huella_normaliza_mayusculas_del_metodo():
    assert fingerprint("post", CLOSE_PATH, None) == fingerprint("POST", CLOSE_PATH, None)


def test_huella_sin_cuerpo_equivale_a_cuerpo_vacio():
    assert fingerprint("POST", CLOSE_PATH, None) == fingerprint("POST", CLOSE_PATH, "")


# ---------------------------------------------------------------------------
# Contratos de datos y ventana de dedupe (§7.1: acotada)
# ---------------------------------------------------------------------------
def test_ttl_es_de_24_horas():
    assert TTL == timedelta(hours=24)


def test_header_es_el_del_contrato():
    assert HEADER == "Idempotency-Key"


def test_replay_es_inmutable():
    replay = Replay(status=200, body={"ok": True})
    with pytest.raises(Exception):
        replay.status = 201  # type: ignore[misc]


def test_idempotency_es_inmutable():
    ctx = Idempotency(endpoint="sales.close_order", key="k", fingerprint="f")
    with pytest.raises(Exception):
        ctx.key = "otra"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# idempotency_of: de la cabecera HTTP al contexto del servicio
# ---------------------------------------------------------------------------
class _Payload(BaseModel):
    cash_session_id: str


def _request(key: str | None, method: str = "POST", path: str = CLOSE_PATH):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers={HEADER: key} if key is not None else {},
    )


def test_contexto_sin_clave_es_none():
    assert idempotency_of(
        _request(None), _Payload(cash_session_id="c"), endpoint="sales.close_order"
    ) is None


def test_cobro_sin_clave_es_422():
    with pytest.raises(AppError) as err:
        idempotency_of(
            _request(None), _Payload(cash_session_id="c"),
            endpoint="sales.close_order", required=True,
        )
    assert err.value.status_code == 422
    assert err.value.code == ErrorCode.IDEMPOTENCY_KEY_REQUIRED


def test_cobro_con_clave_vacia_tambien_es_422():
    # Vacía/espacios cuenta como ausente: 422 REQUIRED, no VALIDATION_ERROR.
    with pytest.raises(AppError) as err:
        idempotency_of(
            _request("   "), _Payload(cash_session_id="c"),
            endpoint="sales.close_order", required=True,
        )
    assert err.value.code == ErrorCode.IDEMPOTENCY_KEY_REQUIRED


def test_clave_malformada_es_422_incluso_en_operacion_opcional():
    with pytest.raises(AppError) as err:
        idempotency_of(
            _request("k" * 201), _Payload(cash_session_id="c"), endpoint="sales.create_order"
        )
    assert err.value.status_code == 422
    assert err.value.code == ErrorCode.VALIDATION_ERROR


def test_contexto_con_clave_lleva_huella_del_contenido_validado():
    payload = _Payload(cash_session_id="c")
    ctx = idempotency_of(
        _request("k-1"), payload, endpoint="sales.close_order", required=True
    )
    assert ctx == Idempotency(
        endpoint="sales.close_order",
        key="k-1",
        fingerprint=fingerprint("POST", CLOSE_PATH, payload.model_dump_json()),
    )


def test_mismo_contenido_misma_huella_aunque_cambien_espacios():
    # La huella se calcula sobre el JSON del modelo (canónico), no sobre los
    # bytes crudos: dos reintentos equivalentes comparten huella.
    a = _Payload.model_validate_json('{"cash_session_id": "c"}')
    b = _Payload.model_validate_json('{ "cash_session_id" :  "c" }')
    ha = idempotency_of(_request("k"), a, endpoint="sales.close_order")
    hb = idempotency_of(_request("k"), b, endpoint="sales.close_order")
    assert ha == hb


def test_contenido_distinto_huella_distinta():
    a = idempotency_of(
        _request("k"), _Payload(cash_session_id="c"), endpoint="sales.close_order"
    )
    b = idempotency_of(
        _request("k"), _Payload(cash_session_id="d"), endpoint="sales.close_order"
    )
    assert a.fingerprint != b.fingerprint

"""Contrato de entrada/salida del catálogo (fase 04) — sin base de datos.

Lo crítico aquí es el dinero (§3): Decimal en Python, STRING en JSON y rechazo
explícito de floats en la entrada (un binario IEEE nunca debe tocar un importe).
También la limpieza de códigos de barras y la semántica PATCH parcial.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.catalog import (
    ProductCreate,
    ProductUpdate,
    TaxRateCreate,
    TierPriceIn,
)

TAX_RATE_ID = uuid4()


def _product_data(**overrides) -> dict:
    data = {
        "name": "Café solo",
        "tax_rate_id": str(TAX_RATE_ID),
        "price": "1.50",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Dinero: string en JSON, nunca float
# ---------------------------------------------------------------------------
def test_dinero_serializa_como_string():
    row = ProductCreate(**_product_data())
    assert row.price == Decimal("1.50")
    dump = row.model_dump(mode="json")
    assert dump["price"] == "1.50"  # string, no float (§3)
    assert dump["tax_rate_id"] == str(TAX_RATE_ID)


def test_dinero_rechaza_float_de_entrada():
    with pytest.raises(ValidationError) as exc:
        ProductCreate(**_product_data(price=1.5))
    assert "float" in str(exc.value)


def test_dinero_acepta_string_y_rechaza_negativos_o_exceso_de_decimales():
    assert ProductCreate(**_product_data(price="0.00")).price == 0
    assert ProductCreate(**_product_data(price="9999999999.99")).price is not None
    with pytest.raises(ValidationError):
        ProductCreate(**_product_data(price="-0.01"))
    with pytest.raises(ValidationError):
        ProductCreate(**_product_data(price="1.999"))  # más de 2 decimales
    with pytest.raises(ValidationError):
        ProductCreate(**_product_data(price="10000000000.00"))  # > numeric(12,2)


def test_precio_por_tarifa_tambien_es_string_en_json():
    row = TierPriceIn(tier_id=uuid4(), price="10.00")
    assert row.model_dump(mode="json")["price"] == "10.00"


def test_tipo_iva_limites_de_porcentaje_y_fecha():
    row = TaxRateCreate(code="reducido", name="IVA reducido", rate="10.00",
                        valid_from="2026-09-01")
    assert str(row.valid_from) == "2026-09-01"
    with pytest.raises(ValidationError):
        TaxRateCreate(code="x", name="x", rate="100.01", valid_from="2026-09-01")
    with pytest.raises(ValidationError):
        TaxRateCreate(code="x", name="x", rate="21.5", valid_from="no-es-fecha")


# ---------------------------------------------------------------------------
# Códigos de barras: strip, sin vacíos ni duplicados
# ---------------------------------------------------------------------------
def test_barcodes_se_limpian():
    row = ProductCreate(**_product_data(barcodes=[" 8400000000017 ", "8400000000024"]))
    assert row.barcodes == ["8400000000017", "8400000000024"]


def test_barcodes_rechaza_vacios_y_duplicados():
    with pytest.raises(ValidationError):
        ProductCreate(**_product_data(barcodes=["123", "   "]))
    with pytest.raises(ValidationError):
        ProductCreate(**_product_data(barcodes=["123", "123"]))


def test_barcodes_ausente_es_none_no_lista_vacia():
    # None = "no tocar la colección" (PATCH); [] = "vaciarla". Son distintos.
    assert ProductCreate(**_product_data()).barcodes is None
    assert ProductCreate(**_product_data(barcodes=[])).barcodes == []


# ---------------------------------------------------------------------------
# PATCH parcial: solo los campos enviados
# ---------------------------------------------------------------------------
def test_update_parcial_solo_incluye_lo_enviado():
    row = ProductUpdate.model_validate({"price": "2.00"})
    assert row.model_dump(exclude_unset=True) == {"price": Decimal("2.00")}


def test_update_aplica_las_mismas_reglas_de_barcode():
    with pytest.raises(ValidationError):
        ProductUpdate.model_validate({"barcodes": ["1", "1"]})
    ok = ProductUpdate.model_validate({"barcodes": [" 42 "]})
    assert ok.barcodes == ["42"]

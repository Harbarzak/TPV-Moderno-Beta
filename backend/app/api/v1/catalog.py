"""Catálogo (fases 04-05 · Productos y Paneles): ``/api/v1/catalog``.

- ``GET/POST/PATCH/DELETE /catalog/departments``    nivel 1 de la jerarquía
- ``GET/POST/PATCH/DELETE /catalog/categories``     nivel 2 (subcategorías)
- ``GET/POST/PATCH/DELETE /catalog/products``       CRUD de productos
- ``GET    /catalog/products/{id}/prices``          histórico inmutable de precios
- ``GET/POST/PATCH       /catalog/tiers``           tarifas de precios
- ``GET/POST             /catalog/tax-rates``       tipos de IVA con vigencia
- ``GET/POST/PATCH/DELETE /catalog/panels``         rejillas del TPV visual
- ``POST/PATCH/DELETE    /catalog/subpanels…``      subpaneles de un panel
- ``PUT    /catalog/panels/{id}/items``             diseño completo de la rejilla
- ``GET    /catalog/pos``                           snapshot optimizado para el TPV
- ``GET    /catalog/panels``                        árbol panel→subpanel→producto (caché de terminal)

Reglas del contrato: el dinero viaja SIEMPRE como string (nunca float, §3);
DELETE = baja lógica (``active=false``), nunca borrado físico; la lectura exige
``products.view`` y la escritura ``products.edit`` (los tokens de PIN pueden leer
el catálogo: es operación de venta, no administración). Los códigos de barras,
imágenes y precios por tarifa se gestionan dentro del payload del producto con
semántica de reemplazo completo si el campo viene (los items de rejilla se
rediseñan con un PUT completo, mismo pacto). Los modificadores de producto
no existen en la arquitectura y no se exponen.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.services import catalog as catalog_service

router = APIRouter(prefix="/catalog", tags=["catalog"])

_read = [Depends(require_permission("products.view"))]
_edit = [Depends(require_permission("products.edit"))]


# ---------------------------------------------------------------------------
# Dinero: Decimal en Python, STRING en JSON. Un float en la entrada se rechaza
# (§3: nada de binarios IEEE en importes).
# ---------------------------------------------------------------------------
def _reject_float(value):
    if isinstance(value, float):
        raise ValueError("El dinero se envía como string (nunca float)")
    return value


def _money_str(value: Decimal) -> str:
    return format(value, "f")


# Límite superior de numeric(12,2) como `le` explícito: pydantic 2.13 no aplica
# `max_digits` a la entrada (string o Decimal), pero sí `ge`/`le`/`decimal_places`.
_MONEY_MAX = Decimal("9999999999.99")

Money = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(ge=0, le=_MONEY_MAX, decimal_places=2),
    PlainSerializer(_money_str, return_type=str, when_used="json"),
]

RatePercent = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(ge=0, le=100, decimal_places=2),
    PlainSerializer(_money_str, return_type=str, when_used="json"),
]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class DepartmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^\S+$")
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = 0


class DepartmentUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32, pattern=r"^\S+$")
    name: str | None = Field(None, min_length=1, max_length=80)
    sort_order: int | None = None
    active: bool | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    department_id: UUID | None = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    department_id: UUID | None = None
    sort_order: int | None = None
    active: bool | None = None


class TierPriceIn(BaseModel):
    tier_id: UUID
    price: Money


class ImageIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    sort_order: int = 0


def _validate_barcodes(value: list[str] | None) -> list[str] | None:
    """Limpieza común de la colección de códigos: strip, sin vacíos ni repetidos."""
    if value is None:
        return None
    cleaned = [code.strip() for code in value]
    if any(not code for code in cleaned):
        raise ValueError("Los códigos de barras no pueden quedar vacíos")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Códigos de barras duplicados en la petición")
    return cleaned


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    short_name: str | None = Field(None, max_length=40)
    sku: str | None = Field(None, max_length=64)
    category_id: UUID | None = None
    tax_rate_id: UUID
    price: Money
    weighable: bool = False
    kitchen: bool = False
    kitchen_station_id: UUID | None = None
    sort_order: int = 0
    active: bool = True
    # Reemplazo completo: si el campo viene, la colección queda exactamente igual.
    barcodes: list[str] | None = None
    tier_prices: list[TierPriceIn] | None = None
    images: list[ImageIn] | None = None

    @field_validator("barcodes")
    @classmethod
    def _clean_barcodes(cls, value: list[str] | None) -> list[str] | None:
        return _validate_barcodes(value)


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    short_name: str | None = Field(None, max_length=40)
    sku: str | None = Field(None, max_length=64)
    category_id: UUID | None = None
    tax_rate_id: UUID | None = None
    price: Money | None = None
    weighable: bool | None = None
    kitchen: bool | None = None
    kitchen_station_id: UUID | None = None
    sort_order: int | None = None
    active: bool | None = None
    barcodes: list[str] | None = None
    tier_prices: list[TierPriceIn] | None = None
    images: list[ImageIn] | None = None

    @field_validator("barcodes")
    @classmethod
    def _clean_barcodes(cls, value: list[str] | None) -> list[str] | None:
        return _validate_barcodes(value)


class TierCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^\S+$")
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = 0


class TierUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=32, pattern=r"^\S+$")
    name: str | None = Field(None, min_length=1, max_length=80)
    sort_order: int | None = None
    active: bool | None = None


class TaxRateCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^\S+$")
    name: str = Field(min_length=1, max_length=80)
    rate: RatePercent
    valid_from: date


class ReorderItem(BaseModel):
    id: UUID
    sort_order: int


class ReorderRequest(BaseModel):
    items: list[ReorderItem] = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# Paneles del TPV visual (fase 05)
# ---------------------------------------------------------------------------
class PanelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = 0


class PanelUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    sort_order: int | None = None
    active: bool | None = None


class SubPanelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = 0


class SubPanelUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    sort_order: int | None = None
    active: bool | None = None


# Rejilla de 20×20 como máximo; el diseñador manda el layout entero en un PUT.
_GRID_MAX = 19


class PanelItemIn(BaseModel):
    product_id: UUID
    subpanel_id: UUID | None = None  # None = botón directo del panel
    label: str | None = Field(None, max_length=40)  # alterna al nombre del producto
    color: str | None = Field(None, max_length=20)
    grid_row: int = Field(0, ge=0, le=_GRID_MAX)
    grid_col: int = Field(0, ge=0, le=_GRID_MAX)
    sort_order: int = 0


class PanelItemsReplace(BaseModel):
    items: list[PanelItemIn] = Field(min_length=1, max_length=400)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    sort_order: int
    active: bool


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID | None
    name: str
    sort_order: int
    active: bool


class TierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    sort_order: int
    active: bool


class TaxRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    rate: RatePercent
    valid_from: date
    valid_to: date | None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str | None
    name: str
    short_name: str | None
    category_id: UUID | None
    tax_rate_id: UUID
    price: Money
    weighable: bool
    kitchen: bool
    kitchen_station_id: UUID | None
    sort_order: int
    active: bool


class TierPriceOut(BaseModel):
    tier_code: str
    price: Money


class ImageOut(BaseModel):
    path: str
    sort_order: int


class ProductDetailResponse(ProductResponse):
    barcodes: list[str]
    tier_prices: list[TierPriceOut]
    images: list[ImageOut]


class PriceHistoryItem(BaseModel):
    price: Money
    valid_from: datetime
    valid_to: datetime | None


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    limit: int
    offset: int


class PosProductOut(BaseModel):
    """Fila del snapshot de venta: todo lo que la pantalla TPV necesita."""

    id: UUID
    name: str
    short_name: str | None
    sku: str | None
    category_id: UUID | None
    department_id: UUID | None
    tax_code: str
    tax_rate: RatePercent
    price: Money
    weighable: bool
    kitchen: bool
    sort_order: int
    barcodes: list[str]
    tier_prices: list[TierPriceOut]

    @classmethod
    def from_service(cls, row) -> "PosProductOut":
        return cls(
            id=row.id,
            name=row.name,
            short_name=row.short_name,
            sku=row.sku,
            category_id=row.category_id,
            department_id=row.department_id,
            tax_code=row.tax_code,
            tax_rate=row.tax_rate,
            price=row.price,
            weighable=row.weighable,
            kitchen=row.kitchen,
            sort_order=row.sort_order,
            barcodes=list(row.barcodes),
            tier_prices=[
                TierPriceOut(tier_code=code, price=price)
                for code, price in row.tier_prices.items()
            ],
        )


class PosCatalogResponse(BaseModel):
    products: list[PosProductOut]


class PanelResponse(BaseModel):
    """Entidad panel (CRUD); el árbol de venta va en ``PanelOut``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sort_order: int
    active: bool


class SubPanelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    panel_id: UUID
    name: str
    sort_order: int
    active: bool


class PanelProductOut(BaseModel):
    """Snapshot de venta embebido en cada botón (caché de terminal, §7.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    short_name: str | None
    sku: str | None
    price: Money
    tax_code: str
    tax_rate: RatePercent
    weighable: bool
    kitchen: bool


class PanelItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None
    color: str | None
    grid_row: int
    grid_col: int
    sort_order: int
    product: PanelProductOut


class SubPanelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sort_order: int
    items: list[PanelItemOut]


class PanelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sort_order: int
    subpanels: list[SubPanelOut]
    items: list[PanelItemOut]


class PanelTreeResponse(BaseModel):
    panels: list[PanelOut]


# ---------------------------------------------------------------------------
# Departamentos
# ---------------------------------------------------------------------------
@router.get("/departments", dependencies=_read, response_model=list[DepartmentResponse])
async def list_departments(
    session: DbSession, include_inactive: bool = False
) -> list[DepartmentResponse]:
    rows = await catalog_service.list_departments(
        session, include_inactive=include_inactive
    )
    return [DepartmentResponse.model_validate(row) for row in rows]


@router.post("/departments", dependencies=_edit, response_model=DepartmentResponse)
async def create_department(
    body: DepartmentCreate, principal: CurrentUser, session: DbSession
) -> DepartmentResponse:
    row = await catalog_service.create_department(
        session, principal, code=body.code, name=body.name, sort_order=body.sort_order
    )
    return DepartmentResponse.model_validate(row)


@router.patch(
    "/departments/{department_id}", dependencies=_edit, response_model=DepartmentResponse
)
async def update_department(
    department_id: UUID, body: DepartmentUpdate, principal: CurrentUser, session: DbSession
) -> DepartmentResponse:
    row = await catalog_service.update_department(
        session, principal, department_id, fields=body.model_dump(exclude_unset=True)
    )
    return DepartmentResponse.model_validate(row)


@router.delete(
    "/departments/{department_id}", dependencies=_edit, response_model=DepartmentResponse
)
async def delete_department(
    department_id: UUID, principal: CurrentUser, session: DbSession
) -> DepartmentResponse:
    """Baja lógica: la fila persiste con ``active=false`` (nunca DELETE físico)."""
    row = await catalog_service.delete_department(session, principal, department_id)
    return DepartmentResponse.model_validate(row)


@router.post("/departments/reorder", dependencies=_edit, response_model=list[DepartmentResponse])
async def reorder_departments(
    body: ReorderRequest, principal: CurrentUser, session: DbSession
) -> list[DepartmentResponse]:
    await catalog_service.reorder_departments(
        session, principal, [(item.id, item.sort_order) for item in body.items]
    )
    rows = await catalog_service.list_departments(session, include_inactive=True)
    return [DepartmentResponse.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Categorías (subcategorías)
# ---------------------------------------------------------------------------
@router.get("/categories", dependencies=_read, response_model=list[CategoryResponse])
async def list_categories(
    session: DbSession,
    department_id: UUID | None = None,
    include_inactive: bool = False,
) -> list[CategoryResponse]:
    rows = await catalog_service.list_categories(
        session, department_id=department_id, include_inactive=include_inactive
    )
    return [CategoryResponse.model_validate(row) for row in rows]


@router.post("/categories", dependencies=_edit, response_model=CategoryResponse)
async def create_category(
    body: CategoryCreate, principal: CurrentUser, session: DbSession
) -> CategoryResponse:
    row = await catalog_service.create_category(
        session,
        principal,
        name=body.name,
        department_id=body.department_id,
        sort_order=body.sort_order,
    )
    return CategoryResponse.model_validate(row)


@router.patch(
    "/categories/{category_id}", dependencies=_edit, response_model=CategoryResponse
)
async def update_category(
    category_id: UUID, body: CategoryUpdate, principal: CurrentUser, session: DbSession
) -> CategoryResponse:
    row = await catalog_service.update_category(
        session, principal, category_id, fields=body.model_dump(exclude_unset=True)
    )
    return CategoryResponse.model_validate(row)


@router.delete(
    "/categories/{category_id}", dependencies=_edit, response_model=CategoryResponse
)
async def delete_category(
    category_id: UUID, principal: CurrentUser, session: DbSession
) -> CategoryResponse:
    row = await catalog_service.delete_category(session, principal, category_id)
    return CategoryResponse.model_validate(row)


@router.post("/categories/reorder", dependencies=_edit, response_model=list[CategoryResponse])
async def reorder_categories(
    body: ReorderRequest, principal: CurrentUser, session: DbSession
) -> list[CategoryResponse]:
    await catalog_service.reorder_categories(
        session, principal, [(item.id, item.sort_order) for item in body.items]
    )
    rows = await catalog_service.list_categories(session, include_inactive=True)
    return [CategoryResponse.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
@router.get("/products", dependencies=_read, response_model=ProductListResponse)
async def list_products(
    session: DbSession,
    category_id: UUID | None = None,
    department_id: UUID | None = None,
    active: bool | None = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListResponse:
    rows, total = await catalog_service.search_products(
        session,
        category_id=category_id,
        department_id=department_id,
        active=active,
        search=search,
        limit=limit,
        offset=offset,
    )
    return ProductListResponse(
        items=[ProductResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/products", dependencies=_edit, response_model=ProductDetailResponse)
async def create_product(
    body: ProductCreate, principal: CurrentUser, session: DbSession
) -> ProductDetailResponse:
    row = await catalog_service.create_product(
        session,
        principal,
        name=body.name,
        tax_rate_id=body.tax_rate_id,
        price=body.price,
        short_name=body.short_name,
        sku=body.sku,
        category_id=body.category_id,
        weighable=body.weighable,
        kitchen=body.kitchen,
        kitchen_station_id=body.kitchen_station_id,
        sort_order=body.sort_order,
        active=body.active,
        barcodes=body.barcodes,
        tier_prices=(
            [(t.tier_id, t.price) for t in body.tier_prices]
            if body.tier_prices is not None
            else None
        ),
        images=(
            [(i.path, i.sort_order) for i in body.images]
            if body.images is not None
            else None
        ),
    )
    return await _product_detail(session, row.id)


@router.get("/products/{product_id}", dependencies=_read, response_model=ProductDetailResponse)
async def get_product(product_id: UUID, session: DbSession) -> ProductDetailResponse:
    return await _product_detail(session, product_id)


@router.get(
    "/products/{product_id}/prices", dependencies=_read, response_model=list[PriceHistoryItem]
)
async def product_price_history(
    product_id: UUID, session: DbSession
) -> list[PriceHistoryItem]:
    rows = await catalog_service.product_price_history(session, product_id)
    return [
        PriceHistoryItem(price=row.price, valid_from=row.valid_from, valid_to=row.valid_to)
        for row in rows
    ]


@router.patch(
    "/products/{product_id}", dependencies=_edit, response_model=ProductDetailResponse
)
async def update_product(
    product_id: UUID, body: ProductUpdate, principal: CurrentUser, session: DbSession
) -> ProductDetailResponse:
    fields = body.model_dump(exclude_unset=True)
    if body.tier_prices is not None:
        fields["tier_prices"] = [(t.tier_id, t.price) for t in body.tier_prices]
    if body.images is not None:
        fields["images"] = [(i.path, i.sort_order) for i in body.images]
    await catalog_service.update_product(
        session, principal, product_id, fields=fields
    )
    return await _product_detail(session, product_id)


@router.delete(
    "/products/{product_id}", dependencies=_edit, response_model=ProductResponse
)
async def delete_product(
    product_id: UUID, principal: CurrentUser, session: DbSession
) -> ProductResponse:
    """Baja lógica: las líneas ya vendidas conservan su FK y su snapshot."""
    row = await catalog_service.delete_product(session, principal, product_id)
    return ProductResponse.model_validate(row)


@router.post("/products/reorder", dependencies=_edit, response_model=ProductListResponse)
async def reorder_products(
    body: ReorderRequest, principal: CurrentUser, session: DbSession
) -> ProductListResponse:
    await catalog_service.reorder_products(
        session, principal, [(item.id, item.sort_order) for item in body.items]
    )
    rows, total = await catalog_service.search_products(session, limit=200)
    return ProductListResponse(
        items=[ProductResponse.model_validate(row) for row in rows],
        total=total,
        limit=200,
        offset=0,
    )


async def _product_detail(session, product_id: UUID) -> ProductDetailResponse:
    row, barcodes, tier_prices, images = await catalog_service.get_product_detail(
        session, product_id
    )
    return ProductDetailResponse(
        **ProductResponse.model_validate(row).model_dump(),
        barcodes=barcodes,
        tier_prices=[
            TierPriceOut(tier_code=code, price=price) for code, price in tier_prices
        ],
        images=[ImageOut(path=img.path, sort_order=img.sort_order) for img in images],
    )


# ---------------------------------------------------------------------------
# Tarifas de precios
# ---------------------------------------------------------------------------
@router.get("/tiers", dependencies=_read, response_model=list[TierResponse])
async def list_tiers(session: DbSession, include_inactive: bool = False):
    rows = await catalog_service.list_price_tiers(session, include_inactive=include_inactive)
    return [TierResponse.model_validate(row) for row in rows]


@router.post("/tiers", dependencies=_edit, response_model=TierResponse)
async def create_tier(
    body: TierCreate, principal: CurrentUser, session: DbSession
) -> TierResponse:
    row = await catalog_service.create_price_tier(
        session, principal, code=body.code, name=body.name, sort_order=body.sort_order
    )
    return TierResponse.model_validate(row)


@router.patch("/tiers/{tier_id}", dependencies=_edit, response_model=TierResponse)
async def update_tier(
    tier_id: UUID, body: TierUpdate, principal: CurrentUser, session: DbSession
) -> TierResponse:
    row = await catalog_service.update_price_tier(
        session, principal, tier_id, fields=body.model_dump(exclude_unset=True)
    )
    return TierResponse.model_validate(row)


# ---------------------------------------------------------------------------
# Tipos de IVA con vigencia
# ---------------------------------------------------------------------------
@router.get("/tax-rates", dependencies=_read, response_model=list[TaxRateResponse])
async def list_tax_rates(session: DbSession, only_current: bool = False):
    rows = await catalog_service.list_tax_rates(session, only_current=only_current)
    return [TaxRateResponse.model_validate(row) for row in rows]


@router.post("/tax-rates", dependencies=_edit, response_model=TaxRateResponse)
async def create_tax_rate(
    body: TaxRateCreate, principal: CurrentUser, session: DbSession
) -> TaxRateResponse:
    """Nueva versión con vigencia: la anterior del mismo código se cierra sola."""
    row = await catalog_service.create_tax_rate_version(
        session,
        principal,
        code=body.code,
        name=body.name,
        rate=body.rate,
        valid_from=body.valid_from,
    )
    return TaxRateResponse.model_validate(row)


# ---------------------------------------------------------------------------
# Snapshot optimizado para la pantalla TPV
# ---------------------------------------------------------------------------
@router.get("/pos", dependencies=_read, response_model=PosCatalogResponse)
async def pos_catalog(session: DbSession) -> PosCatalogResponse:
    """Catálogo vendible en 3 consultas: activos, con categoría y departamento
    también activos, IVA, códigos de barras y precios por tarifa incluidos."""
    rows = await catalog_service.pos_catalog(session)
    return PosCatalogResponse(products=[PosProductOut.from_service(row) for row in rows])


# ---------------------------------------------------------------------------
# Paneles del TPV visual (fase 05)
# ---------------------------------------------------------------------------
@router.get("/panels", dependencies=_read, response_model=PanelTreeResponse)
async def panel_tree(session: DbSession) -> PanelTreeResponse:
    """Árbol Categorías→Paneles→SubPaneles→Productos con el snapshot de venta
    embebido en cada botón: la caché de terminal del TPV visual (3 consultas)."""
    rows = await catalog_service.pos_panels(session)
    return PanelTreeResponse(
        panels=[PanelOut.model_validate(row) for row in rows]
    )


@router.post("/panels", dependencies=_edit, response_model=PanelResponse)
async def create_panel(
    body: PanelCreate, principal: CurrentUser, session: DbSession
) -> PanelResponse:
    row = await catalog_service.create_panel(
        session, principal, name=body.name, sort_order=body.sort_order
    )
    return PanelResponse.model_validate(row)


@router.patch("/panels/{panel_id}", dependencies=_edit, response_model=PanelResponse)
async def update_panel(
    panel_id: UUID, body: PanelUpdate, principal: CurrentUser, session: DbSession
) -> PanelResponse:
    row = await catalog_service.update_panel(
        session, principal, panel_id, fields=body.model_dump(exclude_unset=True)
    )
    return PanelResponse.model_validate(row)


@router.delete("/panels/{panel_id}", dependencies=_edit, response_model=PanelResponse)
async def delete_panel(
    panel_id: UUID, principal: CurrentUser, session: DbSession
) -> PanelResponse:
    """Baja lógica: la rejilla deja de venderse, la fila persiste."""
    row = await catalog_service.delete_panel(session, principal, panel_id)
    return PanelResponse.model_validate(row)


@router.post(
    "/panels/reorder", dependencies=_edit, response_model=PanelTreeResponse
)
async def reorder_panels(
    body: ReorderRequest, principal: CurrentUser, session: DbSession
) -> PanelTreeResponse:
    await catalog_service.reorder_panels(
        session, principal, [(item.id, item.sort_order) for item in body.items]
    )
    rows = await catalog_service.pos_panels(session)
    return PanelTreeResponse(panels=[PanelOut.model_validate(row) for row in rows])


@router.post(
    "/panels/{panel_id}/subpanels", dependencies=_edit, response_model=SubPanelResponse
)
async def create_subpanel(
    panel_id: UUID, body: SubPanelCreate, principal: CurrentUser, session: DbSession
) -> SubPanelResponse:
    row = await catalog_service.create_subpanel(
        session, principal, panel_id, name=body.name, sort_order=body.sort_order
    )
    return SubPanelResponse.model_validate(row)


@router.patch("/subpanels/{subpanel_id}", dependencies=_edit, response_model=SubPanelResponse)
async def update_subpanel(
    subpanel_id: UUID, body: SubPanelUpdate, principal: CurrentUser, session: DbSession
) -> SubPanelResponse:
    row = await catalog_service.update_subpanel(
        session, principal, subpanel_id, fields=body.model_dump(exclude_unset=True)
    )
    return SubPanelResponse.model_validate(row)


@router.delete("/subpanels/{subpanel_id}", dependencies=_edit, response_model=SubPanelResponse)
async def delete_subpanel(
    subpanel_id: UUID, principal: CurrentUser, session: DbSession
) -> SubPanelResponse:
    row = await catalog_service.delete_subpanel(session, principal, subpanel_id)
    return SubPanelResponse.model_validate(row)


@router.post(
    "/subpanels/reorder", dependencies=_edit, response_model=PanelTreeResponse
)
async def reorder_subpanels(
    body: ReorderRequest, principal: CurrentUser, session: DbSession
) -> PanelTreeResponse:
    await catalog_service.reorder_subpanels(
        session, principal, [(item.id, item.sort_order) for item in body.items]
    )
    rows = await catalog_service.pos_panels(session)
    return PanelTreeResponse(panels=[PanelOut.model_validate(row) for row in rows])


@router.put(
    "/panels/{panel_id}/items", dependencies=_edit, response_model=PanelTreeResponse
)
async def replace_panel_items(
    panel_id: UUID, body: PanelItemsReplace, principal: CurrentUser, session: DbSession
) -> PanelTreeResponse:
    """Diseño completo de la rejilla (panel + sus subpaneles) en un PUT: la
    colección queda exactamente como la envía el diseñador. Devuelve el árbol
    ya actualizado, listo para cachear en el terminal."""
    await catalog_service.replace_panel_items(
        session,
        principal,
        panel_id,
        [item.model_dump() for item in body.items],
    )
    rows = await catalog_service.pos_panels(session)
    return PanelTreeResponse(panels=[PanelOut.model_validate(row) for row in rows])


@router.delete("/panel-items/{item_id}", dependencies=_edit, status_code=204)
async def delete_panel_item(
    item_id: UUID, principal: CurrentUser, session: DbSession
) -> None:
    """Quitar un botón suelto de la rejilla (soporte visual, no histórico)."""
    await catalog_service.delete_panel_item(session, principal, item_id)

"""Servicio de catálogo (fase 04 · Productos): CRUD, precios, tarifas, orden, TPV.

Reglas transversales (ARCHITECTURE.md §3) que se aplican aquí:
- Sin borrados físicos: DELETE = baja lógica (``active=false``); las anexas del
  producto (códigos, imágenes, precios por tarifa) sí se reemplazan porque son
  datos de soporte editables, no histórico económico.
- Dinero como ``Decimal`` (la capa api lo serializa a string); el histórico de
  precios es inmutable: cambiar precio = cerrar fila vigente + abrir una nueva.
- Toda mutación de catálogo queda en ``audit_log`` con el usuario que la hizo.

Los modificadores de producto NO existen en la arquitectura (§3 no los define),
así que no se implementan (FASE_04: "si están definidos en arquitectura").

Nunca importa de ``api``: los controladores llaman a estas funciones.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models.catalog import (
    Category,
    Department,
    Panel,
    PriceTier,
    Product,
    SubPanel,
    TaxRate,
)
from app.db.models.restaurant import KitchenStation
from app.repos import auth as auth_repo
from app.repos import catalog as repo
from app.services import events as events_service
from app.services.auth import Principal


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


async def _changed(
    session: AsyncSession,
    principal: Principal,
    *,
    entity: str,
    action: str,
    entity_id: UUID | None = None,
) -> None:
    """Evento ``catalog.changed`` (fase 12) en el tema ``catalog``: aviso
    mínimo {entidad, acción, id} para que los terminales invaliden su caché
    local y re-hidraten; la lectura completa es la API normal, no el evento."""
    await events_service.record(
        session,
        topic="catalog",
        type="catalog.changed",
        payload={
            "entity": entity,
            "action": action,
            "id": str(entity_id) if entity_id is not None else None,
        },
        actor_user_id=principal.user_id,
    )


# ---------------------------------------------------------------------------
# Departamentos (nivel 1 de la jerarquía)
# ---------------------------------------------------------------------------
async def list_departments(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[Department]:
    return await repo.list_departments(session, include_inactive=include_inactive)


async def create_department(
    session: AsyncSession,
    principal: Principal,
    *,
    code: str,
    name: str,
    sort_order: int,
) -> Department:
    if await repo.get_department_by_code(session, code):
        raise _conflict(f"Ya existe un departamento con el código {code}")
    row = Department(code=code, name=name, sort_order=sort_order)
    session.add(row)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="catalog.department_created",
        entity="department",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"code": code, "name": name},
    )
    await _changed(session, principal, entity="department", action="created", entity_id=row.id)
    await session.commit()
    return row


async def update_department(
    session: AsyncSession,
    principal: Principal,
    department_id: UUID,
    *,
    fields: dict,
) -> Department:
    row = await repo.get_department(session, department_id)
    if row is None:
        raise _not_found("Departamento no encontrado")
    if "code" in fields and fields["code"] != row.code:
        other = await repo.get_department_by_code(session, fields["code"])
        if other is not None:
            raise _conflict(f"Ya existe un departamento con el código {fields['code']}")
    for key, value in fields.items():
        setattr(row, key, value)
    await auth_repo.record_audit(
        session,
        action="catalog.department_updated",
        entity="department",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"code": row.code, "name": row.name, "active": row.active},
    )
    await _changed(session, principal, entity="department", action="updated", entity_id=row.id)
    await session.commit()
    return row


async def delete_department(
    session: AsyncSession, principal: Principal, department_id: UUID
) -> Department:
    """Baja lógica: el departamento queda fuera de alta pero sus filas persisten."""
    row = await repo.get_department(session, department_id)
    if row is None:
        raise _not_found("Departamento no encontrado")
    row.active = False
    await auth_repo.record_audit(
        session,
        action="catalog.department_deleted",
        entity="department",
        user_id=principal.user_id,
        entity_id=row.id,
    )
    await _changed(session, principal, entity="department", action="deleted", entity_id=row.id)
    await session.commit()
    return row


async def reorder_departments(
    session: AsyncSession, principal: Principal, items: list[tuple[UUID, int]]
) -> None:
    await _reorder(
        session, principal, repo.Department,
        action="catalog.departments_reordered", label="Departamento", items=items,
    )


# ---------------------------------------------------------------------------
# Categorías (nivel 2; opcionalmente colgadas de un departamento)
# ---------------------------------------------------------------------------
async def list_categories(
    session: AsyncSession,
    *,
    department_id: UUID | None = None,
    include_inactive: bool = False,
) -> list[Category]:
    return await repo.list_categories(
        session, department_id=department_id, include_inactive=include_inactive
    )


async def create_category(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    department_id: UUID | None,
    sort_order: int,
) -> Category:
    if department_id is not None and await repo.get_department(session, department_id) is None:
        raise _not_found("El departamento indicado no existe")
    row = Category(name=name, department_id=department_id, sort_order=sort_order)
    session.add(row)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="catalog.category_created",
        entity="category",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"name": name, "department_id": str(department_id) if department_id else None},
    )
    await _changed(session, principal, entity="category", action="created", entity_id=row.id)
    await session.commit()
    return row


async def update_category(
    session: AsyncSession,
    principal: Principal,
    category_id: UUID,
    *,
    fields: dict,
) -> Category:
    row = await repo.get_category(session, category_id)
    if row is None:
        raise _not_found("Categoría no encontrada")
    if "department_id" in fields and fields["department_id"] is not None:
        if await repo.get_department(session, fields["department_id"]) is None:
            raise _not_found("El departamento indicado no existe")
    for key, value in fields.items():
        setattr(row, key, value)
    await auth_repo.record_audit(
        session,
        action="catalog.category_updated",
        entity="category",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"name": row.name, "active": row.active},
    )
    await _changed(session, principal, entity="category", action="updated", entity_id=row.id)
    await session.commit()
    return row


async def delete_category(
    session: AsyncSession, principal: Principal, category_id: UUID
) -> Category:
    row = await repo.get_category(session, category_id)
    if row is None:
        raise _not_found("Categoría no encontrada")
    row.active = False
    await auth_repo.record_audit(
        session,
        action="catalog.category_deleted",
        entity="category",
        user_id=principal.user_id,
        entity_id=row.id,
    )
    await _changed(session, principal, entity="category", action="deleted", entity_id=row.id)
    await session.commit()
    return row


async def reorder_categories(
    session: AsyncSession, principal: Principal, items: list[tuple[UUID, int]]
) -> None:
    await _reorder(
        session, principal, repo.Category,
        action="catalog.categories_reordered", label="Categoría", items=items,
    )


# ---------------------------------------------------------------------------
# Reordenación común
# ---------------------------------------------------------------------------
async def _reorder(
    session: AsyncSession,
    principal: Principal,
    model,
    *,
    action: str,
    label: str,
    items: list[tuple[UUID, int]],
) -> None:
    """Orden bulk. Los ids desconocidos fallan (no se reordena a medias)."""
    ids = [row_id for row_id, _ in items]
    if ids:
        found = set((await session.scalars(
            select(model.id).where(model.id.in_(ids))
        )).all())
        missing = [str(row_id) for row_id in ids if row_id not in found]
        if missing:
            raise _not_found(f"{label} no encontrado: {', '.join(missing)}")
    await repo.reorder_rows(session, model, items)
    await auth_repo.record_audit(
        session,
        action=action,
        entity=model.__tablename__,
        user_id=principal.user_id,
        after_data={"count": len(items)},
    )
    await _changed(session, principal, entity=model.__tablename__, action="reordered")
    await session.commit()


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
async def search_products(
    session: AsyncSession,
    *,
    category_id: UUID | None = None,
    department_id: UUID | None = None,
    active: bool | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Product], int]:
    return await repo.search_products(
        session,
        category_id=category_id,
        department_id=department_id,
        active=active,
        search=search,
        limit=limit,
        offset=offset,
    )


async def get_product_detail(
    session: AsyncSession, product_id: UUID
) -> tuple[Product, list[str], list[tuple[str, Decimal]], list]:
    """Producto + códigos de barras + precios por tarifa + imágenes."""
    row = await repo.get_product(session, product_id)
    if row is None:
        raise _not_found("Producto no encontrado")
    barcodes = await repo.list_barcodes(session, row.id)
    tier_prices = await repo.list_tier_prices(session, row.id)
    images = await repo.list_images(session, row.id)
    return row, barcodes, tier_prices, images


async def product_price_history(
    session: AsyncSession, product_id: UUID
) -> list:
    """Histórico inmutable de precios (producto existente o 404)."""
    if await repo.get_product(session, product_id) is None:
        raise _not_found("Producto no encontrado")
    return await repo.price_history(session, product_id)


async def create_product(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    tax_rate_id: UUID,
    price: Decimal,
    short_name: str | None = None,
    sku: str | None = None,
    category_id: UUID | None = None,
    weighable: bool = False,
    kitchen: bool = False,
    kitchen_station_id: UUID | None = None,
    sort_order: int = 0,
    active: bool = True,
    barcodes: list[str] | None = None,
    tier_prices: list[tuple[UUID, Decimal]] | None = None,
    images: list[tuple[str, int]] | None = None,
) -> Product:
    """Alta completa: producto + primera fila del histórico de precios + anexas."""
    if await repo.get_tax_rate(session, tax_rate_id) is None:
        raise _not_found("El tipo de IVA indicado no existe")
    if category_id is not None and await repo.get_category(session, category_id) is None:
        raise _not_found("La categoría indicada no existe")
    if kitchen_station_id is not None and await session.get(KitchenStation, kitchen_station_id) is None:
        raise _not_found("La estación de cocina indicada no existe")
    if sku and await repo.get_product_by_sku(session, sku):
        raise _conflict(f"Ya existe un producto con el SKU {sku}")
    await _check_barcodes_free(session, None, barcodes or [])
    await _check_tiers(session, [tier_id for tier_id, _ in tier_prices or []])

    row = Product(
        name=name,
        short_name=short_name,
        sku=sku,
        category_id=category_id,
        tax_rate_id=tax_rate_id,
        price=price,
        weighable=weighable,
        kitchen=kitchen,
        kitchen_station_id=kitchen_station_id,
        sort_order=sort_order,
        active=active,
    )
    session.add(row)
    await session.flush()
    repo.open_price(session, row.id, price)  # primera fila del histórico inmutable
    if barcodes is not None:
        await repo.replace_barcodes(session, row.id, barcodes)
    if images is not None:
        await repo.replace_images(session, row.id, images)
    if tier_prices is not None:
        await repo.replace_tier_prices(session, row.id, tier_prices)

    await auth_repo.record_audit(
        session,
        action="catalog.product_created",
        entity="product",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"name": name, "price": str(price)},
    )
    await _changed(session, principal, entity="product", action="created", entity_id=row.id)
    await session.commit()
    return row


async def update_product(
    session: AsyncSession,
    principal: Principal,
    product_id: UUID,
    *,
    fields: dict,
) -> Product:
    """Modificación parcial. Cambiar el precio cierra la fila vigente del
    histórico y abre una nueva (inmutabilidad económica, §3)."""
    row = await repo.get_product(session, product_id)
    if row is None:
        raise _not_found("Producto no encontrado")

    if "category_id" in fields and fields["category_id"] is not None:
        if await repo.get_category(session, fields["category_id"]) is None:
            raise _not_found("La categoría indicada no existe")
    if "kitchen_station_id" in fields and fields["kitchen_station_id"] is not None:
        if await session.get(KitchenStation, fields["kitchen_station_id"]) is None:
            raise _not_found("La estación de cocina indicada no existe")
    if "tax_rate_id" in fields:
        if await repo.get_tax_rate(session, fields["tax_rate_id"]) is None:
            raise _not_found("El tipo de IVA indicado no existe")
    if "sku" in fields and fields["sku"] and fields["sku"] != row.sku:
        other = await repo.get_product_by_sku(session, fields["sku"])
        if other is not None:
            raise _conflict(f"Ya existe un producto con el SKU {fields['sku']}")

    new_price = fields.pop("price", None)
    barcodes = fields.pop("barcodes", None)
    images = fields.pop("images", None)
    tier_prices = fields.pop("tier_prices", None)

    price_changed = new_price is not None and new_price != row.price
    for key, value in fields.items():
        setattr(row, key, value)
    if price_changed:
        current = await repo.get_current_price_row(session, row.id)
        if current is not None:
            repo.close_price(session, current)
        repo.open_price(session, row.id, new_price)
        row.price = new_price

    if barcodes is not None:
        await _check_barcodes_free(session, row.id, barcodes)
        await repo.replace_barcodes(session, row.id, barcodes)
    if images is not None:
        await repo.replace_images(session, row.id, images)
    if tier_prices is not None:
        await _check_tiers(session, [tier_id for tier_id, _ in tier_prices])
        await repo.replace_tier_prices(session, row.id, tier_prices)

    await auth_repo.record_audit(
        session,
        action="catalog.product_updated",
        entity="product",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={
            "name": row.name,
            "price": str(row.price),
            "active": row.active,
            **({"price_changed": True} if price_changed else {}),
        },
    )
    await _changed(session, principal, entity="product", action="updated", entity_id=row.id)
    await session.commit()
    return row


async def delete_product(
    session: AsyncSession, principal: Principal, product_id: UUID
) -> Product:
    """Baja lógica: las líneas ya vendidas conservan su FK y su snapshot."""
    row = await repo.get_product(session, product_id)
    if row is None:
        raise _not_found("Producto no encontrado")
    row.active = False
    await auth_repo.record_audit(
        session,
        action="catalog.product_deleted",
        entity="product",
        user_id=principal.user_id,
        entity_id=row.id,
    )
    await _changed(session, principal, entity="product", action="deleted", entity_id=row.id)
    await session.commit()
    return row


async def reorder_products(
    session: AsyncSession, principal: Principal, items: list[tuple[UUID, int]]
) -> None:
    await _reorder(
        session, principal, repo.Product,
        action="catalog.products_reordered", label="Producto", items=items,
    )


async def _check_barcodes_free(
    session: AsyncSession, product_id: UUID | None, barcodes: list[str]
) -> None:
    """Unicidad global de códigos de barras (los duplicados dentro del propio
    payload los detecta la validación de entrada)."""
    for code in barcodes:
        owner = await repo.get_barcode_owner(session, code)
        if owner is not None and owner.product_id != product_id:
            raise _conflict(f"El código de barras {code} ya está asignado a otro producto")


async def _check_tiers(session: AsyncSession, tier_ids: list[UUID]) -> None:
    for tier_id in tier_ids:
        if await repo.get_price_tier(session, tier_id) is None:
            raise _not_found("La tarifa indicada no existe")


# ---------------------------------------------------------------------------
# Tarifas de precios
# ---------------------------------------------------------------------------
async def list_price_tiers(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[PriceTier]:
    return await repo.list_price_tiers(session, include_inactive=include_inactive)


async def create_price_tier(
    session: AsyncSession,
    principal: Principal,
    *,
    code: str,
    name: str,
    sort_order: int,
) -> PriceTier:
    if await repo.get_price_tier_by_code(session, code):
        raise _conflict(f"Ya existe una tarifa con el código {code}")
    row = PriceTier(code=code, name=name, sort_order=sort_order)
    session.add(row)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="catalog.price_tier_created",
        entity="price_tier",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"code": code, "name": name},
    )
    await _changed(session, principal, entity="price_tier", action="created", entity_id=row.id)
    await session.commit()
    return row


async def update_price_tier(
    session: AsyncSession,
    principal: Principal,
    tier_id: UUID,
    *,
    fields: dict,
) -> PriceTier:
    row = await repo.get_price_tier(session, tier_id)
    if row is None:
        raise _not_found("Tarifa no encontrada")
    if "code" in fields and fields["code"] != row.code:
        other = await repo.get_price_tier_by_code(session, fields["code"])
        if other is not None:
            raise _conflict(f"Ya existe una tarifa con el código {fields['code']}")
    for key, value in fields.items():
        setattr(row, key, value)
    await auth_repo.record_audit(
        session,
        action="catalog.price_tier_updated",
        entity="price_tier",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"code": row.code, "name": row.name, "active": row.active},
    )
    await _changed(session, principal, entity="price_tier", action="updated", entity_id=row.id)
    await session.commit()
    return row


# ---------------------------------------------------------------------------
# Tipos de IVA con vigencia
# ---------------------------------------------------------------------------
async def list_tax_rates(
    session: AsyncSession, *, only_current: bool = False
) -> list[TaxRate]:
    return await repo.list_tax_rates(session, only_current=only_current)


async def create_tax_rate_version(
    session: AsyncSession,
    principal: Principal,
    *,
    code: str,
    name: str,
    rate: Decimal,
    valid_from,
) -> TaxRate:
    """Nueva versión de un tipo de IVA: si hay una vigente del mismo código,
    se cierra el día antes de la nueva fecha (histórico coherente)."""
    if await repo.get_tax_rate_version(session, code, valid_from) is not None:
        raise _conflict(f"Ya existe una versión del IVA {code} con esa fecha de inicio")
    current = await repo.get_current_tax_rate_by_code(session, code)
    if current is not None and current.valid_from >= valid_from:
        raise AppError(
            422,
            ErrorCode.VALIDATION_ERROR,
            "La nueva versión debe empezar después de la vigente "
            f"(vigente desde {current.valid_from.isoformat()})",
        )
    if current is not None:
        current.valid_to = valid_from  # ck_tax_rates_period exige > valid_from propio
    row = TaxRate(code=code, name=name, rate=rate, valid_from=valid_from)
    session.add(row)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="catalog.tax_rate_created",
        entity="tax_rate",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"code": code, "rate": str(rate), "valid_from": valid_from.isoformat()},
    )
    await _changed(session, principal, entity="tax_rate", action="created", entity_id=row.id)
    await session.commit()
    return row


# ---------------------------------------------------------------------------
# Catálogo optimizado para la pantalla TPV
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PosProduct:
    """Fila del snapshot de venta: todo lo que el TPV necesita para vender."""

    id: UUID
    name: str
    short_name: str | None
    sku: str | None
    category_id: UUID | None
    department_id: UUID | None
    tax_code: str
    tax_rate: Decimal
    price: Decimal
    weighable: bool
    kitchen: bool
    sort_order: int
    barcodes: list[str] = field(default_factory=list)
    tier_prices: dict[str, Decimal] = field(default_factory=dict)


async def pos_catalog(session: AsyncSession) -> list[PosProduct]:
    """Snapshot optimizado (3 consultas en total, sin N+1): solo productos
    activos con categoría y departamento también activos, ordenados para la
    rejilla. Los paneles visuales son fase TPV visual, no de Productos."""
    rows = await repo.pos_products(session)
    ids = [product.id for product, _, _, _ in rows]
    barcodes: dict[UUID, list[str]] = {}
    for pid, code in await repo.pos_barcodes(session, ids):
        barcodes.setdefault(pid, []).append(code)
    tiers: dict[UUID, dict[str, Decimal]] = {}
    for pid, tier_code, price in await repo.pos_tier_prices(session, ids):
        tiers.setdefault(pid, {})[tier_code] = price

    return [
        PosProduct(
            id=product.id,
            name=product.name,
            short_name=product.short_name,
            sku=product.sku,
            category_id=product.category_id,
            department_id=category.department_id if category is not None else None,
            tax_code=tax_rate.code,
            tax_rate=tax_rate.rate,
            price=product.price,
            weighable=product.weighable,
            kitchen=product.kitchen,
            sort_order=product.sort_order,
            barcodes=barcodes.get(product.id, []),
            tier_prices=tiers.get(product.id, {}),
        )
        for product, category, _, tax_rate in rows
    ]


# ---------------------------------------------------------------------------
# Paneles del TPV visual (fase 05 · Paneles y TPV visual)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PanelProductData:
    """Snapshot de venta embebido en cada botón de la rejilla."""

    id: UUID
    name: str
    short_name: str | None
    sku: str | None
    price: Decimal
    tax_code: str
    tax_rate: Decimal
    weighable: bool
    kitchen: bool


@dataclass(frozen=True)
class PosPanelItemData:
    id: UUID
    label: str | None
    color: str | None
    grid_row: int
    grid_col: int
    sort_order: int
    product: PanelProductData


@dataclass(frozen=True)
class PosSubPanelData:
    id: UUID
    name: str
    sort_order: int
    items: list[PosPanelItemData] = field(default_factory=list)


@dataclass(frozen=True)
class PosPanelData:
    id: UUID
    name: str
    sort_order: int
    subpanels: list[PosSubPanelData] = field(default_factory=list)
    items: list[PosPanelItemData] = field(default_factory=list)


async def pos_panels(session: AsyncSession) -> list[PosPanelData]:
    """Árbol completo Categorías→Paneles→SubPaneles→Productos en 3 consultas
    (sin N+1): la caché de terminal que pinta el TPV visual (§7.2). Solo filas
    activas; los productos inactivos desaparecen de la rejilla."""
    panels, subpanels, item_rows = await repo.pos_panels(session)

    by_panel: dict[UUID, list[PosPanelItemData]] = {}
    by_subpanel: dict[UUID, list[PosPanelItemData]] = {}
    for item, product, tax_rate in item_rows:
        data = PosPanelItemData(
            id=item.id,
            label=item.label,
            color=item.color,
            grid_row=item.grid_row,
            grid_col=item.grid_col,
            sort_order=item.sort_order,
            product=PanelProductData(
                id=product.id,
                name=product.name,
                short_name=product.short_name,
                sku=product.sku,
                price=product.price,
                tax_code=tax_rate.code,
                tax_rate=tax_rate.rate,
                weighable=product.weighable,
                kitchen=product.kitchen,
            ),
        )
        target = by_subpanel if item.subpanel_id is not None else by_panel
        key = item.subpanel_id if item.subpanel_id is not None else item.panel_id
        target.setdefault(key, []).append(data)

    subs_by_panel: dict[UUID, list[PosSubPanelData]] = {}
    for sub in subpanels:
        subs_by_panel.setdefault(sub.panel_id, []).append(
            PosSubPanelData(
                id=sub.id, name=sub.name, sort_order=sub.sort_order,
                items=by_subpanel.get(sub.id, []),
            )
        )
    return [
        PosPanelData(
            id=panel.id,
            name=panel.name,
            sort_order=panel.sort_order,
            subpanels=subs_by_panel.get(panel.id, []),
            items=by_panel.get(panel.id, []),
        )
        for panel in panels
    ]


async def create_panel(
    session: AsyncSession, principal: Principal, *, name: str, sort_order: int
) -> Panel:
    row = Panel(name=name, sort_order=sort_order)
    session.add(row)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="catalog.panel_created",
        entity="panel",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"name": name},
    )
    await _changed(session, principal, entity="panel", action="created", entity_id=row.id)
    await session.commit()
    return row


async def update_panel(
    session: AsyncSession, principal: Principal, panel_id: UUID, *, fields: dict
) -> Panel:
    row = await repo.get_panel(session, panel_id)
    if row is None:
        raise _not_found("Panel no encontrado")
    for key, value in fields.items():
        setattr(row, key, value)
    await auth_repo.record_audit(
        session,
        action="catalog.panel_updated",
        entity="panel",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"name": row.name, "active": row.active},
    )
    await _changed(session, principal, entity="panel", action="updated", entity_id=row.id)
    await session.commit()
    return row


async def delete_panel(
    session: AsyncSession, principal: Principal, panel_id: UUID
) -> Panel:
    """Baja lógica: la rejilla deja de venderse pero la fila persiste."""
    row = await repo.get_panel(session, panel_id)
    if row is None:
        raise _not_found("Panel no encontrado")
    row.active = False
    await auth_repo.record_audit(
        session,
        action="catalog.panel_deleted",
        entity="panel",
        user_id=principal.user_id,
        entity_id=row.id,
    )
    await _changed(session, principal, entity="panel", action="deleted", entity_id=row.id)
    await session.commit()
    return row


async def reorder_panels(
    session: AsyncSession, principal: Principal, items: list[tuple[UUID, int]]
) -> None:
    await _reorder(
        session, principal, repo.Panel,
        action="catalog.panels_reordered", label="Panel", items=items,
    )


async def create_subpanel(
    session: AsyncSession,
    principal: Principal,
    panel_id: UUID,
    *,
    name: str,
    sort_order: int,
) -> SubPanel:
    if await repo.get_panel(session, panel_id) is None:
        raise _not_found("Panel no encontrado")
    row = SubPanel(panel_id=panel_id, name=name, sort_order=sort_order)
    session.add(row)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="catalog.subpanel_created",
        entity="subpanel",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"panel_id": str(panel_id), "name": name},
    )
    await _changed(session, principal, entity="subpanel", action="created", entity_id=row.id)
    await session.commit()
    return row


async def update_subpanel(
    session: AsyncSession, principal: Principal, subpanel_id: UUID, *, fields: dict
) -> SubPanel:
    row = await repo.get_subpanel(session, subpanel_id)
    if row is None:
        raise _not_found("Subpanel no encontrado")
    for key, value in fields.items():
        setattr(row, key, value)
    await auth_repo.record_audit(
        session,
        action="catalog.subpanel_updated",
        entity="subpanel",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={"name": row.name, "active": row.active},
    )
    await _changed(session, principal, entity="subpanel", action="updated", entity_id=row.id)
    await session.commit()
    return row


async def delete_subpanel(
    session: AsyncSession, principal: Principal, subpanel_id: UUID
) -> SubPanel:
    row = await repo.get_subpanel(session, subpanel_id)
    if row is None:
        raise _not_found("Subpanel no encontrado")
    row.active = False
    await auth_repo.record_audit(
        session,
        action="catalog.subpanel_deleted",
        entity="subpanel",
        user_id=principal.user_id,
        entity_id=row.id,
    )
    await _changed(session, principal, entity="subpanel", action="deleted", entity_id=row.id)
    await session.commit()
    return row


async def reorder_subpanels(
    session: AsyncSession, principal: Principal, items: list[tuple[UUID, int]]
) -> None:
    await _reorder(
        session, principal, repo.SubPanel,
        action="catalog.subpanels_reordered", label="Subpanel", items=items,
    )


async def replace_panel_items(
    session: AsyncSession,
    principal: Principal,
    panel_id: UUID,
    specs: list[dict],
) -> None:
    """Diseño completo de la rejilla del panel (y de sus subpaneles): los items
    se reemplazan todos de una vez — semántica pensada para el diseñador visual.
    Valida todo ANTES de tocar nada (no se rediseña a medias)."""
    panel = await repo.get_panel(session, panel_id)
    if panel is None:
        raise _not_found("Panel no encontrado")

    # Subpaneles referenciados: deben existir y pertenecer a ESTE panel.
    sub_ids = {spec["subpanel_id"] for spec in specs if spec["subpanel_id"] is not None}
    for sub_id in sub_ids:
        sub = await repo.get_subpanel(session, sub_id)
        if sub is None:
            raise _not_found("Subpanel no encontrado")
        if sub.panel_id != panel_id:
            raise AppError(
                422, ErrorCode.VALIDATION_ERROR,
                "El subpanel indicado no pertenece a este panel",
            )

    # Productos: deben existir (el filtro de activos se aplica al leer).
    product_ids = {spec["product_id"] for spec in specs}
    if product_ids:
        found = set((await session.scalars(
            select(Product.id).where(Product.id.in_(product_ids))
        )).all())
        missing = [str(pid) for pid in product_ids if pid not in found]
        if missing:
            raise _not_found(f"Producto no encontrado: {', '.join(missing)}")

    # Una posición por contenedor: dos botones no pueden pisarse.
    seen: set[tuple[UUID | None, int, int]] = set()
    for spec in specs:
        pos = (spec["subpanel_id"], spec["grid_row"], spec["grid_col"])
        if pos in seen:
            raise _conflict(
                f"Ya hay un botón en la fila {spec['grid_row']}, "
                f"columna {spec['grid_col']} de ese contenedor"
            )
        seen.add(pos)

    rows: list[dict] = []
    for spec in specs:
        row = {k: v for k, v in spec.items() if k in _PANEL_ITEM_COLS}
        if row["subpanel_id"] is None:
            row["panel_id"] = panel_id  # botón directo: CHECK ck_panel_items_parent
        rows.append(row)
    await repo.replace_panel_items(session, panel_id, rows)
    await auth_repo.record_audit(
        session,
        action="catalog.panel_items_replaced",
        entity="panel",
        user_id=principal.user_id,
        entity_id=panel_id,
        after_data={"count": len(specs)},
    )
    await _changed(session, principal, entity="panel", action="items_replaced", entity_id=panel_id)
    await session.commit()


_PANEL_ITEM_COLS = frozenset(
    {"panel_id", "subpanel_id", "product_id", "label", "color",
     "grid_row", "grid_col", "sort_order"}
)


async def delete_panel_item(
    session: AsyncSession, principal: Principal, item_id: UUID
) -> None:
    """Quitar un botón suelto (soporte visual; no toca productos ni histórico)."""
    item = await repo.get_panel_item(session, item_id)
    if item is None:
        raise _not_found("Botón no encontrado")
    await repo.delete_panel_item_row(session, item)
    await auth_repo.record_audit(
        session,
        action="catalog.panel_item_deleted",
        entity="panel_item",
        user_id=principal.user_id,
        entity_id=item_id,
    )
    await _changed(session, principal, entity="panel_item", action="deleted", entity_id=item_id)
    await session.commit()

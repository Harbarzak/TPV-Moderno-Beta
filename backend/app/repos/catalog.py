"""Acceso a datos del catálogo (fase 04 · Productos).

Solo consultas y escrituras: sin política de negocio (eso vive en
``services.catalog``). Toda función recibe la ``AsyncSession`` de la petición.
El histórico de precios (``product_prices``) es inmutable: cambiar el precio es
cerrar la fila vigente (``valid_to``) y abrir una nueva, nunca UPDATE del importe.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import bindparam, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.catalog import (
    Category,
    Department,
    Panel,
    PanelItem,
    PriceTier,
    Product,
    ProductBarcode,
    ProductImage,
    ProductPrice,
    ProductTierPrice,
    SubPanel,
    TaxRate,
)


# ---------------------------------------------------------------------------
# Departamentos y categorías (jerarquía de dos niveles, ARCHITECTURE.md §3)
# ---------------------------------------------------------------------------
async def list_departments(session: AsyncSession, *, include_inactive: bool):
    stmt = select(Department).order_by(Department.sort_order, Department.name)
    if not include_inactive:
        stmt = stmt.where(Department.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_department(session: AsyncSession, department_id: UUID) -> Department | None:
    return await session.get(Department, department_id)


async def get_department_by_code(session: AsyncSession, code: str) -> Department | None:
    return await session.scalar(select(Department).where(Department.code == code))


async def list_categories(
    session: AsyncSession, *, department_id: UUID | None, include_inactive: bool
):
    stmt = select(Category).order_by(Category.sort_order, Category.name)
    if department_id is not None:
        stmt = stmt.where(Category.department_id == department_id)
    if not include_inactive:
        stmt = stmt.where(Category.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_category(session: AsyncSession, category_id: UUID) -> Category | None:
    return await session.get(Category, category_id)


async def reorder_rows(
    session: AsyncSession, model, items: list[tuple[UUID, int]]
) -> None:
    """Orden bulk: UPDATE … FROM VALUES en una sola ida (executemany).

    UPDATE de Core (``model.__table__``) y no de ORM: con el WHERE a
    ``bindparam`` la sincronización ORM no aplica (ni «evaluate» ni «fetch»
    soportan este caso) y a nivel Core el executemany va limpio; las
    consultas que leen después refrescan los objetos desde la BD.
    """
    if items:
        table = model.__table__
        await session.execute(
            table.update()
            .where(table.c.id == bindparam("bid"))
            .values(sort_order=bindparam("sort_order")),
            [{"bid": row_id, "sort_order": order} for row_id, order in items],
        )


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
async def get_product(session: AsyncSession, product_id: UUID) -> Product | None:
    return await session.get(Product, product_id)


async def get_product_by_sku(session: AsyncSession, sku: str) -> Product | None:
    return await session.scalar(select(Product).where(Product.sku == sku))


async def search_products(
    session: AsyncSession,
    *,
    category_id: UUID | None = None,
    department_id: UUID | None = None,
    active: bool | None = None,
    search: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Product], int]:
    """Listado paginado con filtro opcional; devuelve (filas, total)."""
    conds = []
    if category_id is not None:
        conds.append(Product.category_id == category_id)
    if department_id is not None:
        conds.append(Category.department_id == department_id)
    if active is not None:
        conds.append(Product.active.is_(active))
    if search:
        conds.append(Product.name.ilike(f"%{search}%"))

    base = select(Product)
    if department_id is not None:
        base = base.join(Category, Product.category_id == Category.id)
    if conds:
        base = base.where(*conds)

    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    )
    stmt = base.order_by(Product.sort_order, Product.name).limit(limit).offset(offset)
    rows = list((await session.scalars(stmt)).all())
    return rows, int(total or 0)


async def get_barcode_owner(session: AsyncSession, barcode: str) -> ProductBarcode | None:
    """Colisión de unicidad global de códigos de barras (comprobar antes de insertar)."""
    return await session.scalar(
        select(ProductBarcode).where(ProductBarcode.barcode == barcode)
    )


async def replace_barcodes(
    session: AsyncSession, product_id: UUID, barcodes: list[str]
) -> None:
    """Sustitución completa de los códigos del producto (datos de soporte, no
    económicos: el reemplazo físico es aquí la semántica pactada)."""
    await session.execute(delete(ProductBarcode).where(ProductBarcode.product_id == product_id))
    for code in barcodes:
        session.add(ProductBarcode(product_id=product_id, barcode=code))


async def replace_images(
    session: AsyncSession, product_id: UUID, images: list[tuple[str, int]]
) -> None:
    await session.execute(delete(ProductImage).where(ProductImage.product_id == product_id))
    for path, sort_order in images:
        session.add(ProductImage(product_id=product_id, path=path, sort_order=sort_order))


async def list_images(session: AsyncSession, product_id: UUID) -> list[ProductImage]:
    stmt = (
        select(ProductImage)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.sort_order, ProductImage.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def list_barcodes(session: AsyncSession, product_id: UUID) -> list[str]:
    stmt = (
        select(ProductBarcode.barcode)
        .where(ProductBarcode.product_id == product_id)
        .order_by(ProductBarcode.created_at)
    )
    return list((await session.scalars(stmt)).all())


# ---------------------------------------------------------------------------
# Histórico de precios (inmutable) y precios por tarifa
# ---------------------------------------------------------------------------
async def get_current_price_row(
    session: AsyncSession, product_id: UUID
) -> ProductPrice | None:
    return await session.scalar(
        select(ProductPrice).where(
            ProductPrice.product_id == product_id,
            ProductPrice.valid_to.is_(None),
        )
    )


def open_price(session: AsyncSession, product_id: UUID, price: Decimal) -> None:
    """Abre una fila de histórico con el precio nuevo (vigente desde ahora)."""
    session.add(ProductPrice(product_id=product_id, price=price))


def close_price(session: AsyncSession, row: ProductPrice) -> None:
    """Cierra la fila vigente (inmutable: no se toca el importe)."""
    if row.valid_to is None:
        row.valid_to = datetime.now(UTC).replace(microsecond=0)  # timestamptz UTC


async def price_history(
    session: AsyncSession, product_id: UUID
) -> list[ProductPrice]:
    stmt = (
        select(ProductPrice)
        .where(ProductPrice.product_id == product_id)
        .order_by(ProductPrice.valid_from.desc())
    )
    return list((await session.scalars(stmt)).all())


async def list_tier_prices(
    session: AsyncSession, product_id: UUID
) -> list[tuple[str, Decimal]]:
    """Precio por tarifa como (code, price); solo tarifas activas."""
    stmt = (
        select(PriceTier.code, ProductTierPrice.price)
        .join(PriceTier, ProductTierPrice.tier_id == PriceTier.id)
        .where(ProductTierPrice.product_id == product_id, PriceTier.active.is_(True))
        .order_by(PriceTier.sort_order, PriceTier.code)
    )
    rows = (await session.execute(stmt)).all()
    return [(code, price) for code, price in rows]


async def replace_tier_prices(
    session: AsyncSession, product_id: UUID, tiers: list[tuple[UUID, Decimal]]
) -> None:
    await session.execute(
        delete(ProductTierPrice).where(ProductTierPrice.product_id == product_id)
    )
    for tier_id, price in tiers:
        session.add(ProductTierPrice(product_id=product_id, tier_id=tier_id, price=price))


# ---------------------------------------------------------------------------
# Tarifas
# ---------------------------------------------------------------------------
async def get_price_tier(session: AsyncSession, tier_id: UUID) -> PriceTier | None:
    return await session.get(PriceTier, tier_id)


async def get_price_tier_by_code(session: AsyncSession, code: str) -> PriceTier | None:
    return await session.scalar(select(PriceTier).where(PriceTier.code == code))


async def list_price_tiers(session: AsyncSession, *, include_inactive: bool):
    stmt = select(PriceTier).order_by(PriceTier.sort_order, PriceTier.name)
    if not include_inactive:
        stmt = stmt.where(PriceTier.active.is_(True))
    return list((await session.scalars(stmt)).all())


# ---------------------------------------------------------------------------
# Tipos de IVA (vigencia temporal)
# ---------------------------------------------------------------------------
async def get_tax_rate(session: AsyncSession, tax_rate_id: UUID) -> TaxRate | None:
    return await session.get(TaxRate, tax_rate_id)


async def list_tax_rates(session: AsyncSession, *, only_current: bool):
    stmt = select(TaxRate).order_by(TaxRate.code, TaxRate.valid_from.desc())
    if only_current:
        stmt = stmt.where(TaxRate.valid_to.is_(None))
    return list((await session.scalars(stmt)).all())


async def get_current_tax_rate_by_code(
    session: AsyncSession, code: str
) -> TaxRate | None:
    return await session.scalar(
        select(TaxRate).where(TaxRate.code == code, TaxRate.valid_to.is_(None))
    )


async def get_tax_rate_version(
    session: AsyncSession, code: str, valid_from
) -> TaxRate | None:
    return await session.scalar(
        select(TaxRate).where(TaxRate.code == code, TaxRate.valid_from == valid_from)
    )


# ---------------------------------------------------------------------------
# Catálogo optimizado para el TPV (snapshot de venta)
# ---------------------------------------------------------------------------
async def pos_products(session: AsyncSession):
    """Productos vendibles: activos y con su cadena categoría/departamento activa.

    Tres consultas en total (productos+jerarquía+IVA, códigos de barras, precios
    por tarifa): sin N+1. Devuelve tuplas (Product, Category|None, Department|None,
    TaxRate); el IVA va en la propia tupla para evitar lazy-loads en async.
    """
    stmt = (
        select(Product, Category, Department, TaxRate)
        .join(Category, Product.category_id == Category.id, isouter=True)
        .join(Department, Category.department_id == Department.id, isouter=True)
        .join(TaxRate, Product.tax_rate_id == TaxRate.id)
        .where(
            Product.active.is_(True),
            or_(Product.category_id.is_(None), Category.active.is_(True)),
            or_(Category.department_id.is_(None), Department.active.is_(True)),
        )
        .order_by(Department.sort_order, Category.sort_order, Product.sort_order, Product.name)
    )
    return (await session.execute(stmt)).all()


async def pos_barcodes(session: AsyncSession, product_ids: list[UUID]):
    if not product_ids:
        return []
    stmt = (
        select(ProductBarcode.product_id, ProductBarcode.barcode)
        .where(ProductBarcode.product_id.in_(product_ids))
        .order_by(ProductBarcode.created_at)
    )
    return (await session.execute(stmt)).all()


async def pos_tier_prices(session: AsyncSession, product_ids: list[UUID]):
    if not product_ids:
        return []
    stmt = (
        select(ProductTierPrice.product_id, PriceTier.code, ProductTierPrice.price)
        .join(PriceTier, ProductTierPrice.tier_id == PriceTier.id)
        .where(
            ProductTierPrice.product_id.in_(product_ids),
            PriceTier.active.is_(True),
        )
        .order_by(PriceTier.sort_order)
    )
    return (await session.execute(stmt)).all()


# ---------------------------------------------------------------------------
# Paneles del TPV visual (fase 05 · Paneles y TPV visual)
# ---------------------------------------------------------------------------
async def list_panels(session: AsyncSession, *, include_inactive: bool):
    stmt = select(Panel).order_by(Panel.sort_order, Panel.name)
    if not include_inactive:
        stmt = stmt.where(Panel.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_panel(session: AsyncSession, panel_id: UUID) -> Panel | None:
    return await session.get(Panel, panel_id)


async def get_subpanel(session: AsyncSession, subpanel_id: UUID) -> SubPanel | None:
    return await session.get(SubPanel, subpanel_id)


async def get_panel_item(session: AsyncSession, item_id: UUID) -> PanelItem | None:
    return await session.get(PanelItem, item_id)


async def pos_panels(session: AsyncSession):
    """Árbol de venta en 3 consultas (paneles, subpaneles, items+producto), sin
    N+1. Solo filas activas; el producto va embebido con su IVA para que el
    árbol baste como caché de terminal (ARCHITECTURE.md §7.2).

    Devuelve (paneles, subpaneles, filas (PanelItem, Product, TaxRate))."""
    panels = list(
        (await session.scalars(
            select(Panel).where(Panel.active.is_(True)).order_by(Panel.sort_order, Panel.name)
        )).all()
    )
    panel_ids = [panel.id for panel in panels]
    subpanels = list(
        (await session.scalars(
            select(SubPanel)
            .where(SubPanel.panel_id.in_(panel_ids), SubPanel.active.is_(True))
            .order_by(SubPanel.sort_order, SubPanel.name)
        )).all()
    ) if panel_ids else []
    subpanel_ids = [sub.id for sub in subpanels]

    item_rows: list = []
    if subpanel_ids:
        item_rows = list((await session.execute(_panel_items_stmt(panel_ids, subpanel_ids))).all())
    elif panel_ids:
        item_rows = list((await session.execute(_panel_items_stmt(panel_ids, []))).all())
    return panels, subpanels, item_rows


def _panel_items_stmt(panel_ids: list[UUID], subpanel_ids: list[UUID]):
    """Items de los paneles (directos) y de sus subpaneles, con producto e IVA."""
    conds = [PanelItem.panel_id.in_(panel_ids)]
    if subpanel_ids:
        conds.append(PanelItem.subpanel_id.in_(subpanel_ids))
    return (
        select(PanelItem, Product, TaxRate)
        .join(Product, PanelItem.product_id == Product.id)
        .join(TaxRate, Product.tax_rate_id == TaxRate.id)
        .where(or_(*conds), Product.active.is_(True))
        .order_by(PanelItem.grid_row, PanelItem.grid_col, PanelItem.sort_order)
    )


async def replace_panel_items(
    session: AsyncSession,
    panel_id: UUID,
    specs: list[dict],
) -> None:
    """Rediseño de la rejilla: los items del panel y de TODOS sus subpaneles
    (referenciados o no) se reemplazan completos — datos de soporte, mismo
    pacto que barcodes/images."""
    await session.execute(
        delete(PanelItem).where(
            or_(
                PanelItem.panel_id == panel_id,
                PanelItem.subpanel_id.in_(
                    select(SubPanel.id).where(SubPanel.panel_id == panel_id)
                ),
            )
        )
    )
    for spec in specs:
        session.add(PanelItem(**spec))


async def delete_panel_item_row(session: AsyncSession, item: PanelItem) -> None:
    """Quitar un botón suelto de la rejilla (soporte, no histórico económico)."""
    await session.delete(item)

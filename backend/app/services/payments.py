"""Servicio de formas de pago (fase 07 · Pagos): catálogo configurable.

Efectivo, tarjeta y cualquier otra forma que el negocio cree (vale, crédito,
otra): la tabla ``payment_methods`` es dato, no código. La escritura exige
``admin.parameters`` (comprobado en la capa API, ARCHITECTURE.md §7.2); la
lectura es operación de venta (``sales.sell``): la pantalla de cobro pinta un botón
por forma activa. El «kind» gobierna la semántica del cobro: solo ``cash``
admite importe entregado y genera cambio.

Nunca importa de ``api``.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.enums import PaymentKind
from app.db.models.catalog import PaymentMethod
from app.repos import auth as auth_repo
from app.repos import sales as repo
from app.services.auth import Principal


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


async def list_payment_methods(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[PaymentMethod]:
    """Formas de pago ordenadas (para pintar la pantalla de cobro)."""

    return await repo.list_payment_methods(session, include_inactive=include_inactive)


async def create_payment_method(
    session: AsyncSession,
    principal: Principal,
    *,
    code: str,
    name: str,
    kind: PaymentKind,
    opens_drawer: bool = False,
    sort_order: int = 0,
) -> PaymentMethod:
    """Alta de forma de pago; el código es único (409 si existe)."""

    if await repo.find_payment_method_by_code(session, code):
        raise _conflict("Ya existe una forma de pago con ese código")

    method = PaymentMethod(
        code=code,
        name=name,
        kind=kind,
        opens_drawer=opens_drawer,
        sort_order=sort_order,
    )
    session.add(method)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="payments.method_created",
        entity="payment_method",
        user_id=principal.user_id,
        entity_id=method.id,
        after_data={"code": code, "name": name, "kind": kind.value},
    )
    await session.commit()
    return method


async def update_payment_method(
    session: AsyncSession, principal: Principal, method_id: UUID, *, fields: dict
) -> PaymentMethod:
    """Modifica nombre, código, kind, apertura de cajón u orden."""

    method = await repo.get_payment_method(session, method_id)
    if method is None:
        raise _not_found("Forma de pago no encontrada")

    if "code" in fields and fields["code"] != method.code:
        clash = await repo.find_payment_method_by_code(session, fields["code"])
        if clash is not None:
            raise _conflict("Ya existe una forma de pago con ese código")
        method.code = fields["code"]
    if "name" in fields:
        method.name = fields["name"]
    if "kind" in fields:
        method.kind = fields["kind"]
    if "opens_drawer" in fields:
        method.opens_drawer = fields["opens_drawer"]
    if "sort_order" in fields:
        method.sort_order = fields["sort_order"]

    await auth_repo.record_audit(
        session,
        action="payments.method_updated",
        entity="payment_method",
        user_id=principal.user_id,
        entity_id=method.id,
        after_data={"code": method.code, "fields": sorted(fields)},
    )
    await session.commit()
    return method


async def deactivate_payment_method(
    session: AsyncSession, principal: Principal, method_id: UUID
) -> None:
    """Baja lógica: deja de ofrecerse en el cobro; el histórico se conserva."""

    method = await repo.get_payment_method(session, method_id)
    if method is None:
        raise _not_found("Forma de pago no encontrada")
    if not method.active:
        raise _conflict("La forma de pago ya está inactiva")

    method.active = False
    await auth_repo.record_audit(
        session,
        action="payments.method_deactivated",
        entity="payment_method",
        user_id=principal.user_id,
        entity_id=method.id,
        after_data={"code": method.code},
    )
    await session.commit()

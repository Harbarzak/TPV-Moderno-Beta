"""Formas de pago configurables (fase 07 · Pagos): ``/api/v1/admin/payment-methods``.

La pantalla de cobro pinta un botón por forma activa: el catálogo es dato
(``payment_methods``), no código — efectivo, tarjeta, vale, crédito u otras
que el negocio cree (ARCHITECTURE.md §7.2). Escritura con ``admin.parameters``;
lectura con ``sales.sell`` (es operación de venta: se consulta al cobrar).
El «kind» gobierna la semántica del cobro: solo ``cash`` admite importe
entregado y genera cambio. ``DELETE`` es baja lógica: el histórico se conserva.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status as http_status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.db.enums import PaymentKind
from app.db.models.catalog import PaymentMethod
from app.db.models.sales import Payment
from app.services import payments as payments_service

router = APIRouter(prefix="/admin", tags=["payments"])

_read = [Depends(require_permission("sales.sell"))]
_manage = [Depends(require_permission("admin.parameters"))]


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class PaymentMethodResponse(BaseModel):
    id: UUID
    code: str
    name: str
    kind: str
    opens_drawer: bool
    sort_order: int
    active: bool


class PaymentMethodListResponse(BaseModel):
    items: list[PaymentMethodResponse]


class PaymentResponse(BaseModel):
    """Pago de una venta: el dinero viaja SIEMPRE como string (§3)."""

    id: UUID
    order_id: UUID
    payment_method_id: UUID
    code: str
    kind: str
    amount: str
    status: str
    external_ref: str | None
    confirmed_at: datetime | None


def _method_response(method: PaymentMethod) -> PaymentMethodResponse:
    return PaymentMethodResponse(
        id=method.id,
        code=method.code,
        name=method.name,
        kind=method.kind.value,
        opens_drawer=method.opens_drawer,
        sort_order=method.sort_order,
        active=method.active,
    )


def payment_response(payment: Payment, method: PaymentMethod) -> PaymentResponse:
    """Respuesta de un pago (la usa también el router de ventas)."""

    return PaymentResponse(
        id=payment.id,
        order_id=payment.order_id,
        payment_method_id=method.id,
        code=method.code,
        kind=method.kind.value,
        amount=format(payment.amount, "f"),
        status=payment.status.value,
        external_ref=payment.external_ref,
        confirmed_at=payment.confirmed_at,
    )


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class MethodCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    kind: PaymentKind
    opens_drawer: bool = False
    sort_order: int = Field(default=0, ge=0, le=9999)


class MethodUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=40)
    name: str | None = Field(None, min_length=1, max_length=80)
    kind: PaymentKind | None = None
    opens_drawer: bool | None = None
    sort_order: int | None = Field(None, ge=0, le=9999)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/payment-methods", dependencies=_read,
            response_model=PaymentMethodListResponse)
async def list_payment_methods(
    session: DbSession,
    include_inactive: Annotated[bool, Query()] = False,
) -> PaymentMethodListResponse:
    rows = await payments_service.list_payment_methods(
        session, include_inactive=include_inactive
    )
    return PaymentMethodListResponse(items=[_method_response(m) for m in rows])


@router.post("/payment-methods", dependencies=_manage,
             response_model=PaymentMethodResponse,
             status_code=http_status.HTTP_201_CREATED)
async def create_payment_method(
    payload: MethodCreate, session: DbSession, user: CurrentUser
):
    method = await payments_service.create_payment_method(
        session,
        user,
        code=payload.code,
        name=payload.name,
        kind=payload.kind,
        opens_drawer=payload.opens_drawer,
        sort_order=payload.sort_order,
    )
    return _method_response(method)


@router.patch("/payment-methods/{method_id}", dependencies=_manage,
              response_model=PaymentMethodResponse)
async def update_payment_method(
    method_id: UUID, payload: MethodUpdate, session: DbSession, user: CurrentUser
):
    fields = payload.model_fields_set & {"code", "name", "kind", "opens_drawer", "sort_order"}
    method = await payments_service.update_payment_method(
        session, user, method_id, fields={key: getattr(payload, key) for key in fields}
    )
    return _method_response(method)


@router.delete("/payment-methods/{method_id}", dependencies=_manage,
               status_code=http_status.HTTP_204_NO_CONTENT)
async def deactivate_payment_method(
    method_id: UUID, session: DbSession, user: CurrentUser
) -> Response:
    await payments_service.deactivate_payment_method(session, user, method_id)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)

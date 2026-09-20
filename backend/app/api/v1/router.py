"""Router raíz de ``/api/v1``.

Cada dominio añade aquí su router (auth, catalog, sales, cash, reports, admin…);
los healthchecks ya están montados. Los servicios nunca importan de esta capa.
"""

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.cash import router as cash_router
from app.api.v1.documents import admin_router as documents_admin_router
from app.api.v1.documents import router as documents_router
from app.api.v1.fiscal import router as fiscal_router
from app.api.v1.health import router as health_router
from app.api.v1.kds import router as kds_router
from app.api.v1.payments import router as payments_router
from app.api.v1.printing import admin_router as printing_admin_router
from app.api.v1.printing import router as printing_router
from app.api.v1.restaurant import router as restaurant_router
from app.api.v1.reports import router as reports_router
from app.api.v1.sales import router as sales_router
from app.api.v1.ws import router as ws_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(admin_router)
api_v1_router.include_router(catalog_router)
api_v1_router.include_router(payments_router)
api_v1_router.include_router(sales_router)
api_v1_router.include_router(cash_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(documents_admin_router)
api_v1_router.include_router(printing_router)
api_v1_router.include_router(printing_admin_router)
api_v1_router.include_router(restaurant_router)
api_v1_router.include_router(kds_router)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(fiscal_router)
api_v1_router.include_router(ws_router)

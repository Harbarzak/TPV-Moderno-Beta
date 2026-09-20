"""Modelos del dominio TPV. Importar este módulo registra todas las tablas en Base.metadata."""
from app.db.models.security import (
    Device, Permission, RolePermission, SystemRole, Terminal, User, UserSession,
)
from app.db.models.catalog import (
    Category, Customer, Department, Panel, PanelItem, PaymentMethod, PriceTier,
    Product, ProductBarcode, ProductImage, ProductPrice, ProductTierPrice, SubPanel,
    TaxRate,
)
from app.db.models.sales import (
    DiningTable, DocumentSequence, Invoice, InvoiceLine, Order, OrderLine,
    Payment, Refund, SaleEvent, Ticket, Zone,
)
from app.db.models.cash import CashCount, CashCountLine, CashMovement, CashSession
from app.db.models.restaurant import KitchenOrder, KitchenOrderLine, KitchenStation
from app.db.models.printing import PrintJob, Printer
from app.db.models.fiscal import FiscalDocument, FiscalEvent
from app.db.models.system import AuditLog, EventLog, IdempotencyKey, Parameter

__all__ = [
    # seguridad
    "Device", "Permission", "RolePermission", "SystemRole", "Terminal", "User", "UserSession",
    # catálogo
    "Category", "Customer", "Department", "Panel", "PanelItem", "PaymentMethod",
    "PriceTier", "Product", "ProductBarcode", "ProductImage", "ProductPrice",
    "ProductTierPrice", "SubPanel", "TaxRate",
    # ventas
    "DiningTable", "DocumentSequence", "Invoice", "InvoiceLine", "Order", "OrderLine",
    "Payment", "Refund", "SaleEvent", "Ticket", "Zone",
    # caja
    "CashCount", "CashCountLine", "CashMovement", "CashSession",
    # restaurante / KDS
    "KitchenOrder", "KitchenOrderLine", "KitchenStation",
    # impresión
    "PrintJob", "Printer",
    # fiscalidad (plugin, fase 34)
    "FiscalDocument", "FiscalEvent",
    # sistema
    "AuditLog", "EventLog", "IdempotencyKey", "Parameter",
]

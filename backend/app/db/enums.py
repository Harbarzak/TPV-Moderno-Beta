"""Enumerados nativos de PostgreSQL. Fase 01 · Base de datos."""
import enum
import re

from sqlalchemy import Enum as SAEnum


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def pg_enum(e: type[enum.Enum]) -> SAEnum:
    """Enum nativo de PG; el nombre se deriva de la clase en snake_case
    (DeviceKind → device_kind) y los valores son los de schema.sql."""
    return SAEnum(
        e,
        native_enum=True,
        name=_snake(e.__name__),
        values_callable=lambda cls: [m.value for m in cls],
    )


class OrderStatus(str, enum.Enum):
    draft = "draft"
    paid = "paid"
    voided = "voided"


class OrderType(str, enum.Enum):
    bar = "bar"
    restaurant = "restaurant"
    takeaway = "takeaway"


class PaymentKind(str, enum.Enum):
    cash = "cash"
    card = "card"
    voucher = "voucher"
    credit = "credit"
    other = "other"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"
    reversed = "reversed"


class CashMoveKind(str, enum.Enum):
    cash_in = "in"
    cash_out = "out"


class KitchenStatus(str, enum.Enum):
    pending = "pending"
    preparing = "preparing"
    ready = "ready"
    served = "served"
    cancelled = "cancelled"


class DeviceKind(str, enum.Enum):
    agent = "agent"
    printer = "printer"
    pinpad = "pinpad"
    cashdrawer = "cashdrawer"
    display = "display"


class PrinterKind(str, enum.Enum):
    receipt = "receipt"
    kitchen = "kitchen"
    invoice = "invoice"


class PrinterConn(str, enum.Enum):
    network = "network"
    agent = "agent"


class PrintJobKind(str, enum.Enum):
    ticket = "ticket"
    invoice = "invoice"
    kitchen = "kitchen"
    report_x = "report_x"
    report_z = "report_z"
    test = "test"


class PrintJobStatus(str, enum.Enum):
    queued = "queued"
    sent = "sent"
    printed = "printed"
    failed = "failed"
    cancelled = "cancelled"


class SequenceScope(str, enum.Enum):
    ticket = "ticket"
    invoice = "invoice"


class InvoiceStatus(str, enum.Enum):
    issued = "issued"
    voided = "voided"


class SaleEventType(str, enum.Enum):
    sale_closed = "sale_closed"
    sale_voided = "sale_voided"
    refund_issued = "refund_issued"


# Fiscalidad como plugin (fase 34 · FiscalAdapter, ARCHITECTURE.md §2 #8):
# tipos de documento fiscal, su ciclo de vida y la traza de cada operación.
class FiscalDocumentType(str, enum.Enum):
    sale = "sale"        # venta cobrada (sale_closed)
    void = "void"        # anulación (sale_voided)
    refund = "refund"    # devolución (refund_issued)


class FiscalDocumentStatus(str, enum.Enum):
    pending = "pending"      # creado, a la espera de envío (o de reintento)
    sent = "sent"            # entregado al adaptador, sin confirmación aún
    accepted = "accepted"    # aceptado por el régimen fiscal (terminal)
    rejected = "rejected"    # rechazado: reintento o cancelación manual
    cancelled = "cancelled"  # cerrado sin transmisión (terminal)


class FiscalEventKind(str, enum.Enum):
    queued = "queued"          # documento fiscal creado desde un sale_event
    dispatched = "dispatched"  # intento de envío al adaptador
    accepted = "accepted"      # el régimen aceptó el documento
    rejected = "rejected"      # el régimen (o el adaptador) lo rechazó
    cancelled = "cancelled"    # cancelación manual del documento

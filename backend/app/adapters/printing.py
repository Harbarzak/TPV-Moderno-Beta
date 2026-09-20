"""Adaptadores de impresión (capa DeviceAdapter · fase 10).

La cola (``services.printing``) no sabe de hardware: entrega cada ``PrintJob``
a un ``PrinterAdapter``. Los drivers reales — térmica ESC/POS, impresora
Windows, red 9100, tpv-agent — llegarán en la fase 13 implementando ESTE
protocolo; hoy el servidor registra ``NullPrinterAdapter`` para desarrollo y
pruebas. «Cocina» y «copia» NO son adaptadores: son ``printer_kind`` y jobs
nuevos sobre la misma cola.
"""

from typing import Any, Protocol, runtime_checkable

from app.db.models.printing import Printer, PrintJob


class PrinterError(Exception):
    """El adaptador no pudo entregar el trabajo (impresora caída, sin driver…)."""

    def __init__(self, message: str, *, code: str = "PRINTER_UNREACHABLE") -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class PrinterAdapter(Protocol):
    """Contrato DeviceAdapter de impresión: entregar un job a una impresora.

    ``send`` debe ser idempotente desde el punto de vista del negocio (la cola
    puede reintentar el mismo job) y SOLO levantar ``PrinterError`` ante
    fallos — cualquier otra excepción es un bug del adaptador.
    """

    name: str

    async def send(self, *, printer: Printer, job: PrintJob) -> None:
        """Entrega el job; ``PrinterError`` si no se pudo imprimir."""
        ...


class NullPrinterAdapter:
    """Acepta todos los trabajos y los registra en memoria.

    Adaptador por defecto de ``app.state.print_adapter`` mientras los drivers
    reales no existan (fase 13): la cola y sus estados funcionan de verdad,
    la entrega física es un no-op trazable (``deliveries``) para los tests.
    """

    name = "null"

    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []

    async def send(self, *, printer: Printer, job: PrintJob) -> None:
        self.deliveries.append(
            {
                "printer_id": printer.id,
                "printer_name": printer.name,
                "job_id": job.id,
                "kind": job.kind.value,
            }
        )


class FailingPrinterAdapter(NullPrinterAdapter):
    """Adaptador que SIEMPRE falla (tests de reintentos y trazabilidad)."""

    name = "failing"

    def __init__(self, message: str = "Impresora no responde") -> None:
        super().__init__()
        self.message = message

    async def send(self, *, printer: Printer, job: PrintJob) -> None:
        raise PrinterError(self.message)

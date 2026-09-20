"""FiscalAdapter (fase 34 · Fiscalidad): el punto de extensión fiscal REAL.

La interfaz existía vacía desde ADR-003 junto a las de hardware; esta fase la
implementa como plugin desacoplado (ADR-010):

- El motor de ventas NO conoce este módulo: solo emite ``sale_events``
  genéricos (fase 06). El consumidor es ``app.services.fiscal``, que reclama
  eventos pendientes, crea ``FiscalDocument`` y llama :meth:`FiscalAdapter.submit`.
- Sustituir el régimen (ninguno → Veri*Factu → TicketBAI) es cambiar UNA
  variable de entorno (``TPV_FISCAL_PROVIDER``): cero cambios en ventas.

Adaptadores incluidos:

- ``NullFiscalAdapter`` (``none``, por defecto): consume eventos sin crear
  documentos — el plugin está instalado pero sin régimen activo.
- ``VeriFactuAdapter`` y ``TicketBaiAdapter``: ESQUELETOS independientes,
  seleccionables desde hoy, sin desarrollo legal (decisión del usuario,
  2026-09-14: ningún régimen es necesario todavía). Su ``submit`` falla de
  forma explícita y auditable (``FISCAL_NOT_IMPLEMENTED``); activarlos exige
  desarrollar contra la especificación oficial vigente y su entorno de
  pruebas — nunca contra documentación de terceros (ver docs/fiscal/README.md).

Regla de oro (igual que el hardware, fase 11): el fiscal nunca está en el
camino crítico de una venta. El cobro ya está confirmado cuando el plugin
trabaja; un fallo del adaptador se registra y reintenta, jamás revierte.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


class FiscalError(Exception):
    """Fallo de una operación fiscal. ``code`` estable para trazabilidad."""

    def __init__(self, message: str, *, code: str = "FISCAL_PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FiscalSubmission:
    """Sobre que viaja al adaptador: el snapshot fiscal ya persistido."""

    document_id: UUID
    order_id: UUID
    doc_type: str        # 'sale' | 'void' | 'refund' (FiscalDocumentType)
    payload: dict        # snapshot fiscal (dinero string, §3)
    attempts: int        # intentos previos (0 = primer envío)


@dataclass(frozen=True, slots=True)
class FiscalResult:
    """Respuesta del régimen: aceptado (con referencia externa) o rechazado."""

    accepted: bool
    external_ref: str | None = None   # p. ej. CSV de la AEAT o código TBAI
    detail: dict | None = None        # diagnóstico del rechazo, para la traza


@runtime_checkable
class FiscalAdapter(Protocol):
    """Un régimen fiscal: recibe documentos fiscales y confirma o rechaza."""

    name: str

    async def submit(self, submission: FiscalSubmission) -> FiscalResult: ...


# ---------------------------------------------------------------------------
# Sin régimen configurado (por defecto)
# ---------------------------------------------------------------------------
class NullFiscalAdapter:
    """Plugin instalado, régimen no elegido: consume eventos sin documentos.

    La descarga marca los ``sale_events`` como consumidos (la cola no crece
    infinito) y NO crea ``FiscalDocument``: no hay nada que certificar. Al
    activar un régimen real solo se procesan eventos NUEVOS — decisión
    documentada en ADR-010 (el histórico previo no se re-fiscaliza).
    """

    name = "none"

    async def submit(self, submission: FiscalSubmission) -> FiscalResult:
        return FiscalResult(accepted=True, detail={"noop": "sin régimen fiscal activo"})


# ---------------------------------------------------------------------------
# Veri*Factu (régimen estatal) — ESQUELETO sin desarrollo
# ---------------------------------------------------------------------------
class VeriFactuAdapter:
    """Esqueleto de Veri*Factu (RD 1007/2023 y Orden HAC/1177/2024).

    SIN DESARROLLO (decisión del usuario, 2026-09-14): el adaptador es
    seleccionable y auditable, pero su envío no está construido. Activarlo
    exige, contra la especificación técnica oficial de la AEAT vigente en ese
    momento: generar los registros de facturación (XML), la encadenación por
    huella, el QR, el alta en la AEAT como SIF y superar su entorno de
    pruebas antes de producción.

    Plazo legal verificado el 2026-09-14 en la nota informativa oficial de la
    AEAT (ampliación por RDL 15/2025, de 2 de diciembre): SIF adaptados antes
    del **1-1-2027** para obligados del Impuesto sobre Sociedades y antes del
    **1-7-2027** para el resto. Re-verificar en la sede AEAT al activar.
    """

    name = "verifactu"

    async def submit(self, submission: FiscalSubmission) -> FiscalResult:
        raise FiscalError(
            "Veri*Factu no está desarrollado: activar un régimen exige implementar "
            "su envío contra la especificación oficial vigente (docs/fiscal/README.md)",
            code="FISCAL_NOT_IMPLEMENTED",
        )


# ---------------------------------------------------------------------------
# TicketBAI (País Vasco: Araba, Bizkaia, Gipuzkoa) — ESQUELETO sin desarrollo
# ---------------------------------------------------------------------------
class TicketBaiAdapter:
    """Esqueleto de TicketBAI (decretos forales de Álava, Bizkaia y Gipuzkoa).

    SIN DESARROLLO (decisión del usuario, 2026-09-14). TicketBAI ya está en
    plena vigencia en Euskadi (Bizkaia completó su despliegue el 1-1-2026 con
    la Norma Foral 8/2023; Araba desde 2022 y Gipuzkoa desde 2023). Activarlo
    exige, contra las especificaciones de cada Diputación (son TRES regímenes
    hermanos, no uno): generar el fichero TicketBAI con código TBAI y QR,
    firmarlo (XAdES) y entregarlo por lote (fichero batuz/batch) al organismo
    territorial, además de darse de alta como desarrollador.

    Re-verificar los decretos forales vigentes de cada territorio al activar.
    """

    name = "ticketbai"

    async def submit(self, submission: FiscalSubmission) -> FiscalResult:
        raise FiscalError(
            "TicketBAI no está desarrollado: activar un régimen exige implementar su "
            "envío contra los decretos forales vigentes (docs/fiscal/README.md)",
            code="FISCAL_NOT_IMPLEMENTED",
        )


#: Proveedores válidos para ``TPV_FISCAL_PROVIDER`` (falla en el arranque si
#: el valor no está aquí: la configuración errónea se detecta al desplegar).
PROVIDERS: tuple[str, ...] = ("none", "verifactu", "ticketbai")


def build_fiscal_adapter(provider: str) -> FiscalAdapter:
    """Fábrica del adaptador fiscal según configuración (``TPV_FISCAL_PROVIDER``)."""

    adapters = {
        NullFiscalAdapter.name: NullFiscalAdapter,
        VeriFactuAdapter.name: VeriFactuAdapter,
        TicketBaiAdapter.name: TicketBaiAdapter,
    }
    factory = adapters.get(provider)
    if factory is None:
        raise ValueError(
            f"TPV_FISCAL_PROVIDER='{provider}' no válido; usa uno de: {', '.join(PROVIDERS)}"
        )
    return factory()

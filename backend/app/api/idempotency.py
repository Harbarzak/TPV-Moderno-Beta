"""Idempotencia del lado API (fase 14 · Offline): traduce la cabecera HTTP
``Idempotency-Key`` al contexto que consumen los servicios.

La huella cubre método, ruta y contenido YA VALIDADO (``model_dump_json`` del
modelo pydantic): dos reintentos con el mismo JSON comparten huella aunque
lleguen con otros espacios u orden de claves; una petición que cambie un solo
importe no puede heredar la respuesta de otra. El endpoint lógico evita
además reutilizar la clave entre operaciones distintas (crear pedido ≠ añadir
línea), aunque la ruta coincida.
"""

from fastapi import Request
from pydantic import BaseModel

from app.core.errors import AppError, ErrorCode
from app.services.idempotency import HEADER, Idempotency, fingerprint, parse_key


def idempotency_of(
    request: Request,
    payload: BaseModel | None,
    *,
    endpoint: str,
    required: bool = False,
) -> Idempotency | None:
    """Contexto de idempotencia de la petición (o ``None`` si no aplica).

    Sin cabecera: ``None`` (operación sin idempotencia), salvo ``required``
    — el cobro, §4.1 — que responde 422 ``IDEMPOTENCY_KEY_REQUIRED``. Cabecera
    malformada (demasiado larga) → 422 ``VALIDATION_ERROR`` (lo decide
    :func:`app.services.idempotency.parse_key`).
    """

    key = parse_key(request.headers.get(HEADER))
    if key is None:
        if required:
            raise AppError(
                422,
                ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
                f"«{endpoint}» exige la cabecera {HEADER}",
            )
        return None
    body_json = payload.model_dump_json() if payload is not None else None
    return Idempotency(
        endpoint=endpoint,
        key=key,
        fingerprint=fingerprint(request.method, request.url.path, body_json),
    )

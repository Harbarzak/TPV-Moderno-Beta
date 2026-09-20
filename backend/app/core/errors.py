"""Manejo de errores: problemas RFC 9457 (``application/problem+json``) con ``code`` estable.

Contrato de la API (ARCHITECTURE.md §7.1): toda respuesta de error es un Problem Details
con un ``code`` estable legible por los clientes (``VALIDATION_ERROR``, ``NOT_FOUND``,
``PERMISSION_DENIED``, …). Los servicios lanzan :class:`AppError`; el formato lo ponen los
handlers aquí registrados.
"""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

PROBLEM_MEDIA_TYPE = "application/problem+json"

logger = get_logger("tpv.errors")


class ErrorCode:
    """Códigos estables de error (contrato API). Los dominios añadirán los suyos."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"  # recurso duplicado o colisión de unicidad (fase 04)
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    # Ventas (fase 06 · Motor de ventas)
    SALE_ALREADY_PAID = "SALE_ALREADY_PAID"  # operación solo válida en borrador
    # Pagos (fase 07 · Pagos)
    PAYMENT_INSUFFICIENT = "PAYMENT_INSUFFICIENT"  # la suma de pagos no cubre el total
    PAYMENT_EXCESS = "PAYMENT_EXCESS"              # la suma de pagos excede el total
    # Autenticación (fase 03)
    AUTH_REQUIRED = "AUTH_REQUIRED"              # sin token o cabecera malformada
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"  # usuario/contraseña/PIN incorrectos
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    SESSION_REVOKED = "SESSION_REVOKED"          # sesión cerrada (logout/rotación)
    RATE_LIMITED = "RATE_LIMITED"
    # Offline / idempotencia (fase 14 · Offline)
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"  # el cobro exige la cabecera
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"      # clave usada con otro contenido
    # Administración (fase Administración · Backups)
    BACKUP_UNAVAILABLE = "BACKUP_UNAVAILABLE"  # pg_dump no está en el servidor
    BACKUP_FAILED = "BACKUP_FAILED"            # pg_dump devolvió error


class AppError(Exception):
    """Error de aplicación con código estable, estado HTTP y cabeceras opcionales."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def problem_response(
    status_code: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Construye la respuesta Problem Details (RFC 9457) con el ``code`` estable."""
    try:
        title = HTTPStatus(status_code).phrase
    except ValueError:
        title = "Error"
    return JSONResponse(
        {
            "type": f"urn:tpv:error:{code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "code": code,
        },
        status_code=status_code,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Instala los handlers de error de toda la aplicación."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return problem_response(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        parts = [
            f"{'.'.join(str(loc) for loc in error.get('loc', []))}: {error.get('msg', 'invalid')}"
            for error in exc.errors()
        ]
        detail = "; ".join(parts)[:500]
        return problem_response(422, ErrorCode.VALIDATION_ERROR, detail)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_by_status = {
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
        }
        code = code_by_status.get(exc.status_code, f"HTTP_{exc.status_code}")
        return problem_response(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # El request_id ya está en el contexto (middleware): el log queda correlacionado.
        # Respuesta genérica: nunca filtrar detalles internos al cliente.
        logger.error(
            "unhandled_error",
            method=request.method,
            path=request.url.path,
            error=str(exc),
            exc_info=True,
        )
        return problem_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")

"""Idempotencia de operaciones de dinero (fase 14 · Offline; §4.1/§7.1).

Mecanismo: la tabla ``idempotency_keys`` (fase 01) guarda, EN LA MISMA
transacción que el cambio de negocio, la clave del cliente, la huella de la
petición y la respuesta congelada. Así no existe ventana de caída donde el
efecto esté aplicado y el reintento no lo sepa: o está todo, o no está nada.
Repetir la clave dentro de la ventana (24 h) devuelve la MISMA respuesta sin
repetir el efecto (§4.1: "misma key → mismo ticket"); usarla con otro
contenido u otra operación es un error del cliente (409
``IDEMPOTENCY_KEY_REUSED``). Pasada la ventana la clave queda libre (dedupe
acotado, como está documentado).

El cobro (``close``) EXIGE la clave (§4.1): es la única llamada cuyo reintento
puede duplicar dinero; en el resto es opcional (mejora, no requisito). Los
reintentos NUNCA repiten efectos secundarios: ni auditoría, ni eventos, ni
cajón — solo la respuesta congelada.

Los servicios llaman a ``lookup`` AL EMPEZAR (antes de cualquier validación de
negocio: el replay debe ganar incluso sobre un 409 de negocio) y a ``store``
justo antes del commit con la respuesta ya construida (la capa API aporta el
constructor del snapshot: el servicio no conoce los modelos de respuesta). La
carrera de dos reintentos concurrentes la resuelve la PRIMARY KEY: el perdedor
recibe ``IntegrityError`` en el ``flush`` de ``store`` y resuelve con
``replay_after_conflict``.

Nunca importa de ``api``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models.system import IdempotencyKey

HEADER = "Idempotency-Key"

# §7.1: ventana de dedupe acotada; pasada la vuelta, la clave queda libre.
TTL = timedelta(hours=24)

_KEY_MAX = 200  # un UUID v4 mide 36; margen de sobra para claves con prefijo


def _now() -> datetime:
    return datetime.now(UTC)


def parse_key(raw: str | None) -> str | None:
    """Normaliza la cabecera: ``None`` si no viene; 422 si viene malformada.

    Una clave vacía o de espacios cuenta como ausente (un cobro con clave
    vacía responderá 422 ``IDEMPOTENCY_KEY_REQUIRED``, no ``VALIDATION_ERROR``).
    """

    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > _KEY_MAX:
        raise AppError(
            422,
            ErrorCode.VALIDATION_ERROR,
            f"La cabecera {HEADER} excede {_KEY_MAX} caracteres",
        )
    return key


def fingerprint(method: str, path: str, body_json: str | None) -> str:
    """Huella de la petición: método + ruta + contenido ya validado.

    Se calcula sobre el JSON del modelo pydantic (no sobre los bytes crudos):
    dos reintentos con el mismo contenido comparten huella aunque cambien
    espacios u orden de claves; cambiar un solo importe la rompe.
    """

    material = f"{method.upper()} {path} {body_json or ''}"
    return sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Idempotency:
    """Contexto de idempotencia de una operación (con clave)."""

    endpoint: str
    key: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Replay:
    """Respuesta congelada de la primera ejecución: el servicio no repite el
    efecto y la API la devuelve tal cual (``status`` + ``body`` JSON puro)."""

    status: int
    body: dict[str, Any]


Snapshot = Callable[[Any], dict[str, Any]]


async def lookup(session: AsyncSession, idem: Idempotency) -> Replay | None:
    """Primera lectura (antes de tocar el negocio): ``None`` = ejecutar.

    - Clave vencida (``expires_at`` pasado) → libre: se ejecuta y ``store``
      recicla la fila.
    - Misma clave con OTRO endpoint u otra huella → 409: el cliente reutilizó
      mal la clave (bug o petición editada); jamás se devuelve otra respuesta.
    - Misma clave en vuelo (fila sin respuesta aún) → 409: solo ocurre en una
      carrera; el perdedor reintenta y encontrará la respuesta congelada.
    """

    row = await session.get(IdempotencyKey, idem.key)
    if row is None or row.expires_at <= _now():
        return None
    if row.endpoint != idem.endpoint or (row.request_fingerprint or "") != idem.fingerprint:
        raise AppError(
            409,
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            f"La cabecera {HEADER} ya se usó con otro contenido u operación",
        )
    if row.response_body is None:
        raise AppError(
            409,
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            f"Otra petición con la misma {HEADER} está en curso",
        )
    return Replay(status=row.response_status or 200, body=dict(row.response_body))


async def store(
    session: AsyncSession, idem: Idempotency, *, status: int, body: dict[str, Any]
) -> None:
    """Congela clave + respuesta en la MISMA transacción del cambio de negocio.

    Recicla antes la fila vencida con la misma clave (la ventana vencida deja
    la clave libre) y hace ``flush`` para que la colisión de PRIMARY KEY —la
    carrera de dos reintentos— aflora aquí dentro de la transacción y el
    perdedor resuelva con :func:`replay_after_conflict`.
    """

    now = _now()
    await session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.key == idem.key, IdempotencyKey.expires_at <= now
        )
    )
    session.add(
        IdempotencyKey(
            key=idem.key,
            endpoint=idem.endpoint,
            request_fingerprint=idem.fingerprint,
            response_status=status,
            response_body=body,
            created_at=now,
            expires_at=now + TTL,
        )
    )
    await session.flush()


async def replay_after_conflict(
    session: AsyncSession, idem: Idempotency, fallback: AppError
) -> Replay:
    """Resolución de la carrera tras ``IntegrityError`` en ``store``: rollback
    y segunda lectura. Si el ganador ya congeló la respuesta se devuelve su
    replay; si no, la colisión era de negocio (p. ej. «el terminal ya tiene
    una sesión abierta») y se lanza el error correspondiente."""

    await session.rollback()
    replay = await lookup(session, idem)
    if replay is not None:
        return replay
    raise fallback

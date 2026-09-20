"""Primitivas de seguridad: Argon2id, JWT de acceso y tokens de refresco (fase 03).

Sin secretos en código: el secreto JWT llega por ``Settings.jwt_secret`` (TPV_JWT_SECRET,
ADR-008). Aquí no hay E/S ni BD: solo funciones puras que usan ``services`` y ``api``.

Tokens:
- Access: JWT HS256 corto (15 min) con ``sub`` (usuario), ``sid`` (sesión), ``role``,
  ``scope`` (``full`` usuario+contraseña, ``pos`` PIN de terminal) y ``exp``.
- Refresh: opaco aleatorio (``secrets.token_urlsafe``); en BD solo su SHA-256
  (``user_sessions.token_hash``), rotativo y revocable.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import Settings

# Parámetros Argon2id por defecto de argon2-cffi (OWASP): suficientes para LAN.
_hasher = PasswordHasher()

# Hash señuelo: verificar contra él cuando el usuario no existe para que el tiempo
# de respuesta no delate cuentas válidas (enumeración de usuarios).
_dummy_hash: str | None = None

_MIN_SECRET_LEN = 32  # lo que PyJWT recomienda para HS256 (RFC 7518 §3.2)

SCOPE_FULL = "full"  # usuario + contraseña: todos los permisos del rol
SCOPE_POS = "pos"    # PIN de terminal: operaciones de venta, nunca administración


def _require_secret(secret: str) -> None:
    if len(secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            "TPV_JWT_SECRET no está configurada o es demasiado corta "
            f"(mínimo {_MIN_SECRET_LEN} caracteres); los endpoints de autenticación "
            "están deshabilitados"
        )


def hash_password(password: str) -> str:
    """Hash Argon2id de una contraseña o PIN."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True si la contraseña coincide con el hash Argon2id."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Hash corrupto o de otro algoritmo: tratarlo como credencial inválida.
        return False


def verify_dummy(password: str) -> None:
    """Quema tiempo de CPU como una verificación real (usuario inexistente)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash(secrets.token_urlsafe(32))
    try:
        _hasher.verify(_dummy_hash, password)
    except VerifyMismatchError:
        pass


def create_access_token(
    settings: Settings,
    *,
    user_id: UUID,
    session_id: UUID,
    role_code: str,
    scope: str = SCOPE_FULL,
    ttl_minutes: int | None = None,
) -> str:
    """Emite el JWT de acceso ligado a una sesión (``sid``) revocable."""
    _require_secret(settings.jwt_secret)
    now = datetime.now(UTC)
    minutes = settings.access_token_minutes if ttl_minutes is None else ttl_minutes
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role_code,
        "scope": scope,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    """Verifica firma y expiración; devuelve los claims. Lanza jwt.PyJWTError."""
    _require_secret(settings.jwt_secret)
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],  # lista cerrada: nunca aceptar ``alg`` del payload
        options={"require": ["exp", "sub", "sid"]},
    )


def new_refresh_token() -> str:
    """Token de refresco opaco (256 bits de entropía); se entrega al cliente una vez."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Huella que se guarda en ``user_sessions.token_hash`` (nunca el token en claro)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_device_token() -> str:
    """Token de alta de un dispositivo (tpv-agent): se entrega UNA vez en la
    respuesta del alta/rotación y en BD queda solo su SHA-256 (fase Administración)."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    """Huella que se guarda en ``devices.token_hash`` (misma política que el refresh)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

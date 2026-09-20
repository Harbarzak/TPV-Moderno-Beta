"""Tests de las primitivas de seguridad (fase 03). No requieren BD.

Cubren Argon2id, JWT (firma, expiración, algoritmo), tokens de refresco y el
limitador de intentos. Los recorridos completos con PostgreSQL están en
test_auth_api.py (requieren TPV_TEST_DATABASE_URL).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.config import Settings
from app.core.ratelimit import RateLimiter
from app.core.security import (
    SCOPE_FULL,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_dummy,
    verify_password,
)

SECRET = "secreto-de-prueba-nuevo-y-rotado-con-64-chars-9999"
SETTINGS = Settings(env="test", log_level="WARNING", jwt_secret=SECRET)


# ---------------------------------------------------------------------------
# Argon2id
# ---------------------------------------------------------------------------
def test_hash_es_argon2id_y_verifica():
    h = hash_password("Mi-Clave-2026")
    assert h.startswith("$argon2id$")
    assert verify_password("Mi-Clave-2026", h)


def test_hash_rechaza_clave_equivocada_y_hash_basura():
    h = hash_password("correcta")
    assert not verify_password("incorrecta", h)
    # Un hash corrupto no debe reventar: se trata como credencial inválida.
    assert not verify_password("correcta", "no-es-un-hash")


def test_verify_dummy_no_reventar():
    # Camino de usuario inexistente: debe consumir tiempo sin lanzar excepciones.
    assert verify_dummy("lo-que-sea") is None


# ---------------------------------------------------------------------------
# JWT de acceso
# ---------------------------------------------------------------------------
def test_access_token_ida_y_vuelta():
    user_id, session_id = uuid4(), uuid4()
    token = create_access_token(
        SETTINGS, user_id=user_id, session_id=session_id, role_code="waiter"
    )
    claims = decode_access_token(SETTINGS, token)
    assert claims["sub"] == str(user_id)
    assert claims["sid"] == str(session_id)
    assert claims["role"] == "waiter"
    assert claims["scope"] == SCOPE_FULL
    # Vida por defecto: 15 minutos (§6).
    exp = datetime.fromtimestamp(claims["exp"], tz=UTC)
    assert timedelta(minutes=14) < exp - datetime.now(UTC) <= timedelta(minutes=15)


def test_access_token_expirado_rechazado():
    token = create_access_token(
        SETTINGS, user_id=uuid4(), session_id=uuid4(), role_code="waiter",
        ttl_minutes=-1,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(SETTINGS, token)


def test_access_token_con_firma_alterada_rechazado():
    token = create_access_token(
        SETTINGS, user_id=uuid4(), session_id=uuid4(), role_code="waiter"
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(SETTINGS, token + "x")


def test_access_token_con_otro_secreto_rechazado():
    token = create_access_token(
        SETTINGS, user_id=uuid4(), session_id=uuid4(), role_code="waiter"
    )
    otras = Settings(env="test", jwt_secret="otro-secreto-nuevo-y-rotado-largo-0000")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(otras, token)


def test_sin_secreto_o_corto_falla_claro():
    with pytest.raises(RuntimeError, match="TPV_JWT_SECRET"):
        create_access_token(
            Settings(env="test"),  # sin TPV_JWT_SECRET
            user_id=uuid4(), session_id=uuid4(), role_code="waiter",
        )
    corta = Settings(env="test", jwt_secret="corta")
    with pytest.raises(RuntimeError, match="TPV_JWT_SECRET"):
        create_access_token(
            corta, user_id=uuid4(), session_id=uuid4(), role_code="waiter"
        )


# ---------------------------------------------------------------------------
# Tokens de refresco (opacos, solo su hash toca BD)
# ---------------------------------------------------------------------------
def test_refresh_unico_y_hash_determinista():
    a, b = new_refresh_token(), new_refresh_token()
    assert a != b and len(a) > 40
    assert hash_refresh_token(a) == hash_refresh_token(a)
    assert hash_refresh_token(a) != hash_refresh_token(b)
    assert a not in hash_refresh_token(a)  # nunca el token en claro


# ---------------------------------------------------------------------------
# Rate limiter (ventana deslizante, reloj inyectable)
# ---------------------------------------------------------------------------
class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_bloquea_al_superar_intentos():
    clock = _FakeClock()
    rl = RateLimiter(attempts=3, window_seconds=60, clock=clock)
    assert rl.check("ip1")
    assert rl.check("ip1")
    assert rl.check("ip1")
    assert not rl.check("ip1")  # cuarto intento dentro de la ventana
    assert rl.check("ip2")  # otra IP, otra ventana


def test_rate_limiter_recupera_al_pasar_la_ventana():
    clock = _FakeClock()
    rl = RateLimiter(attempts=2, window_seconds=60, clock=clock)
    assert rl.check("ip1") and rl.check("ip1")
    assert not rl.check("ip1")
    clock.now += 61  # la ventana vence: vuelve a permitir
    assert rl.check("ip1")

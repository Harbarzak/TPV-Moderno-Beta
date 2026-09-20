"""Contrato de entrada de la administración (fase 22) — sin base de datos.

Valida las reglas que el instalador y el panel asumen: política de
contraseña/PIN, formato de códigos de rol, ``kind`` de dispositivo cerrado y
la semántica PATCH (solo los campos enviados se tocan).
"""

import pytest
from pydantic import ValidationError

from app.api.v1.admin import (
    DeviceCreate,
    DeviceUpdate,
    PasswordSet,
    PinSet,
    RoleCreate,
    RolePermissionsSet,
    TerminalCreate,
    UserCreate,
    UserUpdate,
)

ROLE_ID = "00000000-0000-0000-0000-000000000001"


def _user_data(**overrides) -> dict:
    data = {
        "username": "camarero1",
        "password": "Clave-Segura-2026",
        "full_name": "Ana García",
        "role_code": "waiter",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Usuarios: username y política de credenciales
# ---------------------------------------------------------------------------
def test_usuario_valido_sin_pin():
    row = UserCreate(**_user_data())
    assert row.pin is None  # sin PIN: solo contraseña


def test_username_limites_del_check_de_bd():
    assert UserCreate(**_user_data(username="ab")).username == "ab"  # 2: mínimo real
    with pytest.raises(ValidationError):
        UserCreate(**_user_data(username="a"))  # la BD exige 2-64
    with pytest.raises(ValidationError):
        UserCreate(**_user_data(username="a" * 65))


def test_password_minimo_ocho():
    with pytest.raises(ValidationError):
        UserCreate(**_user_data(password="7chars!"))
    assert UserCreate(**_user_data(password="8chars!!")).password == "8chars!!"
    with pytest.raises(ValidationError):
        PasswordSet(password="corta")


def test_pin_formato_cuatro_a_seis_digitos():
    assert UserCreate(**_user_data(pin="1234")).pin == "1234"
    assert UserCreate(**_user_data(pin="123456")).pin == "123456"
    for malo in ("123", "1234567", "12a4", "  1234"):
        with pytest.raises(ValidationError):
            UserCreate(**_user_data(pin=malo))
        with pytest.raises(ValidationError):
            PinSet(pin=malo)


def test_patch_de_usuario_solo_lo_enviado():
    row = UserUpdate.model_validate({"active": False})
    assert row.model_dump(exclude_unset=True) == {"active": False}
    row = UserUpdate.model_validate({"full_name": "Nuevo nombre", "role_code": "manager"})
    assert set(row.model_dump(exclude_unset=True)) == {"full_name", "role_code"}
    assert UserUpdate.model_validate({}).model_dump(exclude_unset=True) == {}


def test_pinset_null_es_clave_presente():
    """``{"pin": null}`` retira el PIN; ``{}`` no dice nada (el endpoint lo
    rechaza). exclude_unset distingue ambos."""
    row = PinSet.model_validate({"pin": None})
    assert row.model_dump(exclude_unset=True) == {"pin": None}
    assert PinSet.model_validate({}).model_dump(exclude_unset=True) == {}


# ---------------------------------------------------------------------------
# Roles: código legible y matriz acotada
# ---------------------------------------------------------------------------
def test_codigo_de_rol_formato():
    assert RoleCreate(code="supervisor", name="Supervisor").code == "supervisor"
    for malo in ("Supervisor", "supervisor-1", "s", "1supervisor", "a" * 33):
        with pytest.raises(ValidationError):
            RoleCreate(code=malo, name="x")


def test_matriz_de_permisos_acepta_vacia_y_rechaza_exceso():
    assert RolePermissionsSet(permissions=[]).permissions == []
    assert RolePermissionsSet(permissions=["a.b", "c.d"]).permissions == ["a.b", "c.d"]
    with pytest.raises(ValidationError):
        RolePermissionsSet(permissions=[f"p.{i}" for i in range(501)])


# ---------------------------------------------------------------------------
# Terminales y dispositivos
# ---------------------------------------------------------------------------
def test_terminal_codigo_limites():
    assert TerminalCreate(code="T-1", name="Barra").code == "T-1"
    with pytest.raises(ValidationError):
        TerminalCreate(code="t" * 33, name="x")
    with pytest.raises(ValidationError):
        TerminalCreate(code="", name="x")


def test_kind_de_dispositivo_cerrado():
    assert DeviceCreate(name="Pinpad 1", kind="pinpad").kind.value == "pinpad"
    with pytest.raises(ValidationError):
        DeviceCreate(name="x", kind="teleportador")


def test_patch_de_dispositivo_solo_lo_enviado():
    row = DeviceUpdate.model_validate({"active": False})
    assert row.model_dump(exclude_unset=True) == {"active": False}
    # terminal_id null EXPLÍCITO desasigna; ausente no toca
    row = DeviceUpdate.model_validate({"terminal_id": None})
    assert row.model_dump(exclude_unset=True) == {"terminal_id": None}
    assert DeviceUpdate.model_validate({}).model_dump(exclude_unset=True) == {}

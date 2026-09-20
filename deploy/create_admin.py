"""Alta (o restablecimiento) del usuario administrador inicial del TPV.

Lo llaman ``create-admin.ps1`` e ``install.ps1``. El usuario y el nombre
completo van por argumento; la contraseña SOLO por stdin (nunca en la línea de
comandos, que otros procesos del equipo pueden listar). El hash se genera con
el mismo algoritmo de la app (argon2id, ``app.core.security.hash_password``) —
aquí no hay atajos ni contraseñas prefijadas (ADR-008).

Si el usuario ya existe, se limita a restablecer su contraseña y reactivarlo:
sirve para recuperar un admin bloqueado sin tocar el resto.
"""
import sys

from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.core.security import hash_password


def main() -> int:
    if len(sys.argv) != 3:
        print('Uso: create_admin.py USUARIO "NOMBRE COMPLETO"   (contraseña por stdin)')
        return 2
    username, full_name = sys.argv[1], sys.argv[2]
    password = sys.stdin.readline().rstrip("\r\n")
    if len(password) < 8:
        print("ERROR: la contraseña debe tener al menos 8 caracteres.")
        return 2

    cfg = Settings()
    if not cfg.database_url:
        print("ERROR: TPV_DATABASE_URL no está definida (falta el .env).")
        return 2

    engine = create_engine(cfg.database_url)
    with engine.begin() as conn:
        role_id = conn.execute(text("SELECT id FROM roles WHERE code = 'admin'")).scalar()
        if role_id is None:
            print("ERROR: no existe el rol 'admin' — ejecuta primero el seed (install.ps1 lo hace).")
            return 1
        existing = conn.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": username}
        ).scalar()
        if existing is None:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, full_name, role_id) "
                    "VALUES (:u, :h, :f, :r)"
                ),
                {
                    "u": username,
                    "h": hash_password(password),
                    "f": full_name,
                    "r": str(role_id),
                },
            )
            print(f"OK  Usuario administrador creado: {username}")
        else:
            conn.execute(
                text(
                    "UPDATE users SET password_hash = :h, full_name = :f, active = true "
                    "WHERE id = :id"
                ),
                {"h": hash_password(password), "f": full_name, "id": str(existing)},
            )
            print(f"OK  Contraseña restablecida para: {username}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

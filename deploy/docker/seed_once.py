"""Seed condicional del arranque en Docker (misma política que install.ps1).

Ejecuta ``docs/database/seed.sql`` SOLO si la tabla ``roles`` está vacía: el
seed es idempotente, pero así el arranque de un servidor ya poblado no toca
absolutamente nada. El seed NO crea usuarios ni credenciales: el alta del
administrador la hace ``create_admin.py`` con la contraseña por stdin
(ADR-008 — ningún secreto en imagen, seed o código).
"""

from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import Settings

SEED_FILE = Path(__file__).resolve().parent / "seed.sql"


def main() -> int:
    cfg = Settings()
    if not cfg.database_url:
        print("ERROR: TPV_DATABASE_URL no está definida.")
        return 2

    seed_sql = SEED_FILE.read_text(encoding="utf-8")
    engine = create_engine(cfg.database_url)
    with engine.begin() as conn:
        ya_poblado = conn.execute(text("SELECT count(*) FROM roles")).scalar_one() > 0
        if ya_poblado:
            print("Seed omitido: la base de datos ya tiene roles; no se toca nada.")
            return 0
        conn.execute(text(seed_sql))
    print("Seed aplicado: roles, permisos, tipos de IVA, formas de pago y parámetros.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backups del panel de administración (fase Administración).

Ejecuta ``pg_dump`` (formato custom ``.dump``) contra la base de datos
configurada y lista el contenido de ``TPV_BACKUP_DIR``. Seguridad (ADR-008):
- La contraseña NUNCA va en la línea de comandos (visible en el listado de
  procesos del servidor): viaja en ``PGPASSWORD`` del entorno del subproceso.
- La URL de conexión nunca se loguea ni aparece en ``audit_log``.
- El nombre del fichero lo genera el servidor: la petición no controla rutas.

Un fallo de ``pg_dump`` también se audita (``admin.backup_failed``), siguiendo
el patrón de ``auth.login_failed``: registrar y LUEGO lanzar el error.
"""

import asyncio
import glob
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.repos import auth as auth_repo
from app.services import events as events_service
from app.services.auth import Principal

# Rutas típicas de pg_dump en Windows cuando no está en el PATH (instalador).
_WINDOWS_GLOBS = (r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe",)


def _backup_dir(settings: Settings) -> Path:
    return Path(settings.backup_dir)


def list_backups(settings: Settings) -> list[dict]:
    """Ficheros ``.dump`` del directorio configurado, el más reciente primero.
    Sin directorio (aún no hay backups) devuelve una lista vacía."""
    directory = _backup_dir(settings)
    if not directory.is_dir():
        return []
    items = []
    for path in directory.glob("*.dump"):
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            }
        )
    return sorted(items, key=lambda item: item["modified_at"], reverse=True)


def find_pg_dump() -> str | None:
    found = shutil.which("pg_dump")
    if found:
        return found
    for pattern in _WINDOWS_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]  # la versión más reciente instalada
    return None


async def run_backup(
    session: AsyncSession, principal: Principal, settings: Settings
) -> dict:
    """Lanza ``pg_dump`` y devuelve la ficha del fichero creado."""
    if not settings.database_url:
        raise AppError(
            422, ErrorCode.VALIDATION_ERROR, "El servidor no tiene base de datos configurada"
        )
    pg_dump = find_pg_dump()
    if pg_dump is None:
        raise AppError(
            503,
            ErrorCode.BACKUP_UNAVAILABLE,
            "No se encontró pg_dump en el servidor",
        )

    url = make_url(settings.database_url)
    directory = _backup_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"tpv_{datetime.now(UTC):%Y%m%d_%H%M%S}.dump"
    target = directory / name

    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password  # NUNCA en argv (visible con `ps`)
    process = await asyncio.create_subprocess_exec(
        pg_dump,
        "--format=custom",
        "--file",
        str(target),
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        url.database or "postgres",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        detail = (stderr or b"").decode(errors="replace").strip()[-300:]
        await auth_repo.record_audit(
            session,
            action="admin.backup_failed",
            entity="backup",
            user_id=principal.user_id,
            after_data={"file": name, "error": detail},
        )
        await session.commit()  # el fallo se audita aunque la petición acabe en error
        raise AppError(500, ErrorCode.BACKUP_FAILED, f"pg_dump falló: {detail}")

    info = {
        "name": name,
        "size_bytes": target.stat().st_size,
        "modified_at": datetime.fromtimestamp(target.stat().st_mtime, tz=UTC),
    }
    await auth_repo.record_audit(
        session,
        action="admin.backup_created",
        entity="backup",
        user_id=principal.user_id,
        after_data={"file": name, "size_bytes": info["size_bytes"]},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.backup_created",
        payload={"file": name, "size_bytes": info["size_bytes"]},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return info

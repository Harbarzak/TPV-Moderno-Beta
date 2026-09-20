"""Configuración central del backend.

Todas las variables llevan prefijo ``TPV_`` y se leen del entorno (con `.env` local como
comodidad de desarrollo). Sin valores por defecto que parezcan credenciales: los secretos
se generan nuevos en cada instalación (ADR-008).
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TPV_",
        env_file=".env",
        env_file_encoding="utf-8",
        # El entorno puede llevar variables TPV_* ajenas (p. ej. TPV_TEST_DATABASE_URL
        # en los tests): se ignoran, no se rechazan.
        extra="ignore",
    )

    # Identidad del despliegue
    env: str = "dev"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # Base de datos: vacía a propósito para que la app importe y arranque sin BD;
    # /readyz informará DATABASE_UNAVAILABLE. Solo el servidor abre conexiones (ADR-002).
    database_url: str = ""
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # NullPool (sin pool): para pruebas con event loops efímeros o scripts puntuales.
    db_null_pool: bool = False

    # CORS: solo orígenes servidos por el propio servidor (ARCHITECTURE.md §6).
    cors_origins: list[str] = Field(default_factory=list)

    # Volumen de ficheros del servidor (logos de cabecera, fase 09). Los
    # archivos de impresión NO viven en la BD (ARCHITECTURE.md): en la tabla
    # ``parameters`` solo queda la referencia (mime, tamaño, fecha).
    data_dir: str = "data"

    # Frontends servidos por el propio servidor (fase Instalador, §1.4):
    # carpetas con los builds estáticos de mostrador y PWA móvil, montadas en
    # /app/tpv y /app/movil («actualizar = actualizar el servidor»). Vacías =
    # solo API (desarrollo y tests no cambian); los clientes del TPV llaman a
    # la API por rutas relativas, así que servirlos aquí es cero configuración.
    serve_frontend_dir: str = ""
    serve_mobile_dir: str = ""
    # Panel de administración (fase Administración): tercer build estático,
    # servido en /app/admin con la misma política que mostrador y móvil.
    serve_admin_dir: str = ""

    # Carpeta donde ``pg_dump`` escribe los backups (fase Administración),
    # relativa al directorio de trabajo del servidor como ``data_dir``.
    backup_dir: str = "backups"

    # Autenticación (fase 03). El secreto JWT vive solo en el entorno (ADR-008):
    # sin valor por defecto que parezca credencial; los endpoints de auth fallan de
    # forma clara si falta o es demasiado corta (mín. 32 caracteres).
    jwt_secret: str = ""
    access_token_minutes: int = 15   # JWT de acceso (ARCHITECTURE.md §6)
    refresh_token_hours: int = 12    # rotativo y revocable, en cookie HttpOnly
    auth_rate_limit_attempts: int = 10   # anti-abuso en login/pin: por IP y ruta
    auth_rate_limit_window_seconds: int = 60

    # WebSocket (fase 12, ARCHITECTURE.md §8): latido ping/pong, ventana máxima
    # de replay por suscripción y límite de tiempo del primer frame de auth.
    ws_heartbeat_seconds: int = 15
    ws_replay_limit: int = 500
    ws_auth_timeout_seconds: int = 10

    # Régimen fiscal del plugin (fase 34, ADR-010): "none" consume los
    # sale_events sin certificar nada; "verifactu" y "ticketbai" son
    # esqueletos seleccionables (su envío exige desarrollo contra la
    # especificación oficial — docs/fiscal/README.md). Un valor erróneo
    # falla en el arranque (build_fiscal_adapter).
    fiscal_provider: str = "none"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Admite TPV_CORS_ORIGINS="http://a,http://b" además de una lista JSON."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Instancia única de Settings (la fábrica create_app admite overrides para tests)."""
    return Settings()

"""
Configuración de la aplicación usando Pydantic Settings.
Las variables se cargan desde .env.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación"""

    # Información de la aplicación
    APP_NAME: str = Field(
        default="Digital Twin Traffic Backend",
        description="Nombre de la aplicación",
    )
    APP_VERSION: str = Field(
        default="0.1.0",
        description="Versión de la aplicación",
    )
    APP_DESCRIPTION: str = Field(
        default="Backend de simulación para gemelo digital de tráfico urbano",
        description="Descripción de la aplicación",
    )

    # Configuración del servidor
    HOST: str = Field(
        default="0.0.0.0",
        description="Host del servidor",
    )
    PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Puerto del servidor",
    )
    DEBUG: bool = Field(
        default=True,
        description="Modo debug",
    )

    # CORS
    CORS_ORIGINS: list[str] = Field(
        default=["*"],
        description="Orígenes permitidos para CORS",
    )

    # Logging
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Nivel de logging",
    )

    # Mapa inicial
    MAP_FILE: str | None = Field(
        default=None,
        description="Fichero OSM a auto-importar al inicio si la BD está vacía (relativo a data/)",
    )

    # Simulación
    SIMULATION_TICK_RATE: float = Field(
        default=0.1,
        gt=0,
        description="Segundos entre updates de simulación",
    )
    MAX_VEHICLES: int = Field(
        default=10000,
        ge=1,
        description="Número máximo de vehículos en simulación",
    )

    # Database Configuration
    POSTGRES_HOST: str = Field(
        default="localhost",
        description="PostgreSQL server host",
    )
    POSTGRES_PORT: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description="PostgreSQL server port",
    )
    POSTGRES_USER: str = Field(
        default="dt_user",
        description="PostgreSQL username",
    )
    POSTGRES_PASSWORD: str = Field(
        default="dt_password",
        description="PostgreSQL password",
    )
    POSTGRES_DB: str = Field(
        default="digital_twin",
        description="PostgreSQL database name",
    )

    # Connection Pool Settings
    DB_POOL_SIZE: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Database connection pool size",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=10,
        ge=0,
        le=30,
        description="Maximum overflow connections beyond pool size",
    )

    @property
    def database_url(self) -> str:
        """Build async PostgreSQL connection URL from components."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_url_sync(self) -> str:
        """Build sync PostgreSQL connection URL for Alembic."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Valida que el nivel de logging sea válido"""
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        value_upper = value.upper()
        if value_upper not in allowed_levels:
            raise ValueError(
                f"LOG_LEVEL debe ser uno de: {', '.join(allowed_levels)}"
            )
        return value_upper
    # Configuración de Pydantic Settings
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


# Instancia única de configuración (Singleton)
settings = Settings()

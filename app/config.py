"""
Configuración de la aplicación usando Pydantic Settings.
Las variables se cargan desde .env o variables de entorno.
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

    # Simulación
    SIMULATION_TICK_RATE: float = Field(
        default=0.1,
        gt=0,
        description="Segundos entre updates de simulación",
    )
    MAX_VEHICLES: int = Field(
        default=100,
        ge=1,
        description="Número máximo de vehículos en simulación",
    )

    # Base de datos (para futuros sprints)
    DATABASE_URL: str | None = Field(
        default=None,
        description="URL de conexión a base de datos",
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


# Instancia única de configuración (Singleton)
settings = Settings()

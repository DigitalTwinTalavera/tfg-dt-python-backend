"""
Funciones helper reutilizables.
Proporciona utilidades comunes para evitar duplicación de código.
"""

import sys
import platform
from datetime import datetime, timezone

from app.config import settings
from app.core.constants import ENV_DEVELOPMENT, ENV_PRODUCTION
from app.core.responses import (
    AppInfoResponse,
    SystemInfoResponse,
    ConfigInfoResponse,
)


def get_current_timestamp() -> str:
    """
    Obtiene el timestamp actual en formato ISO 8601 UTC.

    Returns:
        str: Timestamp en formato ISO 8601 con zona horaria UTC
    """
    return datetime.now(timezone.utc).isoformat()


def get_environment() -> str:
    """
    Determina el entorno de ejecución actual.

    Returns:
        str: 'development' si DEBUG está activo, 'production' en caso contrario
    """
    return ENV_DEVELOPMENT if settings.DEBUG else ENV_PRODUCTION


def get_app_info() -> AppInfoResponse:
    """
    Construye la información básica de la aplicación.

    Returns:
        AppInfoResponse: Información de la aplicación desde settings
    """
    return AppInfoResponse(
        name=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
    )


def get_system_info() -> SystemInfoResponse:
    """
    Obtiene información del sistema en el que se ejecuta la aplicación.

    Returns:
        SystemInfoResponse: Información del sistema operativo y Python
    """
    return SystemInfoResponse(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        processor=platform.processor() or "Unknown",
    )


def get_config_info() -> ConfigInfoResponse:
    """
    Obtiene la configuración actual de la aplicación.

    Returns:
        ConfigInfoResponse: Configuración relevante de la aplicación
    """
    return ConfigInfoResponse(
        debug=settings.DEBUG,
        log_level=settings.LOG_LEVEL,
        max_vehicles=settings.MAX_VEHICLES,
        tick_rate=settings.SIMULATION_TICK_RATE,
    )

"""
Router de Health Check.
Proporciona endpoints para verificar el estado del sistema.
"""

from fastapi import APIRouter

from app.config import settings
from app.core.constants import STATUS_OK
from app.core.responses import DetailedHealthCheckResponse, HealthCheckResponse
from app.core.utils import (
    get_app_info,
    get_config_info,
    get_current_timestamp,
    get_environment,
    get_system_info,
)

router = APIRouter()


@router.get("/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """
    Endpoint de health check básico.
    Retorna el estado del servidor y metadatos útiles.
    """
    return HealthCheckResponse(
        status=STATUS_OK,
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        timestamp=get_current_timestamp(),
        environment=get_environment(),
    )


@router.get("/health/detailed", response_model=DetailedHealthCheckResponse)
async def detailed_health_check() -> DetailedHealthCheckResponse:
    """
    Health check detallado con información del sistema.
    """
    return DetailedHealthCheckResponse(
        status=STATUS_OK,
        app=get_app_info(),
        timestamp=get_current_timestamp(),
        system=get_system_info(),
        config=get_config_info(),
    )

"""
Schemas Pydantic para respuestas de la API.
Proporciona tipado fuerte y validación automática.
"""

from pydantic import BaseModel, Field


class AppInfoResponse(BaseModel):
    """Información básica de la aplicación"""
    name: str = Field(..., description="Nombre de la aplicación")
    version: str = Field(..., description="Versión de la aplicación")
    description: str = Field(..., description="Descripción de la aplicación")


class SystemInfoResponse(BaseModel):
    """Información del sistema"""
    python_version: str = Field(..., description="Versión de Python")
    platform: str = Field(..., description="Plataforma del sistema operativo")
    processor: str = Field(..., description="Tipo de procesador")


class ConfigInfoResponse(BaseModel):
    """Configuración de la aplicación"""
    debug: bool = Field(..., description="Modo debug activado")
    log_level: str = Field(..., description="Nivel de logging")
    max_vehicles: int = Field(..., description="Número máximo de vehículos")
    tick_rate: float = Field(..., description="Tasa de actualización de simulación (segundos)")


class DatabaseHealthResponse(BaseModel):
    """Database health status"""
    connected: bool = Field(..., description="Database connection status")
    postgis_version: str | None = Field(
        default=None,
        description="PostGIS extension version",
    )


class RootResponse(BaseModel):
    """Respuesta del endpoint raíz"""
    app: str = Field(..., description="Nombre de la aplicación")
    version: str = Field(..., description="Versión de la aplicación")
    status: str = Field(..., description="Estado del servidor")
    docs: str = Field(..., description="URL de la documentación")


class HealthCheckResponse(BaseModel):
    """Respuesta del health check básico"""
    status: str = Field(..., description="Estado del servicio")
    app: str = Field(..., description="Nombre de la aplicación")
    version: str = Field(..., description="Versión de la aplicación")
    timestamp: str = Field(..., description="Timestamp ISO 8601 UTC")
    environment: str = Field(..., description="Entorno de ejecución")


class DetailedHealthCheckResponse(BaseModel):
    """Respuesta del health check detallado"""
    status: str = Field(..., description="Estado del servicio")
    app: AppInfoResponse = Field(..., description="Información de la aplicación")
    timestamp: str = Field(..., description="Timestamp ISO 8601 UTC")
    system: SystemInfoResponse = Field(..., description="Información del sistema")
    config: ConfigInfoResponse = Field(..., description="Configuración actual")
    database: DatabaseHealthResponse = Field(..., description="Estado de la base de datos")

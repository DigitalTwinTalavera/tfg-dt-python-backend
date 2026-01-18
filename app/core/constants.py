"""
Constantes globales de la aplicación.
Centraliza todos los valores reutilizables para evitar duplicación.
"""

# API Routes
API_PREFIX = "/api"
ROOT_PATH = "/"

# API Documentation
DOCS_URL = "/docs"
REDOC_URL = "/redoc"
OPENAPI_URL = "/openapi.json"

# API Tags
TAG_ROOT = "Root"
TAG_HEALTH = "Health Check"

# Status Messages
STATUS_OK = "ok"
STATUS_RUNNING = "running"

# Environment Types
ENV_DEVELOPMENT = "development"
ENV_PRODUCTION = "production"

# HTTP Methods
CORS_ALLOW_METHODS = ["*"]
CORS_ALLOW_HEADERS = ["*"]

# Time Constants
HEALTHCHECK_INTERVAL_SECONDS = 30
HEALTHCHECK_TIMEOUT_SECONDS = 3
HEALTHCHECK_RETRIES = 3
HEALTHCHECK_START_PERIOD_SECONDS = 5

# System Messages
MSG_STARTUP_SERVER = "🚀 {app_name} v{version}"
MSG_SERVER_URL = "📡 Servidor iniciado en http://{host}:{port}"
MSG_DOCS_URL = "📚 Documentación disponible en http://{host}:{port}{docs_url}"
MSG_SHUTDOWN = "👋 Servidor detenido"

# Response Keys
KEY_STATUS = "status"
KEY_APP = "app"
KEY_VERSION = "version"
KEY_TIMESTAMP = "timestamp"
KEY_ENVIRONMENT = "environment"
KEY_DOCS = "docs"
KEY_SYSTEM = "system"
KEY_CONFIG = "config"
KEY_DESCRIPTION = "description"
KEY_NAME = "name"

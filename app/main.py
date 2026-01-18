"""
Punto de entrada principal de la aplicación FastAPI.
Configura el servidor web, middlewares y rutas.
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, routes
from app.config import settings
from app.core.constants import (
    API_PREFIX,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    DOCS_URL,
    MSG_DB_CONNECTED,
    MSG_DB_DISCONNECTED,
    MSG_DOCS_URL,
    MSG_SERVER_URL,
    MSG_SHUTDOWN,
    MSG_STARTUP_SERVER,
    MSG_WS_URL,
    OPENAPI_URL,
    REDOC_URL,
    ROOT_PATH,
    STATUS_RUNNING,
    TAG_HEALTH,
    TAG_ROOT,
    WS_SIMULATION_PATH,
)
from app.core.responses import RootResponse
from app.db.database import close_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación"""
    # Startup
    print(
        MSG_STARTUP_SERVER.format(
            app_name=settings.APP_NAME,
            version=settings.APP_VERSION,
        )
    )
    print(
        MSG_SERVER_URL.format(
            host=settings.HOST,
            port=settings.PORT,
        )
    )
    print(
        MSG_DOCS_URL.format(
            host=settings.HOST,
            port=settings.PORT,
            docs_url=DOCS_URL,
        )
    )
    print(
        MSG_WS_URL.format(
            host=settings.HOST,
            port=settings.PORT,
            ws_path=WS_SIMULATION_PATH,
        )
    )

    await init_db()
    print(MSG_DB_CONNECTED)

    yield

    # Shutdown
    await close_db()
    print(MSG_DB_DISCONNECTED)
    print(MSG_SHUTDOWN)


# Crear instancia de FastAPI
app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    openapi_url=OPENAPI_URL,
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

# Incluir routers
app.include_router(health.router, prefix=API_PREFIX, tags=[TAG_HEALTH])
app.include_router(routes.router)


@app.get(ROOT_PATH, response_model=RootResponse, tags=[TAG_ROOT])
async def root() -> RootResponse:
    """Endpoint raíz - información básica del API"""
    return RootResponse(
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        status=STATUS_RUNNING,
        docs=DOCS_URL,
    )


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )

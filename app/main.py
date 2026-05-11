"""
Punto de entrada principal de la aplicación FastAPI.
Configura el servidor web, middlewares y rutas.
"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path

from app.api import health, incidents, map, metrics, routes, simulation, zones
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
    OSM_DATA_DIRECTORY,
    REDOC_URL,
    ROOT_PATH,
    STATUS_RUNNING,
    TAG_HEALTH,
    TAG_MAP,
    TAG_ROOT,
    TAG_SIMULATION,
    WS_MAX_MESSAGE_SIZE,
    WS_SIMULATION_PATH,
)
from app.core.responses import RootResponse
from app.api.deps import _graph, _zone_manager
from app.core.simulation_engine import simulation_engine
from app.db.database import async_session_factory, close_db, init_db
from app.services.osm_loader import OSMLoader

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación"""
    # Detección automática de bloqueos del event loop: si una callback dura
    # más de 50 ms, asyncio loguea un warning con el repr del callback. Es
    # complementario al timing por subsistema — delata IO sincrónico u
    # operaciones CPU-bound dentro del event loop sin necesidad de profiler
    # externo.
    import asyncio as _asyncio
    try:
        _asyncio.get_running_loop().slow_callback_duration = 0.05
    except RuntimeError:
        pass

    # Startup
    logger.info(
        MSG_STARTUP_SERVER.format(
            app_name=settings.APP_NAME,
            version=settings.APP_VERSION,
        )
    )
    logger.info(
        MSG_SERVER_URL.format(host=settings.HOST, port=settings.PORT)
    )
    logger.info(
        MSG_DOCS_URL.format(
            host=settings.HOST,
            port=settings.PORT,
            docs_url=DOCS_URL,
        )
    )
    logger.info(
        MSG_WS_URL.format(
            host=settings.HOST,
            port=settings.PORT,
            ws_path=WS_SIMULATION_PATH,
        )
    )

    await init_db()
    logger.info(MSG_DB_CONNECTED)

    # Build in-memory road network graph from database
    async with async_session_factory() as session:
        stats = await _graph.build_from_database(session)
        logger.info(
            "Road network graph built: %d nodes, %d edges (connected=%s, %.0f ms)",
            stats.node_count,
            stats.edge_count,
            stats.is_connected,
            stats.build_time_ms,
        )

    # Auto-import MAP_FILE if DB is empty and MAP_FILE is configured
    if stats.node_count == 0 and settings.MAP_FILE:
        map_path = Path(OSM_DATA_DIRECTORY) / settings.MAP_FILE
        if map_path.exists():
            logger.info("DB empty — auto-importing '%s'...", settings.MAP_FILE)
            async with async_session_factory() as session:
                loader = OSMLoader(session)
                import_stats = await loader.load_from_file(
                    str(map_path), clear_existing=True
                )
            logger.info(
                "Auto-import done: %d nodes, %d edges (%.1fs)",
                import_stats.nodes_imported,
                import_stats.edges_imported,
                import_stats.duration_seconds,
            )
            async with async_session_factory() as session:
                stats = await _graph.build_from_database(session)
            logger.info(
                "Graph rebuilt: %d nodes, %d edges",
                stats.node_count,
                stats.edge_count,
            )
        else:
            logger.warning(
                "MAP_FILE '%s' not found in '%s/'",
                settings.MAP_FILE,
                OSM_DATA_DIRECTORY,
            )

    # Cargar zonas (ZBE / restringidas) desde BD; son persistentes entre
    # reinicios, a diferencia de los incidentes que son estado vivo.
    try:
        await _zone_manager.load_from_db()
    except Exception:
        logger.exception("No se pudieron cargar zonas de BD")

    yield  # Aquí la aplicación está corriendo y puede atender peticiones

    # Shutdown
    await simulation_engine.shutdown()
    await close_db()
    logger.info(MSG_DB_DISCONNECTED)
    logger.info(MSG_SHUTDOWN)


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
app.include_router(map.router, prefix=API_PREFIX, tags=[TAG_MAP])
app.include_router(simulation.router, prefix=API_PREFIX, tags=[TAG_SIMULATION])
app.include_router(incidents.router, prefix=API_PREFIX)
app.include_router(zones.router, prefix=API_PREFIX)
app.include_router(routes.router)
# `/metrics` (Prometheus, scrap-able) en raíz;
# `/api/simulation/metrics` (JSON legible) bajo el prefijo de la API.
app.include_router(metrics.prometheus_router)
app.include_router(metrics.json_router, prefix=API_PREFIX)


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
        ws_max_size=WS_MAX_MESSAGE_SIZE,
    )

"""
Database utility functions for health checks, diagnostics, and geometry operations.
"""

import logging

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SQL_HEALTH_CHECK, SQL_POSTGIS_VERSION, SRID_WGS84
from app.core.responses import DatabaseHealthResponse

logger = logging.getLogger(__name__)


async def check_database_health(session: AsyncSession) -> DatabaseHealthResponse:
    """
    Check database connection and retrieve PostGIS version.

    Args:
        session: Active database session

    Returns:
        DatabaseHealthResponse: Database health status
    """
    try:
        result = await session.execute(text(SQL_HEALTH_CHECK))
        result.scalar()

        postgis_result = await session.execute(text(SQL_POSTGIS_VERSION))
        postgis_version = postgis_result.scalar()

        return DatabaseHealthResponse(
            connected=True,
            postgis_version=postgis_version or "unknown",
        )
    except (SQLAlchemyError, DBAPIError) as e:
        logger.warning("Database health check failed: %s", e)
        return DatabaseHealthResponse(
            connected=False,
            postgis_version=None,
        )


def make_point_geometry(longitude: float, latitude: float):
    """
    Create a PostGIS Point geometry with WGS84 SRID.

    This utility function centralizes the creation of point geometries
    to ensure consistent SRID usage across the application.

    Args:
        longitude: Longitude in degrees (-180 to 180)
        latitude: Latitude in degrees (-90 to 90)

    Returns:
        GeoAlchemy2 geometry expression for use in SQLAlchemy queries
    """
    return ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)

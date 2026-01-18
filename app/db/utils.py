"""
Database utility functions for health checks and diagnostics.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SQL_HEALTH_CHECK, SQL_POSTGIS_VERSION
from app.core.responses import DatabaseHealthResponse


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
    except Exception:
        return DatabaseHealthResponse(
            connected=False,
            postgis_version=None,
        )

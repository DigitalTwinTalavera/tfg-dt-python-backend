"""
FastAPI dependency injection for database repositories.
Provides typed dependency functions for injecting repositories into endpoints.
"""

from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db_session
from app.db.repositories.edge_repository import EdgeRepository
from app.db.repositories.node_repository import NodeRepository
from app.db.repositories.vehicle_repository import VehicleRepository


# Type alias for session dependency
AsyncSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def get_node_repository(
    session: AsyncSessionDep,
) -> AsyncGenerator[NodeRepository, None]:
    """
    Dependency for injecting NodeRepository into endpoints.

    Args:
        session: Database session from dependency injection

    Yields:
        NodeRepository instance
    """
    yield NodeRepository(session)


async def get_edge_repository(
    session: AsyncSessionDep,
) -> AsyncGenerator[EdgeRepository, None]:
    """
    Dependency for injecting EdgeRepository into endpoints.

    Args:
        session: Database session from dependency injection

    Yields:
        EdgeRepository instance
    """
    yield EdgeRepository(session)


async def get_vehicle_repository(
    session: AsyncSessionDep,
) -> AsyncGenerator[VehicleRepository, None]:
    """
    Dependency for injecting VehicleRepository into endpoints.

    Args:
        session: Database session from dependency injection

    Yields:
        VehicleRepository instance
    """
    yield VehicleRepository(session)


# Type aliases for repository dependencies
NodeRepositoryDep = Annotated[NodeRepository, Depends(get_node_repository)]
EdgeRepositoryDep = Annotated[EdgeRepository, Depends(get_edge_repository)]
VehicleRepositoryDep = Annotated[VehicleRepository, Depends(get_vehicle_repository)]

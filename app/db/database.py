"""
Async SQLAlchemy database configuration.
Provides async engine and session factory for PostgreSQL with PostGIS.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.core.constants import DB_POOL_RECYCLE_SECONDS


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# Async engine configuration with connection pooling
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=DB_POOL_RECYCLE_SECONDS,
)

# Async session factory
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI to get database sessions.

    Yields:
        AsyncSession: Database session for request scope
    """
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """
    Initialize database connection on application startup.
    Called from lifespan context manager.
    """
    async with engine.begin():
        pass


async def close_db() -> None:
    """
    Close database connections on application shutdown.
    Called from lifespan context manager.
    """
    await engine.dispose()

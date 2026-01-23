"""
Transaction management utilities for database operations.
Provides context managers and decorators for handling transactions.
"""

from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, AsyncGenerator, Callable, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory

F = TypeVar("F", bound=Callable[..., Any])


@asynccontextmanager
async def transaction() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database transactions.

    Creates a new session and handles commit/rollback automatically.
    Use this when you need explicit transaction control outside of
    FastAPI request handling.

    Example:
        async with transaction() as session:
            repo = NodeRepository(session)
            node = await repo.create(new_node)
            # Commits automatically on exit, rolls back on exception

    Yields:
        AsyncSession with automatic transaction management
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def read_only_transaction() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for read-only database operations.

    Creates a new session that will be rolled back on exit.
    Useful for queries that should not modify the database.

    Example:
        async with read_only_transaction() as session:
            repo = NodeRepository(session)
            nodes = await repo.list()

    Yields:
        AsyncSession that will be rolled back
    """
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


def transactional(func: F) -> F:
    """
    Decorator for wrapping async functions in a transaction.

    The decorated function receives a session as its first argument.
    The transaction is committed on success, rolled back on exception.

    Example:
        @transactional
        async def create_node_with_edges(session, node_data, edge_data):
            node_repo = NodeRepository(session)
            edge_repo = EdgeRepository(session)
            node = await node_repo.create(node_data)
            for edge in edge_data:
                await edge_repo.create(edge)
            return node

    Args:
        func: Async function to wrap

    Returns:
        Wrapped function with transaction management
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async with transaction() as session:
            return await func(session, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class UnitOfWork:
    """
    Unit of Work pattern implementation for coordinating repository operations.

    Provides a single session shared across multiple repositories,
    with automatic transaction management.

    Example:
        async with UnitOfWork() as uow:
            node = await uow.nodes.create(new_node)
            edge = await uow.edges.create(new_edge)
            await uow.commit()

    Attributes:
        session: The shared AsyncSession
        nodes: NodeRepository instance
        edges: EdgeRepository instance
        vehicles: VehicleRepository instance
    """

    def __init__(self) -> None:
        """Initialize the Unit of Work (session created on enter)."""
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "UnitOfWork":
        """Enter the context and create repositories."""
        self._session = async_session_factory()
        await self._session.__aenter__()

        # Import here to avoid circular imports
        from app.db.repositories.edge_repository import EdgeRepository
        from app.db.repositories.node_repository import NodeRepository
        from app.db.repositories.vehicle_repository import VehicleRepository

        self.nodes = NodeRepository(self._session)
        self.edges = EdgeRepository(self._session)
        self.vehicles = VehicleRepository(self._session)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the context, rolling back if there was an exception."""
        if self._session is None:
            return

        if exc_type is not None:
            await self.rollback()
        await self._session.__aexit__(exc_type, exc_val, exc_tb)

    @property
    def session(self) -> AsyncSession:
        """Get the current session."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with'.")
        return self._session

    async def commit(self) -> None:
        """Commit the current transaction."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with'.")
        await self._session.commit()

    async def rollback(self) -> None:
        """Rollback the current transaction."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with'.")
        await self._session.rollback()

    async def flush(self) -> None:
        """Flush pending changes without committing."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with'.")
        await self._session.flush()


async def execute_in_transaction(
    func: Callable[[AsyncSession], Any],
) -> Any:
    """
    Execute a function within a transaction.

    Utility function for running arbitrary code with transaction management.

    Args:
        func: Async function that takes a session and returns a result

    Returns:
        The result of the function

    Example:
        result = await execute_in_transaction(
            lambda session: NodeRepository(session).list()
        )
    """
    async with transaction() as session:
        return await func(session)

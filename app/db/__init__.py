"""
Database module for async SQLAlchemy with PostgreSQL/PostGIS.
Includes repositories, dependencies, and transaction utilities.

Note: To avoid circular imports, repositories and dependencies are not
imported at the top level. Import them directly from their submodules:
    from app.db.repositories import NodeRepository
    from app.db.dependencies import NodeRepositoryDep
"""

from app.db.database import (
    Base,
    async_session_factory,
    close_db,
    engine,
    get_db_session,
    init_db,
)

__all__ = [
    # Database core
    "Base",
    "engine",
    "async_session_factory",
    "get_db_session",
    "init_db",
    "close_db",
]


def __getattr__(name: str):
    """Lazy imports to avoid circular dependencies."""
    # Repositories
    if name in ("BaseRepository", "NodeRepository", "EdgeRepository", "VehicleRepository"):
        from app.db import repositories
        return getattr(repositories, name)

    # Dependencies
    if name in (
        "AsyncSessionDep",
        "NodeRepositoryDep",
        "EdgeRepositoryDep",
        "VehicleRepositoryDep",
        "get_node_repository",
        "get_edge_repository",
        "get_vehicle_repository",
    ):
        from app.db import dependencies
        return getattr(dependencies, name)

    # Transaction utilities
    if name in (
        "transaction",
        "read_only_transaction",
        "transactional",
        "UnitOfWork",
        "execute_in_transaction",
    ):
        from app.db import transaction
        return getattr(transaction, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

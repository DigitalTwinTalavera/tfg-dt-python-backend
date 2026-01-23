"""
Database module for async SQLAlchemy with PostgreSQL/PostGIS.
Includes repositories, dependencies, and transaction utilities.
"""

from app.db.database import (
    Base,
    async_session_factory,
    close_db,
    engine,
    get_db_session,
    init_db,
)
from app.db.dependencies import (
    AsyncSessionDep,
    EdgeRepositoryDep,
    NodeRepositoryDep,
    VehicleRepositoryDep,
    get_edge_repository,
    get_node_repository,
    get_vehicle_repository,
)
from app.db.repositories import (
    BaseRepository,
    EdgeRepository,
    NodeRepository,
    VehicleRepository,
)
from app.db.transaction import (
    UnitOfWork,
    execute_in_transaction,
    read_only_transaction,
    transaction,
    transactional,
)

__all__ = [
    # Database core
    "Base",
    "engine",
    "async_session_factory",
    "get_db_session",
    "init_db",
    "close_db",
    # Repositories
    "BaseRepository",
    "NodeRepository",
    "EdgeRepository",
    "VehicleRepository",
    # Dependencies
    "AsyncSessionDep",
    "NodeRepositoryDep",
    "EdgeRepositoryDep",
    "VehicleRepositoryDep",
    "get_node_repository",
    "get_edge_repository",
    "get_vehicle_repository",
    # Transaction utilities
    "transaction",
    "read_only_transaction",
    "transactional",
    "UnitOfWork",
    "execute_in_transaction",
]

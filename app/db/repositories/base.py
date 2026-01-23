"""
Base repository with generic CRUD operations.
Provides common database operations that can be inherited by specific repositories.
"""

from typing import Any, Generic, Optional, TypeVar, get_args

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DEFAULT_PAGE_LIMIT
from app.db.database import Base

# Type variable for model classes
ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic base repository providing CRUD operations.

    This class provides common database operations that work with any
    SQLAlchemy model. Specific repositories should inherit from this
    class and may override or extend methods as needed.

    Type Parameters:
        ModelType: The SQLAlchemy model class this repository operates on

    Attributes:
        _session: AsyncSession for database operations
        _model: The model class (automatically detected from generic type)
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the repository with a database session.

        Args:
            session: AsyncSession for database operations
        """
        self._session = session
        self._model: type[ModelType] = self._get_model_class()

    def _get_model_class(self) -> type[ModelType]:
        """
        Get the model class from the generic type parameter.

        Returns:
            The model class for this repository
        """
        # Get the generic type argument from the class hierarchy
        for base in type(self).__orig_bases__:  # type: ignore[attr-defined]
            args = get_args(base)
            if args:
                return args[0]
        raise ValueError("Could not determine model class from generic type")

    async def create(self, entity: ModelType) -> ModelType:
        """
        Create a new entity in the database.

        Args:
            entity: The model instance to create

        Returns:
            The created entity with populated ID and timestamps
        """
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def get(self, entity_id: Any) -> Optional[ModelType]:
        """
        Get an entity by its primary key.

        Args:
            entity_id: The primary key value

        Returns:
            The entity if found, None otherwise
        """
        stmt = select(self._model).where(self._model.id == entity_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self, entity_id: Any, values: dict[str, Any]
    ) -> Optional[ModelType]:
        """
        Update an entity by its primary key.

        Args:
            entity_id: The primary key value
            values: Dictionary of column names and new values

        Returns:
            The updated entity if found, None otherwise
        """
        entity = await self.get(entity_id)
        if entity is None:
            return None

        for key, value in values.items():
            if hasattr(entity, key):
                setattr(entity, key, value)

        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def delete(self, entity_id: Any) -> bool:
        """
        Delete an entity by its primary key.

        Args:
            entity_id: The primary key value

        Returns:
            True if entity was deleted, False if not found
        """
        entity = await self.get(entity_id)
        if entity is None:
            return False

        await self._session.delete(entity)
        await self._session.flush()
        return True

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[ModelType]:
        """
        List entities with pagination and optional filtering.

        Args:
            skip: Number of records to skip (offset)
            limit: Maximum number of records to return
            filters: Optional dictionary of field=value filters

        Returns:
            List of entities matching the criteria
        """
        stmt = select(self._model)

        if filters:
            for key, value in filters.items():
                if hasattr(self._model, key):
                    stmt = stmt.where(getattr(self._model, key) == value)

        stmt = stmt.offset(skip).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, filters: Optional[dict[str, Any]] = None) -> int:
        """
        Count entities with optional filtering.

        Args:
            filters: Optional dictionary of field=value filters

        Returns:
            Number of entities matching the criteria
        """
        stmt = select(func.count()).select_from(self._model)

        if filters:
            for key, value in filters.items():
                if hasattr(self._model, key):
                    stmt = stmt.where(getattr(self._model, key) == value)

        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def exists(self, entity_id: Any) -> bool:
        """
        Check if an entity exists by its primary key.

        Args:
            entity_id: The primary key value

        Returns:
            True if entity exists, False otherwise
        """
        stmt = select(func.count()).select_from(self._model).where(
            self._model.id == entity_id
        )
        result = await self._session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def bulk_create(self, entities: list[ModelType]) -> list[ModelType]:
        """
        Create multiple entities in a single transaction.

        Args:
            entities: List of model instances to create

        Returns:
            List of created entities with populated IDs and timestamps
        """
        self._session.add_all(entities)
        await self._session.flush()
        for entity in entities:
            await self._session.refresh(entity)
        return entities

    async def bulk_delete(self, entity_ids: list[Any]) -> int:
        """
        Delete multiple entities by their primary keys.

        Args:
            entity_ids: List of primary key values

        Returns:
            Number of entities deleted
        """
        stmt = delete(self._model).where(self._model.id.in_(entity_ids))
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def bulk_update(
        self, entity_ids: list[Any], values: dict[str, Any]
    ) -> int:
        """
        Update multiple entities with the same values.

        Args:
            entity_ids: List of primary key values
            values: Dictionary of column names and new values

        Returns:
            Number of entities updated
        """
        stmt = (
            update(self._model)
            .where(self._model.id.in_(entity_ids))
            .values(**values)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

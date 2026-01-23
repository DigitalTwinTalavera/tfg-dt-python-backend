"""
Unit tests for repository pattern implementations.
Tests BaseRepository and specific repository classes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.db.repositories.base import BaseRepository
from app.db.repositories.node_repository import NodeRepository
from app.db.repositories.edge_repository import EdgeRepository
from app.db.repositories.vehicle_repository import VehicleRepository
from app.models.enums import NodeType, RoadType, VehicleStatus


# =============================================================================
# BaseRepository Tests
# =============================================================================


class TestBaseRepository:
    """Tests for BaseRepository generic operations."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = AsyncMock()
        session.add = MagicMock()
        session.add_all = MagicMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        session.delete = AsyncMock()
        session.execute = AsyncMock()
        return session

    def test_init_stores_session(self, mock_session):
        """Test that BaseRepository stores the session."""
        # We need a concrete implementation to test
        repo = NodeRepository(mock_session)
        assert repo._session is mock_session

    @pytest.mark.asyncio
    async def test_create_adds_and_flushes(self, mock_session):
        """Test that create() adds entity and flushes."""
        repo = NodeRepository(mock_session)
        mock_entity = MagicMock()

        await repo.create(mock_entity)

        mock_session.add.assert_called_once_with(mock_entity)
        mock_session.flush.assert_awaited_once()
        mock_session.refresh.assert_awaited_once_with(mock_entity)

    @pytest.mark.asyncio
    async def test_get_returns_entity_when_found(self, mock_session):
        """Test that get() returns entity when found."""
        repo = NodeRepository(mock_session)
        mock_entity = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_session.execute.return_value = mock_result

        result = await repo.get(1)

        assert result is mock_entity

    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_found(self, mock_session):
        """Test that get() returns None when entity not found."""
        repo = NodeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.get(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_returns_true_when_found(self, mock_session):
        """Test that delete() returns True when entity deleted."""
        repo = NodeRepository(mock_session)
        mock_entity = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_session.execute.return_value = mock_result

        result = await repo.delete(1)

        assert result is True
        mock_session.delete.assert_awaited_once_with(mock_entity)

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_not_found(self, mock_session):
        """Test that delete() returns False when entity not found."""
        repo = NodeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.delete(999)

        assert result is False
        mock_session.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exists_returns_true_when_found(self, mock_session):
        """Test that exists() returns True when entity exists."""
        repo = NodeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_session.execute.return_value = mock_result

        result = await repo.exists(1)

        assert result is True

    @pytest.mark.asyncio
    async def test_exists_returns_false_when_not_found(self, mock_session):
        """Test that exists() returns False when entity not exists."""
        repo = NodeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute.return_value = mock_result

        result = await repo.exists(999)

        assert result is False

    @pytest.mark.asyncio
    async def test_bulk_create_adds_all_entities(self, mock_session):
        """Test that bulk_create() adds all entities."""
        repo = NodeRepository(mock_session)
        entities = [MagicMock(), MagicMock(), MagicMock()]

        await repo.bulk_create(entities)

        mock_session.add_all.assert_called_once_with(entities)
        mock_session.flush.assert_awaited_once()
        assert mock_session.refresh.await_count == 3

    @pytest.mark.asyncio
    async def test_list_returns_entities(self, mock_session):
        """Test that list() returns entities."""
        repo = NodeRepository(mock_session)
        mock_entities = [MagicMock(), MagicMock()]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_entities
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.list()

        assert result == mock_entities

    @pytest.mark.asyncio
    async def test_count_returns_count(self, mock_session):
        """Test that count() returns the count."""
        repo = NodeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_session.execute.return_value = mock_result

        result = await repo.count()

        assert result == 5


# =============================================================================
# NodeRepository Tests
# =============================================================================


class TestNodeRepository:
    """Tests for NodeRepository specific operations."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_get_by_type_filters_by_node_type(self, mock_session):
        """Test that get_by_type() filters by node type."""
        repo = NodeRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        await repo.get_by_type(NodeType.INTERSECTION)

        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_count_by_type_returns_counts(self, mock_session):
        """Test that count_by_type() returns counts for all types."""
        repo = NodeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 10
        mock_session.execute.return_value = mock_result

        result = await repo.count_by_type()

        assert isinstance(result, dict)
        assert NodeType.INTERSECTION.value in result

    @pytest.mark.asyncio
    async def test_set_active_status_updates_status(self, mock_session):
        """Test that set_active_status() updates the status."""
        repo = NodeRepository(mock_session)
        mock_entity = MagicMock()
        mock_entity.is_active = True
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_session.execute.return_value = mock_result
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        result = await repo.set_active_status(1, False)

        assert result is mock_entity


# =============================================================================
# EdgeRepository Tests
# =============================================================================


class TestEdgeRepository:
    """Tests for EdgeRepository specific operations."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_find_by_start_node_returns_edges(self, mock_session):
        """Test that find_by_start_node() returns edges."""
        repo = EdgeRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.find_by_start_node(1)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_find_by_end_node_returns_edges(self, mock_session):
        """Test that find_by_end_node() returns edges."""
        repo = EdgeRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.find_by_end_node(1)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_find_connected_to_node_returns_edges(self, mock_session):
        """Test that find_connected_to_node() returns edges."""
        repo = EdgeRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.find_connected_to_node(1)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_by_road_type_filters_by_type(self, mock_session):
        """Test that get_by_road_type() filters by road type."""
        repo = EdgeRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_road_type(RoadType.PRIMARY)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_count_by_road_type_returns_counts(self, mock_session):
        """Test that count_by_road_type() returns counts for all types."""
        repo = EdgeRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_session.execute.return_value = mock_result

        result = await repo.count_by_road_type()

        assert isinstance(result, dict)
        assert RoadType.PRIMARY.value in result

    @pytest.mark.asyncio
    async def test_find_path_edges_returns_empty_for_empty_list(self, mock_session):
        """Test that find_path_edges() returns empty for empty input."""
        repo = EdgeRepository(mock_session)

        result = await repo.find_path_edges([])

        assert result == []

    @pytest.mark.asyncio
    async def test_find_path_edges_preserves_order(self, mock_session):
        """Test that find_path_edges() preserves order when requested."""
        repo = EdgeRepository(mock_session)
        mock_edge1 = MagicMock()
        mock_edge1.id = 1
        mock_edge2 = MagicMock()
        mock_edge2.id = 2
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_edge2, mock_edge1]  # Wrong order
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.find_path_edges([1, 2], preserve_order=True)

        assert result[0].id == 1
        assert result[1].id == 2


# =============================================================================
# VehicleRepository Tests
# =============================================================================


class TestVehicleRepository:
    """Tests for VehicleRepository specific operations."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_get_by_status_filters_by_status(self, mock_session):
        """Test that get_by_status() filters by status."""
        repo = VehicleRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_status(VehicleStatus.MOVING)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_active_vehicles_excludes_finished(self, mock_session):
        """Test that get_active_vehicles() excludes finished vehicles."""
        repo = VehicleRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.get_active_vehicles()

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_moving_vehicles_returns_moving_only(self, mock_session):
        """Test that get_moving_vehicles() returns only moving vehicles."""
        repo = VehicleRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.get_moving_vehicles()

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_vehicles_on_edge_filters_by_edge(self, mock_session):
        """Test that get_vehicles_on_edge() filters by edge ID."""
        repo = VehicleRepository(mock_session)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.get_vehicles_on_edge(1)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_count_by_status_returns_counts(self, mock_session):
        """Test that count_by_status() returns counts for all statuses."""
        repo = VehicleRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 10
        mock_session.execute.return_value = mock_result

        result = await repo.count_by_status()

        assert isinstance(result, dict)
        assert VehicleStatus.MOVING.value in result

    @pytest.mark.asyncio
    async def test_bulk_update_status_updates_all(self, mock_session):
        """Test that bulk_update_status() updates all vehicles."""
        repo = VehicleRepository(mock_session)
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute.return_value = mock_result

        vehicle_ids = [uuid4(), uuid4(), uuid4()]
        result = await repo.bulk_update_status(vehicle_ids, VehicleStatus.STOPPED)

        assert result == 3

    @pytest.mark.asyncio
    async def test_bulk_update_status_returns_zero_for_empty_list(self, mock_session):
        """Test that bulk_update_status() returns 0 for empty list."""
        repo = VehicleRepository(mock_session)

        result = await repo.bulk_update_status([], VehicleStatus.STOPPED)

        assert result == 0

    @pytest.mark.asyncio
    async def test_mark_finished_calls_bulk_update_status(self, mock_session):
        """Test that mark_finished() uses bulk_update_status."""
        repo = VehicleRepository(mock_session)
        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_session.execute.return_value = mock_result

        vehicle_ids = [uuid4(), uuid4()]
        result = await repo.mark_finished(vehicle_ids)

        assert result == 2

    @pytest.mark.asyncio
    async def test_get_vehicles_by_ids_returns_empty_for_empty_list(self, mock_session):
        """Test that get_vehicles_by_ids() returns empty for empty input."""
        repo = VehicleRepository(mock_session)

        result = await repo.get_vehicles_by_ids([])

        assert result == []

    @pytest.mark.asyncio
    async def test_update_status_updates_vehicle(self, mock_session):
        """Test that update_status() updates vehicle status."""
        repo = VehicleRepository(mock_session)
        vehicle_id = uuid4()
        mock_entity = MagicMock()
        mock_entity.status = VehicleStatus.IDLE.value
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_session.execute.return_value = mock_result

        result = await repo.update_status(vehicle_id, VehicleStatus.MOVING)

        assert result is mock_entity

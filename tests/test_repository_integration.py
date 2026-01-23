"""
Integration tests for repository pattern with real database.
Tests repositories against PostgreSQL with PostGIS.
"""

import pytest
from uuid import uuid4

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID, ST_MakeLine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.constants import SRID_WGS84
from app.db.database import Base
from app.db.repositories.edge_repository import EdgeRepository
from app.db.repositories.node_repository import NodeRepository
from app.db.repositories.vehicle_repository import VehicleRepository
from app.db.transaction import UnitOfWork, transaction
from app.models.enums import NodeType, RoadType, VehicleStatus
from app.models.road_network import EdgeModel, NodeModel
from app.models.vehicle import VehicleModel


# Test database URL
TEST_DATABASE_URL = settings.database_url


async def create_test_session():
    """Create a test database session."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    session = async_session_maker()
    return session, engine


# =============================================================================
# NodeRepository Integration Tests
# =============================================================================


class TestNodeRepositoryIntegration:
    """Integration tests for NodeRepository with real database."""

    @pytest.mark.asyncio
    async def test_create_and_get_node(self):
        """Test creating and retrieving a node."""
        session, engine = await create_test_session()
        try:
            repo = NodeRepository(session)

            # Create node
            node = NodeModel(
                name="Test Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            created = await repo.create(node)
            await session.commit()

            assert created.id is not None
            assert created.name == "Test Node"

            # Get node
            retrieved = await repo.get(created.id)
            assert retrieved is not None
            assert retrieved.name == "Test Node"

            # Cleanup
            await repo.delete(created.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_by_type(self):
        """Test getting nodes by type."""
        session, engine = await create_test_session()
        try:
            repo = NodeRepository(session)

            # Create test nodes
            node1 = NodeModel(
                name="Intersection 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            node2 = NodeModel(
                name="Traffic Light 1",
                node_type=NodeType.TRAFFIC_LIGHT.value,
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
            )
            await repo.create(node1)
            await repo.create(node2)
            await session.commit()

            # Get by type
            intersections = await repo.get_by_type(NodeType.INTERSECTION)
            assert any(n.name == "Intersection 1" for n in intersections)

            traffic_lights = await repo.get_by_type(NodeType.TRAFFIC_LIGHT)
            assert any(n.name == "Traffic Light 1" for n in traffic_lights)

            # Cleanup
            await repo.delete(node1.id)
            await repo.delete(node2.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_list_with_pagination(self):
        """Test listing nodes with pagination."""
        session, engine = await create_test_session()
        try:
            repo = NodeRepository(session)

            # Create test nodes
            nodes = []
            for i in range(5):
                node = NodeModel(
                    name=f"Pagination Test Node {i}",
                    node_type=NodeType.INTERSECTION.value,
                    position=ST_SetSRID(ST_MakePoint(-3.8196 + i * 0.001, 39.8628), SRID_WGS84),
                )
                await repo.create(node)
                nodes.append(node)
            await session.commit()

            # Test pagination
            page1 = await repo.list(skip=0, limit=2)
            assert len(page1) <= 2

            page2 = await repo.list(skip=2, limit=2)
            assert len(page2) <= 2

            # Cleanup
            for node in nodes:
                await repo.delete(node.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_count_nodes(self):
        """Test counting nodes."""
        session, engine = await create_test_session()
        try:
            repo = NodeRepository(session)

            initial_count = await repo.count()

            # Create a node
            node = NodeModel(
                name="Count Test Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            await repo.create(node)
            await session.commit()

            new_count = await repo.count()
            assert new_count == initial_count + 1

            # Cleanup
            await repo.delete(node.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()


# =============================================================================
# EdgeRepository Integration Tests
# =============================================================================


class TestEdgeRepositoryIntegration:
    """Integration tests for EdgeRepository with real database."""

    @pytest.mark.asyncio
    async def test_create_edge_between_nodes(self):
        """Test creating an edge between two nodes."""
        session, engine = await create_test_session()
        try:
            node_repo = NodeRepository(session)
            edge_repo = EdgeRepository(session)

            # Create start and end nodes
            start_node = NodeModel(
                name="Start Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            end_node = NodeModel(
                name="End Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
            )
            await node_repo.create(start_node)
            await node_repo.create(end_node)
            await session.commit()

            # Create edge
            edge = EdgeModel(
                name="Test Edge",
                start_node_id=start_node.id,
                end_node_id=end_node.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8196, 39.8628),
                        ST_MakePoint(-3.8200, 39.8630),
                    ),
                    SRID_WGS84,
                ),
                length=100.0,
            )
            created = await edge_repo.create(edge)
            await session.commit()

            assert created.id is not None
            assert created.name == "Test Edge"

            # Cleanup
            await edge_repo.delete(created.id)
            await node_repo.delete(start_node.id)
            await node_repo.delete(end_node.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_find_by_start_node(self):
        """Test finding edges by start node."""
        session, engine = await create_test_session()
        try:
            node_repo = NodeRepository(session)
            edge_repo = EdgeRepository(session)

            # Create nodes
            start_node = NodeModel(
                name="Start Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            end_node = NodeModel(
                name="End Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
            )
            await node_repo.create(start_node)
            await node_repo.create(end_node)
            await session.commit()

            # Create edge
            edge = EdgeModel(
                name="Test Edge",
                start_node_id=start_node.id,
                end_node_id=end_node.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8196, 39.8628),
                        ST_MakePoint(-3.8200, 39.8630),
                    ),
                    SRID_WGS84,
                ),
                length=100.0,
            )
            await edge_repo.create(edge)
            await session.commit()

            # Find by start node
            edges = await edge_repo.find_by_start_node(start_node.id)
            assert len(edges) >= 1
            assert any(e.name == "Test Edge" for e in edges)

            # Cleanup
            await edge_repo.delete(edge.id)
            await node_repo.delete(start_node.id)
            await node_repo.delete(end_node.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_find_by_nodes(self):
        """Test finding edge between specific nodes."""
        session, engine = await create_test_session()
        try:
            node_repo = NodeRepository(session)
            edge_repo = EdgeRepository(session)

            # Create nodes
            start_node = NodeModel(
                name="Start Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            end_node = NodeModel(
                name="End Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
            )
            await node_repo.create(start_node)
            await node_repo.create(end_node)
            await session.commit()

            # Create edge
            edge = EdgeModel(
                name="Test Edge",
                start_node_id=start_node.id,
                end_node_id=end_node.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8196, 39.8628),
                        ST_MakePoint(-3.8200, 39.8630),
                    ),
                    SRID_WGS84,
                ),
                length=100.0,
            )
            await edge_repo.create(edge)
            await session.commit()

            # Find by both nodes
            found = await edge_repo.find_by_nodes(start_node.id, end_node.id)
            assert found is not None
            assert found.name == "Test Edge"

            # Cleanup
            await edge_repo.delete(edge.id)
            await node_repo.delete(start_node.id)
            await node_repo.delete(end_node.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()


# =============================================================================
# VehicleRepository Integration Tests
# =============================================================================


class TestVehicleRepositoryIntegration:
    """Integration tests for VehicleRepository with real database."""

    @pytest.mark.asyncio
    async def test_create_and_get_vehicle(self):
        """Test creating and retrieving a vehicle."""
        session, engine = await create_test_session()
        try:
            repo = VehicleRepository(session)

            # Create vehicle
            vehicle = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
                velocity=10.0,
                status=VehicleStatus.MOVING.value,
            )
            created = await repo.create(vehicle)
            await session.commit()

            assert created.id is not None
            assert created.velocity == 10.0

            # Get vehicle
            retrieved = await repo.get(created.id)
            assert retrieved is not None
            assert retrieved.velocity == 10.0

            # Cleanup
            await repo.delete(created.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_by_status(self):
        """Test getting vehicles by status."""
        session, engine = await create_test_session()
        try:
            repo = VehicleRepository(session)

            # Create test vehicles
            moving_vehicle = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
                status=VehicleStatus.MOVING.value,
            )
            idle_vehicle = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
                status=VehicleStatus.IDLE.value,
            )
            await repo.create(moving_vehicle)
            await repo.create(idle_vehicle)
            await session.commit()

            # Get by status
            moving = await repo.get_by_status(VehicleStatus.MOVING)
            assert any(v.id == moving_vehicle.id for v in moving)

            idle = await repo.get_by_status(VehicleStatus.IDLE)
            assert any(v.id == idle_vehicle.id for v in idle)

            # Cleanup
            await repo.delete(moving_vehicle.id)
            await repo.delete(idle_vehicle.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_active_vehicles(self):
        """Test getting active (non-finished) vehicles."""
        session, engine = await create_test_session()
        try:
            repo = VehicleRepository(session)

            # Create test vehicles
            active_vehicle = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
                status=VehicleStatus.MOVING.value,
            )
            finished_vehicle = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
                status=VehicleStatus.FINISHED.value,
            )
            await repo.create(active_vehicle)
            await repo.create(finished_vehicle)
            await session.commit()

            # Get active vehicles
            active = await repo.get_active_vehicles()
            assert any(v.id == active_vehicle.id for v in active)
            assert not any(v.id == finished_vehicle.id for v in active)

            # Cleanup
            await repo.delete(active_vehicle.id)
            await repo.delete(finished_vehicle.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_bulk_update_status(self):
        """Test bulk updating vehicle status."""
        session, engine = await create_test_session()
        try:
            repo = VehicleRepository(session)

            # Create test vehicles
            vehicles = []
            for i in range(3):
                vehicle = VehicleModel(
                    position=ST_SetSRID(ST_MakePoint(-3.8196 + i * 0.001, 39.8628), SRID_WGS84),
                    status=VehicleStatus.IDLE.value,
                )
                await repo.create(vehicle)
                vehicles.append(vehicle)
            await session.commit()

            # Bulk update status
            vehicle_ids = [v.id for v in vehicles]
            updated_count = await repo.bulk_update_status(vehicle_ids, VehicleStatus.MOVING)
            await session.commit()

            assert updated_count == 3

            # Verify updates
            for vehicle in vehicles:
                await session.refresh(vehicle)
                assert vehicle.status == VehicleStatus.MOVING.value

            # Cleanup
            for vehicle in vehicles:
                await repo.delete(vehicle.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_count_by_status(self):
        """Test counting vehicles by status."""
        session, engine = await create_test_session()
        try:
            repo = VehicleRepository(session)

            # Create test vehicles
            vehicle1 = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
                status=VehicleStatus.MOVING.value,
            )
            vehicle2 = VehicleModel(
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
                status=VehicleStatus.IDLE.value,
            )
            await repo.create(vehicle1)
            await repo.create(vehicle2)
            await session.commit()

            # Count by status
            counts = await repo.count_by_status()
            assert isinstance(counts, dict)
            assert VehicleStatus.MOVING.value in counts
            assert VehicleStatus.IDLE.value in counts

            # Cleanup
            await repo.delete(vehicle1.id)
            await repo.delete(vehicle2.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()


# =============================================================================
# UnitOfWork Integration Tests
# =============================================================================


class TestUnitOfWorkIntegration:
    """Integration tests for UnitOfWork pattern."""

    @pytest.mark.asyncio
    async def test_unit_of_work_commit(self):
        """Test session commits changes correctly."""
        session, engine = await create_test_session()
        try:
            repo = NodeRepository(session)

            # Create a node
            node = NodeModel(
                name="UoW Test Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            created = await repo.create(node)
            await session.commit()

            # Verify creation
            retrieved = await repo.get(created.id)
            assert retrieved is not None
            assert retrieved.name == "UoW Test Node"

            # Cleanup
            await repo.delete(created.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_unit_of_work_rollback_on_error(self):
        """Test UnitOfWork rolls back on error."""
        session, engine = await create_test_session()
        try:
            node_id = None
            try:
                # Manually create repositories to test rollback
                node_repo = NodeRepository(session)
                node = NodeModel(
                    name="Rollback Test Node",
                    node_type=NodeType.INTERSECTION.value,
                    position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
                )
                created = await node_repo.create(node)
                node_id = created.id

                # Force an error and rollback
                raise ValueError("Test error")
            except ValueError:
                await session.rollback()

            # Verify rollback - node should not exist
            if node_id:
                retrieved = await node_repo.get(node_id)
                assert retrieved is None
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_unit_of_work_multiple_repositories(self):
        """Test multiple repositories in one session/transaction."""
        session, engine = await create_test_session()
        try:
            node_repo = NodeRepository(session)
            edge_repo = EdgeRepository(session)

            # Create nodes
            node1 = NodeModel(
                name="Node 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            node2 = NodeModel(
                name="Node 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8200, 39.8630), SRID_WGS84),
            )
            await node_repo.create(node1)
            await node_repo.create(node2)
            await session.flush()

            # Create edge between nodes
            edge = EdgeModel(
                name="Test Edge",
                start_node_id=node1.id,
                end_node_id=node2.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8196, 39.8628),
                        ST_MakePoint(-3.8200, 39.8630),
                    ),
                    SRID_WGS84,
                ),
                length=100.0,
            )
            await edge_repo.create(edge)
            await session.commit()

            # Verify all created
            assert await node_repo.exists(node1.id)
            assert await node_repo.exists(node2.id)
            assert await edge_repo.exists(edge.id)

            # Cleanup
            await edge_repo.delete(edge.id)
            await node_repo.delete(node1.id)
            await node_repo.delete(node2.id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()


# =============================================================================
# Transaction Context Manager Tests
# =============================================================================


class TestTransactionContextManager:
    """Integration tests for transaction context manager."""

    @pytest.mark.asyncio
    async def test_transaction_commits_on_success(self):
        """Test that session commits on successful completion."""
        session, engine = await create_test_session()
        node_id = None
        try:
            repo = NodeRepository(session)
            node = NodeModel(
                name="Transaction Test Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            created = await repo.create(node)
            node_id = created.id
            await session.commit()

            # Verify commit by re-fetching
            retrieved = await repo.get(node_id)
            assert retrieved is not None
            assert retrieved.name == "Transaction Test Node"

            # Cleanup
            await repo.delete(node_id)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transaction_rolls_back_on_error(self):
        """Test that transaction rolls back on error."""
        node_id = None

        try:
            async with transaction() as session:
                repo = NodeRepository(session)
                node = NodeModel(
                    name="Rollback Test Node",
                    node_type=NodeType.INTERSECTION.value,
                    position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
                )
                created = await repo.create(node)
                node_id = created.id
                await session.flush()

                raise ValueError("Test error")
        except ValueError:
            pass

        # Verify rollback
        if node_id:
            session, engine = await create_test_session()
            try:
                repo = NodeRepository(session)
                retrieved = await repo.get(node_id)
                assert retrieved is None
            finally:
                await session.close()
                await engine.dispose()

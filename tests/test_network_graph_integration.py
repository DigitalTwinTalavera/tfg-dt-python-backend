"""
Integration tests for RoadNetworkGraph service with real database.
Tests building graph from PostgreSQL and verifying data consistency.
"""

import pytest
from geoalchemy2.functions import ST_MakeLine, ST_MakePoint, ST_SetSRID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.constants import SRID_WGS84
from app.models.enums import NodeType, RoadType
from app.models.road_network import EdgeModel, NodeModel
from app.services.network_graph import RoadNetworkGraph


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
# Integration Tests
# =============================================================================


class TestBuildFromDatabase:
    """Integration tests for building graph from database."""

    @pytest.mark.asyncio
    async def test_build_from_empty_database(self):
        """Test building graph when no nodes/edges exist."""
        session, engine = await create_test_session()
        try:
            graph = RoadNetworkGraph()
            stats = await graph.build_from_database(session)

            # Stats should reflect empty or existing data
            assert stats.node_count >= 0
            assert stats.edge_count >= 0
            assert stats.build_time_ms >= 0
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_with_test_data(self):
        """Test building graph with test nodes and edges."""
        session, engine = await create_test_session()
        try:
            # Create test nodes
            node1 = NodeModel(
                name="Test Node 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8196, 39.8628), SRID_WGS84),
            )
            node2 = NodeModel(
                name="Test Node 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8186, 39.8628), SRID_WGS84),
            )
            session.add(node1)
            session.add(node2)
            await session.flush()

            # Create test edge
            edge = EdgeModel(
                name="Test Edge",
                start_node_id=node1.id,
                end_node_id=node2.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8196, 39.8628),
                        ST_MakePoint(-3.8186, 39.8628),
                    ),
                    SRID_WGS84,
                ),
                length=100.0,
                max_speed=50,
                one_way=False,
            )
            session.add(edge)
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            stats = await graph.build_from_database(session)

            # Verify nodes were loaded
            assert graph.has_node(node1.id)
            assert graph.has_node(node2.id)

            # Verify edge was loaded (bidirectional)
            assert graph.has_edge(node1.id, node2.id)
            assert graph.has_edge(node2.id, node1.id)  # Reverse edge

            # Verify pathfinding works
            path = graph.get_shortest_path(node1.id, node2.id)
            assert path == [node1.id, node2.id]

            # Cleanup
            await session.delete(edge)
            await session.delete(node1)
            await session.delete(node2)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_with_one_way_edge(self):
        """Test that one-way edges are handled correctly."""
        session, engine = await create_test_session()
        try:
            # Create test nodes
            node1 = NodeModel(
                name="One-Way Node 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8296, 39.8728), SRID_WGS84),
            )
            node2 = NodeModel(
                name="One-Way Node 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8286, 39.8728), SRID_WGS84),
            )
            session.add(node1)
            session.add(node2)
            await session.flush()

            # Create one-way edge
            edge = EdgeModel(
                name="One-Way Edge",
                start_node_id=node1.id,
                end_node_id=node2.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8296, 39.8728),
                        ST_MakePoint(-3.8286, 39.8728),
                    ),
                    SRID_WGS84,
                ),
                length=100.0,
                max_speed=50,
                one_way=True,  # One-way!
            )
            session.add(edge)
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            await graph.build_from_database(session)

            # Verify forward edge exists
            assert graph.has_edge(node1.id, node2.id)

            # Verify reverse edge does NOT exist
            assert not graph.has_edge(node2.id, node1.id)

            # Cleanup
            await session.delete(edge)
            await session.delete(node1)
            await session.delete(node2)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_active_only_filter(self):
        """Test that inactive nodes/edges are filtered out."""
        session, engine = await create_test_session()
        try:
            # Create active node
            active_node = NodeModel(
                name="Active Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8396, 39.8828), SRID_WGS84),
                is_active=True,
            )
            # Create inactive node
            inactive_node = NodeModel(
                name="Inactive Node",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8386, 39.8828), SRID_WGS84),
                is_active=False,
            )
            session.add(active_node)
            session.add(inactive_node)
            await session.commit()

            # Build graph with active_only=True (default)
            graph = RoadNetworkGraph()
            await graph.build_from_database(session, active_only=True)

            # Active node should be in graph
            assert graph.has_node(active_node.id)

            # Inactive node should NOT be in graph
            assert not graph.has_node(inactive_node.id)

            # Cleanup
            await session.delete(active_node)
            await session.delete(inactive_node)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_node_attributes_loaded(self):
        """Test that node attributes are correctly loaded."""
        session, engine = await create_test_session()
        try:
            # Create test node
            node = NodeModel(
                name="Attrs Test Node",
                node_type=NodeType.TRAFFIC_LIGHT.value,
                position=ST_SetSRID(ST_MakePoint(-3.8496, 39.8928), SRID_WGS84),
            )
            session.add(node)
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            await graph.build_from_database(session)

            # Get node attributes
            attrs = graph.get_node_attributes(node.id)

            assert attrs["node_id"] == node.id
            assert attrs["node_type"] == NodeType.TRAFFIC_LIGHT.value
            assert abs(attrs["lon"] - (-3.8496)) < 0.0001
            assert abs(attrs["lat"] - 39.8928) < 0.0001

            # Cleanup
            await session.delete(node)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_edge_weight_calculation(self):
        """Test that edge weights are calculated correctly."""
        session, engine = await create_test_session()
        try:
            # Create test nodes
            node1 = NodeModel(
                name="Weight Node 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8596, 39.9028), SRID_WGS84),
            )
            node2 = NodeModel(
                name="Weight Node 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8586, 39.9028), SRID_WGS84),
            )
            session.add(node1)
            session.add(node2)
            await session.flush()

            # Create edge: 1000m at 36 km/h = 100 seconds
            edge = EdgeModel(
                name="Weight Test Edge",
                start_node_id=node1.id,
                end_node_id=node2.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8596, 39.9028),
                        ST_MakePoint(-3.8586, 39.9028),
                    ),
                    SRID_WGS84,
                ),
                length=1000.0,  # 1000 meters
                max_speed=36,  # 36 km/h = 10 m/s
                one_way=True,
            )
            session.add(edge)
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            await graph.build_from_database(session)

            # Get edge weight
            attrs = graph.get_edge_attributes(node1.id, node2.id)

            # weight = length / (max_speed * KMH_TO_MS)
            # weight = 1000 / (36 * 0.2778) = 1000 / 10 = 100 seconds
            assert abs(attrs["weight"] - 100.0) < 1.0

            # Cleanup
            await session.delete(edge)
            await session.delete(node1)
            await session.delete(node2)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()


class TestGraphOperationsIntegration:
    """Integration tests for graph operations with database data."""

    @pytest.mark.asyncio
    async def test_find_nearest_node_with_db_data(self):
        """Test finding nearest node with database data."""
        session, engine = await create_test_session()
        try:
            # Create test nodes at known positions
            node1 = NodeModel(
                name="Near Node 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8696, 39.9128), SRID_WGS84),
            )
            node2 = NodeModel(
                name="Near Node 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8686, 39.9128), SRID_WGS84),
            )
            session.add(node1)
            session.add(node2)
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            await graph.build_from_database(session)

            # Find nearest to node1's position
            nearest = graph.find_nearest_node(-3.8696, 39.9128)
            assert nearest == node1.id

            # Find nearest to node2's position
            nearest = graph.find_nearest_node(-3.8686, 39.9128)
            assert nearest == node2.id

            # Cleanup
            await session.delete(node1)
            await session.delete(node2)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_pathfinding_with_db_network(self):
        """Test pathfinding with a small network from database."""
        session, engine = await create_test_session()
        try:
            # Create a simple triangle network
            #     1
            #    / \
            #   2---3
            node1 = NodeModel(
                name="Triangle 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8796, 39.9228), SRID_WGS84),
            )
            node2 = NodeModel(
                name="Triangle 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8806, 39.9218), SRID_WGS84),
            )
            node3 = NodeModel(
                name="Triangle 3",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8786, 39.9218), SRID_WGS84),
            )
            session.add_all([node1, node2, node3])
            await session.flush()

            # Create edges (bidirectional)
            edge1 = EdgeModel(
                name="Edge 1-2",
                start_node_id=node1.id,
                end_node_id=node2.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8796, 39.9228),
                        ST_MakePoint(-3.8806, 39.9218),
                    ),
                    SRID_WGS84,
                ),
                length=150.0,
                max_speed=50,
                one_way=False,
            )
            edge2 = EdgeModel(
                name="Edge 1-3",
                start_node_id=node1.id,
                end_node_id=node3.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8796, 39.9228),
                        ST_MakePoint(-3.8786, 39.9218),
                    ),
                    SRID_WGS84,
                ),
                length=150.0,
                max_speed=50,
                one_way=False,
            )
            edge3 = EdgeModel(
                name="Edge 2-3",
                start_node_id=node2.id,
                end_node_id=node3.id,
                road_type=RoadType.PRIMARY.value,
                geometry=ST_SetSRID(
                    ST_MakeLine(
                        ST_MakePoint(-3.8806, 39.9218),
                        ST_MakePoint(-3.8786, 39.9218),
                    ),
                    SRID_WGS84,
                ),
                length=200.0,  # Longer edge
                max_speed=50,
                one_way=False,
            )
            session.add_all([edge1, edge2, edge3])
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            await graph.build_from_database(session)

            # Test pathfinding
            # Path from 2 to 3: direct (2-3) vs via 1 (2-1-3)
            # Direct: 200m, Via 1: 300m - should take direct
            path = graph.get_shortest_path(node2.id, node3.id)
            assert len(path) == 2
            assert path[0] == node2.id
            assert path[1] == node3.id

            # Cleanup
            await session.delete(edge1)
            await session.delete(edge2)
            await session.delete(edge3)
            await session.delete(node1)
            await session.delete(node2)
            await session.delete(node3)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rebuild_graph(self):
        """Test rebuilding the graph after changes."""
        session, engine = await create_test_session()
        try:
            # Create initial node
            node1 = NodeModel(
                name="Rebuild Node 1",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8896, 39.9328), SRID_WGS84),
            )
            session.add(node1)
            await session.commit()

            # Build graph
            graph = RoadNetworkGraph()
            stats1 = await graph.build_from_database(session)
            initial_count = stats1.node_count

            # Add another node
            node2 = NodeModel(
                name="Rebuild Node 2",
                node_type=NodeType.INTERSECTION.value,
                position=ST_SetSRID(ST_MakePoint(-3.8886, 39.9328), SRID_WGS84),
            )
            session.add(node2)
            await session.commit()

            # Rebuild graph
            stats2 = await graph.build_from_database(session)

            # Should have one more node
            assert stats2.node_count == initial_count + 1

            # Cleanup
            await session.delete(node1)
            await session.delete(node2)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

"""
Unit tests for RoadNetworkGraph service.
Uses synthetic networks to test graph operations without database.
"""

import json
import time

import networkx as nx
import pytest

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_ROAD_TYPE,
    ATTR_WEIGHT,
)
from app.services.network_graph import GraphStats, RoadNetworkGraph


# =============================================================================
# Fixtures for synthetic networks
# =============================================================================


@pytest.fixture
def empty_graph() -> RoadNetworkGraph:
    """Create an empty RoadNetworkGraph."""
    return RoadNetworkGraph()


@pytest.fixture
def simple_graph() -> RoadNetworkGraph:
    """
    Create a simple graph with 4 nodes and 4 edges.

    Structure:
        1 ---> 2
        |      |
        v      v
        3 ---> 4

    All edges are bidirectional except 1->2 (one-way).
    """
    graph = RoadNetworkGraph()

    # Add nodes
    graph.graph.add_node(1, **{
        ATTR_NODE_ID: 1,
        ATTR_LONGITUDE: -3.8196,
        ATTR_LATITUDE: 39.8628,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_node(2, **{
        ATTR_NODE_ID: 2,
        ATTR_LONGITUDE: -3.8186,
        ATTR_LATITUDE: 39.8628,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_node(3, **{
        ATTR_NODE_ID: 3,
        ATTR_LONGITUDE: -3.8196,
        ATTR_LATITUDE: 39.8618,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_node(4, **{
        ATTR_NODE_ID: 4,
        ATTR_LONGITUDE: -3.8186,
        ATTR_LATITUDE: 39.8618,
        ATTR_NODE_TYPE: "intersection",
    })

    # Add edges with travel time weight (length / speed)
    # 1 -> 2 (one-way, 100m, 50km/h = 13.89m/s, weight = 7.2s)
    graph.graph.add_edge(1, 2, **{
        ATTR_EDGE_ID: 1,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "primary",
        ATTR_ONE_WAY: True,
    })

    # 1 <-> 3 (bidirectional, 100m)
    graph.graph.add_edge(1, 3, **{
        ATTR_EDGE_ID: 2,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: False,
    })
    graph.graph.add_edge(3, 1, **{
        ATTR_EDGE_ID: 2,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: False,
    })

    # 2 <-> 4 (bidirectional, 100m)
    graph.graph.add_edge(2, 4, **{
        ATTR_EDGE_ID: 3,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: False,
    })
    graph.graph.add_edge(4, 2, **{
        ATTR_EDGE_ID: 3,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: False,
    })

    # 3 <-> 4 (bidirectional, 100m)
    graph.graph.add_edge(3, 4, **{
        ATTR_EDGE_ID: 4,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: False,
    })
    graph.graph.add_edge(4, 3, **{
        ATTR_EDGE_ID: 4,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: False,
    })

    return graph


@pytest.fixture
def disconnected_graph() -> RoadNetworkGraph:
    """
    Create a graph with two disconnected components.

    Component 1: 1 -> 2
    Component 2: 3 -> 4
    """
    graph = RoadNetworkGraph()

    # Component 1
    graph.graph.add_node(1, **{
        ATTR_NODE_ID: 1,
        ATTR_LONGITUDE: -3.8196,
        ATTR_LATITUDE: 39.8628,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_node(2, **{
        ATTR_NODE_ID: 2,
        ATTR_LONGITUDE: -3.8186,
        ATTR_LATITUDE: 39.8628,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_edge(1, 2, **{
        ATTR_EDGE_ID: 1,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "primary",
        ATTR_ONE_WAY: False,
    })
    graph.graph.add_edge(2, 1, **{
        ATTR_EDGE_ID: 1,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "primary",
        ATTR_ONE_WAY: False,
    })

    # Component 2
    graph.graph.add_node(3, **{
        ATTR_NODE_ID: 3,
        ATTR_LONGITUDE: -3.8096,
        ATTR_LATITUDE: 39.8528,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_node(4, **{
        ATTR_NODE_ID: 4,
        ATTR_LONGITUDE: -3.8086,
        ATTR_LATITUDE: 39.8528,
        ATTR_NODE_TYPE: "intersection",
    })
    graph.graph.add_edge(3, 4, **{
        ATTR_EDGE_ID: 2,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "primary",
        ATTR_ONE_WAY: False,
    })
    graph.graph.add_edge(4, 3, **{
        ATTR_EDGE_ID: 2,
        ATTR_LENGTH: 100.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 7.2,
        ATTR_ROAD_TYPE: "primary",
        ATTR_ONE_WAY: False,
    })

    return graph


@pytest.fixture
def weighted_graph() -> RoadNetworkGraph:
    """
    Create a graph with different edge weights for testing optimal path.

    Structure:
        1 -----(10s)-----> 2
        |                  |
       (5s)              (5s)
        |                  |
        v                  v
        3 -----(5s)------> 4

    Direct path 1->2 has weight 10s.
    Path 1->3->4->2 has weight 15s.
    Path 1->2->4 has weight 15s.
    Shortest 1->4: 1->3->4 = 10s
    """
    graph = RoadNetworkGraph()

    # Add nodes
    for i in range(1, 5):
        graph.graph.add_node(i, **{
            ATTR_NODE_ID: i,
            ATTR_LONGITUDE: -3.8196 + (i % 2) * 0.001,
            ATTR_LATITUDE: 39.8628 - (i // 3) * 0.001,
            ATTR_NODE_TYPE: "intersection",
        })

    # 1 -> 2 (weight 10s)
    graph.graph.add_edge(1, 2, **{
        ATTR_EDGE_ID: 1,
        ATTR_LENGTH: 500.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 10.0,
        ATTR_ROAD_TYPE: "primary",
        ATTR_ONE_WAY: True,
    })

    # 1 -> 3 (weight 5s)
    graph.graph.add_edge(1, 3, **{
        ATTR_EDGE_ID: 2,
        ATTR_LENGTH: 250.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 5.0,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: True,
    })

    # 2 -> 4 (weight 5s)
    graph.graph.add_edge(2, 4, **{
        ATTR_EDGE_ID: 3,
        ATTR_LENGTH: 250.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 5.0,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: True,
    })

    # 3 -> 4 (weight 5s)
    graph.graph.add_edge(3, 4, **{
        ATTR_EDGE_ID: 4,
        ATTR_LENGTH: 250.0,
        ATTR_MAX_SPEED: 50,
        ATTR_WEIGHT: 5.0,
        ATTR_ROAD_TYPE: "secondary",
        ATTR_ONE_WAY: True,
    })

    return graph


# =============================================================================
# Test RoadNetworkGraph initialization
# =============================================================================


class TestRoadNetworkGraphInit:
    """Tests for RoadNetworkGraph initialization."""

    def test_init_creates_empty_graph(self, empty_graph: RoadNetworkGraph):
        """Test that initialization creates an empty graph."""
        assert empty_graph.node_count == 0
        assert empty_graph.edge_count == 0
        assert empty_graph.stats is None

    def test_init_with_custom_cache_ttl(self):
        """Test initialization with custom cache TTL."""
        graph = RoadNetworkGraph(cache_ttl=600)
        assert graph._cache_ttl == 600

    def test_is_stale_when_never_built(self, empty_graph: RoadNetworkGraph):
        """Test that graph is stale when never built."""
        assert empty_graph.is_stale is True


# =============================================================================
# Test pathfinding methods
# =============================================================================


class TestPathfinding:
    """Tests for pathfinding methods."""

    def test_get_shortest_path_direct(self, simple_graph: RoadNetworkGraph):
        """Test finding a direct path."""
        path = simple_graph.get_shortest_path(1, 2)
        assert path == [1, 2]

    def test_get_shortest_path_indirect(self, simple_graph: RoadNetworkGraph):
        """Test finding an indirect path."""
        # From 2 to 3, must go through 4 (since 1->2 is one-way)
        path = simple_graph.get_shortest_path(2, 3)
        assert path == [2, 4, 3]

    def test_get_shortest_path_weighted(self, weighted_graph: RoadNetworkGraph):
        """Test that shortest path considers weights."""
        # 1->4: Direct via 1->2->4 = 15s, via 1->3->4 = 10s
        path = weighted_graph.get_shortest_path(1, 4)
        assert path == [1, 3, 4]  # Shorter by weight

    def test_get_shortest_path_no_path(self, disconnected_graph: RoadNetworkGraph):
        """Test that no path raises exception."""
        with pytest.raises(nx.NetworkXNoPath):
            disconnected_graph.get_shortest_path(1, 3)

    def test_get_shortest_path_nonexistent_node(self, simple_graph: RoadNetworkGraph):
        """Test that nonexistent node raises exception."""
        with pytest.raises(nx.NodeNotFound):
            simple_graph.get_shortest_path(1, 999)

    def test_get_shortest_path_safe_returns_none(self, disconnected_graph: RoadNetworkGraph):
        """Test safe method returns None when no path."""
        path = disconnected_graph.get_shortest_path_safe(1, 3)
        assert path is None

    def test_get_shortest_path_safe_returns_path(self, simple_graph: RoadNetworkGraph):
        """Test safe method returns path when exists."""
        path = simple_graph.get_shortest_path_safe(1, 2)
        assert path == [1, 2]


# =============================================================================
# Test route calculation methods
# =============================================================================


class TestRouteCalculation:
    """Tests for route calculation methods."""

    def test_get_route_length_single_edge(self, simple_graph: RoadNetworkGraph):
        """Test route length for single edge."""
        length = simple_graph.get_route_length([1, 2])
        assert length == 100.0

    def test_get_route_length_multiple_edges(self, simple_graph: RoadNetworkGraph):
        """Test route length for multiple edges."""
        length = simple_graph.get_route_length([1, 3, 4])
        assert length == 200.0

    def test_get_route_length_empty_path(self, simple_graph: RoadNetworkGraph):
        """Test route length for empty path."""
        length = simple_graph.get_route_length([])
        assert length == 0.0

    def test_get_route_length_single_node(self, simple_graph: RoadNetworkGraph):
        """Test route length for single node path."""
        length = simple_graph.get_route_length([1])
        assert length == 0.0

    def test_get_route_travel_time(self, simple_graph: RoadNetworkGraph):
        """Test route travel time calculation."""
        travel_time = simple_graph.get_route_travel_time([1, 2])
        assert travel_time == 7.2

    def test_get_route_travel_time_multiple_edges(self, simple_graph: RoadNetworkGraph):
        """Test route travel time for multiple edges."""
        travel_time = simple_graph.get_route_travel_time([1, 3, 4])
        assert travel_time == 14.4


# =============================================================================
# Test neighbor methods
# =============================================================================


class TestNeighborMethods:
    """Tests for neighbor query methods."""

    def test_get_neighbors(self, simple_graph: RoadNetworkGraph):
        """Test getting neighbors (successors)."""
        neighbors = simple_graph.get_neighbors(1)
        assert set(neighbors) == {2, 3}

    def test_get_neighbors_one_way_restriction(self, simple_graph: RoadNetworkGraph):
        """Test that one-way edges are respected."""
        # Node 2 can only go to 4 (not back to 1)
        neighbors = simple_graph.get_neighbors(2)
        assert neighbors == [4]

    def test_get_neighbors_nonexistent_node(self, simple_graph: RoadNetworkGraph):
        """Test getting neighbors of nonexistent node."""
        neighbors = simple_graph.get_neighbors(999)
        assert neighbors == []

    def test_get_predecessors(self, simple_graph: RoadNetworkGraph):
        """Test getting predecessors."""
        predecessors = simple_graph.get_predecessors(4)
        assert set(predecessors) == {2, 3}

    def test_get_predecessors_with_one_way(self, simple_graph: RoadNetworkGraph):
        """Test predecessors respects one-way edges."""
        # Node 2 has predecessor 1 (one-way from 1)
        predecessors = simple_graph.get_predecessors(2)
        assert 1 in predecessors


# =============================================================================
# Test find nearest node
# =============================================================================


class TestFindNearestNode:
    """Tests for find_nearest_node method."""

    def test_find_nearest_node(self, simple_graph: RoadNetworkGraph):
        """Test finding nearest node to a point."""
        # Point very close to node 1
        nearest = simple_graph.find_nearest_node(-3.8196, 39.8628)
        assert nearest == 1

    def test_find_nearest_node_between_nodes(self, simple_graph: RoadNetworkGraph):
        """Test finding nearest when point is between nodes."""
        # Point between node 1 and 2 (closer to 2)
        nearest = simple_graph.find_nearest_node(-3.8188, 39.8628)
        assert nearest == 2

    def test_find_nearest_node_empty_graph(self, empty_graph: RoadNetworkGraph):
        """Test finding nearest node in empty graph."""
        nearest = empty_graph.find_nearest_node(-3.8196, 39.8628)
        assert nearest is None


# =============================================================================
# Test graph queries
# =============================================================================


class TestGraphQueries:
    """Tests for basic graph query methods."""

    def test_has_node_exists(self, simple_graph: RoadNetworkGraph):
        """Test has_node returns True for existing node."""
        assert simple_graph.has_node(1) is True

    def test_has_node_not_exists(self, simple_graph: RoadNetworkGraph):
        """Test has_node returns False for nonexistent node."""
        assert simple_graph.has_node(999) is False

    def test_has_edge_exists(self, simple_graph: RoadNetworkGraph):
        """Test has_edge returns True for existing edge."""
        assert simple_graph.has_edge(1, 2) is True

    def test_has_edge_not_exists(self, simple_graph: RoadNetworkGraph):
        """Test has_edge returns False for nonexistent edge."""
        assert simple_graph.has_edge(2, 1) is False  # One-way edge

    def test_get_node_attributes(self, simple_graph: RoadNetworkGraph):
        """Test getting node attributes."""
        attrs = simple_graph.get_node_attributes(1)
        assert attrs[ATTR_NODE_ID] == 1
        assert attrs[ATTR_NODE_TYPE] == "intersection"

    def test_get_node_attributes_nonexistent(self, simple_graph: RoadNetworkGraph):
        """Test getting attributes of nonexistent node."""
        attrs = simple_graph.get_node_attributes(999)
        assert attrs == {}

    def test_get_edge_attributes(self, simple_graph: RoadNetworkGraph):
        """Test getting edge attributes."""
        attrs = simple_graph.get_edge_attributes(1, 2)
        assert attrs[ATTR_EDGE_ID] == 1
        assert attrs[ATTR_LENGTH] == 100.0

    def test_get_edge_attributes_nonexistent(self, simple_graph: RoadNetworkGraph):
        """Test getting attributes of nonexistent edge."""
        attrs = simple_graph.get_edge_attributes(2, 1)  # One-way, doesn't exist
        assert attrs == {}

    def test_get_all_node_ids(self, simple_graph: RoadNetworkGraph):
        """Test getting all node IDs."""
        node_ids = simple_graph.get_all_node_ids()
        assert set(node_ids) == {1, 2, 3, 4}

    def test_get_all_edges(self, simple_graph: RoadNetworkGraph):
        """Test getting all edges."""
        edges = simple_graph.get_all_edges()
        assert (1, 2) in edges
        assert (1, 3) in edges
        assert (3, 1) in edges  # Bidirectional


# =============================================================================
# Test export methods
# =============================================================================


class TestExportMethods:
    """Tests for export methods."""

    def test_export_to_json(self, simple_graph: RoadNetworkGraph):
        """Test exporting graph to JSON."""
        json_str = simple_graph.export_to_json()
        data = json.loads(json_str)

        assert "nodes" in data
        assert "edges" in data
        assert "stats" in data
        assert len(data["nodes"]) == 4

    def test_export_to_dict(self, simple_graph: RoadNetworkGraph):
        """Test exporting graph to dictionary."""
        data = simple_graph.export_to_dict()

        assert "nodes" in data
        assert "edges" in data
        assert "stats" in data
        assert data["stats"]["node_count"] == 4


# =============================================================================
# Test graph manipulation
# =============================================================================


class TestGraphManipulation:
    """Tests for graph manipulation methods."""

    def test_clear_graph(self, simple_graph: RoadNetworkGraph):
        """Test clearing the graph."""
        assert simple_graph.node_count > 0

        simple_graph.clear()

        assert simple_graph.node_count == 0
        assert simple_graph.edge_count == 0
        assert simple_graph.stats is None


# =============================================================================
# Test cache behavior
# =============================================================================


class TestCacheBehavior:
    """Tests for cache-related behavior."""

    def test_is_stale_after_build(self, simple_graph: RoadNetworkGraph):
        """Test that graph is not stale immediately after building."""
        simple_graph._last_build_time = time.time()
        assert simple_graph.is_stale is False

    def test_is_stale_after_ttl_expires(self):
        """Test that graph is stale after TTL expires."""
        graph = RoadNetworkGraph(cache_ttl=1)
        graph._last_build_time = time.time() - 2  # 2 seconds ago
        assert graph.is_stale is True


# =============================================================================
# Performance tests
# =============================================================================


class TestPerformance:
    """Performance-related tests."""

    def test_pathfinding_performance(self):
        """Test that pathfinding completes in reasonable time."""
        # Create a larger graph (100 nodes in a grid)
        graph = RoadNetworkGraph()

        # Create 10x10 grid of nodes
        for i in range(100):
            graph.graph.add_node(i, **{
                ATTR_NODE_ID: i,
                ATTR_LONGITUDE: -3.8 + (i % 10) * 0.001,
                ATTR_LATITUDE: 39.8 + (i // 10) * 0.001,
                ATTR_NODE_TYPE: "intersection",
            })

        # Create edges (grid connections)
        for i in range(100):
            # Right neighbor
            if (i + 1) % 10 != 0:
                graph.graph.add_edge(i, i + 1, **{
                    ATTR_WEIGHT: 1.0,
                    ATTR_LENGTH: 100.0,
                })
                graph.graph.add_edge(i + 1, i, **{
                    ATTR_WEIGHT: 1.0,
                    ATTR_LENGTH: 100.0,
                })
            # Bottom neighbor
            if i + 10 < 100:
                graph.graph.add_edge(i, i + 10, **{
                    ATTR_WEIGHT: 1.0,
                    ATTR_LENGTH: 100.0,
                })
                graph.graph.add_edge(i + 10, i, **{
                    ATTR_WEIGHT: 1.0,
                    ATTR_LENGTH: 100.0,
                })

        # Test pathfinding from corner to corner
        start_time = time.time()
        path = graph.get_shortest_path(0, 99)
        elapsed_ms = (time.time() - start_time) * 1000

        assert path is not None
        assert len(path) > 0
        assert elapsed_ms < 100  # Should complete in under 100ms

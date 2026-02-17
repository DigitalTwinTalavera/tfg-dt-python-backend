"""
Tests para el módulo de cálculo de rutas.
"""

import pytest

from app.core.constants import ATTR_EDGE_ID, ATTR_LENGTH, ATTR_NODE_TYPE, ATTR_WEIGHT
from app.core.route import RouteInfo, compute_route
from app.services.network_graph import RoadNetworkGraph


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def graph():
    """Grafo de prueba con 4 nodos y 3 aristas en línea: 1->2->3->4."""
    g = RoadNetworkGraph()
    g.graph.add_node(1, **{ATTR_NODE_TYPE: "entry_point", "lat": 0.0, "lon": 0.0})
    g.graph.add_node(2, **{ATTR_NODE_TYPE: "intersection", "lat": 0.0, "lon": 0.1})
    g.graph.add_node(3, **{ATTR_NODE_TYPE: "intersection", "lat": 0.0, "lon": 0.2})
    g.graph.add_node(4, **{ATTR_NODE_TYPE: "exit_point", "lat": 0.0, "lon": 0.3})

    g.graph.add_edge(1, 2, **{ATTR_EDGE_ID: 10, ATTR_LENGTH: 100.0, ATTR_WEIGHT: 5.0})
    g.graph.add_edge(2, 3, **{ATTR_EDGE_ID: 20, ATTR_LENGTH: 200.0, ATTR_WEIGHT: 10.0})
    g.graph.add_edge(3, 4, **{ATTR_EDGE_ID: 30, ATTR_LENGTH: 150.0, ATTR_WEIGHT: 7.5})
    return g


@pytest.fixture
def branching_graph():
    """Grafo con dos caminos: 1->2->4 (corto) y 1->3->4 (largo)."""
    g = RoadNetworkGraph()
    g.graph.add_node(1, **{ATTR_NODE_TYPE: "entry_point"})
    g.graph.add_node(2, **{ATTR_NODE_TYPE: "intersection"})
    g.graph.add_node(3, **{ATTR_NODE_TYPE: "intersection"})
    g.graph.add_node(4, **{ATTR_NODE_TYPE: "exit_point"})

    # Camino corto: 1->2->4 (weight=3)
    g.graph.add_edge(1, 2, **{ATTR_EDGE_ID: 10, ATTR_LENGTH: 50.0, ATTR_WEIGHT: 1.0})
    g.graph.add_edge(2, 4, **{ATTR_EDGE_ID: 20, ATTR_LENGTH: 50.0, ATTR_WEIGHT: 2.0})

    # Camino largo: 1->3->4 (weight=20)
    g.graph.add_edge(1, 3, **{ATTR_EDGE_ID: 30, ATTR_LENGTH: 500.0, ATTR_WEIGHT: 10.0})
    g.graph.add_edge(3, 4, **{ATTR_EDGE_ID: 40, ATTR_LENGTH: 500.0, ATTR_WEIGHT: 10.0})
    return g


# =============================================================================
# RouteInfo tests
# =============================================================================


class TestRouteInfo:
    @pytest.mark.unit
    def test_to_dict(self):
        route = RouteInfo(
            start_node_id=1,
            end_node_id=4,
            node_path=[1, 2, 3, 4],
            edge_ids=[10, 20, 30],
            length_m=450.55,
        )
        d = route.to_dict()
        assert d["start_node_id"] == 1
        assert d["end_node_id"] == 4
        assert d["route_edges"] == [10, 20, 30]
        assert d["route_length_m"] == 450.6

    @pytest.mark.unit
    def test_frozen_dataclass(self):
        route = RouteInfo(
            start_node_id=1,
            end_node_id=2,
            node_path=[1, 2],
            edge_ids=[10],
            length_m=100.0,
        )
        with pytest.raises(AttributeError):
            route.start_node_id = 99


# =============================================================================
# compute_route tests
# =============================================================================


class TestComputeRoute:
    @pytest.mark.unit
    def test_simple_route(self, graph):
        route = compute_route(graph, 1, 4)
        assert route is not None
        assert route.start_node_id == 1
        assert route.end_node_id == 4
        assert route.edge_ids == [10, 20, 30]
        assert route.node_path == [1, 2, 3, 4]
        assert route.length_m == 450.0

    @pytest.mark.unit
    def test_partial_route(self, graph):
        route = compute_route(graph, 1, 3)
        assert route is not None
        assert route.edge_ids == [10, 20]
        assert route.length_m == 300.0

    @pytest.mark.unit
    def test_no_path_returns_none(self, graph):
        # No hay camino de 4 a 1 (grafo dirigido)
        route = compute_route(graph, 4, 1)
        assert route is None

    @pytest.mark.unit
    def test_same_node_returns_none(self, graph):
        route = compute_route(graph, 1, 1)
        assert route is None

    @pytest.mark.unit
    def test_nonexistent_node_returns_none(self, graph):
        route = compute_route(graph, 1, 999)
        assert route is None

    @pytest.mark.unit
    def test_chooses_shortest_path(self, branching_graph):
        route = compute_route(branching_graph, 1, 4)
        assert route is not None
        # Debería elegir 1->2->4 (weight=3) en vez de 1->3->4 (weight=20)
        assert route.edge_ids == [10, 20]
        assert route.length_m == 100.0

    @pytest.mark.unit
    def test_empty_graph_returns_none(self):
        g = RoadNetworkGraph()
        route = compute_route(g, 1, 2)
        assert route is None

    @pytest.mark.unit
    def test_route_to_dict_integration(self, graph):
        route = compute_route(graph, 1, 4)
        assert route is not None
        d = route.to_dict()
        assert d["start_node_id"] == 1
        assert d["end_node_id"] == 4
        assert d["route_edges"] == [10, 20, 30]
        assert d["route_length_m"] == 450.0

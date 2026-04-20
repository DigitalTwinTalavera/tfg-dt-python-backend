"""Tests for A* routing with blocked edges and dynamic weights (Phases 3 and 6 TFG)."""

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
from app.services.network_graph import RoadNetworkGraph


@pytest.fixture
def diamond_graph() -> RoadNetworkGraph:
    """
    Diamond graph with two parallel routes between 1 and 4:

        1 --> 2 --> 4    (short route, weight 2.0)
        1 --> 3 --> 4    (long route, weight 6.0)
    """
    g = RoadNetworkGraph()
    base = {ATTR_LATITUDE: 39.86, ATTR_LONGITUDE: -3.81, ATTR_NODE_TYPE: "intersection"}
    g.graph.add_node(1, **{ATTR_NODE_ID: 1, **base})
    g.graph.add_node(2, **{ATTR_NODE_ID: 2, **base, ATTR_LATITUDE: 39.861})
    g.graph.add_node(3, **{ATTR_NODE_ID: 3, **base, ATTR_LATITUDE: 39.859})
    g.graph.add_node(4, **{ATTR_NODE_ID: 4, **base, ATTR_LONGITUDE: -3.809})

    common = {ATTR_MAX_SPEED: 50, ATTR_ROAD_TYPE: "residential", ATTR_ONE_WAY: True}

    g.graph.add_edge(1, 2, **{ATTR_EDGE_ID: 1, ATTR_LENGTH: 10.0, ATTR_WEIGHT: 1.0, **common})
    g.graph.add_edge(2, 4, **{ATTR_EDGE_ID: 2, ATTR_LENGTH: 10.0, ATTR_WEIGHT: 1.0, **common})
    g.graph.add_edge(1, 3, **{ATTR_EDGE_ID: 3, ATTR_LENGTH: 30.0, ATTR_WEIGHT: 3.0, **common})
    g.graph.add_edge(3, 4, **{ATTR_EDGE_ID: 4, ATTR_LENGTH: 30.0, ATTR_WEIGHT: 3.0, **common})
    return g


class TestAstarBlockedEdges:
    @pytest.mark.unit
    def test_shortest_route_chosen_without_blocks(self, diamond_graph):
        path = diamond_graph.get_shortest_path_astar(1, 4)
        assert path == [1, 2, 4]

    @pytest.mark.unit
    def test_blocked_edge_pushes_to_alternate_route(self, diamond_graph):
        # Bloquear 1→2: A* debe elegir 1→3→4 pese a que antes era más lenta.
        path = diamond_graph.get_shortest_path_astar(1, 4, blocked_edges={(1, 2): None})
        assert path == [1, 3, 4]

    @pytest.mark.unit
    def test_blocked_edge_used_if_no_alternative(self, diamond_graph):
        # Eliminamos la ruta alternativa → el bloqueo no desconecta el grafo,
        # sigue encontrándose la ruta aunque penalizada.
        diamond_graph.graph.remove_edge(1, 3)
        path = diamond_graph.get_shortest_path_astar(1, 4, blocked_edges={(1, 2): None})
        assert path == [1, 2, 4]


class TestAstarDynamicWeights:
    @pytest.mark.unit
    def test_dynamic_weight_multiplier_redirects_route(self, diamond_graph):
        # Sin multiplicador, ruta corta. Con multiplicador 10x en 1→2, ruta larga.
        assert diamond_graph.get_shortest_path_astar(1, 4) == [1, 2, 4]
        diamond_graph.set_dynamic_weights({(1, 2): 10.0})
        assert diamond_graph.get_shortest_path_astar(1, 4) == [1, 3, 4]

    @pytest.mark.unit
    def test_clearing_dynamic_weights_restores_routing(self, diamond_graph):
        diamond_graph.set_dynamic_weights({(1, 2): 10.0})
        assert diamond_graph.get_shortest_path_astar(1, 4) == [1, 3, 4]
        diamond_graph.set_dynamic_weights({})
        assert diamond_graph.get_shortest_path_astar(1, 4) == [1, 2, 4]

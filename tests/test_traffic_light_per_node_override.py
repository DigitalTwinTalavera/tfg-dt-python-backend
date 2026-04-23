"""
Tests para el override por nodo del TrafficLightController.
"""

import pytest

from app.core.traffic_light_controller import TrafficLightController
from app.models.enums import NodeType
from app.services.network_graph import RoadNetworkGraph


def _graph_with_two_tls() -> RoadNetworkGraph:
    """Construye un mini-grafo con dos nodos TL y una arista entrante a cada uno."""
    g = RoadNetworkGraph()
    g._graph.clear()
    # Nodes
    g._graph.add_node(
        1, node_id=1, node_type=NodeType.TRAFFIC_LIGHT.value, lat=40.0, lon=-4.0
    )
    g._graph.add_node(
        2, node_id=2, node_type=NodeType.TRAFFIC_LIGHT.value, lat=40.01, lon=-4.01
    )
    g._graph.add_node(10, node_id=10, node_type=NodeType.INTERSECTION.value, lat=40.0, lon=-4.001)
    g._graph.add_node(20, node_id=20, node_type=NodeType.INTERSECTION.value, lat=40.01, lon=-4.011)
    # Edges hacia los TL
    g._graph.add_edge(10, 1)
    g._graph.add_edge(20, 2)
    return g


@pytest.fixture
def controller():
    g = _graph_with_two_tls()
    return TrafficLightController(g)


class TestPerNodeOverride:
    @pytest.mark.unit
    def test_override_one_node_only_affects_that_node(self, controller):
        controller.set_override(1, "red")
        assert controller.get_phase(1) == "red"
        # El otro nodo sigue con su ciclo normal (no override)
        assert not controller.has_override_for_node(2)

    @pytest.mark.unit
    def test_clear_override_for_node_returns_bool(self, controller):
        controller.set_override(1, "green")
        assert controller.clear_override_for_node(1) is True
        # Una segunda llamada no encuentra override → False
        assert controller.clear_override_for_node(1) is False

    @pytest.mark.unit
    def test_knows_node(self, controller):
        assert controller.knows_node(1) is True
        assert controller.knows_node(999) is False

    @pytest.mark.unit
    def test_get_override_mode_reports_per_node(self, controller):
        controller.set_override(1, "red")
        mode = controller.get_override_mode()
        assert mode.startswith("per_node")

    @pytest.mark.unit
    def test_clear_overrides_clears_all(self, controller):
        controller.set_override(1, "red")
        controller.set_override(2, "green")
        controller.clear_overrides()
        assert controller.get_override_mode() == "normal"

"""
Test unitario: A* penaliza las aristas restringidas por ZBE.

No requiere PostGIS — construye un mini-grafo en memoria y verifica que
``get_shortest_path_astar`` elige el camino alternativo cuando el directo
tiene aristas marcadas como ``restricted_edges``.
"""

import pytest

from app.core.constants import ATTR_WEIGHT
from app.services.network_graph import RoadNetworkGraph


@pytest.fixture
def triangle_graph():
    """
    Mini-grafo:
       A -- 1s --> B   (camino directo, peso 1)
       A -- 5s --> C -- 5s --> B   (camino alternativo, peso 10)
    """
    g = RoadNetworkGraph()
    g._graph.clear()
    g._graph.add_node("A", lat=0.0, lon=0.0)
    g._graph.add_node("B", lat=0.01, lon=0.0)
    g._graph.add_node("C", lat=0.005, lon=0.01)
    g._graph.add_edge("A", "B", **{ATTR_WEIGHT: 1.0, "edge_id": 1, "lanes": 1})
    g._graph.add_edge("A", "C", **{ATTR_WEIGHT: 5.0, "edge_id": 2, "lanes": 1})
    g._graph.add_edge("C", "B", **{ATTR_WEIGHT: 5.0, "edge_id": 3, "lanes": 1})
    return g


class TestRestrictedEdges:
    @pytest.mark.unit
    def test_no_restrictions_takes_direct_path(self, triangle_graph):
        path = triangle_graph.get_shortest_path_astar("A", "B")
        assert path == ["A", "B"]

    @pytest.mark.unit
    def test_restriction_on_direct_forces_detour(self, triangle_graph):
        """Si (A,B) está en restricted_edges, A* elige el camino indirecto."""
        path = triangle_graph.get_shortest_path_astar(
            "A", "B", restricted_edges={("A", "B")}
        )
        # 1 * 50 = 50 > 5 + 5 = 10 → rodeo
        assert path == ["A", "C", "B"]

    @pytest.mark.unit
    def test_restriction_on_alt_keeps_direct(self, triangle_graph):
        """Si solo una arista del alt está restringida, sigue con el directo."""
        path = triangle_graph.get_shortest_path_astar(
            "A", "B", restricted_edges={("A", "C")}
        )
        assert path == ["A", "B"]

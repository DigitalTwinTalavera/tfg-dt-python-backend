"""
Test unitario: A* penaliza las aristas restringidas por ZBE.

No requiere PostGIS — construye un mini-grafo en memoria y verifica que
``get_shortest_path_astar`` elige el camino alternativo cuando el directo
tiene aristas marcadas como ``restricted_edges``.
"""

import pytest

from app.core.constants import ATTR_LENGTH, ATTR_WEIGHT
from app.core.physics.vehicle_types import VehicleType
from app.core.route import RouteInfo
from app.core.vehicle_physics import _maybe_reroute_around_blocks
from app.models.enums import VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle


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


# ---------------------------------------------------------------------------
# Reroutes durante el tick deben respetar zonas
# ---------------------------------------------------------------------------


@pytest.fixture
def reroute_graph():
    """
    Grafo con prefijo y dos rutas alternativas A→B:
       O -- 1s --> A
       A -- 1s --> B    (directo)
       A -- 5s --> C -- 5s --> B   (alternativo)
    Con O como nodo origen, el vehículo "ya está en O→A" y al rerutear
    pivota en A.
    """
    g = RoadNetworkGraph()
    g._graph.clear()
    g._graph.add_node("O", lat=-0.005, lon=0.0)
    g._graph.add_node("A", lat=0.0, lon=0.0)
    g._graph.add_node("B", lat=0.01, lon=0.0)
    g._graph.add_node("C", lat=0.005, lon=0.01)
    g._graph.add_edge("O", "A", **{ATTR_WEIGHT: 1.0, ATTR_LENGTH: 50.0, "edge_id": 10, "lanes": 1})
    g._graph.add_edge("A", "B", **{ATTR_WEIGHT: 1.0, ATTR_LENGTH: 50.0, "edge_id": 1, "lanes": 1})
    g._graph.add_edge("A", "C", **{ATTR_WEIGHT: 5.0, ATTR_LENGTH: 250.0, "edge_id": 2, "lanes": 1})
    g._graph.add_edge("C", "B", **{ATTR_WEIGHT: 5.0, ATTR_LENGTH: 250.0, "edge_id": 3, "lanes": 1})
    return g


def _make_truck_on_direct_route(graph: RoadNetworkGraph) -> SimVehicle:
    """Truck sobre la arista O→A, con ruta planificada O→A→B."""
    route = RouteInfo(
        start_node_id="O",
        end_node_id="B",
        node_path=["O", "A", "B"],
        edge_ids=[10, 1],
        length_m=100.0,
    )
    return SimVehicle(
        id="truck-1",
        start_node_id="O",
        end_node_id="B",
        route=route,
        status=VehicleStatus.MOVING,
        current_edge_index=0,
        vtype=VehicleType.TRUCK,
    )


class TestRerouteRespectsZones:
    @pytest.mark.unit
    def test_zone_triggers_reroute_to_alternative(self, reroute_graph):
        """
        Crear una zona ZBE para trucks en (A,B) debe rerutear el truck por
        el camino alternativo O→A→C→B.
        """
        v = _make_truck_on_direct_route(reroute_graph)
        zone_edges = {("A", "B")}
        restricted_by_vtype = {"truck": zone_edges}

        changed = _maybe_reroute_around_blocks(
            v,
            reroute_graph,
            blocked_edges={},
            trigger_blocks=zone_edges,
            restricted_edges_by_vtype=restricted_by_vtype,
        )
        assert changed is True
        assert v.route.node_path == ["O", "A", "C", "B"]

    @pytest.mark.unit
    def test_zone_does_not_affect_other_vtypes(self, reroute_graph):
        """
        Una zona que restringe trucks no debe desviar a un car con la misma
        ruta original: A* sin restricción para "car" devuelve el directo.
        """
        v = _make_truck_on_direct_route(reroute_graph)
        v.id = "car-1"
        v.vtype = VehicleType.CAR
        zone_edges = {("A", "B")}
        restricted_by_vtype = {"truck": zone_edges}

        _maybe_reroute_around_blocks(
            v,
            reroute_graph,
            blocked_edges={},
            trigger_blocks=zone_edges,
            restricted_edges_by_vtype=restricted_by_vtype,
        )
        # Para un car, restricted=None → A* elige el camino directo.
        assert v.route.node_path == ["O", "A", "B"]

    @pytest.mark.unit
    def test_reroute_by_block_avoids_entering_zone(self, reroute_graph):
        """
        Caso clave del bug: un truck con ruta original O→A→C→B (que no toca
        la zona) sufre un bloqueo en (A,C). El reroute debe descartar la
        nueva candidata si introduce un cruce de zona que la vieja no tenía.
        """
        # Truck con ruta alternativa.
        route = RouteInfo(
            start_node_id="O",
            end_node_id="B",
            node_path=["O", "A", "C", "B"],
            edge_ids=[10, 2, 3],
            length_m=550.0,
        )
        v = SimVehicle(
            id="truck-2",
            start_node_id="O",
            end_node_id="B",
            route=route,
            status=VehicleStatus.MOVING,
            current_edge_index=0,
            vtype=VehicleType.TRUCK,
        )
        # Zona en (A,B) — la única alternativa al bloqueo.
        zone_edges = {("A", "B")}
        restricted_by_vtype = {"truck": zone_edges}
        # Bloqueo en (A,C): el camino actual ya no es válido.
        blocked_edges = {("A", "C"): None}

        changed = _maybe_reroute_around_blocks(
            v,
            reroute_graph,
            blocked_edges=blocked_edges,
            trigger_blocks={("A", "C")},
            restricted_edges_by_vtype=restricted_by_vtype,
        )
        # No debe mutar: la única alternativa entraría en la zona y la
        # ruta vieja no lo hacía. Mantener la vieja (penalizada) es el
        # comportamiento correcto.
        assert changed is False
        assert v.route.node_path == ["O", "A", "C", "B"]

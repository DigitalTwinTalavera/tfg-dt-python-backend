"""Tests for permanent collision blocking (Phase 3 TFG)."""

import pytest

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_WAYPOINTS,
    ATTR_WEIGHT,
    MAX_EMERGENCY_DECEL_MS2,
)
from app.core.route import RouteInfo
from app.core.vehicle_physics import (
    NeighborInfo,
    _advance_vehicle_idm,
    _maybe_reroute_around_blocks,
    _periodic_reroute_all,
    _reroute_affected_by_new_blocks,
    _trigger_collision,
)
from app.models.enums import NodeType, VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle


def _make_vehicle(vid: str, node_path: list[int], edge_index: int = 0) -> SimVehicle:
    route = RouteInfo(
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        node_path=node_path,
        edge_ids=[0] * (len(node_path) - 1),
        length_m=100.0,
    )
    return SimVehicle(
        id=vid,
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        route=route,
        status=VehicleStatus.MOVING,
        current_edge_index=edge_index,
        velocity=10.0,
    )


class TestTriggerCollision:
    @pytest.mark.unit
    def test_both_vehicles_marked_collision_and_stopped(self):
        v1 = _make_vehicle("v1", [1, 2, 3])
        v2 = _make_vehicle("v2", [1, 2, 3])
        blocked: dict[tuple[int, int], object | None] = {}

        _trigger_collision(v1, v2, blocked)

        assert v1.status == VehicleStatus.COLLISION
        assert v2.status == VehicleStatus.COLLISION
        assert v1.velocity == 0.0
        assert v2.velocity == 0.0
        assert v1.acceleration == 0.0
        assert v2.acceleration == 0.0
        # Sin timer — el choque es permanente hasta retirada manual.
        assert v1.collision_timer == 0.0
        assert v2.collision_timer == 0.0

    @pytest.mark.unit
    def test_edge_becomes_permanently_blocked(self):
        v1 = _make_vehicle("v1", [10, 20, 30], edge_index=0)
        v2 = _make_vehicle("v2", [10, 20, 30], edge_index=0)
        blocked: dict[tuple[int, int], object | None] = {}

        _trigger_collision(v1, v2, blocked)

        # La arista en curso (10 → 20) queda bloqueada con valor None = permanente.
        assert (10, 20) in blocked
        assert blocked[(10, 20)] is None

    @pytest.mark.unit
    def test_no_block_when_vehicle_past_last_edge(self):
        # Vehículo sin arista siguiente (caso degenerado).
        route = RouteInfo(
            start_node_id=1,
            end_node_id=2,
            node_path=[1, 2],
            edge_ids=[0],
            length_m=50.0,
        )
        v1 = SimVehicle(
            id="v1",
            start_node_id=1,
            end_node_id=2,
            route=route,
            current_edge_index=1,  # ya fuera de rango
            status=VehicleStatus.MOVING,
        )
        v2 = SimVehicle(
            id="v2",
            start_node_id=1,
            end_node_id=2,
            route=route,
            current_edge_index=1,
            status=VehicleStatus.MOVING,
        )
        blocked: dict[tuple[int, int], object | None] = {}

        _trigger_collision(v1, v2, blocked)

        assert blocked == {}
        assert v1.status == VehicleStatus.COLLISION
        assert v2.status == VehicleStatus.COLLISION


# -----------------------------------------------------------------------------
# Plan C: auto-reroute al bloquear + emergency brake ampliado
# -----------------------------------------------------------------------------

def _build_alt_path_graph() -> RoadNetworkGraph:
    """
    Grafo mínimo con dos caminos desde B (=nodo 2) a D (=nodo 4):

        A (1) ── B (2) ── C (3) ── D (4)
                  │                   │
                  └── E (5) ── F (6) ─┘

    Path principal: 1→2→3→4 (length 30 m).
    Path alternativo desde 2: 2→5→6→4 (length 60 m, penalizado pero viable).

    El bloqueo de (2,3) tras la arista actual (1,2) obliga a A* a recalcular
    desde el nodo 2 y elegir 2→5→6→4.
    """
    rng = RoadNetworkGraph()
    g = rng.graph
    nodes = {
        1: (-4.830, 39.960), 2: (-4.829, 39.960),
        3: (-4.828, 39.960), 4: (-4.827, 39.960),
        5: (-4.829, 39.959), 6: (-4.827, 39.959),
    }
    for nid, (lon, lat) in nodes.items():
        g.add_node(
            nid,
            **{
                ATTR_NODE_ID: nid,
                ATTR_LONGITUDE: lon,
                ATTR_LATITUDE: lat,
                ATTR_NODE_TYPE: NodeType.INTERSECTION.value,
            },
        )

    def add(eid, u, v, length):
        lu, la = nodes[u]; lv, lb = nodes[v]
        g.add_edge(
            u, v,
            **{
                ATTR_EDGE_ID: eid,
                ATTR_LENGTH: length,
                ATTR_MAX_SPEED: 30,
                ATTR_WEIGHT: length / 30.0,
                ATTR_ONE_WAY: True,
                ATTR_LANES: 1,
                ATTR_WAYPOINTS: [(lu, la), (lv, lb)],
            },
        )
    add(10, 1, 2, 10.0); add(11, 2, 3, 10.0); add(12, 3, 4, 10.0)
    add(20, 2, 5, 15.0); add(21, 5, 6, 30.0); add(22, 6, 4, 15.0)
    return rng


class TestAutoRerouteOnBlock:
    @pytest.mark.unit
    def test_vehicle_reroutes_when_downstream_edge_blocked(self):
        graph = _build_alt_path_graph()
        # Vehículo en arista 1→2 (ei=0), con ruta [1,2,3,4].
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0,
            progress_on_edge=0.3, velocity=5.0,
        )
        vehicles = {"v1": v}
        # Bloquear (2,3) — arista pendiente a partir del próximo nodo.
        blocked = {(2, 3): None}
        new_blocks = {(2, 3)}

        n = _reroute_affected_by_new_blocks(vehicles, graph, new_blocks, blocked)

        assert n == 1
        # La ruta debe arrancar por 1→2 (arista actual) y seguir por la alternativa.
        assert v.route.node_path[0:2] == [1, 2]
        assert (2, 3) not in list(zip(v.route.node_path, v.route.node_path[1:]))
        # Arista actual preservada: ei=0, progress_on_edge intacto.
        assert v.current_edge_index == 0
        assert v.progress_on_edge == pytest.approx(0.3)

    @pytest.mark.unit
    def test_no_reroute_when_block_is_not_on_route(self):
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0, velocity=5.0,
        )
        blocked = {(5, 6): None}  # en la ruta alternativa, no en la actual
        n = _reroute_affected_by_new_blocks({"v1": v}, graph, {(5, 6)}, blocked)
        assert n == 0
        assert v.route.node_path == [1, 2, 3, 4]

    @pytest.mark.unit
    def test_no_reroute_when_current_edge_itself_is_blocked(self):
        """La arista actual ya está comprometida: no se intenta re-rutear
        (sería inconsistente mover al vehículo a otra ruta estando a mitad
        de la arista bloqueada). Sólo aristas PENDIENTES disparan reroute."""
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=1,  # en arista (2,3)
            velocity=5.0,
        )
        blocked = {(2, 3): None}
        n = _reroute_affected_by_new_blocks({"v1": v}, graph, {(2, 3)}, blocked)
        assert n == 0


class TestEmergencyBrakeWiderWindow:
    @pytest.mark.unit
    def test_brake_triggers_on_slow_leader_far_and_higher_speed(self):
        """Líder a 1.5 m/s (no parado), gap 10 m, ego a 8 m/s: el IDM puro
        puede quedarse corto; la ventana ampliada fuerza decel máximo."""
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0,
            progress_on_edge=0.1, velocity=8.0, desired_speed_ms=13.89,
        )
        leader = NeighborInfo(gap_m=10.0, velocity_ms=1.5, leader_id="v2")
        _advance_vehicle_idm(v, graph, dt=0.1, leader=leader, tl_ref=None)
        # El campo `acceleration` guarda el último a aplicado (clampeado).
        assert v.acceleration == pytest.approx(-MAX_EMERGENCY_DECEL_MS2)


# -----------------------------------------------------------------------------
# Plan D: periodic reroute + dead-wall detection + penalty-as-exclusion
# -----------------------------------------------------------------------------

class TestPeriodicReroute:
    @pytest.mark.unit
    def test_periodic_reroute_rescues_stale_vehicle(self):
        """Plan D1: un vehículo con ruta pendiente por una arista bloqueada
        desde *antes* del tick (no se enteró del bloqueo en su momento) es
        rescatado por `_periodic_reroute_all`."""
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v_stale", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0,
            progress_on_edge=0.3, velocity=5.0,
        )
        # El bloqueo ya estaba en blocked_edges desde un tick anterior; no es
        # un "new_block". `_reroute_affected_by_new_blocks` no disparó.
        blocked = {(2, 3): None}
        n = _periodic_reroute_all({"v_stale": v}, graph, blocked)
        assert n == 1
        # La ruta nueva empieza manteniendo [1,2] y evita (2,3).
        assert v.route.node_path[0:2] == [1, 2]
        pairs = list(zip(v.route.node_path, v.route.node_path[1:]))
        assert (2, 3) not in pairs

    @pytest.mark.unit
    def test_periodic_reroute_is_idempotent_when_route_is_clean(self):
        """Si la ruta pendiente ya no toca ningún bloqueo, no se muta."""
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 5, 6, 4], edge_ids=[10, 20, 21, 22], length_m=50.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0,
            progress_on_edge=0.3, velocity=5.0,
        )
        original = list(v.route.node_path)
        blocked = {(2, 3): None}  # no está en la ruta del vehículo
        n = _periodic_reroute_all({"v1": v}, graph, blocked)
        assert n == 0
        assert v.route.node_path == original

    @pytest.mark.unit
    def test_periodic_reroute_noop_when_no_blocks(self):
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0, velocity=5.0,
        )
        assert _periodic_reroute_all({"v1": v}, graph, {}) == 0


class TestMaybeRerouteAroundBlocks:
    @pytest.mark.unit
    def test_reroutes_single_vehicle_via_helper(self):
        """El helper single-vehicle es el núcleo compartido de D1 y D2."""
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.MOVING, current_edge_index=0, velocity=5.0,
        )
        blocked = {(2, 3): None}
        assert _maybe_reroute_around_blocks(v, graph, blocked) is True
        pairs = list(zip(v.route.node_path, v.route.node_path[1:]))
        assert (2, 3) not in pairs

    @pytest.mark.unit
    def test_helper_does_not_touch_collision_status_vehicles(self):
        """Vehículos en COLLISION no se re-rutean — están quietos."""
        graph = _build_alt_path_graph()
        route = RouteInfo(
            start_node_id=1, end_node_id=4,
            node_path=[1, 2, 3, 4], edge_ids=[10, 11, 12], length_m=30.0,
        )
        v = SimVehicle(
            id="v1", start_node_id=1, end_node_id=4, route=route,
            status=VehicleStatus.COLLISION, current_edge_index=0, velocity=0.0,
        )
        original = list(v.route.node_path)
        assert _maybe_reroute_around_blocks(v, graph, {(2, 3): None}) is False
        assert v.route.node_path == original


class TestBlockedEdgePenaltyExcludes:
    @pytest.mark.unit
    def test_astar_prefers_clean_alternative_over_blocked_shortcut(self):
        """Plan D3: con factor 1e9, A* prefiere cualquier alternativa finita
        frente a una arista bloqueada, incluso si el coste base del bloqueo es
        mucho menor."""
        graph = _build_alt_path_graph()
        # Sin bloqueo: la ruta corta 1→2→3→4 es la ganadora (30 m).
        path_free = graph.get_shortest_path_astar(1, 4)
        assert path_free == [1, 2, 3, 4]
        # Con (2,3) bloqueada: el penalty lleva a la alternativa 2→5→6→4.
        blocked = {(2, 3): None}
        path_blocked = graph.get_shortest_path_astar(1, 4, blocked_edges=blocked)
        assert path_blocked == [1, 2, 5, 6, 4]
        assert (2, 3) not in list(zip(path_blocked, path_blocked[1:]))

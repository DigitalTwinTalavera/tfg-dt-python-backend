"""Tests de integración para la lógica de rotondas (yield + arbitración + colisión)."""

import pytest

from app.core.constants import ATTR_IS_ROUNDABOUT, ATTR_LENGTH, VEHICLE_LENGTH_M
from app.core.route import RouteInfo
from app.core.vehicle_physics import (
    _build_entry_arm_index,
    _build_ring_occupancy,
    _find_roundabout_yield_leader,
    update_vehicles,
)
from app.models.enums import VehicleStatus
from app.services.vehicle_spawner import SimVehicle
from tests.fixtures.road_network_fixtures import build_two_entry_roundabout_graph


def _make_vehicle(
    vid: str,
    node_path: list[int],
    edge_ids: list[int],
    *,
    edge_index: int = 0,
    progress: float = 0.0,
    velocity: float = 5.0,
    status: VehicleStatus = VehicleStatus.MOVING,
) -> SimVehicle:
    route = RouteInfo(
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        node_path=node_path,
        edge_ids=edge_ids,
        length_m=sum([20.0] * (len(edge_ids))),  # aproximado — no se usa en lógica
    )
    return SimVehicle(
        id=vid,
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        route=route,
        status=status,
        current_edge_index=edge_index,
        progress_on_edge=progress,
        velocity=velocity,
        desired_speed_ms=10.0,
        lane=0,
    )


def _physical_gap_m(a: SimVehicle, b: SimVehicle, graph) -> float:
    """Distancia bumper-to-bumper entre `a` y `b` si comparten la misma arista.

    Devuelve +inf si están en aristas distintas.
    """
    if a.current_edge_index != b.current_edge_index:
        return float("inf")
    np_a = a.route.node_path
    np_b = b.route.node_path
    ei = a.current_edge_index
    if np_a[ei] != np_b[ei] or np_a[ei + 1] != np_b[ei + 1]:
        return float("inf")
    attrs = graph.get_edge_attributes(np_a[ei], np_a[ei + 1])
    edge_len = float(attrs.get(ATTR_LENGTH, 1.0))
    front, rear = (a, b) if a.progress_on_edge >= b.progress_on_edge else (b, a)
    raw = (front.progress_on_edge - rear.progress_on_edge) * edge_len
    return raw - getattr(front, "length_m", VEHICLE_LENGTH_M)


@pytest.mark.unit
def test_convergent_entries_do_not_stack():
    """Dos vehículos apuntando al mismo anillo desde brazos distintos no se solapan."""
    graph = build_two_entry_roundabout_graph()

    # Rutas: A_in(1) → N_A(10) → N_B(11) → N_C(12) → A_out(20)
    route_a_nodes = [1, 10, 11, 12, 20]
    route_a_edges = [100, 200, 201, 300]
    # Rutas: B_in(2) → N_B(11) → N_C(12) → A_out(20)
    route_b_nodes = [2, 11, 12, 20]
    route_b_edges = [101, 201, 300]

    va = _make_vehicle(
        "v-A", route_a_nodes, route_a_edges,
        edge_index=0, progress=0.85, velocity=5.0,
    )
    vb = _make_vehicle(
        "v-B", route_b_nodes, route_b_edges,
        edge_index=0, progress=0.85, velocity=5.0,
    )
    vehicles = {va.id: va, vb.id: vb}
    blocked: dict = {}

    dt = 0.1
    for _ in range(10):
        update_vehicles(vehicles, graph, dt, tl_controller=None, blocked_edges=blocked)
        # Al final de cada tick, ningún par comparte arista con solapamiento físico.
        gap = _physical_gap_m(va, vb, graph)
        assert gap >= 0.5, (
            f"Solapamiento en rotonda: gap={gap:.2f} m "
            f"(A en edge {va.current_edge_index} prog={va.progress_on_edge:.3f}, "
            f"B en edge {vb.current_edge_index} prog={vb.progress_on_edge:.3f})"
        )
        # Tampoco colisión (la arbitración debe evitar llegar a ese punto).
        assert va.status != VehicleStatus.COLLISION
        assert vb.status != VehicleStatus.COLLISION


@pytest.mark.unit
def test_entry_arbitration_deterministic_tiebreak():
    """Con progreso/velocidad idénticos, el ganador del tie-break es estable por id."""
    graph = build_two_entry_roundabout_graph()
    # Ambas rutas convergen en N_A (id=10): A_in→N_A y C_in→N_A.
    route_a_nodes = [1, 10, 11, 12, 20]
    route_a_edges = [100, 200, 201, 300]
    route_c_nodes = [3, 10, 11, 12, 20]
    route_c_edges = [102, 200, 201, 300]

    def _eval(first_id: str, second_id: str) -> tuple[bool, bool]:
        v1 = _make_vehicle(
            first_id, route_a_nodes, route_a_edges,
            edge_index=0, progress=0.90, velocity=5.0,
        )
        v2 = _make_vehicle(
            second_id, route_c_nodes, route_c_edges,
            edge_index=0, progress=0.90, velocity=5.0,
        )
        pool = {v1.id: v1, v2.id: v2}
        ring_occ = _build_ring_occupancy(pool, graph)
        arms = _build_entry_arm_index(pool, graph)
        # Ambos vehículos están en el índice de brazos para el mismo entry_node.
        assert len(arms.get(10, [])) == 2
        yl1 = _find_roundabout_yield_leader(v1, graph, ring_occ, arms)
        yl2 = _find_roundabout_yield_leader(v2, graph, ring_occ, arms)
        return (yl1 is not None, yl2 is not None)

    # Primera corrida.
    y1_a, y2_a = _eval("v-001", "v-002")
    # Segunda corrida (mismos ids, mismos progresos) — mismo resultado.
    y1_b, y2_b = _eval("v-001", "v-002")
    assert (y1_a, y2_a) == (y1_b, y2_b)
    # Exactamente uno pierde (recibe yield-leader), el otro no.
    assert y1_a != y2_a, "La arbitración debe elegir exactamente un ganador"
    # El id lexicográficamente menor gana (min en `_remaining` con tie-break por id).
    assert not y1_a, "v-001 debería ganar el tie-break sobre v-002"
    assert y2_a


@pytest.mark.unit
def test_stationary_overlap_triggers_collision():
    """Dos vehículos parados solapados geométricamente en el anillo → COLLISION."""
    graph = build_two_entry_roundabout_graph()

    # Ambos sobre la arista de anillo N_A→N_B (edge_id 200), progresos 0.10/0.105.
    # Con edge_len=10 m, la separación física es 0.05 m → overlap (vehicle_length ≈ 4.5 m).
    ring_route_nodes = [10, 11, 12, 20]
    ring_route_edges = [200, 201, 300]

    front = _make_vehicle(
        "v-front", ring_route_nodes, ring_route_edges,
        edge_index=0, progress=0.105, velocity=0.0,
    )
    rear = _make_vehicle(
        "v-rear", ring_route_nodes, ring_route_edges,
        edge_index=0, progress=0.100, velocity=0.0,
    )
    vehicles = {front.id: front, rear.id: rear}
    blocked: dict = {}

    dt = 0.1
    for _ in range(8):
        update_vehicles(vehicles, graph, dt, tl_controller=None, blocked_edges=blocked)

    assert front.status == VehicleStatus.COLLISION
    assert rear.status == VehicleStatus.COLLISION
    # La arista del anillo donde ocurrió queda bloqueada permanentemente.
    assert (10, 11) in blocked
    assert blocked[(10, 11)] is None

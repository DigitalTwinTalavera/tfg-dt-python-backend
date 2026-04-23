"""Tests de integración para la lógica de rotondas (yield + arbitración + colisión)."""

import pytest

from app.core.constants import (
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LENGTH,
    MAX_EMERGENCY_DECEL_MS2,
    VEHICLE_LENGTH_M,
)
from app.core.route import RouteInfo
from app.core.vehicle_physics import (
    _advance_vehicle_idm,
    _build_edge_index,
    _build_entry_arm_index,
    _build_ring_occupancy,
    _evaluate_lane_change,
    _find_leader,
    _find_roundabout_yield_leader,
    _target_roundabout_lane,
    update_vehicles,
)
from app.models.enums import VehicleStatus
from app.services.vehicle_spawner import SimVehicle
from tests.fixtures.road_network_fixtures import (
    build_two_entry_roundabout_graph,
    build_two_lane_roundabout_graph,
)


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
        # Ambos vehículos están en el índice de brazos para el mismo entry_node
        # y el mismo carril destino (anillo de 1 carril → target=0).
        assert len(arms.get((10, 0), [])) == 2
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
    # > COLLISION_PROXIMITY_DURATION_S (1.0 s) → 12 ticks de margen.
    for _ in range(12):
        update_vehicles(vehicles, graph, dt, tl_controller=None, blocked_edges=blocked)

    assert front.status == VehicleStatus.COLLISION
    assert rear.status == VehicleStatus.COLLISION
    # La arista del anillo donde ocurrió queda bloqueada permanentemente.
    assert (10, 11) in blocked
    assert blocked[(10, 11)] is None


# ---------------------------------------------------------------------------
# Rotonda de 2 carriles — selección de carril + look-ahead intra-anillo
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_entry_lane_chosen_by_route_exit_distance():
    """El carril al entrar al anillo depende de cuántos arcos recorra el vehículo.

    - Ruta corta (sale en la PRÓXIMA salida, 1 arco de anillo) → carril exterior (0).
    - Ruta larga (recorre ≥ 2 arcos) → carril interior (n_lanes-1 = 1).
    """
    graph = build_two_lane_roundabout_graph()

    # Ruta corta: A_in(1) → N_A(10) → N_B(11) → N_C(12) → C_out(21).
    # Entra por arista del anillo 10→11, sale por 12→21: recorre 2 arcos de
    # anillo (10→11 y 11→12) → interior.
    # Ajustamos la intención: para conseguir ruta "corta" (1 arco) usamos
    # A_in(1) → N_A(10) → A_out(20), que no pasa por anillo en absoluto.
    # Versión corta REAL de 1 arco de anillo: B_in(2) → N_B(11) → N_C(12) → C_out(21).
    short_route_nodes = [2, 11, 12, 21]
    short_route_edges = [101, 201, 301]
    v_short = _make_vehicle(
        "v-short", short_route_nodes, short_route_edges,
        edge_index=0, progress=0.5, velocity=5.0,
    )
    assert _target_roundabout_lane(v_short, graph) == 0, "1 arco de anillo → exterior"

    # Ruta larga: A_in(1) → N_A(10) → N_B(11) → N_C(12) → C_out(21): 2 arcos.
    long_route_nodes = [1, 10, 11, 12, 21]
    long_route_edges = [100, 200, 201, 301]
    v_long = _make_vehicle(
        "v-long", long_route_nodes, long_route_edges,
        edge_index=0, progress=0.5, velocity=5.0,
    )
    assert _target_roundabout_lane(v_long, graph) == 1, "2 arcos de anillo → interior"


@pytest.mark.unit
def test_convergent_entries_different_target_lanes_both_enter():
    """Dos vehículos que apuntan al mismo entry_node pero a carriles distintos entran sin ceder."""
    graph = build_two_lane_roundabout_graph()

    # vA: B_in(2) → N_B(11) → N_C(12) → C_out(21). 1 arco (11→12) → lane=0.
    route_a_nodes = [2, 11, 12, 21]
    route_a_edges = [101, 201, 301]
    # vB: B_in(2) → N_B(11) → N_C(12) → N_A(10) → A_out(20). 2 arcos → lane=1.
    # Pero entrando por el mismo nodo N_B, son contendientes del mismo entry.
    route_b_nodes = [2, 11, 12, 10, 20]
    route_b_edges = [101, 201, 202, 300]

    va = _make_vehicle(
        "v-A", route_a_nodes, route_a_edges,
        edge_index=0, progress=0.90, velocity=5.0,
    )
    vb = _make_vehicle(
        "v-B", route_b_nodes, route_b_edges,
        edge_index=0, progress=0.90, velocity=5.0,
    )

    pool = {va.id: va, vb.id: vb}
    ring_occ = _build_ring_occupancy(pool, graph)
    arms = _build_entry_arm_index(pool, graph)

    # Cada uno apunta a su propio carril destino → índice segmentado.
    assert _target_roundabout_lane(va, graph) == 0
    assert _target_roundabout_lane(vb, graph) == 1
    assert len(arms.get((11, 0), [])) == 1
    assert len(arms.get((11, 1), [])) == 1

    # Ninguno recibe líder virtual (yield): van a carriles distintos.
    assert _find_roundabout_yield_leader(va, graph, ring_occ, arms) is None
    assert _find_roundabout_yield_leader(vb, graph, ring_occ, arms) is None


@pytest.mark.unit
def test_same_ring_lookahead_prevents_rear_end():
    """Coche rápido en arco A alcanza al lento del arco B del mismo anillo, mismo carril → frena."""
    graph = build_two_lane_roundabout_graph()

    # Ambos vehículos con ruta larga → carril interior (lane=1).
    route_nodes = [2, 11, 12, 10, 20]
    route_edges = [101, 201, 202, 300]

    # Vehículo de atrás: en arco 11→12 (edge_index=1), casi saliendo, velocidad 8 m/s.
    rear = _make_vehicle(
        "v-rear", route_nodes, route_edges,
        edge_index=1, progress=0.90, velocity=8.0,
    )
    rear.lane = 1
    # Vehículo delantero: en arco 12→10 (edge_index=2), progress bajo, velocidad 2 m/s.
    front = _make_vehicle(
        "v-front", route_nodes, route_edges,
        edge_index=2, progress=0.10, velocity=2.0,
    )
    front.lane = 1

    vehicles = {rear.id: rear, front.id: front}
    blocked: dict = {}

    dt = 0.1
    for _ in range(5):
        update_vehicles(vehicles, graph, dt, tl_controller=None, blocked_edges=blocked)

    # Nadie ha colisionado: el IDM vio al líder del arco siguiente y frenó.
    assert rear.status != VehicleStatus.COLLISION
    assert front.status != VehicleStatus.COLLISION
    # El de atrás se ha frenado sensiblemente (< 5 m/s tras 0.5 s persiguiendo).
    assert rear.velocity < 5.0, f"rear.velocity={rear.velocity:.2f} — look-ahead no frenó a tiempo"


# ---------------------------------------------------------------------------
# Entradas a rotonda — MOBIL inhibido, look-ahead extendido y emergency brake
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mobil_inhibited_on_entry_to_ring():
    """MOBIL no cambia de carril cuando la siguiente arista es rotonda.

    Disciplina la cola ante la línea de ceda-el-paso: un cambio aquí causa
    alcances en el nuevo carril. Se muta la arista de entrada a 2 carriles y
    40 m para que MOBIL tenga margen físico; luego se verifica que el gate
    nuevo bloquea el cambio aunque haya incentivo claro.
    """
    graph = build_two_lane_roundabout_graph()
    g = graph.graph
    # Alargar entrada B_in→N_B (edge 101) a 40 m y 2 carriles.
    g[2][11][ATTR_LENGTH] = 40.0
    g[2][11][ATTR_LANES] = 2

    # Ruta larga: recorre 2 arcos del anillo (target_lane = 1).
    route_nodes = [2, 11, 12, 10, 20]
    route_edges = [101, 201, 202, 300]

    ego = _make_vehicle(
        "v-ego", route_nodes, route_edges,
        edge_index=0, progress=0.20, velocity=8.0,
    )
    ego.lane = 0
    # Vehículo lento delante en lane=0 → incentivo MOBIL a pasarse a lane=1.
    slow = _make_vehicle(
        "v-slow", route_nodes, route_edges,
        edge_index=0, progress=0.50, velocity=1.0,
    )
    slow.lane = 0

    pool = {ego.id: ego, slow.id: slow}
    edge_index = _build_edge_index(pool)
    leader = _find_leader(ego, edge_index, graph)

    # MOBIL evaluaría cambio (lane 1 libre, lane 0 con coche lento delante),
    # pero el gate de entry-to-ring debe dejar `ego.lane` inalterado.
    _evaluate_lane_change(ego, edge_index, graph, leader)
    assert ego.lane == 0, (
        "MOBIL no debería cambiar de carril en aristas de aproximación a rotonda"
    )


@pytest.mark.unit
def test_entry_lookahead_sees_stopped_ring_leader_at_25m():
    """El look-ahead extendido (30 m) ve al líder parado en el anillo siguiente.

    Ego a 25 m de la línea de entrada: bajo el umbral antiguo (8 m) no veía
    al coche parado en el anillo siguiente → alcanzaba. Con ventana de 30 m
    lo ve y decelera.
    """
    graph = build_two_lane_roundabout_graph()
    g = graph.graph
    # Alargar entrada B_in→N_B (edge 101) a 40 m para poder estar a 25 m del final.
    g[2][11][ATTR_LENGTH] = 40.0

    # Ego en entrada B_in→N_B a 25 m del nodo de entrada N_B.
    # progress = 1 - 25/40 = 0.375.
    route_ego_nodes = [2, 11, 12, 21]
    route_ego_edges = [101, 201, 301]
    ego = _make_vehicle(
        "v-ego", route_ego_nodes, route_ego_edges,
        edge_index=0, progress=0.375, velocity=13.0,
    )
    ego.lane = 0

    # Leader parado en el PRIMER arco del anillo N_B→N_C (edge 201) a progress=0.05.
    # Con el filtro de carril destino en entry, el ego a lane_target=0 (ruta corta
    # de 1 arco 11→12) solo ve candidatos en lane=0.
    leader_veh = _make_vehicle(
        "v-block", route_ego_nodes, route_ego_edges,
        edge_index=1, progress=0.05, velocity=0.0,
    )
    leader_veh.lane = 0

    pool = {ego.id: ego, leader_veh.id: leader_veh}
    edge_index = _build_edge_index(pool)

    leader_info = _find_leader(ego, edge_index, graph)
    assert leader_info is not None, (
        "Look-ahead de entrada debería detectar al líder parado en el anillo"
    )
    assert leader_info.leader_id == "v-block"
    assert leader_info.velocity_ms == 0.0


@pytest.mark.unit
def test_emergency_brake_close_stopped_leader():
    """Con líder parado y gap<5 m, `_advance_vehicle_idm` fuerza freno máximo."""
    from app.core.vehicle_physics import NeighborInfo

    graph = build_two_lane_roundabout_graph()
    g = graph.graph
    # Pista recta de 50 m para que el gap de 3 m sea geométricamente posible.
    g[2][11][ATTR_LENGTH] = 50.0

    route_nodes = [2, 11, 12, 21]
    route_edges = [101, 201, 301]
    ego = _make_vehicle(
        "v-ego", route_nodes, route_edges,
        edge_index=0, progress=0.30, velocity=8.0,
    )
    leader_info = NeighborInfo(gap_m=3.0, velocity_ms=0.0, leader_id="v-block")
    _advance_vehicle_idm(ego, graph, dt=0.1, leader=leader_info)
    assert ego.acceleration == pytest.approx(-MAX_EMERGENCY_DECEL_MS2, abs=1e-6), (
        f"Emergency brake debería aplicarse: a={ego.acceleration:.2f}"
    )

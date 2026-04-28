"""
Detección de semáforos y señales de tráfico (STOP / YIELD).

Funciones que generan un *líder virtual* en la línea de stop cuando un
vehículo se aproxima a un cruce con fase roja/amarilla bloqueante o a una
señal de STOP/YIELD activa. El líder virtual lo consume el IDM como si
fuera un vehículo parado, produciendo el frenado natural sin lógica
especial fuera del modelo de car-following.

Tres fuentes de TL en orden de cercanía:
  1. TLs *mid-way* en la arista actual (nodos TRAFFIC_LIGHT que están
     sobre la geometría del edge pero no son endpoints del DiGraph).
  2. El `end_node` de la arista actual si es un TL.
  3. TLs mid-way en la siguiente arista + su `end_node` (look-ahead para
     aristas cortas donde el IDM no tendría margen para frenar).
"""

from __future__ import annotations

from app.core.constants import (
    ATTR_LENGTH,
    ATTR_MID_TLS,
    ATTR_NODE_TYPE,
    MIN_EDGE_LENGTH_M,
    SIGN_DETECTION_ZONE_M,
    STOP_SIGN_DWELL_SPEED_MS,
    STOP_SIGN_DWELL_TIME_S,
    TL_PHASE_RED,
    TL_PHASE_YELLOW,
    VEHICLE_LENGTH_M,
    YELLOW_BRAKE_DISTANCE_M,
    YIELD_SIGN_GAP_MIN_M,
    YIELD_SIGN_TTC_S,
)
from app.core.physics.neighbor import NeighborInfo
from app.models.enums import NodeType
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle


def _phase_blocks(phase: str, vehicle: SimVehicle, dist_to_stop: float) -> bool:
    """¿Esta fase obliga a parar al vehículo en la línea de stop?

    Rojo siempre bloquea. Amarillo bloquea si el vehículo no es `yellow_runs_light`
    Y está lo suficientemente lejos para frenar con seguridad.
    """
    if phase == TL_PHASE_RED:
        return True
    if phase == TL_PHASE_YELLOW:
        if dist_to_stop < YELLOW_BRAKE_DISTANCE_M:
            return False
        return not getattr(vehicle, "yellow_runs_light", False)
    return False


def _check_traffic_light(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    tl_controller: object,
) -> NeighborInfo | None:
    """
    Genera un líder virtual en la línea de stop del TL más cercano que bloquee.

    Se comprueban tres fuentes de TL, en orden de cercanía al vehículo:

      1. TLs *mid-way* en la arista actual (nodos TRAFFIC_LIGHT que están sobre
         la geometría del edge pero no son endpoints del DiGraph — caso
         dominante en OSM, donde un way se vuelve una sola arista first→last).
      2. El `end_node` de la arista actual si es un TL.
      3. TLs mid-way en la siguiente arista + su `end_node` (look-ahead para
         aristas cortas donde el IDM no tendría margen para frenar).

    La fase se consulta por arista cuando hay info (`get_phase_for_edge`) para
    que dos brazos del mismo cruce vean fases opuestas; los TLs mid-way usan
    `get_phase` (fase del grupo 0) porque no tienen agrupación por eje.

    Rojo siempre frena. Amarillo frena si el vehículo no es `yellow_runs_light`
    y la línea de stop está a >= YELLOW_BRAKE_DISTANCE_M.
    """
    node_path = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(node_path) - 1:
        return None

    start_node = node_path[ei]
    end_node = node_path[ei + 1]
    edge_attrs = graph.get_edge_attributes(start_node, end_node)
    edge_len = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
    pos_on_edge_m = edge_len * vehicle.progress_on_edge
    dist_to_end = edge_len - pos_on_edge_m

    # 1) TLs mid-way en la arista actual (ordenados por distancia ascendente
    # desde start_node). El primero con fase bloqueante que esté por delante
    # del vehículo define la línea de stop.
    mid_tls: list[tuple[int, float]] = edge_attrs.get(ATTR_MID_TLS, []) or []
    for tl_nid, dist_from_start in mid_tls:
        if dist_from_start <= pos_on_edge_m:
            continue  # ya lo pasó
        dist_to_tl = dist_from_start - pos_on_edge_m
        phase = tl_controller.get_phase(tl_nid)  # type: ignore[union-attr]
        if _phase_blocks(phase, vehicle, dist_to_tl):
            return NeighborInfo(gap_m=max(dist_to_tl - VEHICLE_LENGTH_M, 0.01), velocity_ms=0.0)

    # 2) TL en el end_node (cruce clásico): consulta por arista entrante.
    end_phase = tl_controller.get_phase_for_edge(end_node, (start_node, end_node))  # type: ignore[union-attr]
    if _phase_blocks(end_phase, vehicle, dist_to_end):
        return NeighborInfo(gap_m=max(dist_to_end - VEHICLE_LENGTH_M, 0.01), velocity_ms=0.0)

    # 3) Look-ahead a la arista siguiente (mid-TLs + end_node).
    if ei + 2 < len(node_path):
        next_end = node_path[ei + 2]
        next_attrs = graph.get_edge_attributes(end_node, next_end)
        next_len = max(float(next_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)

        next_mid_tls: list[tuple[int, float]] = next_attrs.get(ATTR_MID_TLS, []) or []
        for tl_nid, dist_from_start in next_mid_tls:
            total_dist = dist_to_end + dist_from_start
            phase = tl_controller.get_phase(tl_nid)  # type: ignore[union-attr]
            if _phase_blocks(phase, vehicle, total_dist):
                return NeighborInfo(gap_m=max(total_dist - VEHICLE_LENGTH_M, 0.01), velocity_ms=0.0)

        next_end_phase = tl_controller.get_phase_for_edge(next_end, (end_node, next_end))  # type: ignore[union-attr]
        total_dist = dist_to_end + next_len
        if _phase_blocks(next_end_phase, vehicle, total_dist):
            return NeighborInfo(gap_m=max(total_dist - VEHICLE_LENGTH_M, 0.01), velocity_ms=0.0)

    return None


def _node_type_of(graph: RoadNetworkGraph, node_id: int) -> str | None:
    attrs = graph.get_node_attributes(node_id)
    raw = attrs.get(ATTR_NODE_TYPE)
    if raw is None:
        return None
    if isinstance(raw, NodeType):
        return raw.value
    return str(raw)


def _check_stop_yield_sign(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    edge_index: "dict[tuple[int, int], dict[int, list[SimVehicle]]]",
    dt: float,
) -> NeighborInfo | None:
    """Genera un líder virtual ante STOP / YIELD en el end_node de la arista.

    STOP:
      - Mientras el vehículo no haya dwelled (velocidad < STOP_SIGN_DWELL_SPEED_MS
        durante STOP_SIGN_DWELL_TIME_S) frente a este nodo, genera un líder
        estático en la línea de stop (v=0).
      - Una vez cumplido el dwell (`stop_sign_cleared_node == end_node`),
        libera el paso para este nodo. Se resetea al avanzar a otra arista.

    YIELD:
      - Líder virtual solo si se detecta un vehículo convergiendo por otra
        rama al mismo nodo con TTC < YIELD_SIGN_TTC_S o gap < YIELD_SIGN_GAP_MIN_M.
      - No exige parada; si la intersección está libre, pasa sin frenar.
    """
    node_path = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(node_path) - 1:
        return None

    start_node = node_path[ei]
    end_node = node_path[ei + 1]

    # Reset del flag de STOP cumplido cuando cambiamos de arista objetivo.
    if vehicle.stop_sign_cleared_node != -1 and vehicle.stop_sign_cleared_node != end_node:
        vehicle.stop_sign_cleared_node = -1
        vehicle.stop_sign_dwell_timer = 0.0

    node_kind = _node_type_of(graph, end_node)
    if node_kind not in (NodeType.STOP_SIGN.value, NodeType.YIELD_SIGN.value):
        return None

    edge_attrs = graph.get_edge_attributes(start_node, end_node)
    edge_len = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
    dist_to_sign = edge_len * (1.0 - vehicle.progress_on_edge)
    if dist_to_sign > SIGN_DETECTION_ZONE_M:
        return None

    if node_kind == NodeType.STOP_SIGN.value:
        # Dwell tracking: acumulamos tiempo con velocidad muy baja cerca del nodo.
        if dist_to_sign < 2.0 and vehicle.velocity < STOP_SIGN_DWELL_SPEED_MS:
            vehicle.stop_sign_dwell_timer += dt
            if vehicle.stop_sign_dwell_timer >= STOP_SIGN_DWELL_TIME_S:
                vehicle.stop_sign_cleared_node = end_node
        if vehicle.stop_sign_cleared_node == end_node:
            return None  # ya paró
        gap = max(dist_to_sign - VEHICLE_LENGTH_M, 0.01)
        return NeighborInfo(gap_m=gap, velocity_ms=0.0)

    # YIELD: ceder a tráfico que converge al mismo nodo por otra arista.
    for (u, w), lanes_dict in edge_index.items():
        if w != end_node or (u == start_node and w == end_node):
            continue
        other_attrs = graph.get_edge_attributes(u, w)
        other_len = max(float(other_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
        for lane_list in lanes_dict.values():
            for other in lane_list:
                if other.id == vehicle.id:
                    continue
                dist_other_to_node = other_len * (1.0 - other.progress_on_edge)
                ttc = dist_other_to_node / max(other.velocity, 1.0)
                if ttc < YIELD_SIGN_TTC_S or dist_other_to_node < YIELD_SIGN_GAP_MIN_M:
                    gap = max(dist_to_sign - VEHICLE_LENGTH_M, 0.2)
                    return NeighborInfo(gap_m=gap, velocity_ms=0.0)

    return None

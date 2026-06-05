"""
Física de movimiento de vehículos a lo largo de sus rutas.

Modelo de car-following: Intelligent Driver Model (IDM).
  - Cada vehículo tiene una velocidad deseada individual (desired_speed_ms).
  - La aceleración viene del IDM: libre en carretera vacía, frenado suave
    detrás de un líder, frenado de emergencia ante un semáforo en rojo.
  - El avance es por distancia (v·dt) en lugar de tiempo constante.

Detección de líder:
  - Se construye un índice {(start_node, end_node): [vehicles sorted by progress desc]}.
  - Para cada vehículo, el líder es el primero con mayor progreso en la misma arista.

Restricción de semáforos:
  - Si el nodo final de la arista actual está en rojo, se genera un "líder virtual"
    estacionado en la línea de stop (distancia = longitud_restante - vehicle_length).

Los vehículos siguen los waypoints reales de la geometría de la calzada
(LineString de PostGIS) en lugar de interpolar en línea recta entre nodos.

Fórmula de heading (bearing de brújula):
    heading = 0  → Norte  (lat aumenta, lon constante)
    heading = 90 → Este   (lon aumenta, lat constante)
    heading = atan2(Δlon, Δlat) * 180 / π  (mod 360)
"""

from __future__ import annotations

import asyncio
import bisect
import concurrent.futures
import logging
import math
import os
import threading
import time
from app.core.constants import (
    ATTR_CURVE_VMAX,
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_MID_TLS,
    ATTR_RING_RADIUS_M,
    ATTR_ROUNDABOUT_ID,
    ATTR_SPLINE_LENGTH,
    ATTR_SPLINE_SAMPLES,
    ATTR_USE_SPLINE,
    ATTR_WAYPOINTS,
    COLLISION_GAP_THRESHOLD_ROUNDABOUT_M,
    COLLISION_GAP_THRESHOLD_STRAIGHT_M,
    COLLISION_PROXIMITY_DURATION_S,
    COLLISION_RELATIVE_SPEED_MIN_MS,
    CURVE_LATERAL_ACCEL_MAX_MS2,
    DEFAULT_VEHICLE_SPEED_KMH,
    EDGE_HEADING_BLEND_DIST_M,
    EDGE_HEADING_BLEND_DIST_ROUNDABOUT_EXIT_M,
    EMERGENCY_BRAKE_EGO_V_DELTA_MS,
    EMERGENCY_BRAKE_GAP_MAX_M,
    EMERGENCY_BRAKE_LEADER_V_MAX_MS,
    ENTRY_ARBITRATION_ZONE_M,
    ATTR_NODE_TYPE,
    HARD_CLAMP_MARGIN_M,
    INTERSECTION_DETECTION_ZONE_M,
    KMH_TO_MS,
    LOOKAHEAD_ENTRY_TRIGGER_M,
    LOOKAHEAD_NON_ROUND_TRIGGER_M,
    LOOKAHEAD_ROUNDABOUT_TRIGGER_M,
    MAX_EMERGENCY_DECEL_MS2,
    MIN_EDGE_LENGTH_M,
    MIN_ROUNDABOUT_RADIUS_M,
    MOBIL_EVAL_INTERVAL_TICKS,
    MOBIL_TRAPPED_EVAL_INTERVAL_TICKS,
    MOBIL_MIN_VELOCITY_MS,
    MOBIL_MIN_DIST_TO_EDGE_END_M,
    TL_PHASE_GREEN,
    TL_PHASE_RED,
    TL_PHASE_YELLOW,
    VEHICLE_LENGTH_M,
    VEHICLE_PHYSICS_PARALLEL_THRESHOLD,
    YIELD_DETECTION_ZONE_M,
    RING_EXIT_MIN_GAP_M,
    RING_EXIT_MIN_LEADER_V_MS,
    YIELD_GAP_MIN_M,
    YIELD_TTC_THRESHOLD_S,
)
from app.core.instrumentation import SplitTimer, registry, time_block
from app.core.physics.idm import IDMModel
from app.core.physics.mobil import (
    LaneChangeDirection,
    LaneContext,
    MOBILModel,
)
from app.core.physics.vehicle_types import PROFILES, VehicleType
from app.core.spline import position_at_arc_length
from app.models.enums import NodeType, VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M: float = 6_371_000.0

# Un IDMModel por tipo de vehículo. Stateless tras construcción → seguro para
# acceso concurrente desde múltiples hilos. Se indexa por VehicleType para
# evitar la construcción repetida en el hot-path del tick.
_IDM_BY_TYPE: dict[VehicleType, IDMModel] = {
    vtype: IDMModel(profile.idm) for vtype, profile in PROFILES.items()
}

# MOBILModel por tipo de vehículo (usa los mismos IDMParameters que el IDM).
# Stateless tras construcción → seguro para acceso concurrente desde hilos.
_MOBIL_BY_TYPE: dict[VehicleType, MOBILModel] = {
    vtype: MOBILModel(idm_params=profile.idm) for vtype, profile in PROFILES.items()
}


def _mobil_for(vehicle: SimVehicle) -> MOBILModel:
    """Devuelve el MOBILModel correspondiente al tipo del vehículo."""
    return _MOBIL_BY_TYPE[vehicle.vtype]


def _idm_for(vehicle: SimVehicle) -> IDMModel:
    """Devuelve el IDMModel correspondiente al tipo del vehículo."""
    return _IDM_BY_TYPE[vehicle.vtype]

# Caché de longitudes de segmentos por arista (start_node, end_node).
# Pre-poblada en `prewarm_segment_cache` al cargar el grafo: durante el tick
# los workers solo leen este dict, evitando races bajo free-threading en el
# resize que provocaría una escritura concurrente.
_SEG_CACHE: dict[tuple[int, int], tuple[list[float], float]] = {}


def prewarm_segment_cache(graph: RoadNetworkGraph) -> int:
    """
    Pre-popula ``_SEG_CACHE`` con los lengths por segmento de cada arista
    con waypoints. Idempotente: re-llamarlo sobre el mismo grafo no escribe.

    Devuelve el número de entradas añadidas.

    Llamado desde ``RoadNetworkGraph.build_from_database`` tras cargar las
    aristas. Sin este pre-warm, dos workers podrían escribir simultáneamente
    en ``_SEG_CACHE`` durante el primer tick post-load — bajo free-threading
    eso puede corromper la estructura interna del dict en un resize.
    """
    added = 0
    for u, v, attrs in graph.graph.edges(data=True):
        waypoints = attrs.get(ATTR_WAYPOINTS)
        if not waypoints or len(waypoints) < 2:
            continue
        key = (u, v)
        if key not in _SEG_CACHE:
            _SEG_CACHE[key] = _waypoint_segments(waypoints)
            added += 1
    return added


# ---------------------------------------------------------------------------
# Neighbor info
# ---------------------------------------------------------------------------

# Compartido con `app.core.physics.traffic_signs` y otros módulos de física.
from app.core.physics.neighbor import NeighborInfo  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine_approx(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """
    Fast approximation of great-circle distance in metres.

    Uses the equirectangular projection (valid for short distances, < ~100 km).
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat   = lat2_r - lat1_r
    dlon   = math.radians(lon2 - lon1) * math.cos((lat1_r + lat2_r) * 0.5)
    return _EARTH_RADIUS_M * math.hypot(dlon, dlat)


def _waypoint_segments(
    waypoints: list[tuple[float, float]],
) -> tuple[list[float], float]:
    """
    Compute per-segment lengths and total length for a waypoint list.

    Args:
        waypoints: List of (lon, lat) pairs.

    Returns:
        (segment_lengths, total_length) where segment_lengths[i] is the
        distance between waypoints[i] and waypoints[i+1].
    """
    lengths: list[float] = []
    total: float = 0.0
    for i in range(len(waypoints) - 1):
        d = _haversine_approx(
            waypoints[i][0], waypoints[i][1],
            waypoints[i + 1][0], waypoints[i + 1][1],
        )
        lengths.append(d)
        total += d
    return lengths, total


def _blend_heading_deg(from_deg: float, to_deg: float, t: float) -> float:
    """Interpolación circular entre dos headings en grados (resultado en [0, 360))."""
    diff = ((to_deg - from_deg + 540.0) % 360.0) - 180.0
    return (from_deg + diff * t) % 360.0


def _position_along_waypoints(
    waypoints: list[tuple[float, float]],
    progress: float,
    _cache_key: tuple[int, int] | None = None,
) -> tuple[float, float, float]:
    """
    Compute the interpolated (lon, lat, heading) at fractional progress [0, 1]
    along a list of waypoints.

    Args:
        waypoints:   List of (lon, lat) pairs (at least 2).
        progress:    Value in [0, 1] representing position along the route.
        _cache_key:  Optional (start_node, end_node) key for the segment-length
                     cache.  When provided, haversine distances are computed only
                     on the first call for that edge and reused on all subsequent
                     ticks (edge geometry is immutable).

    Returns:
        (longitude, latitude, heading_degrees)
    """
    if len(waypoints) < 2:
        lon, lat = waypoints[0] if waypoints else (0.0, 0.0)
        return lon, lat, 0.0

    if _cache_key is not None:
        if _cache_key not in _SEG_CACHE:
            _SEG_CACHE[_cache_key] = _waypoint_segments(waypoints)
        seg_lengths, total_length = _SEG_CACHE[_cache_key]
    else:
        seg_lengths, total_length = _waypoint_segments(waypoints)

    if total_length < 1e-6:
        lon, lat = waypoints[-1]
        return lon, lat, 0.0

    target_dist = progress * total_length
    accumulated: float = 0.0

    for i, seg_len in enumerate(seg_lengths):
        if accumulated + seg_len >= target_dist or i == len(seg_lengths) - 1:
            local_t = (target_dist - accumulated) / seg_len if seg_len > 1e-6 else 1.0
            local_t = max(0.0, min(1.0, local_t))

            lon1, lat1 = waypoints[i]
            lon2, lat2 = waypoints[i + 1]

            lon = lon1 + local_t * (lon2 - lon1)
            lat = lat1 + local_t * (lat2 - lat1)

            dlon = lon2 - lon1
            dlat = lat2 - lat1
            heading = math.degrees(math.atan2(dlon, dlat)) % 360.0

            return lon, lat, heading
        accumulated += seg_len

    lon, lat = waypoints[-1]
    lon_prev, lat_prev = waypoints[-2]
    heading = math.degrees(math.atan2(lon - lon_prev, lat - lat_prev)) % 360.0
    return lon, lat, heading


def _get_waypoints(
    edge_attrs: dict,
    start_node_attrs: dict,
    end_node_attrs: dict,
) -> list[tuple[float, float]]:
    """
    Get the waypoints for an edge.

    Falls back to a two-point straight line between start/end nodes if the
    edge has no geometry stored.
    """
    waypoints: list[tuple[float, float]] = edge_attrs.get(ATTR_WAYPOINTS, [])
    if len(waypoints) >= 2:
        return waypoints
    start_lon = float(start_node_attrs.get(ATTR_LONGITUDE, 0.0))
    start_lat = float(start_node_attrs.get(ATTR_LATITUDE,  0.0))
    end_lon   = float(end_node_attrs.get(ATTR_LONGITUDE,   0.0))
    end_lat   = float(end_node_attrs.get(ATTR_LATITUDE,    0.0))
    return [(start_lon, start_lat), (end_lon, end_lat)]


def _edge_position(
    edge_attrs: dict,
    progress: float,
    waypoints: list[tuple[float, float]],
    _cache_key: tuple[int, int] | None = None,
) -> tuple[float, float, float]:
    """
    Devuelve (lon, lat, heading) en una arista a `progress` ∈ [0, 1].

    Si la arista trae una tabla de muestreo de spline (rotondas tras F2 en
    network_graph), interpolamos en arc-length sobre la curva. En caso
    contrario, lerp lineal entre waypoints (path histórico).
    """
    if edge_attrs.get(ATTR_USE_SPLINE):
        table = edge_attrs.get(ATTR_SPLINE_SAMPLES)
        spline_length = float(edge_attrs.get(ATTR_SPLINE_LENGTH, 0.0))
        if table and spline_length > 0.0:
            s_target = max(0.0, min(1.0, progress)) * spline_length
            return position_at_arc_length(table, s_target)
    return _position_along_waypoints(waypoints, progress, _cache_key=_cache_key)


# ---------------------------------------------------------------------------
# Leader detection
# ---------------------------------------------------------------------------

## Tipo del índice por edge → lane → list[vehicles] (DESC por progreso).
## Permite que `_find_leader` y MOBIL salten directos al carril que les interesa
## sin escanear vecinos de otros carriles. Crítico con 4000 veh: evita el peor
## caso O(N_edge) cuando todos los candidatos por delante están en otro carril.
EdgeLaneIndex = "dict[tuple[int, int], dict[int, list[SimVehicle]]]"


def _build_edge_index(
    vehicles: dict[str, SimVehicle],
) -> "dict[tuple[int, int], dict[int, list[SimVehicle]]]":
    """
    Índice {(start_node, end_node): {lane: [veh ASC por progress]}}.

    Listas ordenadas ASC permiten dos optimizaciones en `_find_leader`:
      - Same-lane: `bisect_right(lane_list, ego.progress, key=progress)` da el
        primer veh con progress > ego en O(log k); ese es el líder real (el
        más cercano por delante).
      - Lookahead next-edge sin ring: el primer elemento de cada lane es el
        de menor progress (más cerca del inicio del edge → el que más molesta
        al ego que llega). Iterar `lane_list[0]` para cada lane es O(n_lanes)
        en vez de O(N_next_edge).

    Solo incluye vehículos activos (no FINISHED).
    """
    index: dict[tuple[int, int], dict[int, list[SimVehicle]]] = {}
    for v in vehicles.values():
        if v.status == VehicleStatus.FINISHED:
            continue
        node_path = v.route.node_path
        ei = v.current_edge_index
        if ei < len(node_path) - 1:
            key = (node_path[ei], node_path[ei + 1])
            lane = getattr(v, "lane", 0)
            index.setdefault(key, {}).setdefault(lane, []).append(v)
    for lanes_dict in index.values():
        for lane_list in lanes_dict.values():
            lane_list.sort(key=_vehicle_progress)
    return index


def _vehicle_progress(v: SimVehicle) -> float:
    """Key estable para sort/bisect por progress_on_edge."""
    return v.progress_on_edge


def _build_converging_edges_index(
    edge_index: "dict[tuple[int, int], dict[int, list[SimVehicle]]]",
) -> dict[int, list[tuple[int, int]]]:
    """
    {end_node: [(u, end_node) con vehículos activos]}.

    `_check_stop_yield_sign` YIELD necesita iterar las aristas que convergen
    al mismo nodo. Sin este índice, cada veh con YIELD escanea TODO el
    `edge_index` — O(E×N) cross-veh. Con el índice, O(deg_in(end_node)),
    típicamente 2-4 aristas en un cruce urbano.
    """
    out: dict[int, list[tuple[int, int]]] = {}
    for (u, w) in edge_index.keys():
        out.setdefault(w, []).append((u, w))
    return out


def _find_leader(
    vehicle: SimVehicle,
    edge_index: "dict[tuple[int, int], dict[int, list[SimVehicle]]]",
    graph: RoadNetworkGraph,
) -> NeighborInfo | None:
    """
    Busca el vehículo más cercano por delante en la misma arista y carril.

    Returns:
        NeighborInfo con el gap bumper-to-bumper y la velocidad del líder,
        o None si la arista/carril están libres.
    """
    node_path = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(node_path) - 1:
        return None

    key = (node_path[ei], node_path[ei + 1])
    edge_attrs = graph.get_edge_attributes(node_path[ei], node_path[ei + 1])
    edge_len = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)

    ego_lane = getattr(vehicle, "lane", 0)
    same_lane_list = edge_index.get(key, {}).get(ego_lane, [])
    if same_lane_list:
        # Lista ASC: el primer índice con progress > ego.progress es el líder
        # más cercano por delante. Si coincide con ego, mirar al siguiente
        # (el ego está incluido en la lista).
        idx = bisect.bisect_right(
            same_lane_list, vehicle.progress_on_edge, key=_vehicle_progress
        )
        if idx < len(same_lane_list) and same_lane_list[idx].id == vehicle.id:
            idx += 1
        if idx < len(same_lane_list):
            candidate = same_lane_list[idx]
            cand_len = getattr(candidate, "length_m", VEHICLE_LENGTH_M)
            raw_gap = (candidate.progress_on_edge - vehicle.progress_on_edge) * edge_len - cand_len
            if raw_gap <= 0.0:
                return NeighborInfo(gap_m=0.01, velocity_ms=0.0, leader_id=candidate.id)
            return NeighborInfo(gap_m=raw_gap, velocity_ms=candidate.velocity, leader_id=candidate.id)

    # ── Look-ahead al inicio de la siguiente arista ────────────────────────────
    # Gate fraccional (>70%) + gate por distancia absoluta cuando la actual o la
    # siguiente son rotonda: en arcos cortos (15 m) un tick puede saltar 65%→105%
    # sin disparar el gate del 70%, dejando que un coche rápido alcance al lento
    # del arco siguiente sin verlo. Se dispara tanto al entrar al anillo como
    # dentro del propio anillo (dos arcos consecutivos del mismo rid). En
    # entry-to-ring (cur NO anillo, next SÍ) se usa ventana más ancha porque
    # el ego puede tener que frenar desde velocidad de crucero ante cola en
    # el anillo siguiente (v²/2b ≈ 34 m con b=2.5).
    if ei + 1 < len(node_path) - 1:
        next_attrs = graph.get_edge_attributes(node_path[ei + 1], node_path[ei + 2])
        remaining_current_m = (1.0 - vehicle.progress_on_edge) * edge_len
        cur_is_roundabout = bool(edge_attrs.get(ATTR_IS_ROUNDABOUT))
        next_is_roundabout = bool(next_attrs.get(ATTR_IS_ROUNDABOUT))
        is_entry = next_is_roundabout and not cur_is_roundabout
        if cur_is_roundabout or next_is_roundabout:
            threshold = (
                LOOKAHEAD_ENTRY_TRIGGER_M if is_entry else LOOKAHEAD_ROUNDABOUT_TRIGGER_M
            )
            distance_gate = remaining_current_m < threshold
        else:
            # Transición entre aristas planas: dispara también cuando la
            # distancia restante es pequeña, no sólo a partir del 70% del
            # progreso. Sin esto, en una arista de 30 m con un coche en
            # progress=0.65, el lookahead nunca dispara y el ego puede plantar
            # cara a un líder que aparece de golpe en la siguiente arista
            # (Fix A clampea el avance, pero el frenado del IDM se aplica
            # antes con datos reales si hacemos lookahead aquí).
            distance_gate = remaining_current_m < LOOKAHEAD_NON_ROUND_TRIGGER_M
        # Salida de anillo (cur=ring, next=non-ring): la cola del exit edge no
        # debe llevar al ego a v=0 dentro del ring (principio: ceder fuera del
        # ring, nunca dentro). Hacemos lookahead normalmente, pero clampamos el
        # gap_m y el velocity_ms del líder para que el IDM frene de forma
        # gradual sin alcanzar v=0; al cruzar al exit, el `_find_leader` normal
        # toma el relevo con datos reales y para correctamente fuera del ring.
        is_ring_exit = cur_is_roundabout and not next_is_roundabout
        if vehicle.progress_on_edge > 0.70 or distance_gate:
            next_key = (node_path[ei + 1], node_path[ei + 2])
            next_edge_len = max(float(next_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
            next_lanes_dict = edge_index.get(next_key, {})
            # Dentro del mismo anillo los carriles están geométricamente alineados
            # entre arcos: un coche en lane=0 del arco A solo es líder de los
            # que vienen detrás en lane=0; los de lane=1 no le afectan. Fuera
            # de esta condición mantenemos el comportamiento conservador de
            # ignorar el carril (entrada a rotonda, cambio a calle normal…)
            # porque el lane puede reasignarse en la transición.
            same_ring = (
                cur_is_roundabout
                and next_is_roundabout
                and edge_attrs.get(ATTR_ROUNDABOUT_ID) == next_attrs.get(ATTR_ROUNDABOUT_ID)
                and edge_attrs.get(ATTR_ROUNDABOUT_ID) is not None
            )
            first_on_next: "SimVehicle | None" = None
            if same_ring:
                lst = next_lanes_dict.get(ego_lane)
                if lst:
                    first_on_next = lst[0]
            elif is_entry:
                # El ego entrará en un carril determinista según su ruta; los
                # circulantes del anillo en otro carril no le afectan.
                ego_target_lane = _target_roundabout_lane(vehicle, graph)
                lst = next_lanes_dict.get(ego_target_lane)
                if lst:
                    first_on_next = lst[0]
            else:
                # Sin anillo: el primer veh de cada lane (ASC) es el de menor
                # progress; el mínimo entre esos es el que más molesta. O(n_lanes).
                best_progress = float("inf")
                for lst in next_lanes_dict.values():
                    if lst and lst[0].progress_on_edge < best_progress:
                        best_progress = lst[0].progress_on_edge
                        first_on_next = lst[0]
            if first_on_next is not None:
                if first_on_next.id != vehicle.id:
                    dist_on_next = first_on_next.progress_on_edge * next_edge_len
                    leader_len = getattr(first_on_next, "length_m", VEHICLE_LENGTH_M)
                    total_gap = remaining_current_m + dist_on_next - leader_len
                    if is_ring_exit:
                        # Clamp para impedir que el IDM lleve al ego a v=0
                        # dentro del ring cuando hay cola en el borde de la
                        # salida (v_lead=0, gap≈0). El ego desacelera pero no
                        # para; al cruzar al exit, el _find_leader same-edge
                        # toma el control con datos reales.
                        gap_m = max(total_gap, RING_EXIT_MIN_GAP_M)
                        v_lead = max(first_on_next.velocity, RING_EXIT_MIN_LEADER_V_MS)
                    else:
                        gap_m = max(total_gap, 0.01)
                        v_lead = first_on_next.velocity
                    return NeighborInfo(
                        gap_m=gap_m,
                        velocity_ms=v_lead,
                        leader_id=first_on_next.id,
                    )

    return None


def _build_ring_occupancy(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
) -> dict[int, list[SimVehicle]]:
    """
    Índice {rotunda_id → vehículos que circulan actualmente en ese anillo}.

    Además, durante el mismo paso pre-computa para cada vehículo del ring un
    cache `v._ring_arc_to_entry: dict[node_id, arc_m]` que mapea cada nodo
    futuro de su ruta dentro del ring a la distancia arc que tiene que
    recorrer para alcanzarlo. Esto elimina `_arc_distance_on_ring`
    (O(L) por par ego×other) — `_find_roundabout_yield_leader` lo lee como
    dict lookup O(1) sin volver a iterar rutas.
    """
    occ: dict[int, list[SimVehicle]] = {}
    for v in vehicles.values():
        if v.status == VehicleStatus.FINISHED:
            continue
        np_ = v.route.node_path
        ei = v.current_edge_index
        if ei >= len(np_) - 1:
            continue
        attrs = graph.get_edge_attributes(np_[ei], np_[ei + 1])
        if not attrs.get(ATTR_IS_ROUNDABOUT):
            continue
        rid = attrs.get(ATTR_ROUNDABOUT_ID)
        if rid is None:
            continue
        occ.setdefault(int(rid), []).append(v)
        # Pre-cómputo del cache de arc-distances a nodos posteriores del ring.
        edge_len = max(float(attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
        arc_cache: dict[int, float] = {}
        arc = (1.0 - v.progress_on_edge) * edge_len
        arc_cache[np_[ei + 1]] = arc
        for j in range(ei + 1, len(np_) - 1):
            a2 = graph.get_edge_attributes(np_[j], np_[j + 1])
            if not a2.get(ATTR_IS_ROUNDABOUT):
                break
            arc += float(a2.get(ATTR_LENGTH, 0.0))
            arc_cache[np_[j + 1]] = arc
        v._ring_arc_to_entry = arc_cache  # type: ignore[attr-defined]
    return occ


def _target_roundabout_lane(
    v: SimVehicle,
    graph: RoadNetworkGraph,
) -> int:
    """
    Devuelve el carril que `v` debería ocupar dentro del anillo según su ruta.

    Estrategia de rotonda de 2 carriles realista:
      - Si la ruta recorre 1 arco o menos dentro del anillo (sale en la próxima
        salida) → carril EXTERIOR (lane 0).
      - Si la ruta recorre ≥ 2 arcos → carril INTERIOR (lane n_lanes-1).
      - Si la primera arista del anillo es de 1 carril → 0.

    Cacheada per-vehículo en `v._target_ring_lane_cache = (edge_idx, lane)`:
    el resultado depende solo de la ruta y de `current_edge_index`, ambos
    estables dentro del mismo tick. Al avanzar de edge se invalida solo.
    Devuelve 0 si el vehículo no tiene arista de anillo en su ruta.
    """
    np_ = v.route.node_path
    ei = v.current_edge_index
    cached: tuple[int, int] | None = getattr(v, "_target_ring_lane_cache", None)
    if cached is not None and cached[0] == ei:
        return cached[1]
    # Localizar el primer arco de anillo a partir de la posición actual del vehículo.
    first_ring_k: int | None = None
    first_rid: int | None = None
    first_n_lanes: int = 1
    for k in range(ei, len(np_) - 1):
        attrs = graph.get_edge_attributes(np_[k], np_[k + 1])
        if attrs.get(ATTR_IS_ROUNDABOUT):
            first_ring_k = k
            first_rid = attrs.get(ATTR_ROUNDABOUT_ID)
            first_n_lanes = max(int(attrs.get(ATTR_LANES, 1)), 1)
            break
    if first_ring_k is None or first_rid is None or first_n_lanes <= 1:
        v._target_ring_lane_cache = (ei, 0)  # type: ignore[attr-defined]
        return 0
    # Contar arcos consecutivos con el mismo rid hasta la primera arista no-anillo.
    ring_arc_count = 0
    for k in range(first_ring_k, len(np_) - 1):
        attrs = graph.get_edge_attributes(np_[k], np_[k + 1])
        if (
            attrs.get(ATTR_IS_ROUNDABOUT)
            and attrs.get(ATTR_ROUNDABOUT_ID) == first_rid
        ):
            ring_arc_count += 1
        else:
            break
    result = 0 if ring_arc_count <= 1 else first_n_lanes - 1
    v._target_ring_lane_cache = (ei, result)  # type: ignore[attr-defined]
    return result


def _build_entry_arm_index(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
) -> dict[tuple[int, int], list[SimVehicle]]:
    """
    Índice {(entry_node_id, target_lane) → vehículos que se aproximan a esa
    línea de entrada y pretenden ocupar ese carril dentro del anillo}.

    Un vehículo se considera "aproximándose" cuando:
      - su arista actual NO es de rotonda;
      - su arista siguiente SÍ es de rotonda;
      - la distancia restante hasta el nodo de entrada < ENTRY_ARBITRATION_ZONE_M.

    Se segmenta por carril de destino (calculado con `_target_roundabout_lane`)
    para que dos vehículos que entran al mismo nodo pero a carriles distintos
    NO se arbitren como conflicto: cada carril tiene su propia cola de
    prioridad.
    """
    arms: dict[tuple[int, int], list[SimVehicle]] = {}
    for v in vehicles.values():
        if v.status == VehicleStatus.FINISHED:
            continue
        np_ = v.route.node_path
        ei = v.current_edge_index
        if ei + 2 > len(np_) - 1:
            continue
        cur = graph.get_edge_attributes(np_[ei], np_[ei + 1])
        if cur.get(ATTR_IS_ROUNDABOUT):
            continue
        nxt = graph.get_edge_attributes(np_[ei + 1], np_[ei + 2])
        if not nxt.get(ATTR_IS_ROUNDABOUT):
            continue
        edge_len = max(float(cur.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
        dist_to_ring = (1.0 - v.progress_on_edge) * edge_len
        if dist_to_ring > ENTRY_ARBITRATION_ZONE_M:
            continue
        target_lane = _target_roundabout_lane(v, graph)
        arms.setdefault((np_[ei + 1], target_lane), []).append(v)
    return arms


def _arc_distance_on_ring(
    v: SimVehicle,
    entry_node: int,
    graph: RoadNetworkGraph,
) -> float:
    """
    Estima la distancia angular (en metros) del vehículo `v` al nodo `entry_node`
    siguiendo el sentido de circulación de la rotonda.

    Aproximación pragmática: suma `remaining_on_edge` + longitudes de aristas
    posteriores de la ruta del vehículo hasta alcanzar `entry_node` (o tope).
    Si la ruta del vehículo no pasa por la entrada, devuelve inf (no relevante
    para esta entrada).
    """
    np_ = v.route.node_path
    ei = v.current_edge_index
    if ei >= len(np_) - 1:
        return float("inf")

    attrs = graph.get_edge_attributes(np_[ei], np_[ei + 1])
    edge_len = max(float(attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
    arc = (1.0 - v.progress_on_edge) * edge_len

    # Si el vehículo ya está en la arista que termina en entry_node, estamos listos.
    if np_[ei + 1] == entry_node:
        return arc

    # Recorrer aristas sucesivas — sólo las que siguen siendo anillo.
    for j in range(ei + 1, len(np_) - 1):
        a2 = graph.get_edge_attributes(np_[j], np_[j + 1])
        if not a2.get(ATTR_IS_ROUNDABOUT):
            break
        arc += float(a2.get(ATTR_LENGTH, 0.0))
        if np_[j + 1] == entry_node:
            return arc
    return float("inf")


def _find_roundabout_yield_leader(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    ring_occupancy: dict[int, list[SimVehicle]],
    entry_arms: dict[tuple[int, int], list[SimVehicle]],
) -> NeighborInfo | None:
    """
    Genera un "líder virtual" parado en la línea de entrada de la rotonda.

    Dispara en dos casos:
      1. Un vehículo ya circulando en el anillo llegará antes que el ego al nodo
         de entrada Y va en el MISMO carril de destino (regla de ceda-el-paso
         clásica). Si el circulante va por otro carril su trayectoria no
         conflicta con la del ego.
      2. Otro vehículo se aproxima al MISMO entry_node desde otra arista
         convergente Y pretende ocupar el MISMO carril destino del anillo; en
         ese caso se arbitra cross-arm. Si apuntan a carriles distintos, ambos
         entran sin ceder.

    Solo se evalúa para vehículos fuera del anillo cuya siguiente arista es
    rotonda y están dentro de YIELD_DETECTION_ZONE_M de la línea de entrada.

    El líder virtual hace que el IDM frene hasta parar en la línea. Cuando el
    conflicto se aleja, el leader desaparece y el IDM acelera de nuevo.
    """
    np_ = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei + 2 > len(np_) - 1:  # no hay "arista siguiente"
        return None

    cur = graph.get_edge_attributes(np_[ei], np_[ei + 1])
    if cur.get(ATTR_IS_ROUNDABOUT):
        return None  # ya dentro del anillo

    nxt = graph.get_edge_attributes(np_[ei + 1], np_[ei + 2])
    if not nxt.get(ATTR_IS_ROUNDABOUT):
        return None  # no va a entrar en rotonda

    rid = nxt.get(ATTR_ROUNDABOUT_ID)
    if rid is None:
        return None

    edge_len = max(float(cur.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
    dist_to_ring = (1.0 - vehicle.progress_on_edge) * edge_len
    if dist_to_ring > YIELD_DETECTION_ZONE_M:
        return None

    entry_node = np_[ei + 1]
    ego_target_lane = _target_roundabout_lane(vehicle, graph)

    # ── (1) Vehículos ya circulando: regla de ceda clásica ─────────────────────
    must_yield = False
    for other in ring_occupancy.get(int(rid), ()):
        if other.id == vehicle.id:
            continue
        # Otro carril del anillo: trayectorias paralelas, no conflictan.
        if getattr(other, "lane", 0) != ego_target_lane:
            continue
        # Lookup O(1) en el cache pre-computado durante `_build_ring_occupancy`.
        # Si entry_node no está en el cache, `other` no pasa por él en su ruta.
        arc_cache: dict[int, float] | None = getattr(other, "_ring_arc_to_entry", None)
        if arc_cache is None:
            continue
        arc = arc_cache.get(entry_node)
        if arc is None:
            continue
        # Con `other.velocity` muy baja el TTC crece — tope inferior 1 m/s evita
        # generar leaders perpetuos cuando alguien circula a paso de humano.
        ttc = arc / max(other.velocity, 1.0)
        if ttc < YIELD_TTC_THRESHOLD_S or arc < YIELD_GAP_MIN_M:
            must_yield = True
            break

    # ── (2) Arbitración cross-arm: gana el más cercano al entry_node ──────────
    # Segmentada por carril destino — dos coches que entran al mismo nodo pero
    # a carriles distintos NO conflictan. Tie-break determinista por vehicle.id.
    if not must_yield:
        contenders = entry_arms.get((entry_node, ego_target_lane), ())
        if len(contenders) > 1:
            def _remaining(v: SimVehicle) -> tuple[float, str]:
                v_np = v.route.node_path
                v_ei = v.current_edge_index
                attrs = graph.get_edge_attributes(v_np[v_ei], v_np[v_ei + 1])
                el = max(float(attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
                return ((1.0 - v.progress_on_edge) * el, v.id)
            winner = min(contenders, key=_remaining)
            if winner.id != vehicle.id:
                must_yield = True

    if not must_yield:
        return None

    # Líder virtual posicionado s0 metros DETRÁS de la línea de entrada para
    # que el IDM, manteniendo `gap = s0` en reposo, deje al ego con su frontal
    # AL FINAL del carril (justo en la línea), no varios metros antes.
    # Si reportásemos `gap = dist_to_ring`, el IDM mantendría s0 de holgura y
    # el ego se pararía s0 = 8 m ANTES de la línea: visualmente no parece un
    # yield correcto. `IDM_S0_DEFAULT_M` (parameters.py: IDMParameters().s0)
    # es 8 m; se usa el valor numérico aquí para evitar dependencia circular.
    gap = max(dist_to_ring + 8.0, 0.2)
    return NeighborInfo(gap_m=gap, velocity_ms=0.0, leader_id=None)


# ---------------------------------------------------------------------------
# Curvature-based speed cap (Fase 4)
# ---------------------------------------------------------------------------

def _curvature_radius_m(
    waypoints: list[tuple[float, float]],
) -> float:
    """
    Estima el radio de curvatura (metros) del polyline tomando 3 waypoints
    equidistantes (primero, medio, último). Si no hay tres puntos distinguibles,
    devuelve inf (tratado como recta).
    """
    if len(waypoints) < 3:
        return float("inf")
    p1 = waypoints[0]
    p2 = waypoints[len(waypoints) // 2]
    p3 = waypoints[-1]

    # Proyección local equirectangular alrededor de p1 (metros relativos).
    cos_lat = math.cos(math.radians(p1[1]))
    x1, y1 = 0.0, 0.0
    x2 = (p2[0] - p1[0]) * cos_lat * 111_320.0
    y2 = (p2[1] - p1[1]) * 111_320.0
    x3 = (p3[0] - p1[0]) * cos_lat * 111_320.0
    y3 = (p3[1] - p1[1]) * 111_320.0

    # Fórmula del circunradio por 3 puntos.
    a = math.hypot(x2 - x1, y2 - y1)
    b = math.hypot(x3 - x2, y3 - y2)
    c = math.hypot(x3 - x1, y3 - y1)
    s = (a + b + c) * 0.5
    area_sq = max(s * (s - a) * (s - b) * (s - c), 0.0)
    area = math.sqrt(area_sq)
    if area < 1e-6:
        return float("inf")  # colineales → recta
    R = (a * b * c) / (4.0 * area)
    return max(R, MIN_ROUNDABOUT_RADIUS_M)


def _edge_curvature_vmax(
    edge_attrs: dict,
    lateral_accel_max_ms2: float = CURVE_LATERAL_ACCEL_MAX_MS2,
) -> float:
    """
    Velocidad máxima segura según la curvatura del tramo (m/s).

    v_max = sqrt(a_lat_max * R).

    Se cachea en `edge_attrs[ATTR_CURVE_VMAX]` porque la geometría de la arista
    es inmutable tras cargarla al grafo. Los tramos rectos devuelven inf.

    Para aristas de rotonda con `ATTR_RING_RADIUS_M` precomputado (tras la
    splinificación en network_graph) usamos el radio circular del anillo
    completo en vez del estimador por 3 waypoints — es más estable cuando la
    spline tiene pocos puntos de control y refleja mejor la dinámica real
    del giro continuo dentro del ring.
    """
    cached = edge_attrs.get(ATTR_CURVE_VMAX)
    if cached is not None:
        return float(cached)
    ring_radius = edge_attrs.get(ATTR_RING_RADIUS_M)
    if ring_radius is not None:
        R = max(float(ring_radius), MIN_ROUNDABOUT_RADIUS_M)
        v_max = math.sqrt(lateral_accel_max_ms2 * R)
    else:
        waypoints = edge_attrs.get(ATTR_WAYPOINTS) or []
        R = _curvature_radius_m(waypoints)
        if math.isinf(R):
            v_max = float("inf")
        else:
            v_max = math.sqrt(lateral_accel_max_ms2 * R)
    edge_attrs[ATTR_CURVE_VMAX] = v_max
    return v_max


def _build_lane_context(
    ego: SimVehicle,
    target_lane: int,
    target_lane_vehicles: list[SimVehicle],
    edge_len: float,
) -> LaneContext:
    """
    Construye el LaneContext para el carril `target_lane` en la arista del ego.

    `target_lane_vehicles` ya viene filtrado por el caller usando el índice
    per-lane (`edge_index[edge_key][target_lane]`). Aquí solo hace falta
    encontrar el más cercano por delante y por detrás del ego.
    """
    ctx = LaneContext(lane_index=target_lane)
    ego_len = getattr(ego, "length_m", VEHICLE_LENGTH_M)
    best_front_gap = float("inf")
    best_back_gap = float("inf")
    for cand in target_lane_vehicles:
        if cand.id == ego.id:
            continue
        cand_len = getattr(cand, "length_m", VEHICLE_LENGTH_M)
        if cand.progress_on_edge > ego.progress_on_edge:
            gap = (cand.progress_on_edge - ego.progress_on_edge) * edge_len - cand_len
            gap = max(gap, 0.01)
            if gap < best_front_gap:
                best_front_gap = gap
                ctx.gap_front = gap
                ctx.v_front = cand.velocity
        elif cand.progress_on_edge < ego.progress_on_edge:
            gap = (ego.progress_on_edge - cand.progress_on_edge) * edge_len - ego_len
            gap = max(gap, 0.01)
            if gap < best_back_gap:
                best_back_gap = gap
                ctx.gap_back = gap
                ctx.v_back = cand.velocity
                ctx.v_back_current_accel = cand.acceleration
    return ctx


def _evaluate_lane_change(
    vehicle: SimVehicle,
    edge_index: "dict[tuple[int, int], dict[int, list[SimVehicle]]]",
    graph: RoadNetworkGraph,
    leader: NeighborInfo | None,
    closed_lanes: dict[tuple[int, int], set[int]] | None = None,
    tick_count: int = 0,
) -> None:
    """
    Evalúa MOBIL para decidir si el vehículo debe cambiar de carril.

    - No-op si la arista tiene ≤ 1 carril o el vehículo está cerca del final
      de la arista (donde el lane ya se reasigna al entrar en la siguiente).
    - Modifica `vehicle.lane` in-place si MOBIL decide un cambio seguro.
    - Si el carril actual del ego figura en ``closed_lanes[edge]``, se evalúa
      con ``force_change=True`` (bypass de cooldown y del umbral de incentivo)
      aceptando el mejor candidato seguro.
    """
    current_lane = getattr(vehicle, "lane", 0)
    node_path = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(node_path) - 1:
        return

    key = (node_path[ei], node_path[ei + 1])
    closed_set: set[int] = set()
    if closed_lanes is not None:
        closed_set = closed_lanes.get(key, set())

    trapped = current_lane in closed_set

    # Early-out barato antes de cualquier lookup de grafo: un vehículo casi
    # parado no tiene incentivo de aceleración para cambiar de carril y gastar
    # CPU construyendo contextos y llamando al modelo es puro waste. En tráfico
    # urbano gran parte del parque está en esta franja cada tick.
    # Excepción: si está atrapado en un carril cerrado, SÍ evaluamos — la
    # alternativa es quedarse bloqueando el tráfico y generar backpressure.
    if not trapped and vehicle.velocity < MOBIL_MIN_VELOCITY_MS:
        return

    edge_attrs = graph.get_edge_attributes(*key)
    # Dentro del anillo de una rotonda no cambiamos de carril: los cambios
    # provocan trayectorias cruzadas y colisiones en la curvatura. MOBIL se
    # ejecuta solo en tramos rectos/aproximaciones.
    if edge_attrs.get(ATTR_IS_ROUNDABOUT):
        return
    # Aproximación a rotonda: disciplinar la cola. Un cambio de carril aquí
    # crea alcances en el nuevo carril cuando el circulante que ya venía por
    # detrás no puede frenar a tiempo — y MOBIL no gana nada, porque el
    # carril destino lo fija _target_roundabout_lane según la ruta.
    # Excepción: si el ego está atrapado en un carril cerrado, aún debe salir.
    if not trapped and ei + 1 < len(node_path) - 1:
        next_attrs = graph.get_edge_attributes(node_path[ei + 1], node_path[ei + 2])
        if bool(next_attrs.get(ATTR_IS_ROUNDABOUT)):
            return
    n_lanes = max(int(edge_attrs.get(ATTR_LANES, 1)), 1)
    if n_lanes <= 1:
        return
    edge_len = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)

    # Cerca del fin de arista: no merece la pena cambiar salvo que esté atrapado.
    dist_to_end = edge_len - vehicle.progress_on_edge * edge_len
    if not trapped and dist_to_end < MOBIL_MIN_DIST_TO_EDGE_END_M:
        return

    same_edge_lanes = edge_index.get(key, {})

    # Construir contextos sólo para carriles existentes. Convención: lane 0 es
    # el carril derecho (el más cercano al bordillo); lane+1 es el izquierdo.
    # Con el índice per-lane, MOBIL recibe directamente la lista del carril
    # destino — sin escanear los demás carriles.
    lane_left_ctx: LaneContext | None = None
    lane_right_ctx: LaneContext | None = None
    if current_lane + 1 < n_lanes:
        lane_left_ctx = _build_lane_context(
            vehicle, current_lane + 1,
            same_edge_lanes.get(current_lane + 1, []),
            edge_len,
        )
    if current_lane - 1 >= 0:
        lane_right_ctx = _build_lane_context(
            vehicle, current_lane - 1,
            same_edge_lanes.get(current_lane - 1, []),
            edge_len,
        )
    if lane_left_ctx is None and lane_right_ctx is None:
        return

    mobil = _mobil_for(vehicle)
    decision = mobil.evaluate_lane_change(
        v_ego=vehicle.velocity,
        v0_ego=vehicle.desired_speed_ms,
        current_accel=vehicle.acceleration,
        gap_front_current=leader.gap_m if leader is not None else None,
        v_front_current=leader.velocity_ms if leader is not None else None,
        lane_left=lane_left_ctx,
        lane_right=lane_right_ctx,
        closed_lanes_on_edge=closed_set or None,
        force_change=trapped,
    )
    if not decision.should_change:
        return
    if decision.direction == LaneChangeDirection.LEFT:
        vehicle.lane = current_lane + 1
    elif decision.direction == LaneChangeDirection.RIGHT:
        vehicle.lane = current_lane - 1
    vehicle.last_lane_change_tick = tick_count


# Funciones de detección de TL + STOP/YIELD extraídas a
# `app.core.physics.traffic_signs`. Se re-exportan las usadas internamente
# por `update_vehicles` y `_advance_vehicle_idm`, más `_check_stop_yield_sign`
# que también consume `test_stop_yield_sign.py`.
from app.core.physics.traffic_signs import (  # noqa: E402, F401
    _check_stop_yield_sign,
    _check_traffic_light,
)

# Arbitraje de cruces no señalizados (priority-to-the-right + TTC tiebreak).
from app.core.physics.intersection import (  # noqa: E402, F401
    _build_intersection_arm_index,
    _check_intersection_yield,
)






# ---------------------------------------------------------------------------
# IDM advance
# ---------------------------------------------------------------------------

def _advance_vehicle_idm(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    dt: float,
    leader: NeighborInfo | None,
    tl_ref: object | None = None,
    blocked_edges_set: set[tuple[int, int]] | None = None,
    closed_lanes: dict[tuple[int, int], set[int]] | None = None,
) -> bool:
    """
    Avanza un vehículo por su ruta durante dt segundos usando el IDM.

    Flujo:
      1. Calcula aceleración IDM basada en el líder (o libre si no hay líder).
      2. Actualiza velocidad: v_new = clamp(v + a·dt, 0, v_max).
      3. Avanza la posición: dist = v_new · dt (Euler explícito).
      4. Recorre aristas multi-segmento hasta consumir la distancia.
         → Hard-stop: si el nodo de destino está bloqueado (rojo siempre,
           amarillo si el vehículo no tiene yellow_runs_light), se detiene
           antes de cruzar aunque el IDM no haya podido frenar a tiempo
           (aristas cortas).
         → Hard-stop por bloqueo de tramo: si la siguiente arista está en
           ``blocked_edges_set`` (cierre/incidente activado tras el spawn),
           se detiene en el borde del tramo actual. La penalización en A*
           sólo cubre rutas calculadas tras el bloqueo; sin esta validación
           los vehículos con ruta previa cruzaban el cierre.
      5. Interpola la posición geográfica con los waypoints reales.

    Args:
        tl_ref: Objeto con método get_phase_for_edge(node_id, edge) → str,
                opcional. Cuando se proporciona, activa el hard-stop de
                seguridad en el bucle de avance de aristas.
        blocked_edges_set: conjunto de claves (start_n, end_n) bloqueadas.
                Si se proporciona, el vehículo no entra en ninguna.
        closed_lanes: mapa edge → set de carriles cerrados; se aplica al
                clampar el carril en la transición a la nueva arista para
                aterrizar en un carril abierto siempre que sea posible.

    Returns:
        True si el vehículo completó su ruta en este tick.
    """
    node_path = vehicle.route.node_path

    if len(node_path) < 2 or vehicle.current_edge_index >= len(node_path) - 1:
        vehicle.status   = VehicleStatus.FINISHED
        vehicle.velocity = 0.0
        return True

    ei = vehicle.current_edge_index
    start_n = node_path[ei]
    end_n   = node_path[ei + 1]
    edge_attrs  = graph.get_edge_attributes(start_n, end_n)
    speed_kmh   = float(edge_attrs.get(ATTR_MAX_SPEED, DEFAULT_VEHICLE_SPEED_KMH)) or DEFAULT_VEHICLE_SPEED_KMH
    v_max       = speed_kmh * KMH_TO_MS

    # Velocidad deseada individual, acotada al límite de la vía actual
    desired_v = min(getattr(vehicle, "desired_speed_ms", v_max), v_max)

    # Velocidad máxima por curvatura del tramo (rotondas y curvas cerradas).
    # Se cachea en el edge_attrs. Si hay perfil con lateral_accel_max,
    # usar ese; si no, el default global.
    lateral_cap = CURVE_LATERAL_ACCEL_MAX_MS2
    profile = PROFILES.get(getattr(vehicle, "vtype", VehicleType.CAR))
    if profile is not None:
        lateral_cap = float(getattr(profile, "lateral_accel_max_ms2", lateral_cap))
    curve_vmax = _edge_curvature_vmax(edge_attrs, lateral_cap)
    desired_v = min(desired_v, curve_vmax)

    # ── IDM acceleration ───────────────────────────────────────────────────────
    idm = _idm_for(vehicle)
    if leader is not None:
        a = idm.calculate_acceleration(
            v=vehicle.velocity,
            v0=desired_v,
            s=leader.gap_m,
            v_lead=leader.velocity_ms,
        )
    else:
        a = idm.calculate_acceleration(v=vehicle.velocity, v0=desired_v)

    # Red de seguridad: ante líder casi parado a gap corto y ego más rápido,
    # forzar freno máximo. Ampliado respecto a la versión previa (líder parado
    # y gap<5m) para atajar rear-ends de aproximación a cola — principal causa
    # primaria de colisiones en E2E con 5000 veh. Cubre el caso donde un
    # vehículo llega a 8 m/s detrás de una cola a 1 m/s en 10 m: el IDM puro
    # no alcanza MAX_EMERGENCY_DECEL_MS2 a tiempo.
    if (
        leader is not None
        and leader.velocity_ms < EMERGENCY_BRAKE_LEADER_V_MAX_MS
        and leader.gap_m < EMERGENCY_BRAKE_GAP_MAX_M
        and vehicle.velocity > leader.velocity_ms + EMERGENCY_BRAKE_EGO_V_DELTA_MS
    ):
        a = -MAX_EMERGENCY_DECEL_MS2
    a = max(a, -MAX_EMERGENCY_DECEL_MS2)  # límite físico de frenado
    # Clamp al rango físico del perfil: útil para el cliente (dead reckoning)
    a = min(a, idm.params.a)

    # ── Update velocity ────────────────────────────────────────────────────────
    new_v = max(0.0, min(vehicle.velocity + a * dt, v_max))

    # Safety cap: si el gap con el líder es ínfimo, no superar su velocidad
    # (evita que el IDM "atraviese" al vehículo delantero en aristas cortas)
    if leader is not None and leader.gap_m < 1.0:
        new_v = min(new_v, max(leader.velocity_ms, 0.0))

    vehicle.velocity     = new_v
    vehicle.acceleration = a

    # ── Distance to travel this tick ──────────────────────────────────────────
    # Euler explícito: dist = v_new * dt
    # (v_new ya incorpora la aceleración; evitamos double-count con 0.5·a·dt²)
    remaining_dist = new_v * dt

    # Hard clamp final: nunca rebasar al líder (real o virtual). El IDM debería
    # garantizarlo, pero ante caídas bruscas de gap (aristas cortas, líder que
    # acaba de aparecer por look-ahead) puede quedarse corto. Con este límite
    # el avance del tick queda acotado a `gap - HARD_CLAMP_MARGIN_M`. Si la
    # distancia disponible se anula, se fuerza v=0 para que el siguiente tick
    # arranque desde el reposo en vez de mantener velocidad residual.
    if leader is not None:
        max_advance = max(leader.gap_m - HARD_CLAMP_MARGIN_M, 0.0)
        if remaining_dist > max_advance:
            remaining_dist = max_advance
            if remaining_dist <= 1e-6:
                vehicle.velocity = 0.0

    # ── Multi-edge advance loop ────────────────────────────────────────────────
    while remaining_dist > 1e-6 and ei < len(node_path) - 1:
        start_n    = node_path[ei]
        end_n      = node_path[ei + 1]
        edge_attrs = graph.get_edge_attributes(start_n, end_n)
        edge_len   = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)

        pos_on_edge = edge_len * vehicle.progress_on_edge
        dist_to_end = edge_len - pos_on_edge

        # ── Hard-stop por TLs mid-way en la arista actual ──────────────────
        # Si el IDM no frenó lo bastante, bloqueamos manualmente antes del
        # cruce peatonal / semáforo interno. mid_tls está ordenada por
        # distancia creciente desde start_node.
        # Excepción: dentro del anillo no se aplica — `highway=crossing` y
        # similares dentro de la rotonda no deben parar al circulante (ver
        # _check_traffic_light para la lógica equivalente sobre el líder IDM).
        cur_is_ring_hard = bool(edge_attrs.get(ATTR_IS_ROUNDABOUT))
        mid_stop_hit = False
        if tl_ref is not None and not cur_is_ring_hard:
            mid_tls: list[tuple[int, float]] = edge_attrs.get(ATTR_MID_TLS, []) or []
            for tl_nid, dist_from_start in mid_tls:
                if dist_from_start <= pos_on_edge:
                    continue
                dist_to_tl = dist_from_start - pos_on_edge
                if dist_to_tl > remaining_dist:
                    break  # fuera de alcance este tick
                phase = tl_ref.get_phase(tl_nid)  # type: ignore[union-attr]
                blocks = phase == TL_PHASE_RED or (
                    phase == TL_PHASE_YELLOW
                    and not getattr(vehicle, "yellow_runs_light", False)
                )
                if blocks:
                    stop_pos_m = max(dist_from_start - VEHICLE_LENGTH_M, pos_on_edge)
                    vehicle.progress_on_edge = min(stop_pos_m / edge_len, 0.98)
                    vehicle.velocity = 0.0
                    remaining_dist = 0.0
                    mid_stop_hit = True
                    break
        if mid_stop_hit:
            break

        if remaining_dist >= dist_to_end:
            # ── Hard-stop de seguridad en línea de stop del end_node ──────
            # Aunque el IDM ya debería haber frenado (via _check_traffic_light),
            # en aristas muy cortas puede que la distancia no haya sido suficiente.
            # ROJO → siempre frena; AMARILLO → frena si yellow_runs_light es False.
            # Excepción: si la arista actual es de anillo, el end_node es
            # interno o de salida del ring; los crossings internos no deben
            # parar al circulante.
            if tl_ref is not None and not cur_is_ring_hard:
                phase = tl_ref.get_phase_for_edge(end_n, (start_n, end_n))  # type: ignore[union-attr]
                blocks = phase == TL_PHASE_RED or (
                    phase == TL_PHASE_YELLOW
                    and not getattr(vehicle, "yellow_runs_light", False)
                )
                if blocks:
                    stop_progress = max(
                        vehicle.progress_on_edge,
                        min(1.0 - (VEHICLE_LENGTH_M / edge_len), 0.98),
                    )
                    vehicle.progress_on_edge = stop_progress
                    vehicle.velocity         = 0.0
                    remaining_dist           = 0.0
                    break

            # Hard-stop si la siguiente arista está bloqueada (cierre/incidente
            # activado tras calcular la ruta). La penalización en A* solo cubre
            # rutas nuevas; sin este check el vehículo cruzaba el tramo cerrado
            # hasta que el reroute periódico lo alcanzaba (3+ s con 1500 veh).
            # Excepción: si la arista actual es de anillo, no se aplica — un
            # coche dentro del ring no debe pararse por una salida bloqueada
            # (principio: ceder fuera del ring, nunca dentro). Cuando cruce al
            # exit edge, el `_find_leader` y la lógica normal de fuera del ring
            # lo gestionarán; el reroute urgente lo desviará si la salida sigue
            # bloqueada en ticks posteriores.
            if (
                blocked_edges_set is not None
                and ei + 2 <= len(node_path) - 1
                and not cur_is_ring_hard
            ):
                next_key = (node_path[ei + 1], node_path[ei + 2])
                if next_key in blocked_edges_set:
                    stop_progress = max(
                        vehicle.progress_on_edge,
                        min(1.0 - (VEHICLE_LENGTH_M / edge_len), 0.98),
                    )
                    vehicle.progress_on_edge = stop_progress
                    vehicle.velocity         = 0.0
                    remaining_dist           = 0.0
                    break

            remaining_dist           -= dist_to_end
            # Guardar la tangente final de la arista saliente antes de avanzar
            # al siguiente tramo: se usa para mezclar con la tangente inicial
            # de la entrante durante los primeros EDGE_HEADING_BLEND_DIST_M
            # metros y evitar un snap visible de heading en el cruce.
            outgoing_start_attrs = graph.get_node_attributes(start_n)
            outgoing_end_attrs   = graph.get_node_attributes(end_n)
            outgoing_wps = _get_waypoints(
                edge_attrs, outgoing_start_attrs, outgoing_end_attrs
            )
            _, _, end_heading = _edge_position(
                edge_attrs, 1.0, outgoing_wps, _cache_key=(start_n, end_n)
            )
            vehicle.prev_edge_end_heading = end_heading
            vehicle.prev_edge_was_roundabout = bool(
                edge_attrs.get(ATTR_IS_ROUNDABOUT)
            )
            ei                       += 1
            vehicle.progress_on_edge  = 0.0
            # Al entrar en la nueva arista, clampear el carril a su número de
            # carriles disponible (una calle de 1 carril recibe todos los
            # cambios → lane=0). MOBIL decidirá si conviene volver a cambiar.
            if ei < len(node_path) - 1:
                new_edge_attrs = graph.get_edge_attributes(
                    node_path[ei], node_path[ei + 1]
                )
                new_lanes = max(int(new_edge_attrs.get(ATTR_LANES, 1)), 1)
                cur_lane = getattr(vehicle, "lane", 0)
                new_lane = min(cur_lane, new_lanes - 1)
                # Si el carril destino está cerrado en la nueva arista, aterrizar
                # en el carril abierto más cercano. Si TODOS están cerrados se
                # mantiene el clamp original — MOBIL/trapped_in_closed forzará
                # reroute en el siguiente tick.
                if closed_lanes is not None:
                    new_edge_key = (node_path[ei], node_path[ei + 1])
                    closed_set = closed_lanes.get(new_edge_key, set())
                    if new_lane in closed_set:
                        open_lanes = [l for l in range(new_lanes) if l not in closed_set]
                        if open_lanes:
                            new_lane = min(open_lanes, key=lambda l: abs(l - cur_lane))
                vehicle.lane = new_lane
                # Cruzando la línea de entrada a un anillo: forzar el carril
                # según la ruta (exterior si sale ya, interior si da ≥1 vuelta
                # más). Reparte carga entre carriles y evita que todo el
                # tráfico se apile en lane=0 cuando la arista previa era de
                # 1 carril. Gated a la transición NO-anillo → SÍ-anillo.
                entering_ring = bool(new_edge_attrs.get(ATTR_IS_ROUNDABOUT)) and not bool(
                    edge_attrs.get(ATTR_IS_ROUNDABOUT)
                )
                if entering_ring:
                    target = _target_roundabout_lane(vehicle, graph)
                    vehicle.lane = min(target, new_lanes - 1)
        else:
            vehicle.progress_on_edge += remaining_dist / edge_len
            vehicle.progress_on_edge  = min(vehicle.progress_on_edge, 1.0)
            remaining_dist            = 0.0

    vehicle.current_edge_index = ei

    # ── Route finished ─────────────────────────────────────────────────────────
    if ei >= len(node_path) - 1:
        last_attrs        = graph.get_node_attributes(node_path[-1])
        vehicle.longitude  = float(last_attrs.get(ATTR_LONGITUDE, vehicle.longitude))
        vehicle.latitude   = float(last_attrs.get(ATTR_LATITUDE,  vehicle.latitude))
        vehicle.velocity   = 0.0
        vehicle.status     = VehicleStatus.FINISHED
        return True

    # ── Interpolate position along road geometry ───────────────────────────────
    start_n     = node_path[ei]
    end_n       = node_path[ei + 1]
    edge_attrs  = graph.get_edge_attributes(start_n, end_n)
    start_attrs = graph.get_node_attributes(start_n)
    end_attrs   = graph.get_node_attributes(end_n)

    waypoints = _get_waypoints(edge_attrs, start_attrs, end_attrs)
    lon, lat, heading = _edge_position(
        edge_attrs, vehicle.progress_on_edge, waypoints, _cache_key=(start_n, end_n)
    )

    # ── Blend de tangentes entre aristas ──────────────────────────────────────
    # Durante los primeros `blend_dist` metros de una arista entrante,
    # mezclamos la tangente final de la saliente con la actual. Así el
    # cliente ve un giro continuo en vez de un snap instantáneo. A la salida
    # de una rotonda usamos una zona más larga para evitar cortes bruscos.
    if vehicle.prev_edge_end_heading >= 0.0:
        cur_edge_len = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
        pos_on_edge_m = vehicle.progress_on_edge * cur_edge_len
        blend_dist = EDGE_HEADING_BLEND_DIST_M
        if getattr(vehicle, "prev_edge_was_roundabout", False):
            blend_dist = EDGE_HEADING_BLEND_DIST_ROUNDABOUT_EXIT_M
        if pos_on_edge_m < blend_dist:
            blend_t = pos_on_edge_m / blend_dist
            heading = _blend_heading_deg(
                vehicle.prev_edge_end_heading, heading, blend_t
            )
        else:
            # Ya fuera de la zona de mezcla: limpiar el estado.
            vehicle.prev_edge_end_heading = -1.0
            vehicle.prev_edge_was_roundabout = False

    vehicle.longitude = lon
    vehicle.latitude  = lat
    vehicle.heading   = heading

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Funciones de rerouting extraídas a `app.core.physics.rerouting`. Se
# re-exportan aquí para conservar la API pública usada por
# `update_vehicles`, `zone_manager`, `test_collision.py` y `test_routing_zbe.py`.
from app.core.physics.rerouting import (  # noqa: E402, F401
    _mark_new_blocks_detected,
    _maybe_reroute_around_blocks,
    _periodic_reroute_all,
    _periodic_reroute_batch,
    _reroute_affected_by_new_blocks,
)


_INTERSECTION_HANDLED_ELSEWHERE_TYPES = {
    NodeType.TRAFFIC_LIGHT.value,
    NodeType.STOP_SIGN.value,
    NodeType.YIELD_SIGN.value,
    NodeType.ROUNDABOUT.value,
}
_RECENT_LANE_CHANGE_TICK_WINDOW = 3
_NEAR_ROUNDABOUT_ENTRY_M = 15.0


def _classify_collision_segment(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    tick_count: int,
) -> str:
    """
    Devuelve una etiqueta describiendo el contexto en que se produjo el choque
    para emitir contadores y atribuirlo a la hipótesis dominante (MOBIL,
    rotonda, cruce no señalizado, otro). Categorías mutuamente excluyentes,
    chequeadas en orden de especificidad:

      1. ``recent_lane_change`` — el ego cambió de carril en los últimos
         ``_RECENT_LANE_CHANGE_TICK_WINDOW`` ticks (señal directa de MOBIL).
      2. ``on_roundabout`` — la arista actual es de rotonda.
      3. ``near_roundabout_entry`` — la arista siguiente es de rotonda y el
         ego está a menos de ``_NEAR_ROUNDABOUT_ENTRY_M`` del fin de la actual.
      4. ``near_intersection_node`` — el siguiente nodo tiene grado de entrada
         ≥ 2 y NO está gestionado por TL/STOP/YIELD/ROUNDABOUT, y el ego está
         a menos de ``INTERSECTION_DETECTION_ZONE_M`` del fin de la arista.
      5. ``straight_section`` — ninguna de las anteriores; sospecha de fallo
         del IDM o del hard-clamp en tramo recto.
    """
    if tick_count - vehicle.last_lane_change_tick < _RECENT_LANE_CHANGE_TICK_WINDOW:
        return "recent_lane_change"
    np_ = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(np_) - 1:
        return "straight_section"
    cur_attrs = graph.get_edge_attributes(np_[ei], np_[ei + 1])
    if cur_attrs.get(ATTR_IS_ROUNDABOUT):
        return "on_roundabout"
    edge_len = float(cur_attrs.get(ATTR_LENGTH, 0.0)) or 0.0
    dist_to_next_node = max(edge_len - vehicle.progress_on_edge, 0.0)
    # Roundabout entry: la arista siguiente es de rotonda.
    if ei + 2 < len(np_):
        nxt_attrs = graph.get_edge_attributes(np_[ei + 1], np_[ei + 2])
        if nxt_attrs.get(ATTR_IS_ROUNDABOUT) and dist_to_next_node < _NEAR_ROUNDABOUT_ENTRY_M:
            return "near_roundabout_entry"
    # Intersection no señalizada: in_degree ≥ 2 + NodeType no gestionado en otro lado.
    if dist_to_next_node < INTERSECTION_DETECTION_ZONE_M:
        next_node = np_[ei + 1]
        try:
            in_deg = graph.graph.in_degree(next_node)
        except (AttributeError, TypeError):
            in_deg = 0
        if in_deg >= 2:
            node_attrs = graph.get_node_attributes(next_node)
            raw_type = node_attrs.get(ATTR_NODE_TYPE)
            type_str = raw_type.value if isinstance(raw_type, NodeType) else (
                str(raw_type) if raw_type is not None else None
            )
            if type_str not in _INTERSECTION_HANDLED_ELSEWHERE_TYPES:
                return "near_intersection_node"
    return "straight_section"


def _trigger_collision(
    v1: SimVehicle,
    v2: SimVehicle,
    blocked_edges: dict[tuple[int, int], object | None],
    pending_collisions: list[tuple[str, str, tuple[int, int]]] | None = None,
) -> None:
    """
    Marca dos vehículos como colisionados y bloquea el tramo indefinidamente.

    Comportamiento de gemelo digital: el choque NO se auto-limpia. Los dos
    vehículos se quedan en estado COLLISION (v=0) y la arista queda en
    ``blocked_edges`` con valor ``None`` (bloqueo permanente) hasta que el
    operador los retire por API (``/simulation/vehicles/{id}/clear-collision``).
    A* los evita multiplicando su peso por BLOCKED_EDGE_PENALTY_FACTOR en vez
    de excluirlas, para no perder conectividad cuando no hay alternativa.

    Si se pasa ``pending_collisions``, se añade una tupla
    ``(v1_id, v2_id, (u, v))`` para que el engine la transforme en un
    IncidentModel (tipo ACCIDENT) tras el tick.
    """
    for v in (v1, v2):
        v.status = VehicleStatus.COLLISION
        v.velocity = 0.0
        v.acceleration = 0.0
        v.collision_timer = 0.0  # sin timer — limpiado por API

    # Bloquear el tramo de la arista donde ocurrió la colisión (usando v1)
    np_ = v1.route.node_path
    ei = v1.current_edge_index
    if ei < len(np_) - 1:
        edge_key = (np_[ei], np_[ei + 1])
        blocked_edges[edge_key] = None
        if pending_collisions is not None:
            pending_collisions.append((v1.id, v2.id, edge_key))


def update_vehicles(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    dt: float,
    tl_controller: object | None = None,
    blocked_edges: dict[tuple[int, int], object | None] | None = None,
    tick_count: int = 0,
    closed_lanes: dict[tuple[int, int], set[int]] | None = None,
    pending_collisions: list[tuple[str, str, tuple[int, int]]] | None = None,
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> list[str]:
    """
    Avanza todos los vehículos activos a lo largo de sus rutas usando IDM.

    Args:
        vehicles:      Diccionario vehicle_id → SimVehicle (modificado in-place).
        graph:         Grafo de red vial.
        dt:            Intervalo de tiempo en segundos.
        tl_controller: TrafficLightController (opcional). Si se pasa, los
                       vehículos frenan ante semáforos en rojo/amarillo.
        blocked_edges: Mapa de aristas bloqueadas por colisiones (modificado
                       in-place cuando se detecta una nueva colisión). Valor
                       ``None`` = bloqueo permanente (retirada manual por API).
        tick_count:    Contador global de ticks. Usado para cadenciar el
                       reroute proactivo periódico (Plan D1).
        closed_lanes:  Mapa arista → set de carriles cerrados por incidentes.
                       MOBIL lo usa para descartar carriles cerrados como
                       destino y para forzar la salida de vehículos atrapados.

    Returns:
        Lista de vehicle_ids que terminaron su ruta en este tick.
    """
    if blocked_edges is None:
        blocked_edges = {}
    if closed_lanes is None:
        closed_lanes = {}
    if pending_collisions is None:
        pending_collisions = []

    # Snapshot de bloqueos ANTES del tick para detectar nuevos bloqueos al
    # final y disparar auto-reroute de los vehículos cuya ruta atraviese
    # alguna arista recién bloqueada (mitigación de pileups en cascada).
    blocked_before: set[tuple[int, int]] = set(blocked_edges.keys())

    with time_block("phys.build_edge_index_ms"):
        edge_index = _build_edge_index(vehicles)
        converging_edges = _build_converging_edges_index(edge_index)
    with time_block("phys.build_ring_occupancy_ms"):
        ring_occupancy = _build_ring_occupancy(vehicles, graph)
    with time_block("phys.build_entry_arm_index_ms"):
        entry_arms = _build_entry_arm_index(vehicles, graph)
    with time_block("phys.build_intersection_arm_index_ms"):
        intersection_arms = _build_intersection_arm_index(vehicles, graph)
    finished_ids: list[str] = []

    # Snapshot del lookup de bloqueos para `_advance_vehicle_idm`. Las nuevas
    # colisiones de este tick añaden entradas a `blocked_edges` durante el
    # bucle pero no se reflejan aquí: los afectados se cubren con el
    # `_reroute_affected_by_new_blocks` posterior. Esto sólo previene que
    # vehículos crucen aristas bloqueadas ANTES de que arrancara este tick.
    blocked_lookup: set[tuple[int, int]] | None = (
        set(blocked_edges) if blocked_edges else None
    )

    st = SplitTimer()
    n_active = 0

    for vehicle in list(vehicles.values()):
        if vehicle.status == VehicleStatus.FINISHED:
            continue

        # Vehículos en colisión: NO se auto-limpian (retirada manual por API).
        # Se dejan en place con v=0 hasta que el operador llame al endpoint.
        if vehicle.status == VehicleStatus.COLLISION:
            vehicle.velocity = 0.0
            continue

        # Vehículos pausados manualmente: no se mueven
        if vehicle.status == VehicleStatus.PAUSED:
            continue

        if vehicle.status == VehicleStatus.IDLE:
            vehicle.status = VehicleStatus.MOVING

        n_active += 1
        st.mark()  # arranque limpio por vehículo (no atribuir el filtro a leader_find)

        # Determinar el líder más restrictivo (vehículo, semáforo, yield-rotonda o señal).
        leader = _find_leader(vehicle, edge_index, graph)
        if tl_controller is not None:
            tl_leader = _check_traffic_light(vehicle, graph, tl_controller)
            if tl_leader is not None and (leader is None or tl_leader.gap_m < leader.gap_m):
                leader = tl_leader
        st.split("leader_find_ms")
        yield_leader = _find_roundabout_yield_leader(vehicle, graph, ring_occupancy, entry_arms)
        if yield_leader is not None and (leader is None or yield_leader.gap_m < leader.gap_m):
            leader = yield_leader
        st.split("round_yield_ms")
        sign_leader = _check_stop_yield_sign(
            vehicle, graph, edge_index, dt, converging_edges=converging_edges
        )
        if sign_leader is not None and (leader is None or sign_leader.gap_m < leader.gap_m):
            leader = sign_leader
        st.split("sign_check_ms")
        intersection_leader = _check_intersection_yield(vehicle, graph, intersection_arms)
        if intersection_leader is not None and (
            leader is None or intersection_leader.gap_m < leader.gap_m
        ):
            leader = intersection_leader
        st.split("intersection_yield_ms")

        # Plan D2 — dead-wall detection. Si el líder detectado es un vehículo
        # en COLLISION, la arista donde está ya figura en blocked_edges. Se
        # intenta reroute inmediato desde el siguiente nodo (sin esperar al
        # pase periódico). El IDM sigue frenando este tick; el reroute se
        # aplica a la ruta pendiente.
        if (
            leader is not None
            and leader.leader_id is not None
        ):
            ldr_v = vehicles.get(leader.leader_id)
            if ldr_v is not None and ldr_v.status == VehicleStatus.COLLISION:
                _maybe_reroute_around_blocks(
                    vehicle,
                    graph,
                    blocked_edges,
                    restricted_edges_by_vtype=restricted_edges_by_vtype,
                )

        # MOBIL: evaluar cambio de carril cada MOBIL_EVAL_INTERVAL_TICKS ticks
        # (el cooldown está escalonado al spawnear para repartir carga).
        # Excepción: si el vehículo está en un carril cerrado por un incidente,
        # se evalúa inmediatamente (bypass del cooldown) con force_change=True.
        np_ = vehicle.route.node_path
        ei = vehicle.current_edge_index
        trapped_in_closed = False
        if ei < len(np_) - 1:
            trapped_in_closed = vehicle.lane in closed_lanes.get(
                (np_[ei], np_[ei + 1]), set()
            )
        if vehicle.mobil_cooldown_ticks <= 0:
            _evaluate_lane_change(
                vehicle, edge_index, graph, leader,
                closed_lanes=closed_lanes, tick_count=tick_count,
            )
            vehicle.mobil_cooldown_ticks = MOBIL_EVAL_INTERVAL_TICKS
        else:
            # Trapped en carril cerrado: acelerar la próxima eval al cooldown
            # secundario en vez de bypassear el cooldown (que dispararía MOBIL
            # cada tick para todos los trapped — cuello dominante a 4k+ veh).
            if trapped_in_closed and vehicle.mobil_cooldown_ticks > MOBIL_TRAPPED_EVAL_INTERVAL_TICKS:
                vehicle.mobil_cooldown_ticks = MOBIL_TRAPPED_EVAL_INTERVAL_TICKS
            vehicle.mobil_cooldown_ticks -= 1
        st.split("mobil_ms")

        # Umbral de gap diferenciado según el tramo: en rotonda toleramos
        # gaps mayores antes de declarar choque (curvatura y waypoints).
        # (np_ y ei ya fueron definidos en el bloque MOBIL anterior.)
        in_roundabout = False
        if ei < len(np_) - 1:
            cur_attrs = graph.get_edge_attributes(np_[ei], np_[ei + 1])
            in_roundabout = bool(cur_attrs.get(ATTR_IS_ROUNDABOUT))
        gap_threshold = (
            COLLISION_GAP_THRESHOLD_ROUNDABOUT_M
            if in_roundabout
            else COLLISION_GAP_THRESHOLD_STRAIGHT_M
        )

        # Detección determinista de colisiones: gap pequeño SOSTENIDO + velocidad
        # relativa real (evita falsos positivos entre dos vehículos parados).
        rel_speed = 0.0
        if leader is not None:
            rel_speed = abs(vehicle.velocity - leader.velocity_ms)
        # Solapamiento geométrico con ambos vehículos efectivamente parados:
        # el sentinel de _find_leader devuelve gap_m=0.01 cuando hay overlap.
        # Sin este flag, `rel_speed==0` impedía detectar stacks permanentes.
        is_geometric_overlap = (
            leader is not None
            and leader.leader_id is not None
            and leader.gap_m <= 0.05
            and leader.velocity_ms == 0.0
            and vehicle.velocity <= 0.2
        )
        too_close = (
            leader is not None
            and leader.leader_id is not None
            and leader.gap_m < gap_threshold
            and (rel_speed >= COLLISION_RELATIVE_SPEED_MIN_MS or is_geometric_overlap)
            and vehicle.status == VehicleStatus.MOVING
        )
        if too_close:
            vehicle.proximity_timer = getattr(vehicle, "proximity_timer", 0.0) + dt
            if vehicle.proximity_timer >= COLLISION_PROXIMITY_DURATION_S:
                other = vehicles.get(leader.leader_id)  # type: ignore[union-attr]
                if other is not None and other.status == VehicleStatus.MOVING:
                    _trigger_collision(
                        vehicle, other, blocked_edges, pending_collisions
                    )
                    registry.inc(
                        f"phys.collision.{_classify_collision_segment(vehicle, graph, tick_count)}"
                    )
                    vehicle.proximity_timer = 0.0
                    st.split("collision_check_ms")
                    continue  # no avanzar este tick
        else:
            vehicle.proximity_timer = 0.0
        st.split("collision_check_ms")

        if _advance_vehicle_idm(
            vehicle,
            graph,
            dt,
            leader,
            tl_ref=tl_controller,
            blocked_edges_set=blocked_lookup,
            closed_lanes=closed_lanes,
        ):
            finished_ids.append(vehicle.id)
        st.split("idm_ms")

    st.emit("phys.")
    registry.gauge("phys.n_active_in_loop", n_active)

    # Auto-reroute en bloque: detectar aristas bloqueadas en este tick y
    # re-planificar a los vehículos MOVING cuya ruta pendiente las atraviese.
    new_blocks = set(blocked_edges.keys()) - blocked_before
    if new_blocks:
        _mark_new_blocks_detected(tick_count)
        with time_block("phys.reroute_new_blocks_ms"):
            _reroute_affected_by_new_blocks(
                vehicles,
                graph,
                new_blocks,
                blocked_edges,
                restricted_edges_by_vtype=restricted_edges_by_vtype,
            )

    # Plan D1: reroute proactivo amortizado por tick. Cada tick revisa
    # PERIODIC_REROUTE_BATCH_SIZE vehículos desde un cursor rotatorio, cubriendo
    # todos los activos cada ceil(N / batch) ticks. Reemplaza el pase all-in-one
    # cada PERIODIC_REROUTE_TICK_INTERVAL ticks que producía picos de 1-1.5 s.
    # Se ejecuta también con ZBE-only (sin blocked_edges) — la función
    # internamente decide saltarse el pase si no hay nada que mirar.
    with time_block("phys.reroute_periodic_batch_ms"):
        _periodic_reroute_batch(
            vehicles,
            graph,
            blocked_edges,
            tick_count,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
        )

    return finished_ids


# ---------------------------------------------------------------------------
# Multi-thread parallel physics (ThreadPoolExecutor + zone bucketing)
# ---------------------------------------------------------------------------
#
# Cada celda de la cuadrícula espacial (ver `app.core.spatial_partition`) se
# procesa en un hilo del pool. Los workers comparten memoria con el main:
# leen los índices globales (edge_index, ring_occupancy, entry_arms,
# intersection_arms) construidos pre-dispatch y mutan in-place los
# `SimVehicle` de su bucket. Sin serialización, sin IPC.
#
# Sincronización: un único `_COLLISION_LOCK` protege las mutaciones cross-
# thread (trigger de colisión, append a `pending_collisions`, mutación de
# `blocked_edges`). Las escrituras a campos del propio vehículo son owner-
# thread: cada `SimVehicle` está en exactamente un bucket por tick.
#
# Free-threading (Python 3.14t): sin GIL los workers escalan linealmente con
# cores. Bajo GIL clásico, la ganancia viene de eliminar el coste de IPC del
# antiguo ProcessPoolExecutor (serialización + deserialización de cada
# vehículo) — los workers se serializan en CPU pero las lecturas/escrituras
# en memoria compartida son gratis.

_COLLISION_LOCK = threading.Lock()
_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _ensure_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Singleton lazy del pool de hilos. No requiere re-creación por grafo
    (los workers no capturan estado del grafo; lo reciben por argumento)."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=os.cpu_count() or 1,
            thread_name_prefix="phys",
        )
    return _EXECUTOR


def _process_bucket(
    bucket: list[SimVehicle],
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    dt: float,
    edge_index: dict,
    ring_occupancy: dict,
    entry_arms: dict,
    intersection_arms: dict,
    tl_controller: object | None,
    blocked_snapshot: frozenset[tuple[int, int]] | None,
    blocked_edges: dict[tuple[int, int], object | None],
    closed_lanes: dict[tuple[int, int], set[int]],
    restricted_by_vtype: dict[str, set[tuple[int, int]]] | None,
    pending_collisions: list[tuple[str, str, tuple[int, int]]],
    tick_count: int,
    converging_edges: dict[int, list[tuple[int, int]]] | None = None,
) -> tuple[list[str], dict[str, int], list[tuple[str, str]]]:
    """
    Worker thread: pipeline completo (líder + yields + MOBIL + colisión +
    IDM) para un bucket de vehículos de una zona espacial.

    Mutaciones:
      - Owner-thread sobre cada `SimVehicle` del bucket (velocity, lane,
        position, status, timers). Safe sin lock — cada vehículo está en
        exactamente un bucket.
      - **No** dispara `_trigger_collision` (no toma lock global). Cuando
        detecta una colisión sostenida, registra el par `(ego_id, other_id)`
        en una lista local y devuelve la lista al main thread. Este aplica
        los trigger en serie post-`asyncio.gather` (dedup natural por par).

    Returns:
        (finished_ids_local, split_acc_ns, collision_candidates) —
        - IDs terminados en este bucket.
        - Acumuladores del SplitTimer (ns) para mergear en el main thread.
        - Pares (ego_id, other_id) candidatos a colisión.
    """
    local_finished: list[str] = []
    collision_candidates: list[tuple[str, str]] = []
    st = SplitTimer()

    for v in bucket:
        # Defensa: otro hilo pudo marcar este veh. como COLLISION mid-tick
        # si fue detectado como líder en una colisión cross-zone. En ese
        # caso v.velocity ya está a 0 y debe saltarse el resto del pipeline.
        if v.status != VehicleStatus.MOVING:
            continue

        st.mark()

        leader = _find_leader(v, edge_index, graph)
        if tl_controller is not None:
            tl_ldr = _check_traffic_light(v, graph, tl_controller)
            if tl_ldr is not None and (leader is None or tl_ldr.gap_m < leader.gap_m):
                leader = tl_ldr
        st.split("leader_find_ms")

        yield_ldr = _find_roundabout_yield_leader(v, graph, ring_occupancy, entry_arms)
        if yield_ldr is not None and (leader is None or yield_ldr.gap_m < leader.gap_m):
            leader = yield_ldr
        st.split("round_yield_ms")

        sign_ldr = _check_stop_yield_sign(
            v, graph, edge_index, dt, converging_edges=converging_edges
        )
        if sign_ldr is not None and (leader is None or sign_ldr.gap_m < leader.gap_m):
            leader = sign_ldr
        st.split("sign_check_ms")

        intersection_ldr = _check_intersection_yield(v, graph, intersection_arms)
        if intersection_ldr is not None and (
            leader is None or intersection_ldr.gap_m < leader.gap_m
        ):
            leader = intersection_ldr
        st.split("intersection_yield_ms")

        # Plan D2 — dead-wall detection: reroute inmediato si el líder está
        # en COLLISION (su arista ya está en blocked_edges, pero la ruta
        # propia puede haberse calculado antes del bloqueo).
        if leader is not None and leader.leader_id is not None:
            ldr_v = vehicles.get(leader.leader_id)
            if ldr_v is not None and ldr_v.status == VehicleStatus.COLLISION:
                _maybe_reroute_around_blocks(
                    v,
                    graph,
                    blocked_edges,
                    restricted_edges_by_vtype=restricted_by_vtype,
                )

        # MOBIL: cambio de carril cada N ticks o forzado si está atrapado en
        # carril cerrado. Lectura de edge_index — sin contention bajo lectores
        # concurrentes (el dict no se muta durante el tick).
        np_ = v.route.node_path
        ei = v.current_edge_index
        trapped_p = False
        if ei < len(np_) - 1:
            trapped_p = v.lane in closed_lanes.get((np_[ei], np_[ei + 1]), set())
        if v.mobil_cooldown_ticks <= 0:
            _evaluate_lane_change(
                v, edge_index, graph, leader,
                closed_lanes=closed_lanes, tick_count=tick_count,
            )
            v.mobil_cooldown_ticks = MOBIL_EVAL_INTERVAL_TICKS
        else:
            # Trapped: acelerar la próxima eval al cooldown secundario en vez
            # de bypass total (que dispararía MOBIL cada tick a 4k+ veh).
            if trapped_p and v.mobil_cooldown_ticks > MOBIL_TRAPPED_EVAL_INTERVAL_TICKS:
                v.mobil_cooldown_ticks = MOBIL_TRAPPED_EVAL_INTERVAL_TICKS
            v.mobil_cooldown_ticks -= 1
        st.split("mobil_ms")

        # Detección de colisión: gap pequeño sostenido + velocidad relativa
        # (o solapamiento geométrico si ambos están casi parados).
        in_round = False
        if ei < len(np_) - 1:
            cur_attrs = graph.get_edge_attributes(np_[ei], np_[ei + 1])
            in_round = bool(cur_attrs.get(ATTR_IS_ROUNDABOUT))
        gap_threshold = (
            COLLISION_GAP_THRESHOLD_ROUNDABOUT_M
            if in_round
            else COLLISION_GAP_THRESHOLD_STRAIGHT_M
        )

        rel_speed = 0.0
        if leader is not None:
            rel_speed = abs(v.velocity - leader.velocity_ms)
        is_geometric_overlap = (
            leader is not None
            and leader.leader_id is not None
            and leader.gap_m <= 0.05
            and leader.velocity_ms == 0.0
            and v.velocity <= 0.2
        )
        too_close = (
            leader is not None
            and leader.leader_id is not None
            and leader.gap_m < gap_threshold
            and (rel_speed >= COLLISION_RELATIVE_SPEED_MIN_MS or is_geometric_overlap)
            and v.status == VehicleStatus.MOVING
        )

        if too_close:
            v.proximity_timer = getattr(v, "proximity_timer", 0.0) + dt
            if v.proximity_timer >= COLLISION_PROXIMITY_DURATION_S:
                other = vehicles.get(leader.leader_id)  # type: ignore[union-attr]
                if other is not None and other.status == VehicleStatus.MOVING:
                    # Sin lock: registramos el par y el main thread aplica el
                    # trigger post-gather (deduplicado natural por par).
                    collision_candidates.append((v.id, other.id))
                    v.proximity_timer = 0.0
                    st.split("collision_check_ms")
                    continue  # no avanzar IDM este tick (será marcado COLLISION en main)
        else:
            v.proximity_timer = 0.0
        st.split("collision_check_ms")

        # IDM advance — mutación owner-thread sobre v.
        if _advance_vehicle_idm(
            v,
            graph,
            dt,
            leader,
            tl_ref=tl_controller,
            blocked_edges_set=blocked_snapshot,
            closed_lanes=closed_lanes,
        ):
            local_finished.append(v.id)
        st.split("idm_ms")

    return local_finished, dict(st.acc), collision_candidates


async def update_vehicles_parallel(
    vehicles: "dict[str, SimVehicle]",  # type: ignore[name-defined]
    graph: "RoadNetworkGraph",          # type: ignore[name-defined]
    dt: float,
    tl_controller: object | None = None,
    blocked_edges: "dict[tuple[int, int], object | None] | None" = None,
    tick_count: int = 0,
    closed_lanes: "dict[tuple[int, int], set[int]] | None" = None,
    pending_collisions: "list[tuple[str, str, tuple[int, int]]] | None" = None,
    zone_manager: object | None = None,
) -> list[str]:
    """
    Avanza todos los vehículos en paralelo bucketeando por zonas espaciales.

    Pipeline:
      1. Categorizar vehículos (FINISHED → pre_finished; COLLISION/PAUSED
         entran al edge_index como obstáculo pero no se despachan).
      2. Construir índices globales en main thread (edge_index, ring_occupancy,
         entry_arms, intersection_arms).
      3. Bucketear los `MOVING` por celda espacial (`SpatialGrid`), ordenados
         DESC por tamaño (LPT) para work-stealing efectivo.
      4. Despachar una tarea por bucket al `ThreadPoolExecutor`. Cada worker
         ejecuta el pipeline completo (líder + yields + MOBIL + colisión +
         IDM) sobre su bucket. Sin serialización, sin IPC.
      5. Agregar `finished_ids` y métricas; ejecutar reroute post-tick.

    Modos:
      - n < `VEHICLE_PHYSICS_PARALLEL_THRESHOLD`: `asyncio.to_thread(update_vehicles)`.
      - n >= threshold: bucketing por zona + ThreadPoolExecutor.
      - Fallback automático al path secuencial si el pool falla.

    Determinismo: el bucketing cambia el orden de procesamiento entre celdas,
    por lo que MOBIL puede producir resultados ligeramente distintos entre
    runs en flotas densas. La física por vehículo aislado es idéntica al
    path secuencial.

    Returns:
        Lista de vehicle_ids que terminaron su ruta en este tick.
    """
    from app.core.spatial_partition import ensure_grid

    n = len(vehicles)

    # Snapshot del dict de restricciones por vtype. El ZoneManager lo reasigna
    # en bloque (no muta in-place), así que la referencia es estable durante el tick.
    restricted_by_vtype: dict[str, set[tuple[int, int]]] | None = None
    if zone_manager is not None:
        try:
            restricted_by_vtype = zone_manager.restricted_edges_by_vtype()  # type: ignore[attr-defined]
        except Exception:
            restricted_by_vtype = None

    registry.gauge("phys.path", 0 if n < VEHICLE_PHYSICS_PARALLEL_THRESHOLD else 1)

    if n < VEHICLE_PHYSICS_PARALLEL_THRESHOLD:
        return await asyncio.to_thread(
            update_vehicles,
            vehicles,
            graph,
            dt,
            tl_controller,
            blocked_edges,
            tick_count,
            closed_lanes,
            pending_collisions,
            restricted_by_vtype,
        )

    try:
        if blocked_edges is None:
            blocked_edges = {}
        if closed_lanes is None:
            closed_lanes = {}
        if pending_collisions is None:
            pending_collisions = []

        # Snapshot previo: las aristas añadidas durante este tick (por
        # colisiones detectadas en workers) activarán auto-reroute post-tick.
        blocked_before: set[tuple[int, int]] = set(blocked_edges.keys())
        _parallel_t0_ns = time.perf_counter_ns()

        # Categorización:
        #   - FINISHED → pre_finished (out)
        #   - COLLISION / PAUSED → solo edge_index (visible como líder)
        #   - IDLE → MOVING; MOVING → bucket
        pre_finished: list[str] = []
        active_vehicles: dict[str, SimVehicle] = {}
        index_vehicles: dict[str, SimVehicle] = {}
        for v in vehicles.values():
            if v.status == VehicleStatus.FINISHED:
                pre_finished.append(v.id)
                continue
            if v.status == VehicleStatus.COLLISION:
                v.velocity = 0.0
                index_vehicles[v.id] = v
                continue
            if v.status == VehicleStatus.PAUSED:
                index_vehicles[v.id] = v
                continue
            if v.status == VehicleStatus.IDLE:
                v.status = VehicleStatus.MOVING
            active_vehicles[v.id] = v
            index_vehicles[v.id] = v

        with time_block("phys.build_edge_index_ms"):
            edge_index = _build_edge_index(index_vehicles)
            converging_edges = _build_converging_edges_index(edge_index)
        with time_block("phys.build_ring_occupancy_ms"):
            ring_occupancy = _build_ring_occupancy(index_vehicles, graph)
        with time_block("phys.build_entry_arm_index_ms"):
            entry_arms = _build_entry_arm_index(index_vehicles, graph)
        with time_block("phys.build_intersection_arm_index_ms"):
            intersection_arms = _build_intersection_arm_index(index_vehicles, graph)

        # Snapshot inmutable de bloqueos pre-tick para hard-stop en IDM.
        # Las entradas que añadan los workers por colisiones quedan fuera
        # de este snapshot y se procesan por `_reroute_affected_by_new_blocks`
        # post-tick.
        blocked_snapshot: frozenset[tuple[int, int]] | None = (
            frozenset(blocked_edges.keys()) if blocked_edges else None
        )

        # Bucketing por celda espacial con LPT scheduling.
        grid = ensure_grid(graph)
        buckets = grid.bucket_vehicles(list(active_vehicles.values()))

        results_finished: list[str] = []
        agg_acc: dict[str, int] = {}

        if buckets:
            executor = _ensure_executor()
            loop = asyncio.get_running_loop()
            _idm_t0_ns = time.perf_counter_ns()
            futures = [
                loop.run_in_executor(
                    executor,
                    _process_bucket,
                    bucket,
                    vehicles,
                    graph,
                    dt,
                    edge_index,
                    ring_occupancy,
                    entry_arms,
                    intersection_arms,
                    tl_controller,
                    blocked_snapshot,
                    blocked_edges,
                    closed_lanes,
                    restricted_by_vtype,
                    pending_collisions,
                    tick_count,
                    converging_edges,
                )
                for bucket in buckets
            ]
            results = await asyncio.gather(*futures)
            registry.record(
                "phys.idm_workers_wallclock_ms",
                (time.perf_counter_ns() - _idm_t0_ns) / 1_000_000.0,
            )
            registry.gauge("phys.n_workers", min(os.cpu_count() or 1, len(buckets)))
            registry.gauge("phys.n_buckets", len(buckets))
            registry.gauge("phys.max_bucket_size", max((len(b) for b in buckets), default=0))

            all_collision_candidates: list[tuple[str, str]] = []
            for chunk_finished, chunk_acc, chunk_collisions in results:
                results_finished.extend(chunk_finished)
                for name, ns in chunk_acc.items():
                    agg_acc[name] = agg_acc.get(name, 0) + ns
                all_collision_candidates.extend(chunk_collisions)

            # Flush serial post-gather: dedup por par y aplicar trigger sin lock
            # (single-thread aquí). Elimina la contención de `_COLLISION_LOCK`
            # bajo bursts de colisiones que serializaba a todos los workers.
            seen_pairs: set[frozenset] = set()
            for ego_id, other_id in all_collision_candidates:
                pair = frozenset((ego_id, other_id))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                ego = vehicles.get(ego_id)
                other = vehicles.get(other_id)
                if ego is None or other is None:
                    continue
                if ego.status != VehicleStatus.MOVING or other.status != VehicleStatus.MOVING:
                    continue
                _trigger_collision(ego, other, blocked_edges, pending_collisions)
                registry.inc(
                    f"phys.collision.{_classify_collision_segment(ego, graph, tick_count)}"
                )

        # Emitir tiempos agregados de los SplitTimer per-worker (ms). El
        # factor `_SPLIT_SAMPLE_EVERY` (1 por defecto) escala las muestras
        # cuando el sampling per-vehículo está activo, para preservar la
        # magnitud cross-veh estimada.
        from app.core.instrumentation import _SPLIT_SAMPLE_EVERY as _sse
        for name, ns in agg_acc.items():
            registry.record(f"phys.{name}", (ns * _sse) / 1_000_000.0)
        registry.gauge("phys.n_active_in_loop", len(active_vehicles))

    except Exception:
        logger.exception(
            "ThreadPool de física falló (n=%d veh); degradando a asyncio.to_thread", n
        )
        global _EXECUTOR
        _EXECUTOR = None
        return await asyncio.to_thread(
            update_vehicles,
            vehicles,
            graph,
            dt,
            tl_controller,
            blocked_edges,
            tick_count,
            closed_lanes,
            pending_collisions,
            restricted_by_vtype,
        )

    finished_ids: list[str] = list(pre_finished)
    finished_ids.extend(results_finished)

    # Auto-reroute de vehículos afectados por bloqueos surgidos en este tick.
    new_blocks = set(blocked_edges.keys()) - blocked_before
    if new_blocks:
        _mark_new_blocks_detected(tick_count)
        with time_block("phys.reroute_new_blocks_ms"):
            _reroute_affected_by_new_blocks(
                vehicles,
                graph,
                new_blocks,
                blocked_edges,
                restricted_edges_by_vtype=restricted_by_vtype,
            )

    # Plan D1: pase proactivo amortizado por tick. Helper sale temprano si
    # no hay bloqueos ni restricciones de zona.
    with time_block("phys.reroute_periodic_batch_ms"):
        _periodic_reroute_batch(
            vehicles,
            graph,
            blocked_edges,
            tick_count,
            restricted_edges_by_vtype=restricted_by_vtype,
        )

    registry.record(
        "phys.parallel_total_ms",
        (time.perf_counter_ns() - _parallel_t0_ns) / 1_000_000.0,
    )
    return finished_ids

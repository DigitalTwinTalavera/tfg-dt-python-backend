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
import concurrent.futures
import logging
import math
import multiprocessing
import os
from dataclasses import dataclass

from app.core.constants import (
    ATTR_CURVE_VMAX,
    ATTR_EDGE_ID,
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_MID_TLS,
    ATTR_NODE_TYPE,
    ATTR_ROUNDABOUT_ID,
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
    KMH_TO_MS,
    LOOKAHEAD_ENTRY_TRIGGER_M,
    LOOKAHEAD_ROUNDABOUT_TRIGGER_M,
    MAX_EMERGENCY_DECEL_MS2,
    MIN_EDGE_LENGTH_M,
    MIN_ROUNDABOUT_RADIUS_M,
    MOBIL_EVAL_INTERVAL_TICKS,
    MOBIL_MIN_VELOCITY_MS,
    MOBIL_MIN_DIST_TO_EDGE_END_M,
    PERIODIC_REROUTE_BATCH_SIZE,
    PERIODIC_REROUTE_TICK_INTERVAL,
    SIGN_DETECTION_ZONE_M,
    STOP_SIGN_DWELL_SPEED_MS,
    STOP_SIGN_DWELL_TIME_S,
    TL_PHASE_GREEN,
    TL_PHASE_RED,
    TL_PHASE_YELLOW,
    VEHICLE_LENGTH_M,
    VEHICLE_PHYSICS_PARALLEL_THRESHOLD,
    YELLOW_BRAKE_DISTANCE_M,
    YIELD_DETECTION_ZONE_M,
    YIELD_GAP_MIN_M,
    YIELD_SIGN_GAP_MIN_M,
    YIELD_SIGN_TTC_S,
    YIELD_TTC_THRESHOLD_S,
)
from app.core.physics.idm import IDMModel
from app.core.physics.mobil import (
    LaneChangeDirection,
    LaneContext,
    MOBILModel,
)
from app.core.physics.vehicle_types import PROFILES, VehicleType
from app.models.enums import NodeType, VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M: float = 6_371_000.0

# Un IDMModel por tipo de vehículo. Stateless tras construcción → seguro para
# multiprocessing. Se indexa por VehicleType para evitar la construcción
# repetida en el hot-path del tick.
_IDM_BY_TYPE: dict[VehicleType, IDMModel] = {
    vtype: IDMModel(profile.idm) for vtype, profile in PROFILES.items()
}
# Fallback para vehículos sin vtype explícito (tests legacy, serialización vieja).
_IDM_FALLBACK = IDMModel()

# MOBILModel por tipo de vehículo (usa los mismos IDMParameters que el IDM).
# Stateless tras construcción → seguro para multiprocessing.
_MOBIL_BY_TYPE: dict[VehicleType, MOBILModel] = {
    vtype: MOBILModel(idm_params=profile.idm) for vtype, profile in PROFILES.items()
}
_MOBIL_FALLBACK = MOBILModel()


def _mobil_for(vehicle: SimVehicle) -> MOBILModel:
    """Devuelve el MOBILModel correspondiente al tipo del vehículo."""
    vtype = getattr(vehicle, "vtype", None)
    if vtype is None:
        return _MOBIL_FALLBACK
    return _MOBIL_BY_TYPE.get(vtype, _MOBIL_FALLBACK)


def _idm_for(vehicle: SimVehicle) -> IDMModel:
    """Devuelve el IDMModel correspondiente al tipo del vehículo."""
    vtype = getattr(vehicle, "vtype", None)
    if vtype is None:
        return _IDM_FALLBACK
    return _IDM_BY_TYPE.get(vtype, _IDM_FALLBACK)

# Caché de longitudes de segmentos por arista (start_node, end_node).
_SEG_CACHE: dict[tuple[int, int], tuple[list[float], float]] = {}


# ---------------------------------------------------------------------------
# Neighbor info
# ---------------------------------------------------------------------------

@dataclass
class NeighborInfo:
    """
    Información sobre el vehículo líder (o semáforo virtual) que precede a un ego.

    Attrs:
        gap_m:       Distancia bumper-to-bumper en metros (≥ 0.01 m).
        velocity_ms: Velocidad del líder en m/s (0.0 para semáforo en rojo).
        leader_id:   ID del vehículo líder real, o None si es un líder virtual (semáforo).
    """
    gap_m: float
    velocity_ms: float
    leader_id: str | None = None


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


# ---------------------------------------------------------------------------
# Leader detection
# ---------------------------------------------------------------------------

def _build_edge_index(
    vehicles: dict[str, SimVehicle],
) -> dict[tuple[int, int], list[SimVehicle]]:
    """
    Construye un índice {(start_node, end_node): [vehicles sorted by progress desc]}.

    Permite encontrar el líder de cualquier vehículo en O(k) donde k es el
    número de vehículos en la misma arista (en promedio muy pequeño).

    Solo incluye vehículos activos (no FINISHED).
    """
    index: dict[tuple[int, int], list[SimVehicle]] = {}
    for v in vehicles.values():
        if v.status == VehicleStatus.FINISHED:
            continue
        node_path = v.route.node_path
        ei = v.current_edge_index
        if ei < len(node_path) - 1:
            key = (node_path[ei], node_path[ei + 1])
            index.setdefault(key, []).append(v)
    # Ordenar por progreso descendente → el primero de la lista es el líder
    for key in index:
        index[key].sort(key=lambda v: v.progress_on_edge, reverse=True)
    return index


def _find_leader(
    vehicle: SimVehicle,
    edge_index: dict[tuple[int, int], list[SimVehicle]],
    graph: RoadNetworkGraph,
) -> NeighborInfo | None:
    """
    Busca el vehículo más cercano por delante en la misma arista.

    Returns:
        NeighborInfo con el gap bumper-to-bumper y la velocidad del líder,
        o None si la arista está libre.
    """
    node_path = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(node_path) - 1:
        return None

    key = (node_path[ei], node_path[ei + 1])
    edge_attrs = graph.get_edge_attributes(node_path[ei], node_path[ei + 1])
    edge_len = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)

    ego_lane = getattr(vehicle, "lane", 0)
    for candidate in edge_index.get(key, []):
        if candidate.id == vehicle.id:
            continue
        # Sólo vehículos en el mismo carril bloquean al ego.
        if getattr(candidate, "lane", 0) != ego_lane:
            continue
        if candidate.progress_on_edge > vehicle.progress_on_edge:
            cand_len = getattr(candidate, "length_m", VEHICLE_LENGTH_M)
            raw_gap = (candidate.progress_on_edge - vehicle.progress_on_edge) * edge_len - cand_len
            if raw_gap <= 0.0:
                # Overlap geométrico: parada de emergencia — forzar velocidad cero
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
            distance_gate = False
        if vehicle.progress_on_edge > 0.70 or distance_gate:
            next_key = (node_path[ei + 1], node_path[ei + 2])
            next_edge_len = max(float(next_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
            candidates_next = edge_index.get(next_key, [])
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
            if same_ring:
                candidates_next = [
                    c for c in candidates_next if getattr(c, "lane", 0) == ego_lane
                ]
            elif is_entry:
                # El ego entrará en un carril determinista según su ruta; los
                # circulantes del anillo en otro carril no le afectan.
                ego_target_lane = _target_roundabout_lane(vehicle, graph)
                candidates_next = [
                    c for c in candidates_next if getattr(c, "lane", 0) == ego_target_lane
                ]
            if candidates_next:
                # El que tiene menor progress está más cerca del inicio → el que más molesta.
                first_on_next = min(candidates_next, key=lambda v: v.progress_on_edge)
                if first_on_next.id != vehicle.id:
                    dist_on_next = first_on_next.progress_on_edge * next_edge_len
                    leader_len = getattr(first_on_next, "length_m", VEHICLE_LENGTH_M)
                    total_gap = remaining_current_m + dist_on_next - leader_len
                    return NeighborInfo(
                        gap_m=max(total_gap, 0.01),
                        velocity_ms=first_on_next.velocity,
                        leader_id=first_on_next.id,
                    )

    return None


def _build_ring_occupancy(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
) -> dict[int, list[SimVehicle]]:
    """
    Índice {rotunda_id → vehículos que circulan actualmente en ese anillo}.

    Se precomputa una vez por tick y lo reutiliza `_find_roundabout_yield_leader`
    para evitar recorrer todas las aristas de la rotonda en cada candidato a
    entrar.
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

    Función pura (sin RNG) — puede llamarse varias veces por tick y cachearse.
    Devuelve 0 si el vehículo no tiene arista de anillo en su ruta.
    """
    np_ = v.route.node_path
    ei = v.current_edge_index
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
    if ring_arc_count <= 1:
        return 0  # carril exterior: saldrá en la próxima
    return first_n_lanes - 1  # carril interior: dará (al menos) un arco más


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
        # `_arc_distance_on_ring` respeta la direccionalidad del anillo y
        # devuelve inf si la ruta del otro no pasa por nuestro entry_node.
        arc = _arc_distance_on_ring(other, entry_node, graph)
        if math.isinf(arc):
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

    # Líder virtual: parado en la línea de entrada. El IDM frenará para no
    # cruzarlo. Gap = distancia a la línea menos un margen de seguridad.
    gap = max(dist_to_ring - 0.5, 0.2)
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
    """
    cached = edge_attrs.get(ATTR_CURVE_VMAX)
    if cached is not None:
        return float(cached)
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
    same_edge_vehicles: list[SimVehicle],
    edge_len: float,
) -> LaneContext:
    """
    Construye el LaneContext para el carril `target_lane` en la arista del ego.

    Recorre sólo los vehículos en la misma arista (ya filtrados por el caller),
    busca el más cercano por delante y el más cercano por detrás en `target_lane`
    y devuelve sus gaps bumper-to-bumper y velocidades para evaluar MOBIL.
    """
    ctx = LaneContext(lane_index=target_lane)
    ego_len = getattr(ego, "length_m", VEHICLE_LENGTH_M)
    best_front_gap = float("inf")
    best_back_gap = float("inf")
    for cand in same_edge_vehicles:
        if cand.id == ego.id:
            continue
        if getattr(cand, "lane", 0) != target_lane:
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
    edge_index: dict[tuple[int, int], list[SimVehicle]],
    graph: RoadNetworkGraph,
    leader: NeighborInfo | None,
    closed_lanes: dict[tuple[int, int], set[int]] | None = None,
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

    same_edge = edge_index.get(key, [])

    # Construir contextos sólo para carriles existentes. Convención: lane 0 es
    # el carril derecho (el más cercano al bordillo); lane+1 es el izquierdo.
    lane_left_ctx: LaneContext | None = None
    lane_right_ctx: LaneContext | None = None
    if current_lane + 1 < n_lanes:
        lane_left_ctx = _build_lane_context(
            vehicle, current_lane + 1, same_edge, edge_len
        )
    if current_lane - 1 >= 0:
        lane_right_ctx = _build_lane_context(
            vehicle, current_lane - 1, same_edge, edge_len
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


# ---------------------------------------------------------------------------
# STOP / YIELD sign checks (Fase 7.1)
# ---------------------------------------------------------------------------

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
    edge_index: dict[tuple[int, int], list[SimVehicle]],
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
    for (u, w), others in edge_index.items():
        if w != end_node or (u == start_node and w == end_node):
            continue
        other_attrs = graph.get_edge_attributes(u, w)
        other_len = max(float(other_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
        for other in others:
            if other.id == vehicle.id:
                continue
            dist_other_to_node = other_len * (1.0 - other.progress_on_edge)
            ttc = dist_other_to_node / max(other.velocity, 1.0)
            if ttc < YIELD_SIGN_TTC_S or dist_other_to_node < YIELD_SIGN_GAP_MIN_M:
                gap = max(dist_to_sign - VEHICLE_LENGTH_M, 0.2)
                return NeighborInfo(gap_m=gap, velocity_ms=0.0)

    return None


# ---------------------------------------------------------------------------
# IDM advance
# ---------------------------------------------------------------------------

def _advance_vehicle_idm(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    dt: float,
    leader: NeighborInfo | None,
    tl_ref: object | None = None,
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
      5. Interpola la posición geográfica con los waypoints reales.

    Args:
        tl_ref: Objeto con método get_phase_for_edge(node_id, edge) → str,
                opcional. Cuando se proporciona, activa el hard-stop de
                seguridad en el bucle de avance de aristas.

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
        mid_stop_hit = False
        if tl_ref is not None:
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
            if tl_ref is not None:
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
            _, _, end_heading = _position_along_waypoints(
                outgoing_wps, 1.0, _cache_key=(start_n, end_n)
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
                vehicle.lane = min(cur_lane, new_lanes - 1)
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
    lon, lat, heading = _position_along_waypoints(
        waypoints, vehicle.progress_on_edge, _cache_key=(start_n, end_n)
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

def _maybe_reroute_around_blocks(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    blocked_edges: dict[tuple[int, int], object | None],
    trigger_blocks: set[tuple[int, int]] | None = None,
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> bool:
    """
    Re-rutea un vehículo individual si su ruta pendiente toca alguna arista
    de ``trigger_blocks`` (subconjunto relevante — típicamente bloques recién
    creados o el conjunto total). Si se pasa ``None`` se usa ``blocked_edges``
    completo (modo periódico / dead-wall).

    La arista CURRENT (ei → ei+1) NO se re-rutea: el vehículo ya está sobre
    ella y comprometido a su geometría. Se re-planifica desde NEXT node
    (``node_path[ei+1]``) hacia el destino; se mantiene progreso, carril y
    prefijo [0..ei].

    Si A* devuelve una ruta que aún contiene algún bloqueo conocido (no hay
    alternativa real), no se muta: seguir con el camino original penalizado
    es equivalente y evita churn.

    ``restricted_edges_by_vtype`` (vtype_str → set[(u,v)]) se inyecta desde el
    ``ZoneManager``. Si el vtype del vehículo tiene restricciones, se pasan a
    A* para que rodee la zona, y se descartan rutas que metan al vehículo en
    una zona donde su ruta original no entraba (mejora estricta).

    Returns:
        True si el vehículo fue re-ruteado; False en cualquier otro caso.
    """
    if vehicle.status != VehicleStatus.MOVING:
        return False
    np_ = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(np_) - 1:
        return False

    check_set = trigger_blocks if trigger_blocks is not None else set(blocked_edges.keys())
    if not check_set:
        return False

    # ¿Alguna arista PENDIENTE (a partir de ei+1) toca el conjunto de trigger?
    hit = False
    for i in range(ei + 1, len(np_) - 1):
        if (np_[i], np_[i + 1]) in check_set:
            hit = True
            break
    if not hit:
        return False

    from app.core.route import RouteInfo, compute_route

    pivot_node = np_[ei + 1]
    end_node = vehicle.route.end_node_id

    restricted: set[tuple[int, int]] | None = None
    if restricted_edges_by_vtype is not None:
        vt = getattr(vehicle.vtype, "value", None)
        if vt is not None:
            r = restricted_edges_by_vtype.get(vt)
            if r:
                restricted = r

    new_route = compute_route(
        graph,
        pivot_node,
        end_node,
        blocked_edges=blocked_edges,
        restricted_edges=restricted,
    )
    if new_route is None or len(new_route.node_path) < 2:
        return False

    nnp = new_route.node_path
    # Si la nueva ruta sigue atravesando un bloqueo conocido, no aporta.
    blocked_set = set(blocked_edges.keys())
    if any((nnp[i], nnp[i + 1]) in blocked_set for i in range(len(nnp) - 1)):
        return False
    # Si la nueva ruta introduce un cruce de zona restringida que la vieja
    # NO tenía, descartar (no empeorar). Si ambas cruzan, aceptar el reroute
    # — A* eligió el menos malo dada la penalización ZBE_EDGE_PENALTY_FACTOR.
    if restricted:
        new_hits = any(
            (nnp[i], nnp[i + 1]) in restricted for i in range(len(nnp) - 1)
        )
        if new_hits:
            old_hits = any(
                (np_[i], np_[i + 1]) in restricted
                for i in range(ei + 1, len(np_) - 1)
            )
            if not old_hits:
                return False

    # Concatenar prefijo [0..ei] + nueva ruta (que empieza en pivot=np_[ei+1]).
    prefix = np_[: ei + 1]
    combined = list(prefix) + list(nnp)

    # Recalcular edge_ids y length_m del path completo.
    total_len = 0.0
    edge_ids: list[int] = []
    for i in range(len(combined) - 1):
        ea = graph.get_edge_attributes(combined[i], combined[i + 1])
        eid = ea.get(ATTR_EDGE_ID)
        if eid is not None:
            edge_ids.append(eid)
        total_len += ea.get(ATTR_LENGTH, 0.0)

    vehicle.route = RouteInfo(
        start_node_id=vehicle.route.start_node_id,
        end_node_id=end_node,
        node_path=combined,
        edge_ids=edge_ids,
        length_m=total_len,
    )
    return True


def _reroute_affected_by_new_blocks(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    new_blocks: set[tuple[int, int]],
    blocked_edges: dict[tuple[int, int], object | None],
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> int:
    """
    Re-rutea a todos los vehículos MOVING cuya cola de ruta pase por alguna
    arista recién bloqueada. Se ejecuta una vez por tick tras procesar todas
    las colisiones nuevas.

    Returns:
        Número de vehículos re-ruteados.
    """
    if not new_blocks:
        return 0
    rerouted = 0
    for vehicle in vehicles.values():
        if _maybe_reroute_around_blocks(
            vehicle,
            graph,
            blocked_edges,
            trigger_blocks=new_blocks,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
        ):
            rerouted += 1
    return rerouted


def _periodic_reroute_all(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    blocked_edges: dict[tuple[int, int], object | None],
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> int:
    """
    Plan D1 — reroute proactivo. Recorre TODOS los MOVING y re-planifica a los
    que siguen enrutados por aristas bloqueadas. Se llama periódicamente (ver
    ``PERIODIC_REROUTE_TICK_INTERVAL``). Idempotente: si una ruta ya es limpia,
    `_maybe_reroute_around_blocks` sale sin mutar.

    Returns:
        Número de vehículos re-ruteados en esta pasada.
    """
    if not blocked_edges:
        return 0
    blocked_set = set(blocked_edges.keys())
    rerouted = 0
    for vehicle in vehicles.values():
        if _maybe_reroute_around_blocks(
            vehicle,
            graph,
            blocked_edges,
            trigger_blocks=blocked_set,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
        ):
            rerouted += 1
    return rerouted


def _periodic_reroute_batch(
    vehicles: dict[str, SimVehicle],
    graph: "RoadNetworkGraph",  # type: ignore[name-defined]
    blocked_edges: dict[tuple[int, int], object | None],
    tick_count: int,
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> int:
    """
    Versión amortizada de ``_periodic_reroute_all``. En lugar de revisar los N
    vehículos en un único tick (→ picos de 1-1.5 s con 3500+ vehículos), cada
    tick procesa ``PERIODIC_REROUTE_BATCH_SIZE`` vehículos arrancando desde un
    cursor rotatorio derivado del ``tick_count``. Cada vehículo es visitado
    cada ``ceil(N / batch)`` ticks, cobertura idéntica a la versión all-in-one
    pero con latencia constante por tick (≤ 5-10 ms típicamente).

    El reroute inmediato cuando aparece un nuevo bloqueo se mantiene vía
    ``_reroute_affected_by_new_blocks`` — esto es el safety net para los
    vehículos que esa pasada no atrapó.
    """
    if not blocked_edges:
        return 0
    if PERIODIC_REROUTE_BATCH_SIZE <= 0:
        return 0
    # snapshot del orden: dict.values() en Python 3.7+ es orden de inserción,
    # estable mientras no haya inserciones/borrados dentro del batch.
    vehicles_list = list(vehicles.values())
    n = len(vehicles_list)
    if n == 0:
        return 0
    batch_size = min(PERIODIC_REROUTE_BATCH_SIZE, n)
    start = (tick_count * batch_size) % n
    blocked_set = set(blocked_edges.keys())
    rerouted = 0
    for i in range(batch_size):
        idx = start + i
        if idx >= n:
            idx -= n
        if _maybe_reroute_around_blocks(
            vehicles_list[idx],
            graph,
            blocked_edges,
            trigger_blocks=blocked_set,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
        ):
            rerouted += 1
    return rerouted


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

    edge_index = _build_edge_index(vehicles)
    ring_occupancy = _build_ring_occupancy(vehicles, graph)
    entry_arms = _build_entry_arm_index(vehicles, graph)
    finished_ids: list[str] = []

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

        # Determinar el líder más restrictivo (vehículo, semáforo, yield-rotonda o señal).
        leader = _find_leader(vehicle, edge_index, graph)
        if tl_controller is not None:
            tl_leader = _check_traffic_light(vehicle, graph, tl_controller)
            if tl_leader is not None and (leader is None or tl_leader.gap_m < leader.gap_m):
                leader = tl_leader
        yield_leader = _find_roundabout_yield_leader(vehicle, graph, ring_occupancy, entry_arms)
        if yield_leader is not None and (leader is None or yield_leader.gap_m < leader.gap_m):
            leader = yield_leader
        sign_leader = _check_stop_yield_sign(vehicle, graph, edge_index, dt)
        if sign_leader is not None and (leader is None or sign_leader.gap_m < leader.gap_m):
            leader = sign_leader

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
        if vehicle.mobil_cooldown_ticks <= 0 or trapped_in_closed:
            _evaluate_lane_change(
                vehicle, edge_index, graph, leader, closed_lanes=closed_lanes
            )
            vehicle.mobil_cooldown_ticks = MOBIL_EVAL_INTERVAL_TICKS
        else:
            vehicle.mobil_cooldown_ticks -= 1

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
                    vehicle.proximity_timer = 0.0
                    continue  # no avanzar este tick
        else:
            vehicle.proximity_timer = 0.0

        if _advance_vehicle_idm(vehicle, graph, dt, leader, tl_ref=tl_controller):
            finished_ids.append(vehicle.id)

    # Auto-reroute en bloque: detectar aristas bloqueadas en este tick y
    # re-planificar a los vehículos MOVING cuya ruta pendiente las atraviese.
    new_blocks = set(blocked_edges.keys()) - blocked_before
    if new_blocks:
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
    if blocked_edges:
        _periodic_reroute_batch(
            vehicles,
            graph,
            blocked_edges,
            tick_count,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
        )

    return finished_ids


# ---------------------------------------------------------------------------
# Lightweight TL proxy for parallel workers
# ---------------------------------------------------------------------------

class _FrozenTL:
    """
    Snapshot inmutable de fases por arista para uso en procesos worker.

    Estructura: `{node_id: {"u_v": phase, ...}}` — misma que
    `TrafficLightController.get_snapshot()`. Los workers no tienen acceso al
    controlador vivo; se les pasa este snapshot serializable.
    """
    __slots__ = ("_phases",)

    def __init__(self, phases: dict[int, dict[str, str]]) -> None:
        self._phases = phases

    def get_phase_for_edge(self, node_id: int, incoming_edge: tuple[int, int]) -> str:
        edges = self._phases.get(node_id)
        if not edges:
            return TL_PHASE_GREEN
        key = f"{incoming_edge[0]}_{incoming_edge[1]}"
        if key in edges:
            return edges[key]
        # Fallback: TL sin aristas entrantes registradas (mid-street OSM) → "*"
        if "*" in edges:
            return edges["*"]
        return next(iter(edges.values()), TL_PHASE_GREEN)

    def get_phase(self, node_id: int) -> str:
        """Fase representativa del nodo (primera arista del diccionario)."""
        edges = self._phases.get(node_id)
        if not edges:
            return TL_PHASE_GREEN
        return next(iter(edges.values()))


# ---------------------------------------------------------------------------
# Multi-core parallel physics (ProcessPoolExecutor)
# ---------------------------------------------------------------------------

_worker_graph: "RoadNetworkGraph | None" = None  # type: ignore[name-defined]
_EXECUTOR: concurrent.futures.ProcessPoolExecutor | None = None
_EXECUTOR_GRAPH_ID: int = 0


def _worker_init(graph: "RoadNetworkGraph") -> None:  # type: ignore[name-defined]
    global _worker_graph
    _worker_graph = graph


def _ensure_executor(
    graph: "RoadNetworkGraph",  # type: ignore[name-defined]
) -> concurrent.futures.ProcessPoolExecutor:
    global _EXECUTOR, _EXECUTOR_GRAPH_ID
    gid = id(graph)
    if _EXECUTOR is None or _EXECUTOR_GRAPH_ID != gid:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=os.cpu_count(),
            initializer=_worker_init,
            initargs=(graph,),
            mp_context=multiprocessing.get_context("spawn"),
        )
        _EXECUTOR_GRAPH_ID = gid
    return _EXECUTOR


def _vehicle_to_dict(
    v: "SimVehicle",  # type: ignore[name-defined]
    leader: "NeighborInfo | None" = None,
    tl_phases: "dict[int, dict[str, str]] | None" = None,
) -> dict:
    """Serializa los campos mutables de un SimVehicle para IPC entre procesos."""
    vtype = getattr(v, "vtype", None)
    return {
        "id": v.id,
        "status": v.status.value,
        "node_path": v.route.node_path,
        "edge_ids": v.route.edge_ids,
        "current_edge_index": v.current_edge_index,
        "progress_on_edge": v.progress_on_edge,
        "velocity": v.velocity,
        "acceleration": v.acceleration,
        "longitude": v.longitude,
        "latitude": v.latitude,
        "heading": v.heading,
        "desired_speed_ms": getattr(v, "desired_speed_ms", 13.89),
        "yellow_runs_light": getattr(v, "yellow_runs_light", False),
        "vtype": vtype.value if vtype is not None else None,
        "lane": getattr(v, "lane", 0),
        "length_m": getattr(v, "length_m", VEHICLE_LENGTH_M),
        "prev_edge_end_heading": getattr(v, "prev_edge_end_heading", -1.0),
        # Líder pre-computado en el proceso principal para evitar
        # la necesidad de reconstruir el edge_index en cada worker.
        "leader_gap_m": leader.gap_m if leader is not None else None,
        "leader_vel_ms": leader.velocity_ms if leader is not None else None,
        # Snapshot completo de fases de semáforos para _FrozenTL en workers.
        "tl_phases": tl_phases,
    }


def _process_chunk(
    vehicle_dicts: list[dict], dt: float
) -> tuple[list[dict], list[str]]:
    """
    Worker-process entry point.

    Reconstruye SimVehicle ligeros desde dicts, avanza cada uno con IDM
    usando el líder pre-computado, y devuelve los campos actualizados
    más la lista de IDs terminados.

    El hard-stop de semáforo se activa a través de _FrozenTL, reconstruido
    desde la lista de nodos bloqueados serializada en el dict del vehículo.
    Todos los vehículos del mismo chunk comparten el mismo snapshot, por lo
    que se crea una sola instancia de _FrozenTL por chunk.
    """
    from app.core.route import RouteInfo
    from app.models.enums import VehicleStatus
    from app.services.vehicle_spawner import SimVehicle

    finished_ids: list[str] = []
    updates: list[dict] = []

    # Reconstruir _FrozenTL desde el primer vehículo del chunk (todos comparten el mismo snapshot)
    frozen_tl: _FrozenTL | None = None
    if vehicle_dicts:
        raw_phases = vehicle_dicts[0].get("tl_phases")
        if raw_phases is not None:
            # node_id keys llegan como int/str según IPC — normalizar a int
            frozen_tl = _FrozenTL({int(k): v for k, v in raw_phases.items()})

    for vd in vehicle_dicts:
        node_path = vd["node_path"]
        route = RouteInfo(
            start_node_id=node_path[0] if node_path else 0,
            end_node_id=node_path[-1] if node_path else 0,
            node_path=node_path,
            edge_ids=vd["edge_ids"],
            length_m=0.0,
        )
        vtype_str = vd.get("vtype")
        vtype_val = VehicleType(vtype_str) if vtype_str else VehicleType.CAR
        v = SimVehicle(
            id=vd["id"],
            start_node_id=0,
            end_node_id=0,
            route=route,
            status=VehicleStatus(vd["status"]),
            current_edge_index=vd["current_edge_index"],
            longitude=vd["longitude"],
            latitude=vd["latitude"],
            velocity=vd["velocity"],
            acceleration=vd["acceleration"],
            heading=vd["heading"],
            progress_on_edge=vd["progress_on_edge"],
            desired_speed_ms=vd.get("desired_speed_ms", 13.89),
            yellow_runs_light=vd.get("yellow_runs_light", False),
            vtype=vtype_val,
            lane=vd.get("lane", 0),
            length_m=vd.get("length_m", VEHICLE_LENGTH_M),
            prev_edge_end_heading=vd.get("prev_edge_end_heading", -1.0),
        )

        # Reconstruir líder pre-computado
        leader: NeighborInfo | None = None
        if vd.get("leader_gap_m") is not None:
            leader = NeighborInfo(
                gap_m=vd["leader_gap_m"],
                velocity_ms=vd["leader_vel_ms"],
            )

        finished = _advance_vehicle_idm(v, _worker_graph, dt, leader, tl_ref=frozen_tl)  # type: ignore[arg-type]
        if finished:
            finished_ids.append(v.id)
        updates.append({
            "id": v.id,
            "status": v.status.value,
            "current_edge_index": v.current_edge_index,
            "progress_on_edge": v.progress_on_edge,
            "velocity": v.velocity,
            "acceleration": v.acceleration,
            "longitude": v.longitude,
            "latitude": v.latitude,
            "heading": v.heading,
            "lane": v.lane,
            "prev_edge_end_heading": v.prev_edge_end_heading,
        })

    return updates, finished_ids


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
    Avanza todos los vehículos usando todos los cores disponibles.

    La detección de líderes y semáforos se realiza siempre en el proceso
    principal (requiere visibilidad global de todos los vehículos), y los
    resultados se pasan como datos a los workers para el cálculo IDM.

    - Con < _PARALLEL_THRESHOLD vehículos: asyncio.to_thread (sin IPC overhead).
    - Con ≥ _PARALLEL_THRESHOLD: ProcessPoolExecutor con un chunk por core.
    - Fallback automático a asyncio.to_thread si el ProcessPool falla.

    Returns:
        Lista de vehicle_ids que terminaron su ruta en este tick.
    """
    from app.models.enums import VehicleStatus

    n = len(vehicles)

    # Snapshot del dict de restricciones por vtype. Reasignado en bloque por
    # el ZoneManager, así que la referencia es estable durante el tick.
    restricted_by_vtype: dict[str, set[tuple[int, int]]] | None = None
    if zone_manager is not None:
        try:
            restricted_by_vtype = zone_manager.restricted_edges_by_vtype()  # type: ignore[attr-defined]
        except Exception:
            restricted_by_vtype = None

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
        # ── Pre-compute leaders in main process ────────────────────────────────
        # La detección requiere el estado global de todos los vehículos,
        # por lo que no puede delegarse a workers independientes.
        if blocked_edges is None:
            blocked_edges = {}
        if closed_lanes is None:
            closed_lanes = {}
        if pending_collisions is None:
            pending_collisions = []

        # Snapshot previo: cualquier arista añadida durante este tick activará
        # el auto-reroute de los vehículos cuya ruta pendiente la atraviese.
        blocked_before: set[tuple[int, int]] = set(blocked_edges.keys())

        # Vehículos en COLLISION se quedan en place (retirada manual por API).
        # PAUSED y FINISHED se excluyen del despacho a workers. IDLE → MOVING.
        pre_finished: list[str] = []
        active_vehicles: dict[str, SimVehicle] = {}
        for v in vehicles.values():
            if v.status == VehicleStatus.FINISHED:
                pre_finished.append(v.id)
                continue
            if v.status == VehicleStatus.COLLISION:
                v.velocity = 0.0
                continue  # permanece hasta retirada manual
            if v.status == VehicleStatus.PAUSED:
                continue
            if v.status == VehicleStatus.IDLE:
                v.status = VehicleStatus.MOVING
            active_vehicles[v.id] = v

        edge_index = _build_edge_index(active_vehicles)
        ring_occupancy = _build_ring_occupancy(active_vehicles, graph)
        entry_arms = _build_entry_arm_index(active_vehicles, graph)
        vehicle_list = list(active_vehicles.values())

        leaders: dict[str, NeighborInfo | None] = {}
        colliding_ids: set[str] = set()
        for v in vehicle_list:
            ldr = _find_leader(v, edge_index, graph)
            if tl_controller is not None:
                tl_ldr = _check_traffic_light(v, graph, tl_controller)
                if tl_ldr is not None and (ldr is None or tl_ldr.gap_m < ldr.gap_m):
                    ldr = tl_ldr
            yield_ldr = _find_roundabout_yield_leader(v, graph, ring_occupancy, entry_arms)
            if yield_ldr is not None and (ldr is None or yield_ldr.gap_m < ldr.gap_m):
                ldr = yield_ldr
            sign_ldr = _check_stop_yield_sign(v, graph, edge_index, dt)
            if sign_ldr is not None and (ldr is None or sign_ldr.gap_m < ldr.gap_m):
                ldr = sign_ldr

            # Umbral contextual (rotonda vs recto) + velocidad relativa mínima.
            np_ = v.route.node_path
            ei = v.current_edge_index
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
            if ldr is not None:
                rel_speed = abs(v.velocity - ldr.velocity_ms)

            too_close = (
                ldr is not None
                and ldr.leader_id is not None
                and ldr.gap_m < gap_threshold
                and rel_speed >= COLLISION_RELATIVE_SPEED_MIN_MS
                and v.status == VehicleStatus.MOVING
            )
            if too_close:
                v.proximity_timer = getattr(v, "proximity_timer", 0.0) + dt
                if v.proximity_timer >= COLLISION_PROXIMITY_DURATION_S:
                    other = vehicles.get(ldr.leader_id)  # type: ignore[union-attr]
                    if other is not None and other.status == VehicleStatus.MOVING:
                        _trigger_collision(
                            v, other, blocked_edges, pending_collisions
                        )
                        v.proximity_timer = 0.0
                        colliding_ids.add(v.id)
                        colliding_ids.add(other.id)
            else:
                v.proximity_timer = 0.0

            # MOBIL en el proceso principal: los workers no tienen acceso al
            # edge_index completo (sólo a su chunk), por lo que el cambio de
            # carril se decide aquí y se propaga ya decidido. Si el vehículo
            # está en un carril cerrado se fuerza la evaluación (bypass cooldown).
            np_p = v.route.node_path
            ei_p = v.current_edge_index
            trapped_p = False
            if ei_p < len(np_p) - 1:
                trapped_p = v.lane in closed_lanes.get((np_p[ei_p], np_p[ei_p + 1]), set())
            if v.mobil_cooldown_ticks <= 0 or trapped_p:
                _evaluate_lane_change(
                    v, edge_index, graph, ldr, closed_lanes=closed_lanes
                )
                v.mobil_cooldown_ticks = MOBIL_EVAL_INTERVAL_TICKS
            else:
                v.mobil_cooldown_ticks -= 1

            # Plan D2 — dead-wall detection en el path paralelo. Si el líder
            # es un vehículo en COLLISION, reruta inmediato desde el siguiente
            # nodo sin esperar al pase periódico. El IDM seguirá frenando en
            # el worker con el líder ya calculado.
            if ldr is not None and ldr.leader_id is not None:
                ldr_v = vehicles.get(ldr.leader_id)
                if ldr_v is not None and ldr_v.status == VehicleStatus.COLLISION:
                    _maybe_reroute_around_blocks(
                        v,
                        graph,
                        blocked_edges,
                        restricted_edges_by_vtype=restricted_by_vtype,
                    )

            leaders[v.id] = ldr

        # Serializar snapshot completo de fases para _FrozenTL en workers
        tl_phases_dict: dict[int, dict[str, str]] | None = None
        if tl_controller is not None:
            tl_phases_dict = tl_controller.get_snapshot()  # type: ignore[union-attr]

        # Excluir colisionados de esta iteración: su status ya es COLLISION y
        # el worker no debe reintegrarlos en el IDM ni mover su posición.
        dispatch_list = [v for v in vehicle_list if v.id not in colliding_ids]
        n_dispatch = len(dispatch_list)
        if n_dispatch == 0:
            results = []
        else:
            executor  = _ensure_executor(graph)
            n_workers = min(os.cpu_count() or 1, n_dispatch)
            chunk_size = max(1, math.ceil(n_dispatch / n_workers))
            chunks = [
                [_vehicle_to_dict(v, leaders.get(v.id), tl_phases_dict)
                 for v in dispatch_list[i : i + chunk_size]]
                for i in range(0, n_dispatch, chunk_size)
            ]

            loop    = asyncio.get_running_loop()
            futures = [
                loop.run_in_executor(executor, _process_chunk, chunk, dt)
                for chunk in chunks
            ]
            results = await asyncio.gather(*futures)

    except Exception:
        logger.exception(
            "ProcessPoolExecutor falló (n=%d vehículos); degradando a asyncio.to_thread", n
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
    for updates, chunk_finished in results:
        for upd in updates:
            vid = upd["id"]
            if vid in vehicles:
                v = vehicles[vid]
                v.status             = VehicleStatus(upd["status"])
                v.current_edge_index = upd["current_edge_index"]
                v.progress_on_edge   = upd["progress_on_edge"]
                v.velocity           = upd["velocity"]
                v.acceleration       = upd["acceleration"]
                v.longitude          = upd["longitude"]
                v.latitude           = upd["latitude"]
                v.heading            = upd["heading"]
                v.lane               = upd.get("lane", v.lane)
                v.prev_edge_end_heading = upd.get(
                    "prev_edge_end_heading", v.prev_edge_end_heading
                )
        finished_ids.extend(chunk_finished)

    # Auto-reroute de vehículos afectados por bloqueos surgidos en este tick.
    # Se ejecuta tras aplicar los updates de los workers para que el
    # current_edge_index e índices asociados sean los más recientes.
    new_blocks = set(blocked_edges.keys()) - blocked_before
    if new_blocks:
        _reroute_affected_by_new_blocks(
            vehicles,
            graph,
            new_blocks,
            blocked_edges,
            restricted_edges_by_vtype=restricted_by_vtype,
        )

    # Plan D1: pase proactivo amortizado por tick (ver _periodic_reroute_batch).
    # Sustituye el pase all-in-one cada PERIODIC_REROUTE_TICK_INTERVAL ticks.
    if blocked_edges:
        _periodic_reroute_batch(
            vehicles,
            graph,
            blocked_edges,
            tick_count,
            restricted_edges_by_vtype=restricted_by_vtype,
        )

    return finished_ids

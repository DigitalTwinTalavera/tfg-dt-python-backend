"""
Física de movimiento de vehículos a lo largo de sus rutas.

Avanza cada vehículo activo una distancia = velocidad × dt en cada tick.
Cuando un vehículo cruza el final de un segmento, el tiempo sobrante se
aplica al siguiente segmento. Cuando se agotan los segmentos, el vehículo
pasa a FINISHED.

Los vehículos siguen los waypoints reales de la geometría de la calzada
(LineString de PostGIS) en lugar de interpolar en línea recta entre nodos.

Fórmula de heading (bearing de brújula):
    heading = 0  → Norte  (lat aumenta, lon constante)
    heading = 90 → Este   (lon aumenta, lat constante)
    heading = atan2(Δlon, Δlat) * 180 / π  (mod 360)
"""

from __future__ import annotations

import logging
import math

from app.core.constants import (
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_WAYPOINTS,
    KMH_TO_MS,
)
from app.models.enums import VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle

logger = logging.getLogger(__name__)

_DEFAULT_SPEED_KMH: float = 50.0   # Fallback when edge has no speed data
_MIN_EDGE_LENGTH: float = 0.1      # Minimum edge length (metres) to avoid division by zero
_EARTH_RADIUS_M: float = 6_371_000.0  # Mean Earth radius in metres


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


def _position_along_waypoints(
    waypoints: list[tuple[float, float]],
    progress: float,
) -> tuple[float, float, float]:
    """
    Compute the interpolated (lon, lat, heading) at fractional progress [0, 1]
    along a list of waypoints.

    Args:
        waypoints: List of (lon, lat) pairs (at least 2).
        progress:  Value in [0, 1] representing position along the route.

    Returns:
        (longitude, latitude, heading_degrees)
    """
    if len(waypoints) < 2:
        lon, lat = waypoints[0] if waypoints else (0.0, 0.0)
        return lon, lat, 0.0

    seg_lengths, total_length = _waypoint_segments(waypoints)

    if total_length < 1e-6:
        # Degenerate geometry — all waypoints at same spot
        lon, lat = waypoints[-1]
        return lon, lat, 0.0

    target_dist = progress * total_length
    accumulated: float = 0.0

    for i, seg_len in enumerate(seg_lengths):
        if accumulated + seg_len >= target_dist or i == len(seg_lengths) - 1:
            # Position is on segment i
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

    # Fallback: end of last waypoint
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
    # Fallback: straight line
    start_lon = float(start_node_attrs.get(ATTR_LONGITUDE, 0.0))
    start_lat = float(start_node_attrs.get(ATTR_LATITUDE,  0.0))
    end_lon   = float(end_node_attrs.get(ATTR_LONGITUDE,   0.0))
    end_lat   = float(end_node_attrs.get(ATTR_LATITUDE,    0.0))
    return [(start_lon, start_lat), (end_lon, end_lat)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_vehicles(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    dt: float,
) -> list[str]:
    """
    Avanza todos los vehículos activos a lo largo de sus rutas.

    Args:
        vehicles: Diccionario vehicle_id → SimVehicle (modificado in-place).
        graph: Grafo de red vial para consultas de coordenadas y velocidades.
        dt: Intervalo de tiempo en segundos (un tick de simulación).

    Returns:
        Lista de vehicle_ids que han terminado su ruta en este tick.
    """
    finished_ids: list[str] = []

    for vehicle in list(vehicles.values()):
        if vehicle.status == VehicleStatus.FINISHED:
            continue
        # Activate vehicles that were just spawned
        if vehicle.status == VehicleStatus.IDLE:
            vehicle.status = VehicleStatus.MOVING

        if _advance_vehicle(vehicle, graph, dt):
            finished_ids.append(vehicle.id)

    return finished_ids


def _advance_vehicle(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    dt: float,
) -> bool:
    """
    Avanza un vehículo por su ruta durante dt segundos siguiendo los
    waypoints de la geometría real de la calzada.

    Returns:
        True si el vehículo completó su ruta en este tick.
    """
    node_path = vehicle.route.node_path

    if len(node_path) < 2 or vehicle.current_edge_index >= len(node_path) - 1:
        vehicle.status    = VehicleStatus.FINISHED
        vehicle.velocity  = 0.0
        return True

    remaining_time = dt
    edge_idx       = vehicle.current_edge_index

    # ── Advance through edges until dt is consumed or route ends ──────────────
    while remaining_time > 1e-9 and edge_idx < len(node_path) - 1:
        start_node = node_path[edge_idx]
        end_node   = node_path[edge_idx + 1]

        edge_attrs  = graph.get_edge_attributes(start_node, end_node)
        edge_length = max(float(edge_attrs.get(ATTR_LENGTH, 1.0)), _MIN_EDGE_LENGTH)
        speed_kmh   = float(edge_attrs.get(ATTR_MAX_SPEED, _DEFAULT_SPEED_KMH)) or _DEFAULT_SPEED_KMH
        speed_ms    = speed_kmh * KMH_TO_MS

        # Distance and time remaining to reach end of this edge
        dist_to_end = edge_length * (1.0 - vehicle.progress_on_edge)
        time_to_end = dist_to_end / speed_ms

        if remaining_time >= time_to_end:
            # ── Cross into next edge ───────────────────────────────────────────
            remaining_time           -= time_to_end
            edge_idx                 += 1
            vehicle.progress_on_edge  = 0.0
            vehicle.velocity          = speed_ms
        else:
            # ── Stay on this edge ─────────────────────────────────────────────
            advance_m                 = speed_ms * remaining_time
            vehicle.progress_on_edge += advance_m / edge_length
            vehicle.progress_on_edge  = min(vehicle.progress_on_edge, 1.0)
            vehicle.velocity          = speed_ms
            remaining_time            = 0.0

    vehicle.current_edge_index = edge_idx

    # ── Route finished ─────────────────────────────────────────────────────────
    if edge_idx >= len(node_path) - 1:
        last_node_attrs   = graph.get_node_attributes(node_path[-1])
        vehicle.longitude  = float(last_node_attrs.get(ATTR_LONGITUDE, vehicle.longitude))
        vehicle.latitude   = float(last_node_attrs.get(ATTR_LATITUDE,  vehicle.latitude))
        vehicle.velocity   = 0.0
        vehicle.status     = VehicleStatus.FINISHED
        return True

    # ── Interpolate position along road geometry ───────────────────────────────
    start_node  = node_path[edge_idx]
    end_node    = node_path[edge_idx + 1]
    edge_attrs  = graph.get_edge_attributes(start_node, end_node)
    start_attrs = graph.get_node_attributes(start_node)
    end_attrs   = graph.get_node_attributes(end_node)

    waypoints = _get_waypoints(edge_attrs, start_attrs, end_attrs)
    lon, lat, heading = _position_along_waypoints(waypoints, vehicle.progress_on_edge)

    vehicle.longitude = lon
    vehicle.latitude  = lat
    vehicle.heading   = heading

    return False

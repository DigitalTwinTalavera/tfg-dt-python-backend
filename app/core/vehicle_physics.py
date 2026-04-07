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

import asyncio
import concurrent.futures
import logging
import math
import multiprocessing
import os

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

# Cache de longitudes de segmentos por arista (start_node, end_node).
# La geometría de las aristas no cambia durante la simulación, por lo que
# calcular los segmentos haversine una sola vez y reutilizarlos elimina
# ~250 000 llamadas trigonométricas/seg con 5000 vehículos a 10 Hz.
_SEG_CACHE: dict[tuple[int, int], tuple[list[float], float]] = {}


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
    lon, lat, heading = _position_along_waypoints(
        waypoints, vehicle.progress_on_edge, _cache_key=(start_node, end_node)
    )

    vehicle.longitude = lon
    vehicle.latitude  = lat
    vehicle.heading   = heading

    return False


# ---------------------------------------------------------------------------
# Multi-core parallel physics (ProcessPoolExecutor)
# ---------------------------------------------------------------------------

# Worker-process state — populated once per worker via initializer.
_worker_graph: "RoadNetworkGraph | None" = None  # type: ignore[name-defined]

# Module-level executor — created on first use, reused across ticks.
_EXECUTOR: concurrent.futures.ProcessPoolExecutor | None = None
_EXECUTOR_GRAPH_ID: int = 0  # id() of the graph used to create the executor

# Minimum vehicle count to justify IPC overhead.
_PARALLEL_THRESHOLD: int = 500


def _worker_init(graph: "RoadNetworkGraph") -> None:  # type: ignore[name-defined]
    """Initializer for each worker process: pre-loads the road graph."""
    global _worker_graph
    _worker_graph = graph


def _ensure_executor(
    graph: "RoadNetworkGraph",  # type: ignore[name-defined]
) -> concurrent.futures.ProcessPoolExecutor:
    """Return (creating if needed) a ProcessPoolExecutor whose workers hold *graph*."""
    global _EXECUTOR, _EXECUTOR_GRAPH_ID
    gid = id(graph)
    if _EXECUTOR is None or _EXECUTOR_GRAPH_ID != gid:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        # spawn evita conflictos con el event loop de asyncio que usa fork en Linux.
        # Los workers importan el módulo de nuevo, lo que es seguro porque
        # _process_chunk hace sus propios imports internos.
        _EXECUTOR = concurrent.futures.ProcessPoolExecutor(
            max_workers=os.cpu_count(),
            initializer=_worker_init,
            initargs=(graph,),
            mp_context=multiprocessing.get_context("spawn"),
        )
        _EXECUTOR_GRAPH_ID = gid
    return _EXECUTOR


def _vehicle_to_dict(v: "SimVehicle") -> dict:  # type: ignore[name-defined]
    """Serialize the mutable fields of a SimVehicle for inter-process transfer."""
    return {
        "id": v.id,
        "status": v.status.value,
        "node_path": v.route.node_path,
        "edge_ids": v.route.edge_ids,
        "current_edge_index": v.current_edge_index,
        "progress_on_edge": v.progress_on_edge,
        "velocity": v.velocity,
        "longitude": v.longitude,
        "latitude": v.latitude,
        "heading": v.heading,
        "acceleration": v.acceleration,
    }


def _process_chunk(
    vehicle_dicts: list[dict], dt: float
) -> tuple[list[dict], list[str]]:
    """
    Worker-process entry point.

    Reconstructs lightweight SimVehicle objects from dicts, advances each one,
    and returns the updated mutable fields plus the list of finished vehicle IDs.
    """
    from app.core.route import RouteInfo
    from app.models.enums import VehicleStatus
    from app.services.vehicle_spawner import SimVehicle

    finished_ids: list[str] = []
    updates: list[dict] = []

    for vd in vehicle_dicts:
        node_path = vd["node_path"]
        route = RouteInfo(
            start_node_id=node_path[0] if node_path else 0,
            end_node_id=node_path[-1] if node_path else 0,
            node_path=node_path,
            edge_ids=vd["edge_ids"],
            length_m=0.0,
        )
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
        )
        finished = _advance_vehicle(v, _worker_graph, dt)  # type: ignore[arg-type]
        if finished:
            finished_ids.append(v.id)
        updates.append({
            "id": v.id,
            "status": v.status.value,
            "current_edge_index": v.current_edge_index,
            "progress_on_edge": v.progress_on_edge,
            "velocity": v.velocity,
            "longitude": v.longitude,
            "latitude": v.latitude,
            "heading": v.heading,
        })

    return updates, finished_ids


async def update_vehicles_parallel(
    vehicles: "dict[str, SimVehicle]",  # type: ignore[name-defined]
    graph: "RoadNetworkGraph",          # type: ignore[name-defined]
    dt: float,
) -> list[str]:
    """
    Avanza todos los vehículos usando todos los cores disponibles.

    - Con < _PARALLEL_THRESHOLD vehículos: corre update_vehicles en asyncio.to_thread
      para liberar el event loop sin incurrir en el overhead de IPC.
    - Con ≥ _PARALLEL_THRESHOLD vehículos: divide la lista en chunks (uno por core),
      los procesa en paralelo en un ProcessPoolExecutor y reintegra los resultados.
    - Si el ProcessPoolExecutor falla (error de pickle, imports, etc.) cae
      automáticamente a asyncio.to_thread para no romper la simulación.

    Returns:
        Lista de vehicle_ids que terminaron su ruta en este tick.
    """
    from app.models.enums import VehicleStatus

    n = len(vehicles)

    if n < _PARALLEL_THRESHOLD:
        return await asyncio.to_thread(update_vehicles, vehicles, graph, dt)

    try:
        executor = _ensure_executor(graph)
        vehicle_list = list(vehicles.values())
        n_workers = min(os.cpu_count() or 1, n)
        chunk_size = math.ceil(n / n_workers)
        chunks = [
            [_vehicle_to_dict(v) for v in vehicle_list[i : i + chunk_size]]
            for i in range(0, n, chunk_size)
        ]

        loop = asyncio.get_running_loop()
        futures = [
            loop.run_in_executor(executor, _process_chunk, chunk, dt)
            for chunk in chunks
        ]
        results = await asyncio.gather(*futures)

    except Exception:
        # Fallback: el ProcessPoolExecutor puede fallar al arrancar (pickle del grafo,
        # imports de app.* en workers spawn, etc.). En ese caso degradamos
        # gracefully al path de un solo hilo para no romper la simulación.
        logger.exception(
            "ProcessPoolExecutor falló (n=%d vehículos); degradando a asyncio.to_thread",
            n,
        )
        global _EXECUTOR
        _EXECUTOR = None  # Forzar recreación en el próximo tick
        return await asyncio.to_thread(update_vehicles, vehicles, graph, dt)

    finished_ids: list[str] = []
    for updates, chunk_finished in results:
        for upd in updates:
            vid = upd["id"]
            if vid in vehicles:
                v = vehicles[vid]
                v.status = VehicleStatus(upd["status"])
                v.current_edge_index = upd["current_edge_index"]
                v.progress_on_edge = upd["progress_on_edge"]
                v.velocity = upd["velocity"]
                v.longitude = upd["longitude"]
                v.latitude = upd["latitude"]
                v.heading = upd["heading"]
        finished_ids.extend(chunk_finished)

    return finished_ids

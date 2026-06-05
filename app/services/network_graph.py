"""
Road Network Graph Service.

Builds and maintains an in-memory NetworkX graph from PostgreSQL road network data.
Provides fast pathfinding and connectivity queries for traffic simulation.
"""

import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Optional

import networkx as nx

logger = logging.getLogger(__name__)
from geoalchemy2.functions import ST_AsGeoJSON, ST_X, ST_Y
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_MID_TLS,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_ROAD_TYPE,
    ATTR_RING_RADIUS_M,
    ATTR_ROUNDABOUT_ID,
    ATTR_SPLINE_LENGTH,
    ATTR_SPLINE_SAMPLES,
    ATTR_USE_SPLINE,
    ATTR_WAYPOINTS,
    ATTR_WEIGHT,
    BLOCKED_EDGE_PENALTY_FACTOR,
    DEFAULT_EDGE_WEIGHT,
    GRAPH_CACHE_TTL_SECONDS,
    KMH_TO_MS,
    RDP_MIN_POINTS,
    RDP_TOLERANCE_MAX_M,
    RDP_TOLERANCE_MIN_M,
    RDP_TOLERANCE_PER_RADIUS,
    ROAD_TYPE_WEIGHT_FACTORS,
    SPLINE_SAMPLES_PER_SEGMENT,
    ZBE_EDGE_PENALTY_FACTOR,
)
from app.core.spline import build_spline_table, rdp
from app.models.enums import NodeType

# Heurística A*: cota inferior admisible basada en distancia geográfica.
# Velocidad máxima posible en la red (autovía ≈ 130 km/h) con el factor de
# penalización mínimo (motorway = 0.6), que reduce el peso real.
# weight = travel_time * road_factor  →  mínimo posible = dist/v_max * 0.6
_ASTAR_MAX_SPEED_MS: float = 130.0 * KMH_TO_MS   # ≈ 36.1 m/s
_ASTAR_MIN_ROAD_FACTOR: float = 0.6               # factor motorway
_METERS_PER_DEGREE: float = 111_320.0             # aprox. metros por grado lat/lon


@dataclass
class GraphStats:
    """Statistics about the road network graph."""

    node_count: int
    edge_count: int
    is_connected: bool
    build_time_ms: float
    last_updated: float


class RoadNetworkGraph:
    """
    In-memory graph representation of the road network.

    Uses NetworkX DiGraph for efficient pathfinding and connectivity queries.
    Supports one-way streets via directed edges and bidirectional roads via
    two directed edges.

    Attributes:
        _graph: NetworkX DiGraph instance
        _stats: Graph statistics
        _last_build_time: Timestamp of last graph build
        _cache_ttl: Cache time-to-live in seconds
    """

    def __init__(self, cache_ttl: int = GRAPH_CACHE_TTL_SECONDS) -> None:
        """
        Initialize the RoadNetworkGraph.

        Args:
            cache_ttl: Cache time-to-live in seconds (default: 5 minutes)
        """
        self._graph: nx.DiGraph = nx.DiGraph()
        self._stats: Optional[GraphStats] = None
        self._last_build_time: float = 0
        self._cache_ttl = cache_ttl
        # Indices por rotonda — reconstruidos al final de build_from_database.
        self._roundabout_edges: dict[int, list[tuple[int, int]]] = {}
        self._roundabout_length: dict[int, float] = {}
        # Pesos dinámicos (Fase 6): multiplicador aplicado a edge_weight en A*.
        self._dynamic_weights: dict[tuple[int, int], float] = {}
        # Cache de edge attributes para evitar `dict(self._graph.edges[u, v])`
        # repetido — el hot path de física llama 10-15 veces por veh por tick.
        # Las attrs son inmutables tras `build_from_database`, así que cacheamos
        # la MISMA referencia (todos los consumers leen, ninguno muta). Bajo
        # free-threading: writes y reads del dict son atómicos, peor caso =
        # rebuild doble del mismo key, correctness OK.
        self._edge_attrs_cache: dict[tuple[int, int], dict[str, Any]] = {}
        self._node_attrs_cache: dict[int, dict[str, Any]] = {}

    @property
    def graph(self) -> nx.DiGraph:
        """Get the underlying NetworkX graph."""
        return self._graph

    @property
    def stats(self) -> Optional[GraphStats]:
        """Get graph statistics."""
        return self._stats

    @property
    def is_stale(self) -> bool:
        """Check if the graph cache is stale and needs rebuilding."""
        if self._last_build_time == 0:
            return True
        return (time.time() - self._last_build_time) > self._cache_ttl

    @property
    def node_count(self) -> int:
        """Get the number of nodes in the graph."""
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        """Get the number of edges in the graph."""
        return self._graph.number_of_edges()

    async def build_from_database(
        self,
        session: AsyncSession,
        *,
        active_only: bool = True,
    ) -> GraphStats:
        """
        Build the graph from database nodes and edges.

        Loads all nodes and edges from PostgreSQL and constructs an in-memory
        NetworkX DiGraph. For bidirectional roads, creates edges in both
        directions. For one-way roads, creates only the forward edge.

        Args:
            session: AsyncSession for database queries
            active_only: If True, only include active nodes and edges

        Returns:
            GraphStats with information about the built graph
        """
        start_time = time.time()

        # Clear existing graph
        self._graph.clear()
        self._edge_attrs_cache.clear()
        self._node_attrs_cache.clear()

        # Load nodes
        await self._load_nodes(session, active_only)

        # Load edges
        await self._load_edges(session, active_only)

        # Pre-poblar la caché de segmentos por arista antes de que los workers
        # del path paralelo la consulten. Bajo free-threading, escrituras
        # concurrentes a un dict global durante un resize pueden corromperlo,
        # así que llenamos toda la caché en main thread aquí.
        from app.core.vehicle_physics import prewarm_segment_cache

        n_cached = prewarm_segment_cache(self)
        logger.info("Pre-warmed segment cache: %d edges", n_cached)

        # Diagnóstico: cobertura de TLs (endpoint vs mid-way). Un porcentaje
        # alto de mid-way TLs sin detectar en edges indica mismatch de coordenadas.
        tl_total = 0
        tl_with_in_edges = 0
        for nid, attrs in self._graph.nodes(data=True):
            if attrs.get(ATTR_NODE_TYPE) == NodeType.TRAFFIC_LIGHT.value:
                tl_total += 1
                if self._graph.in_degree(nid) > 0:
                    tl_with_in_edges += 1
        edges_with_mid_tl = sum(
            1 for _, _, a in self._graph.edges(data=True) if a.get(ATTR_MID_TLS)
        )
        mid_tl_count = sum(
            len(a.get(ATTR_MID_TLS, [])) for _, _, a in self._graph.edges(data=True)
        )
        logger.info(
            "Graph loaded: TLs=%d (endpoint=%d, mid-way-candidates=%d), "
            "edges-with-mid-TL=%d, mid-TL-refs=%d",
            tl_total, tl_with_in_edges, tl_total - tl_with_in_edges,
            edges_with_mid_tl, mid_tl_count,
        )

        # Calculate statistics
        build_time_ms = (time.time() - start_time) * 1000
        self._last_build_time = time.time()

        # Check connectivity (use underlying graph for undirected check)
        is_connected = False
        if self._graph.number_of_nodes() > 0:
            undirected = self._graph.to_undirected()
            is_connected = nx.is_connected(undirected)

        self._stats = GraphStats(
            node_count=self._graph.number_of_nodes(),
            edge_count=self._graph.number_of_edges(),
            is_connected=is_connected,
            build_time_ms=build_time_ms,
            last_updated=self._last_build_time,
        )

        return self._stats

    async def _load_nodes(
        self,
        session: AsyncSession,
        active_only: bool,
    ) -> None:
        """Load nodes from database into the graph."""
        # Import here to avoid circular imports
        from app.models.road_network import NodeModel

        stmt = select(
            NodeModel.id,
            NodeModel.node_type,
            ST_X(NodeModel.position).label("longitude"),
            ST_Y(NodeModel.position).label("latitude"),
        )

        if active_only:
            stmt = stmt.where(NodeModel.is_active.is_(True))

        result = await session.execute(stmt)
        rows = result.fetchall()

        for row in rows:
            self._graph.add_node(
                row.id,
                **{
                    ATTR_NODE_ID: row.id,
                    ATTR_LONGITUDE: row.longitude,
                    ATTR_LATITUDE: row.latitude,
                    ATTR_NODE_TYPE: row.node_type,
                },
            )

    async def _load_edges(
        self,
        session: AsyncSession,
        active_only: bool,
    ) -> None:
        """Load edges from database into the graph."""
        # Import here to avoid circular imports
        from app.models.road_network import EdgeModel

        stmt = select(
            EdgeModel.id,
            EdgeModel.start_node_id,
            EdgeModel.end_node_id,
            EdgeModel.length,
            EdgeModel.max_speed,
            EdgeModel.road_type,
            EdgeModel.one_way,
            EdgeModel.lanes,
            EdgeModel.is_roundabout,
            EdgeModel.roundabout_id,
            ST_AsGeoJSON(EdgeModel.geometry).label("geometry_json"),
        )

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await session.execute(stmt)
        rows = result.fetchall()

        # Coordenada (redondeada) → node_id, sólo para nodos TRAFFIC_LIGHT.
        # Se usa para detectar TLs en waypoints intermedios del LineString de
        # cada edge (los TLs mid-way no son endpoints del DiGraph, porque cada
        # way OSM se vuelve UNA arista first→last → el TL queda "dentro" del
        # polyline).
        # Precisión 6 decimales (~11 cm en longitud) para tolerar drift de
        # PostGIS entre WKT de edges y ST_X/ST_Y de nodos.
        tl_by_coord: dict[tuple[float, float], int] = {}
        for nid, n_attrs in self._graph.nodes(data=True):
            if n_attrs.get(ATTR_NODE_TYPE) == NodeType.TRAFFIC_LIGHT.value:
                key = (
                    round(float(n_attrs.get(ATTR_LONGITUDE, 0.0)), 6),
                    round(float(n_attrs.get(ATTR_LATITUDE, 0.0)), 6),
                )
                tl_by_coord[key] = nid

        for row in rows:
            # Calculate weight as travel time (s) × road-type penalty factor.
            # Minor roads get higher weights so Dijkstra prefers major roads.
            max_speed_ms = row.max_speed * KMH_TO_MS
            travel_time = row.length / max_speed_ms if max_speed_ms > 0 else DEFAULT_EDGE_WEIGHT
            road_factor = ROAD_TYPE_WEIGHT_FACTORS.get(row.road_type, 1.0)
            weight = travel_time * road_factor

            # Parse geometry waypoints from GeoJSON LineString
            waypoints: list[tuple[float, float]] = []
            if row.geometry_json:
                geom = json.loads(row.geometry_json)
                waypoints = [(c[0], c[1]) for c in geom.get("coordinates", [])]

            # Detectar TLs intermedios y calcular su distancia acumulada desde
            # start_node a lo largo del LineString. Usamos la misma fórmula que
            # _calculate_length de osm_loader (distancia euclídea × 111320).
            # Los waypoints extremos (índice 0 y -1) son start/end_node → los
            # saltamos para evitar duplicar el TL endpoint cuando ya lo hay.
            mid_tls_fwd: list[tuple[int, float]] = []
            if waypoints and tl_by_coord:
                cum_dist = 0.0
                for i in range(len(waypoints)):
                    if i > 0:
                        lon1, lat1 = waypoints[i - 1]
                        lon2, lat2 = waypoints[i]
                        dlat = lat2 - lat1
                        dlon = lon2 - lon1
                        cum_dist += math.sqrt(dlat * dlat + dlon * dlon) * _METERS_PER_DEGREE
                    if i == 0 or i == len(waypoints) - 1:
                        continue  # endpoints ya son start/end del edge
                    key = (round(waypoints[i][0], 6), round(waypoints[i][1], 6))
                    tl_nid = tl_by_coord.get(key)
                    if tl_nid is not None:
                        mid_tls_fwd.append((tl_nid, cum_dist))

            edge_attrs = {
                ATTR_EDGE_ID: row.id,
                ATTR_LENGTH: row.length,
                ATTR_MAX_SPEED: row.max_speed,
                ATTR_WEIGHT: weight,
                ATTR_ROAD_TYPE: row.road_type,
                ATTR_ONE_WAY: row.one_way,
                ATTR_WAYPOINTS: waypoints,
                ATTR_MID_TLS: mid_tls_fwd,
                ATTR_LANES: max(int(row.lanes), 1),
                ATTR_IS_ROUNDABOUT: bool(row.is_roundabout),
                ATTR_ROUNDABOUT_ID: row.roundabout_id,
            }

            # Add forward edge
            self._graph.add_edge(row.start_node_id, row.end_node_id, **edge_attrs)

            # Add reverse edge for bidirectional roads (reversed waypoint order)
            if not row.one_way:
                total_len = float(row.length)
                mid_tls_rev = [
                    (nid, max(total_len - d, 0.0)) for nid, d in reversed(mid_tls_fwd)
                ]
                reverse_attrs = {
                    **edge_attrs,
                    ATTR_WAYPOINTS: list(reversed(waypoints)),
                    ATTR_MID_TLS: mid_tls_rev,
                }
                self._graph.add_edge(row.end_node_id, row.start_node_id, **reverse_attrs)

        # Reconstruir índices por rotonda tras cargar aristas. Necesario antes
        # de la fusión para saber qué nodos pertenecen al ring.
        self._rebuild_roundabout_indices()

        # Fusión de nodos del ring: cada anillo OSM viene partido en varias
        # ways → tras la carga aparecen 4-10 nodos donde topológicamente sólo
        # debería haber entradas/salidas. Fusionamos los degree-2 internos
        # SIN tocar la BD (los nodos físicos siguen existiendo, simplemente no
        # entran en el DiGraph del simulador).
        try:
            fused = self._fuse_ring_nodes()
            if fused:
                logger.info(
                    "Fused %d ring-internal node(s) across roundabouts", fused
                )
        except Exception:  # pragma: no cover — fusion errors must not break boot
            logger.exception("Ring-node fusion failed; continuing without fusion")

        # RDP + Catmull-Rom centrípeto sobre las aristas con is_roundabout.
        # Sustituye la geometría poligonal por una curva muestreada — los
        # vehículos siguen la curva en arc-length (vehicle_physics) y el
        # cliente la dibuja como malla curva (edge_renderer).
        try:
            spline_count = self._splinify_roundabout_edges()
            if spline_count:
                logger.info(
                    "Splinified %d roundabout edge(s) with centripetal Catmull-Rom",
                    spline_count,
                )
        except Exception:  # pragma: no cover
            logger.exception(
                "Roundabout splinification failed; falling back to polyline geometry"
            )

        # Reconstrucción final tras fusión + spline (longitudes cambian).
        self._rebuild_roundabout_indices()

    # ------------------------------------------------------------------
    # Fase 1: fusión de nodos internos del ring
    # ------------------------------------------------------------------
    def _fuse_ring_nodes(self) -> int:
        """
        Fusiona nodos degree-2 internos de cada rotonda.

        Un nodo es **fusible** si:
          - Pertenece al ring (`is_roundabout=True` en sus aristas).
          - No es TRAFFIC_LIGHT.
          - Sus únicas aristas son las del ring (no tiene entradas/salidas
            externas, ni siquiera bidireccionales).
          - Dentro del ring tiene exactamente 1 in y 1 out (anillo direccional).

        Las cadenas maximales de fusibles entre dos no-fusibles (anchors) se
        colapsan en una única arista con geometría concatenada y `mid_tls`
        re-mapeados con offset de longitud.

        Las fusiones que cerrarían el ring sobre el mismo anchor (self-loop)
        se cortan justo antes — preferimos un edge corto suelto a un self-loop
        que rompería el cálculo de gap del IDM.

        Returns:
            Número de nodos eliminados del grafo (≥ 0).
        """
        # Snapshot determinista — ordenar rids permite reproducir el orden de
        # fusiones entre runs.
        rids = sorted(self._roundabout_edges.keys())
        removed_total = 0

        for rid in rids:
            ring_edges = list(self._roundabout_edges.get(rid, ()))
            if not ring_edges:
                continue

            ring_edge_set: set[tuple[int, int]] = set(ring_edges)
            ring_node_set: set[int] = set()
            ring_in: dict[int, list[tuple[int, int]]] = {}
            ring_out: dict[int, list[tuple[int, int]]] = {}
            for u, v in ring_edges:
                ring_node_set.add(u)
                ring_node_set.add(v)
                ring_in.setdefault(v, []).append((u, v))
                ring_out.setdefault(u, []).append((u, v))

            def _is_fusible(n: int) -> bool:
                attrs = self._graph.nodes.get(n)
                if attrs is None:
                    return False
                if attrs.get(ATTR_NODE_TYPE) == NodeType.TRAFFIC_LIGHT.value:
                    return False
                if n not in ring_node_set:
                    return False
                in_ring = len(ring_in.get(n, ()))
                out_ring = len(ring_out.get(n, ()))
                if in_ring != 1 or out_ring != 1:
                    return False
                # Cualquier arista hacia/desde fuera del ring → no fusible.
                if self._graph.in_degree(n) != in_ring:
                    return False
                if self._graph.out_degree(n) != out_ring:
                    return False
                return True

            anchors = sorted(n for n in ring_node_set if not _is_fusible(n))
            if not anchors:
                # Anillo cerrado sin entradas — dejar como está para no crear
                # un self-loop con TODA la rotonda.
                continue

            for anchor in anchors:
                for (u_out, v_out) in list(ring_out.get(anchor, ())):
                    if (u_out, v_out) not in ring_edge_set:
                        continue  # ya fusionado en otra cadena
                    chain: list[tuple[int, int]] = [(u_out, v_out)]
                    cur = v_out
                    while _is_fusible(cur):
                        nxt_list = ring_out.get(cur, [])
                        if len(nxt_list) != 1:
                            break
                        nxt = nxt_list[0]
                        if nxt[1] == anchor:
                            # Cortar antes de cerrar el ring sobre el mismo
                            # anchor — un edge corto suelto es preferible al
                            # self-loop.
                            break
                        chain.append(nxt)
                        cur = nxt[1]

                    if len(chain) <= 1:
                        continue  # nada que fusionar

                    merged_attrs, merged_intermediates = self._merge_edge_chain(chain)
                    new_end = chain[-1][1]

                    # Eliminar las aristas originales del grafo (y del set de
                    # control para no reprocesarlas) antes de añadir la fusionada.
                    for e in chain:
                        if self._graph.has_edge(*e):
                            self._graph.remove_edge(*e)
                        ring_edge_set.discard(e)

                    # Eliminar nodos intermedios fusibles si quedaron sin aristas.
                    for n in merged_intermediates:
                        if n in self._graph and self._graph.degree(n) == 0:
                            self._graph.remove_node(n)
                            removed_total += 1

                    self._graph.add_edge(anchor, new_end, **merged_attrs)

        return removed_total

    def _merge_edge_chain(
        self,
        chain: list[tuple[int, int]],
    ) -> tuple[dict[str, Any], list[int]]:
        """
        Construye los atributos de la arista fusionada a partir de una cadena
        de aristas consecutivas del ring.

        Los waypoints se concatenan eliminando el punto duplicado en cada
        empalme (`wps[:-1] + next_wps`). Los `mid_tls` se desplazan por la
        longitud acumulada de las aristas anteriores. El resto de atributos
        (lanes, max_speed, road_type, is_roundabout, roundabout_id, one_way)
        se copian de la primera arista — son uniformes dentro de un anillo
        OSM por construcción.

        Returns:
            (merged_attrs, intermediate_node_ids) — los nodos intermedios de
            la cadena que pueden eliminarse del grafo.
        """
        first_u, first_v = chain[0]
        first_attrs = self._graph.edges[first_u, first_v]

        merged_waypoints: list[tuple[float, float]] = list(
            first_attrs.get(ATTR_WAYPOINTS, [])
        )
        merged_mid_tls: list[tuple[int, float]] = list(
            first_attrs.get(ATTR_MID_TLS, [])
        )
        merged_length: float = float(first_attrs.get(ATTR_LENGTH, 0.0))

        for (u, v) in chain[1:]:
            attrs = self._graph.edges[u, v]
            wps = list(attrs.get(ATTR_WAYPOINTS, []))
            if merged_waypoints and wps and merged_waypoints[-1] == wps[0]:
                merged_waypoints.extend(wps[1:])
            else:
                merged_waypoints.extend(wps)
            offset = merged_length
            for tl_id, dist in attrs.get(ATTR_MID_TLS, []):
                merged_mid_tls.append((tl_id, offset + float(dist)))
            merged_length += float(attrs.get(ATTR_LENGTH, 0.0))

        max_speed_kmh = float(first_attrs.get(ATTR_MAX_SPEED, 50.0))
        max_speed_ms = max_speed_kmh * KMH_TO_MS
        travel_time = (
            merged_length / max_speed_ms if max_speed_ms > 0 else DEFAULT_EDGE_WEIGHT
        )
        road_factor = ROAD_TYPE_WEIGHT_FACTORS.get(
            first_attrs.get(ATTR_ROAD_TYPE), 1.0
        )
        weight = travel_time * road_factor

        merged_attrs: dict[str, Any] = {
            **first_attrs,  # preserva edge_id, road_type, max_speed, lanes, ...
            ATTR_LENGTH: merged_length,
            ATTR_WEIGHT: weight,
            ATTR_WAYPOINTS: merged_waypoints,
            ATTR_MID_TLS: merged_mid_tls,
        }
        # El edge_id de la fusión se queda con el de la primera arista — sirve
        # como hint para overlays/incident manager. No se usa como FK contra BD.

        intermediates = [v for (_, v) in chain[:-1]]
        return merged_attrs, intermediates

    # ------------------------------------------------------------------
    # Fase 2: RDP + spline samples para aristas del ring
    # ------------------------------------------------------------------
    def _splinify_roundabout_edges(self) -> int:
        """
        Sustituye la poligonal de cada arista con `is_roundabout=True` por un
        muestreo arc-length de una spline Catmull-Rom centrípeta.

        Pasos por arista:
          1. Aplicar RDP con tolerancia adaptativa al radio del anillo
             (`ε = clamp(0.012·R, 0.20, 0.50) m`); preservar los waypoints
             que sostienen un mid_tl como índices protegidos.
          2. Construir la tabla de muestras `(s_m, lon, lat)`.
          3. Sobrescribir `ATTR_LENGTH` y recalcular `ATTR_WEIGHT`.
          4. Re-mapear cada `mid_tl` a su `s_m` correspondiente en la spline,
             buscando el waypoint asociado en la tabla.
          5. Marcar `ATTR_USE_SPLINE=True`.

        Returns:
            Número de aristas convertidas a spline.
        """
        count = 0
        for u, v, attrs in list(self._graph.edges(data=True)):
            if not attrs.get(ATTR_IS_ROUNDABOUT):
                continue
            wps: list[tuple[float, float]] = list(attrs.get(ATTR_WAYPOINTS, []))
            if len(wps) < 2:
                continue
            mid_tls: list[tuple[int, float]] = list(attrs.get(ATTR_MID_TLS, []))

            # Indexar TLs por waypoint para protegerlos en RDP.
            #   waypoint_idx → tl_id
            tl_at_idx: dict[int, int] = {}
            for tl_id, _dist in mid_tls:
                # Buscar el waypoint cuyo (lon, lat) coincide con el TL. Sólo
                # los nodos del grafo tienen lon/lat exactos; si no encontramos
                # el tl_id por coordenadas dejamos al TL en su distancia
                # original (será re-mapeada por interpolación, ver paso 4).
                tl_attrs = self._graph.nodes.get(tl_id)
                if tl_attrs is None:
                    continue
                tl_lon = round(float(tl_attrs.get(ATTR_LONGITUDE, 0.0)), 6)
                tl_lat = round(float(tl_attrs.get(ATTR_LATITUDE, 0.0)), 6)
                for i, (wlon, wlat) in enumerate(wps):
                    if round(wlon, 6) == tl_lon and round(wlat, 6) == tl_lat:
                        tl_at_idx[i] = tl_id
                        break

            # Tolerancia RDP adaptativa al radio del anillo. Si la rotonda no
            # tiene roundabout_length (caso degenerado), usar el límite
            # inferior — más seguro que descartar.
            rid = attrs.get(ATTR_ROUNDABOUT_ID)
            ring_len = self._roundabout_length.get(rid, 0.0) if rid is not None else 0.0
            if ring_len > 0:
                R_ring = ring_len / (2.0 * math.pi)
                eps = max(
                    RDP_TOLERANCE_MIN_M,
                    min(RDP_TOLERANCE_MAX_M, RDP_TOLERANCE_PER_RADIUS * R_ring),
                )
            else:
                eps = RDP_TOLERANCE_MIN_M

            simplified = rdp(wps, eps, protected_indices=tl_at_idx.keys())

            # Cota dura: nunca colapsar por debajo de RDP_MIN_POINTS.
            if len(simplified) < RDP_MIN_POINTS and len(wps) >= RDP_MIN_POINTS:
                # Insertar puntos intermedios uniformemente del original hasta
                # alcanzar el mínimo.
                step = max(1, len(wps) // RDP_MIN_POINTS)
                simplified = [wps[i] for i in range(0, len(wps), step)]
                if simplified[-1] != wps[-1]:
                    simplified.append(wps[-1])

            # Construir spline samples sobre los waypoints simplificados. Las
            # phantom points se dejan en reflexión por defecto — un kink
            # imperceptible en el empalme de aristas del ring queda en los
            # nodos de entrada/salida, donde el ramal externo crea un cambio
            # de dirección visible que disimula el efecto.
            table, total_length = build_spline_table(
                simplified, samples_per_segment=SPLINE_SAMPLES_PER_SEGMENT,
            )
            if not table or total_length <= 0:
                continue

            # Re-mapear cada mid_tl a su arc-length en la spline.
            # 1) Localizar la muestra cuyo waypoint coincide con el TL.
            # 2) Como la spline pasa por todos los waypoints, sus muestras a
            #    índice (rdp_idx * SPLINE_SAMPLES_PER_SEGMENT) son exactamente
            #    los waypoints. Para TLs supervivientes a RDP esto es exacto;
            #    para los que cayeran fuera (no debería pasar al estar
            #    protegidos), usamos su distancia original escalada.
            rdp_idx_by_tl: dict[int, int] = {}
            for i, p in enumerate(simplified):
                key6 = (round(p[0], 6), round(p[1], 6))
                for orig_i, tl_id in tl_at_idx.items():
                    if (round(wps[orig_i][0], 6), round(wps[orig_i][1], 6)) == key6:
                        rdp_idx_by_tl[tl_id] = i
                        break

            new_mid_tls: list[tuple[int, float]] = []
            old_total = float(attrs.get(ATTR_LENGTH, 0.0)) or total_length
            for tl_id, dist in mid_tls:
                rdp_i = rdp_idx_by_tl.get(tl_id)
                if rdp_i is not None:
                    sample_i = rdp_i * SPLINE_SAMPLES_PER_SEGMENT
                    if 0 <= sample_i < len(table):
                        new_mid_tls.append((tl_id, table[sample_i][0]))
                        continue
                # Fallback: escalar proporcionalmente (preserva orden y
                # posición relativa aunque la spline haya cambiado el length
                # total un epsilon).
                scaled = (dist / old_total) * total_length if old_total > 0 else dist
                new_mid_tls.append((tl_id, scaled))

            max_speed_kmh = float(attrs.get(ATTR_MAX_SPEED, 50.0))
            max_speed_ms = max_speed_kmh * KMH_TO_MS
            travel_time = (
                total_length / max_speed_ms if max_speed_ms > 0 else DEFAULT_EDGE_WEIGHT
            )
            road_factor = ROAD_TYPE_WEIGHT_FACTORS.get(
                attrs.get(ATTR_ROAD_TYPE), 1.0
            )

            attrs[ATTR_WAYPOINTS] = simplified
            attrs[ATTR_LENGTH] = total_length
            attrs[ATTR_WEIGHT] = travel_time * road_factor
            attrs[ATTR_USE_SPLINE] = True
            attrs[ATTR_SPLINE_SAMPLES] = table
            attrs[ATTR_SPLINE_LENGTH] = total_length
            attrs[ATTR_MID_TLS] = new_mid_tls
            if ring_len > 0:
                # Cacheamos el radio circular del anillo para que
                # vehicle_physics._edge_curvature_vmax aplique un cap de
                # velocidad consistente — el cálculo por 3 waypoints pierde
                # precisión cuando la spline tiene pocos puntos de control.
                attrs[ATTR_RING_RADIUS_M] = ring_len / (2.0 * math.pi)
            count += 1

        return count

    def _rebuild_roundabout_indices(self) -> None:
        """
        Recalcular self._roundabout_edges y self._roundabout_length a partir
        de las aristas actualmente cargadas en el grafo. Se llama al final
        de _load_edges.
        """
        self._roundabout_edges.clear()
        self._roundabout_length.clear()
        for u, v, attrs in self._graph.edges(data=True):
            rid = attrs.get(ATTR_ROUNDABOUT_ID)
            if rid is None or not attrs.get(ATTR_IS_ROUNDABOUT):
                continue
            self._roundabout_edges.setdefault(rid, []).append((u, v))
            self._roundabout_length[rid] = (
                self._roundabout_length.get(rid, 0.0)
                + float(attrs.get(ATTR_LENGTH, 0.0))
            )

    def get_roundabout_members(self, rid: int) -> list[tuple[int, int]]:
        """
        Devuelve las aristas (u, v) que pertenecen a la rotonda `rid`.

        Las aristas del anillo son direccionales — si la rotonda es de doble
        carril o bidireccional (poco común en OSM) aparecen en ambos sentidos.
        """
        return list(self._roundabout_edges.get(rid, ()))

    def get_roundabout_length(self, rid: int) -> float:
        """Longitud total (suma de aristas, en metros) del anillo `rid`."""
        return self._roundabout_length.get(rid, 0.0)

    def iter_roundabouts(self):
        """Itera los ids de rotondas cargadas."""
        return iter(self._roundabout_edges.keys())

    def node_lonlat(self, node_id: int) -> tuple[float, float]:
        """
        Devuelve (lon, lat) del nodo. Fallback (0.0, 0.0) si no existe,
        para facilitar uso en loops sin chequeo previo.
        """
        if node_id not in self._graph:
            return (0.0, 0.0)
        attrs = self._graph.nodes[node_id]
        return (
            float(attrs.get(ATTR_LONGITUDE, 0.0)),
            float(attrs.get(ATTR_LATITUDE, 0.0)),
        )

    def set_dynamic_weights(
        self, weights_by_edge: dict[tuple[int, int], float]
    ) -> None:
        """
        Aplica multiplicadores por arista al peso en A* (Fase 6).
        El dict sobrescribe por completo los pesos previos.
        """
        self._dynamic_weights = dict(weights_by_edge)

    def get_shortest_path(
        self,
        start: int,
        end: int,
        *,
        weight: str = ATTR_WEIGHT,
    ) -> list[int]:
        """
        Find the shortest path between two nodes using A*.

        Uses A* with a geographic heuristic (straight-line travel-time lower
        bound) instead of Dijkstra, which explores fewer nodes and scales
        better as the network grows.

        Args:
            start: Starting node ID
            end: Ending node ID
            weight: Edge attribute to use as weight (default: travel time)

        Returns:
            List of node IDs forming the shortest path

        Raises:
            nx.NetworkXNoPath: If no path exists between the nodes
            nx.NodeNotFound: If start or end node is not in the graph
        """
        return self.get_shortest_path_astar(start, end)

    def get_shortest_path_safe(
        self,
        start: int,
        end: int,
        *,
        weight: str = ATTR_WEIGHT,
    ) -> Optional[list[int]]:
        """
        Find the shortest path between two nodes (returns None if not found).

        Args:
            start: Starting node ID
            end: Ending node ID
            weight: Edge attribute to use as weight

        Returns:
            List of node IDs or None if no path exists
        """
        try:
            return self.get_shortest_path(start, end, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_shortest_path_astar(
        self,
        start: int,
        end: int,
        *,
        blocked_edges: Optional[dict[tuple[int, int], Any]] = None,
        restricted_edges: Optional[set[tuple[int, int]]] = None,
    ) -> list[int]:
        """
        Find the shortest path using A* with a geographic heuristic.

        The heuristic is the straight-line travel-time lower bound:
            h(u) = euclidean_distance(u, end) * MIN_ROAD_FACTOR / MAX_SPEED_MS

        This is admissible because:
        - Road distance ≥ straight-line distance.
        - Actual edge weight = travel_time * road_factor ≥ dist * MIN_FACTOR / MAX_SPEED.

        Args:
            start: Starting node ID
            end:   Ending node ID
            blocked_edges: Mapping (u,v) → cualquier valor (None o TTL). Las
                aristas presentes pagan BLOCKED_EDGE_PENALTY_FACTOR. No se
                eliminan del grafo para no perder conectividad.
            restricted_edges: Aristas dentro de zonas restringidas para el
                vtype solicitante (ZBE force_reroute). Reciben
                ZBE_EDGE_PENALTY_FACTOR en la función de peso.

        Returns:
            List of node IDs forming the shortest path

        Raises:
            nx.NetworkXNoPath: If no path exists
            nx.NodeNotFound:   If start or end is not in the graph
        """
        if start not in self._graph:
            raise nx.NodeNotFound(f"Source {start} is not in G")
        if end not in self._graph:
            raise nx.NodeNotFound(f"Target {end} is not in G")
        end_attrs = self._graph.nodes[end]
        end_lat: float = end_attrs.get(ATTR_LATITUDE, 0.0)
        end_lon: float = end_attrs.get(ATTR_LONGITUDE, 0.0)

        def _heuristic(u: int, _v: int) -> float:
            u_attrs = self._graph.nodes[u]
            dlat = u_attrs.get(ATTR_LATITUDE, 0.0) - end_lat
            dlon = u_attrs.get(ATTR_LONGITUDE, 0.0) - end_lon
            dist_m = math.sqrt(dlat * dlat + dlon * dlon) * _METERS_PER_DEGREE
            return dist_m * _ASTAR_MIN_ROAD_FACTOR / _ASTAR_MAX_SPEED_MS

        dynamic = self._dynamic_weights
        blocked = blocked_edges or {}
        restricted = restricted_edges or set()

        if not dynamic and not blocked and not restricted:
            return nx.astar_path(
                self._graph, start, end, heuristic=_heuristic, weight=ATTR_WEIGHT
            )

        def _weight(u: int, v: int, data: dict[str, Any]) -> float:
            base = float(data.get(ATTR_WEIGHT, DEFAULT_EDGE_WEIGHT))
            mult = dynamic.get((u, v), 1.0)
            if (u, v) in blocked:
                mult *= BLOCKED_EDGE_PENALTY_FACTOR
            if (u, v) in restricted:
                mult *= ZBE_EDGE_PENALTY_FACTOR
            return base * mult

        return nx.astar_path(
            self._graph, start, end, heuristic=_heuristic, weight=_weight
        )

    def get_shortest_path_astar_safe(
        self,
        start: int,
        end: int,
        *,
        blocked_edges: Optional[dict[tuple[int, int], Any]] = None,
        restricted_edges: Optional[set[tuple[int, int]]] = None,
    ) -> Optional[list[int]]:
        """
        A* shortest path (returns None if no path exists instead of raising).

        Args:
            start: Starting node ID
            end:   Ending node ID
            blocked_edges: Mapping de aristas bloqueadas (valor None o TTL);
                se penalizan por BLOCKED_EDGE_PENALTY_FACTOR en vez de
                eliminarlas.
            restricted_edges: Aristas restringidas por ZBE para el vtype
                solicitante; pagan ZBE_EDGE_PENALTY_FACTOR.

        Returns:
            List of node IDs or None if no path exists
        """
        try:
            return self.get_shortest_path_astar(
                start,
                end,
                blocked_edges=blocked_edges,
                restricted_edges=restricted_edges,
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_astar_path(
        self,
        start: int,
        end: int,
        *,
        weight: str = ATTR_WEIGHT,
    ) -> list[int]:
        """
        Find the shortest path using A* with a geographic heuristic.

        The heuristic estimates travel time from the straight-line distance
        between nodes using their GPS coordinates. It is admissible because
        actual travel time >= euclidean_distance / max_speed.

        Args:
            start: Starting node ID
            end: Ending node ID
            weight: Edge attribute to use as weight (default: travel time)

        Returns:
            List of node IDs forming the shortest path

        Raises:
            nx.NetworkXNoPath: If no path exists between the nodes
            nx.NodeNotFound: If start or end node is not in the graph
        """
        def _heuristic(u: int, v: int) -> float:
            u_d = self._graph.nodes[u]
            v_d = self._graph.nodes[v]
            # Approximate degrees → metres at ~40°N (Talavera de la Reina)
            dlon = (u_d.get(ATTR_LONGITUDE, 0) - v_d.get(ATTR_LONGITUDE, 0)) * 82000
            dlat = (u_d.get(ATTR_LATITUDE, 0)  - v_d.get(ATTR_LATITUDE, 0))  * 111320
            dist_m = (dlon ** 2 + dlat ** 2) ** 0.5
            # 100 km/h = 27.78 m/s as upper bound → admissible heuristic
            return dist_m / 27.78

        return nx.astar_path(self._graph, start, end, heuristic=_heuristic, weight=weight)

    def get_astar_path_safe(
        self,
        start: int,
        end: int,
        *,
        weight: str = ATTR_WEIGHT,
    ) -> Optional[list[int]]:
        """
        Find the shortest path using A* (returns None if not found).

        Args:
            start: Starting node ID
            end: Ending node ID
            weight: Edge attribute to use as weight

        Returns:
            List of node IDs or None if no path exists
        """
        try:
            return self.get_astar_path(start, end, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_route_length(self, path: list[int]) -> float:
        """
        Calculate the total length of a path in meters.

        Args:
            path: List of node IDs forming a path

        Returns:
            Total path length in meters
        """
        if len(path) < 2:
            return 0.0

        total_length = 0.0
        for i in range(len(path) - 1):
            edge_data = self._graph.get_edge_data(path[i], path[i + 1])
            if edge_data:
                total_length += edge_data.get(ATTR_LENGTH, 0.0)

        return total_length

    def get_route_travel_time(self, path: list[int]) -> float:
        """
        Calculate the total travel time of a path in seconds.

        Args:
            path: List of node IDs forming a path

        Returns:
            Total travel time in seconds
        """
        if len(path) < 2:
            return 0.0

        total_time = 0.0
        for i in range(len(path) - 1):
            edge_data = self._graph.get_edge_data(path[i], path[i + 1])
            if edge_data:
                total_time += edge_data.get(ATTR_WEIGHT, 0.0)

        return total_time

    def get_neighbors(self, node_id: int) -> list[int]:
        """
        Get all neighboring nodes (successors in directed graph).

        Args:
            node_id: The node ID

        Returns:
            List of neighbor node IDs
        """
        if node_id not in self._graph:
            return []
        return list(self._graph.successors(node_id))

    def get_predecessors(self, node_id: int) -> list[int]:
        """
        Get all predecessor nodes (nodes with edges to this node).

        Args:
            node_id: The node ID

        Returns:
            List of predecessor node IDs
        """
        if node_id not in self._graph:
            return []
        return list(self._graph.predecessors(node_id))

    def find_nearest_node(
        self,
        longitude: float,
        latitude: float,
    ) -> Optional[int]:
        """
        Find the nearest node to a given point.

        Uses simple Euclidean distance for speed. For more accurate results
        with large distances, consider using Haversine formula.

        Args:
            longitude: Longitude of the point
            latitude: Latitude of the point

        Returns:
            Node ID of the nearest node, or None if graph is empty
        """
        if self._graph.number_of_nodes() == 0:
            return None

        nearest_node: Optional[int] = None
        min_distance = float("inf")

        for node_id, attrs in self._graph.nodes(data=True):
            node_lon = attrs.get(ATTR_LONGITUDE, 0)
            node_lat = attrs.get(ATTR_LATITUDE, 0)

            # Simple Euclidean distance (fast approximation)
            distance = (node_lon - longitude) ** 2 + (node_lat - latitude) ** 2

            if distance < min_distance:
                min_distance = distance
                nearest_node = node_id

        return nearest_node

    def has_node(self, node_id: int) -> bool:
        """Check if a node exists in the graph."""
        return node_id in self._graph

    def has_edge(self, start_id: int, end_id: int) -> bool:
        """Check if an edge exists between two nodes."""
        return self._graph.has_edge(start_id, end_id)

    def get_node_attributes(self, node_id: int) -> dict[str, Any]:
        """
        Cached lookup — devuelve la MISMA referencia (los consumers leen, no
        mutan). Cacheado permanentemente: los attrs son inmutables tras
        `build_from_database`.
        """
        cached = self._node_attrs_cache.get(node_id)
        if cached is not None:
            return cached
        if node_id not in self._graph:
            empty: dict[str, Any] = {}
            self._node_attrs_cache[node_id] = empty
            return empty
        attrs = dict(self._graph.nodes[node_id])
        self._node_attrs_cache[node_id] = attrs
        return attrs

    def get_edge_attributes(self, start_id: int, end_id: int) -> dict[str, Any]:
        """
        Cached lookup de attrs por arista. Devuelve la MISMA referencia
        cacheada en cada llamada (los consumers leen, no mutan). Empty dict
        para aristas inexistentes también se cachea, así que llamadas
        repetidas a aristas inválidas tampoco hacen `has_edge` cada vez.
        """
        key = (start_id, end_id)
        cached = self._edge_attrs_cache.get(key)
        if cached is not None:
            return cached
        if not self._graph.has_edge(start_id, end_id):
            empty: dict[str, Any] = {}
            self._edge_attrs_cache[key] = empty
            return empty
        attrs = dict(self._graph.edges[start_id, end_id])
        self._edge_attrs_cache[key] = attrs
        return attrs

    def get_all_node_ids(self) -> list[int]:
        """Get all node IDs in the graph."""
        return list(self._graph.nodes())

    def get_all_edges(self) -> list[tuple[int, int]]:
        """Get all edges as (start, end) tuples."""
        return list(self._graph.edges())

    def export_to_json(self) -> str:
        """
        Export the graph to JSON format for debugging/visualization.

        Returns:
            JSON string representation of the graph
        """
        data = {
            "nodes": [
                {"id": node_id, **attrs}
                for node_id, attrs in self._graph.nodes(data=True)
            ],
            "edges": [
                {"source": u, "target": v, **attrs}
                for u, v, attrs in self._graph.edges(data=True)
            ],
            "stats": {
                "node_count": self._graph.number_of_nodes(),
                "edge_count": self._graph.number_of_edges(),
                "last_updated": self._last_build_time,
            },
        }
        return json.dumps(data, indent=2)

    def export_to_dict(self) -> dict[str, Any]:
        """
        Export the graph to a dictionary for API responses.

        Returns:
            Dictionary representation of the graph
        """
        return {
            "nodes": [
                {"id": node_id, **attrs}
                for node_id, attrs in self._graph.nodes(data=True)
            ],
            "edges": [
                {"source": u, "target": v, **attrs}
                for u, v, attrs in self._graph.edges(data=True)
            ],
            "stats": {
                "node_count": self._graph.number_of_nodes(),
                "edge_count": self._graph.number_of_edges(),
                "is_connected": self._stats.is_connected if self._stats else False,
                "build_time_ms": self._stats.build_time_ms if self._stats else 0,
            },
        }

    def clear(self) -> None:
        """Clear the graph and reset statistics."""
        self._graph.clear()
        self._stats = None
        self._last_build_time = 0

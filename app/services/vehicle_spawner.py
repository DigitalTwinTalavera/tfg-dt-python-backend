"""
Spawner de vehículos.

Genera vehículos en nodos de entrada con rutas calculadas por NetworkX
y aplica el límite MAX_VEHICLES. Las transiciones de estado
(IDLE → MOVING → FINISHED) se manejan en el motor de simulación.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import networkx as nx

from app.config import settings
from app.core.constants import (
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_TYPE,
    DEFAULT_MAX_SPEED_MS,
    KMH_TO_MS,
    MIN_EDGE_LENGTH_M,
    SPAWN_INITIAL_PROGRESS_MAX,
    SPAWN_INITIAL_PROGRESS_MIN,
    SPAWN_SPEED_VARIANCE_MAX,
    SPAWN_SPEED_VARIANCE_MIN,
    YELLOW_BRAKE_PROBABILITY,
)
from app.core.physics.vehicle_types import (
    PROFILES,
    VehicleType,
    VehicleTypeProfile,
    get_profile,
)
from app.core.route import RouteInfo, compute_route
from app.models.enums import NodeType, VehicleStatus
from app.services.network_graph import RoadNetworkGraph

logger = logging.getLogger(__name__)


@dataclass
class SimVehicle:
    """Representación en memoria de un vehículo de simulación."""

    id: str
    start_node_id: int
    end_node_id: int
    route: RouteInfo
    status: VehicleStatus = VehicleStatus.IDLE
    current_edge_index: int = 0
    longitude: float = 0.0
    latitude: float = 0.0
    velocity: float = 0.0
    acceleration: float = 0.0
    heading: float = 0.0
    progress_on_edge: float = 0.0
    desired_speed_ms: float = DEFAULT_MAX_SPEED_MS  # velocidad deseada individual (m/s)
    yellow_runs_light: bool = False   # set at spawn: 15% True → runs yellow lights
    collision_timer: float = 0.0      # countdown to auto-remove after collision (s)
    proximity_timer: float = 0.0      # tiempo sostenido con gap < umbral (s)
    vtype: VehicleType = VehicleType.CAR  # tipo de vehículo (car, moto, truck)
    lane: int = 0                          # carril actual (0 = derecha)
    length_m: float = 4.5                  # longitud bumper-to-bumper (m)
    # Heading (grados) de la última tangente de la arista que se acaba de
    # terminar. Se usa para mezclar con la tangente inicial de la arista
    # entrante durante los primeros EDGE_HEADING_BLEND_DIST_M metros y evitar
    # un snap visible al cambiar de tramo. < 0 → sin mezcla activa.
    prev_edge_end_heading: float = -1.0
    # True si la arista anterior era una rotonda — amplía la distancia de
    # mezcla de heading a la salida para evitar giros abruptos.
    prev_edge_was_roundabout: bool = False
    # Cooldown de evaluación MOBIL (ticks). Evita recalcular cambios de carril
    # en cada tick: sólo cuando el contador llega a 0 se re-evalúa, y al hacerlo
    # se resetea a MOBIL_EVAL_INTERVAL_TICKS. Inicializado vía hash(id) para
    # repartir la carga computacional entre ticks.
    mobil_cooldown_ticks: int = 0
    # Runtime STOP/YIELD sign tracking.
    # `stop_sign_cleared_node` guarda el ID del nodo STOP cuya parada obligatoria
    # ya se ha cumplido; mientras coincida con el end_node actual, el vehículo
    # puede pasar sin volver a parar. Se resetea al cambiar de arista.
    stop_sign_cleared_node: int = -1
    # Tiempo acumulado (s) con velocidad < STOP_SIGN_DWELL_SPEED_MS frente al
    # STOP. Al superar STOP_SIGN_DWELL_TIME_S se marca como cumplido.
    stop_sign_dwell_timer: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
            "route_edges": self.route.edge_ids,
            "route_length_m": round(self.route.length_m, 1),
            "status": self.status.value,
            "vtype": self.vtype.value,
            "lane": self.lane,
        }


class VehicleSpawner:
    """
    Genera vehículos en nodos de entrada con destino aleatorio
    y ruta calculada por A* (peso = tiempo de viaje).

    El método spawn_background() devuelve de inmediato y calcula las rutas
    en un hilo de fondo (asyncio.to_thread) para no bloquear el event loop.
    """

    def __init__(
        self,
        graph: RoadNetworkGraph,
        max_vehicles: int | None = None,
    ) -> None:
        self._graph = graph
        self._max_vehicles = max_vehicles or settings.MAX_VEHICLES
        self._vehicles: dict[str, SimVehicle] = {}
        self._counter = 0
        # Caché de rutas: evita recalcular la misma ruta (start, end) varias veces.
        # Con pocos nodos de entrada/salida (≤ decenas en Talavera), tras el primer
        # spawn todos los vehículos siguientes son cache-hits y el coste es O(1).
        self._route_cache: dict[tuple[int, int], RouteInfo] = {}
        self._counter_lock = threading.Lock()  # Protege _counter ante acceso concurrente
        # blocked_edges: mapa arista → None (bloqueo permanente, retirada manual).
        # Se usa como penalización en A* (no elimina la arista del grafo).
        self.blocked_edges: dict[tuple[int, int], object | None] = {}

    @property
    def graph(self) -> RoadNetworkGraph:
        return self._graph

    @property
    def max_vehicles(self) -> int:
        return self._max_vehicles

    @max_vehicles.setter
    def max_vehicles(self, value: int) -> None:
        """Actualiza el límite máximo de vehículos (hot-update desde config)."""
        self._max_vehicles = value

    @property
    def vehicles(self) -> dict[str, SimVehicle]:
        return self._vehicles

    @property
    def active_count(self) -> int:
        return sum(
            1 for v in self._vehicles.values()
            if v.status != VehicleStatus.FINISHED
        )

    def _next_id(self) -> str:
        with self._counter_lock:
            self._counter += 1
            return f"v_{self._counter}"

    def _get_nodes_by_type(self, node_type: str) -> list[int]:
        """Devuelve los IDs de nodos con un tipo dado."""
        return [
            nid
            for nid, attrs in self._graph.graph.nodes(data=True)
            if attrs.get(ATTR_NODE_TYPE) == node_type
        ]

    def get_entry_nodes(self) -> list[int]:
        # Combinamos ENTRY_POINT con INTERSECTION para que cualquier calle del
        # interior pueda ser origen de un spawn (y no solo los bordes del mapa).
        entry = self._get_nodes_by_type(NodeType.ENTRY_POINT.value)
        inter = self._get_nodes_by_type(NodeType.INTERSECTION.value)
        nodes = list({*entry, *inter})
        if not nodes:
            nodes = list(self._graph.graph.nodes())
        return nodes

    def get_exit_nodes(self) -> list[int]:
        exit_ = self._get_nodes_by_type(NodeType.EXIT_POINT.value)
        inter = self._get_nodes_by_type(NodeType.INTERSECTION.value)
        nodes = list({*exit_, *inter})
        if not nodes:
            nodes = list(self._graph.graph.nodes())
        return nodes

    def _filter_to_scc(
        self,
        entry_nodes: list[int],
        exit_nodes: list[int],
    ) -> tuple[list[int], list[int]]:
        """
        Filtra los nodos de spawn al mayor componente fuertemente conexo (SCC).

        En un grafo dirigido, el SCC garantiza que *cualquier* par (start, end)
        dentro de él tiene ruta válida en ambas direcciones. Esto evita que
        random.choice seleccione nodos de subgrafos desconectados (calles de
        sentido único que crean dead-ends) y hace que cada intento de spawn
        tenga éxito.

        Returns:
            (entry_filtrado, exit_filtrado) — si el filtrado deja alguna lista
            vacía, devuelve las listas originales como fallback.
        """
        try:
            sccs = sorted(
                nx.strongly_connected_components(self._graph.graph),
                key=len,
                reverse=True,
            )
        except Exception:
            return entry_nodes, exit_nodes

        if not sccs:
            return entry_nodes, exit_nodes

        largest_scc = sccs[0]
        filtered_entries = [n for n in entry_nodes if n in largest_scc]
        filtered_exits = [n for n in exit_nodes if n in largest_scc]

        if not filtered_entries or not filtered_exits:
            logger.warning(
                "El SCC mayor (%d nodos) no contiene nodos de entrada/salida; "
                "usando listas completas.",
                len(largest_scc),
            )
            return entry_nodes, exit_nodes

        logger.debug(
            "SCC filter: %d/%d entry nodes, %d/%d exit nodes válidos",
            len(filtered_entries), len(entry_nodes),
            len(filtered_exits), len(exit_nodes),
        )
        return filtered_entries, filtered_exits

    # -------------------------------------------------------------------------
    # Spawn asíncrono (fire-and-forget)
    # -------------------------------------------------------------------------

    async def spawn_background(
        self,
        count: int,
        on_complete: Callable[[list[SimVehicle]], Awaitable[None]] | None = None,
    ) -> int:
        """
        Inicia el spawn de `count` vehículos en background y devuelve
        inmediatamente el número solicitado.

        El cálculo de rutas (CPU-bound, A* + caché) se ejecuta en un hilo
        del pool de asyncio para no bloquear el event loop. Los vehículos
        se añaden al dict `_vehicles` al terminar y, opcionalmente, se
        invoca el callback `on_complete(batch)` para notificar al broadcaster.

        Args:
            count:       Número de vehículos a generar.
            on_complete: Corrutina llamada con la lista de vehículos creados.
                         Úsala para enviar el mensaje WS vehicles_batch_spawned.

        Returns:
            Número de vehículos que se intentarán crear (≤ count).

        Raises:
            ValueError: Si el grafo no tiene nodos navegables.
        """
        entry_nodes = self.get_entry_nodes()
        exit_nodes = self.get_exit_nodes()

        if not entry_nodes:
            raise ValueError("El grafo no tiene nodos navegables para spawning")
        if not exit_nodes:
            raise ValueError("El grafo no tiene nodos navegables para destinos")

        entry_nodes, exit_nodes = self._filter_to_scc(entry_nodes, exit_nodes)

        if count <= 0:
            return 0

        # Aviso por observabilidad si el usuario ha configurado un tope soft
        # y la petición lo supera; no se recorta, el motor acepta N completo.
        if self.active_count + count > self._max_vehicles:
            logger.warning(
                "Spawn %d superará max_vehicles=%d (activos=%d) — se acepta igualmente",
                count, self._max_vehicles, self.active_count,
            )

        asyncio.create_task(
            self._spawn_task(entry_nodes, exit_nodes, count, on_complete)
        )
        return count

    async def _spawn_task(
        self,
        entry_nodes: list[int],
        exit_nodes: list[int],
        count: int,
        on_complete: Callable[[list[SimVehicle]], Awaitable[None]] | None = None,
    ) -> None:
        """
        Tarea de background: calcula rutas en un hilo y añade los vehículos.
        Invoca on_complete(batch) tras añadirlos para que el broadcaster
        envíe el mensaje vehicles_batch_spawned al cliente.
        """
        try:
            # Ejecutar el cálculo síncrono (CPU-bound) en un hilo del pool
            # para liberar el event loop durante el proceso.
            batch = await asyncio.to_thread(
                self._spawn_sync_batch, entry_nodes, exit_nodes, count
            )

            # Añadir los vehículos al dict principal (event loop = thread-safe)
            for vehicle in batch:
                self._vehicles[vehicle.id] = vehicle

            logger.info(
                "Background spawn completado: %d/%d vehículos (activos: %d/%d)",
                len(batch),
                count,
                self.active_count,
                self._max_vehicles,
            )

            # Notificar al broadcaster para enviar el mensaje WS batch
            if on_complete and batch:
                await on_complete(batch)

        except Exception:
            logger.exception("Error en background spawn")

    def clear_route_cache(self) -> None:
        """Invalida la caché de rutas (llamar si el grafo cambia)."""
        self._route_cache.clear()

    def _spawn_sync_batch(
        self,
        entry_nodes: list[int],
        exit_nodes: list[int],
        count: int,
    ) -> list[SimVehicle]:
        """
        Genera `count` vehículos sincrónicamente (diseñado para correr en hilo).

        Estrategia:
          - Selección de origen/destino ponderada por capacidad libre de sus
            aristas salientes/entrantes → las calles vacías atraen más spawns.
          - Sin topes por rotonda: el A* con pesos dinámicos ya redistribuye.
          - Búsqueda de hueco libre probando todos los carriles y varios
            progress antes de descartar la ruta.

        Rendimiento: construye UNA sola vez un índice
        `occupants_by_edge_lane[(u,v)][lane]` con los ocupantes actuales y lo
        actualiza incrementalmente al spawnear. Esto convierte la búsqueda de
        hueco en O(ocupantes_de_la_arista) en lugar de O(N_total) por intento.
        """
        spawned: list[SimVehicle] = []

        # Índice unificado: ocupación por arista y carril + conteo por arista.
        # Recorremos los vehículos activos UNA vez (O(N)) y servimos todas las
        # consultas de spawn desde este índice.
        occupants_by_edge_lane: dict[
            tuple[int, int], dict[int, list[tuple[float, float]]]
        ] = {}
        edge_occupancy: dict[tuple[int, int], int] = {}
        for v in self._vehicles.values():
            if v.status == VehicleStatus.FINISHED:
                continue
            np_ = v.route.node_path
            ei = v.current_edge_index
            if ei >= len(np_) - 1:
                continue
            edge = (np_[ei], np_[ei + 1])
            edge_occupancy[edge] = edge_occupancy.get(edge, 0) + 1
            lane_map = occupants_by_edge_lane.setdefault(edge, {})
            lane_id = int(getattr(v, "lane", 0))
            lane_map.setdefault(lane_id, []).append(
                (v.progress_on_edge, v.length_m)
            )

        entry_weights = self._node_spawn_weights(
            entry_nodes, edge_occupancy, outgoing=True
        )
        exit_weights = self._node_spawn_weights(
            exit_nodes, edge_occupancy, outgoing=False
        )

        # Safety cap proporcional al batch: con el índice ya no escala con N.
        safety_cap = max(count * 4, 2_000)
        attempts = 0
        edges_used: set[tuple[int, int]] = set()

        while len(spawned) < count and attempts < safety_cap:
            attempts += 1

            start = _weighted_choice(entry_nodes, entry_weights)
            end = _weighted_choice(exit_nodes, exit_weights)
            if start == end:
                continue

            key = (start, end)
            route = self._route_cache.get(key)
            if route is None:
                route = compute_route(
                    self._graph, start, end, blocked_edges=self.blocked_edges
                )
                if route is not None:
                    self._route_cache[key] = route

            if route is None:
                continue

            node_attrs = self._graph.get_node_attributes(start)
            profile = _pick_vehicle_profile()
            first_node = route.node_path[0]
            second_node = route.node_path[1]
            first_edge_attrs = self._graph.get_edge_attributes(first_node, second_node)
            n_lanes = max(int(first_edge_attrs.get(ATTR_LANES, 1)), 1)
            first_edge_len = max(
                float(first_edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M
            )

            # Busca un hueco libre (lane, progress) en la primera arista antes
            # de descartar la ruta. Devuelve None si todo está ocupado.
            slot = self._find_free_spawn_slot(
                first_node=first_node,
                second_node=second_node,
                first_edge_len=first_edge_len,
                n_lanes=n_lanes,
                vehicle_length=profile.length_m,
                occupants_by_edge_lane=occupants_by_edge_lane,
            )
            if slot is None:
                continue
            lane, progress = slot

            vehicle = SimVehicle(
                id=self._next_id(),
                start_node_id=start,
                end_node_id=end,
                route=route,
                longitude=node_attrs.get(ATTR_LONGITUDE, 0.0),
                latitude=node_attrs.get(ATTR_LATITUDE, 0.0),
                vtype=profile.vtype,
                lane=lane,
                length_m=profile.length_m,
            )
            # Velocidad deseada individual: perfil · varianza, acotada al límite del
            # primer tramo para que los camiones no intenten ir a 130 km/h en ciudad.
            first_edge_kmh = _get_first_edge_speed_kmh(route, self._graph)
            edge_v_limit = first_edge_kmh * KMH_TO_MS
            profile_target = min(profile.idm.v0, profile.max_speed_ms, edge_v_limit)
            desired_ms = profile_target * random.uniform(
                SPAWN_SPEED_VARIANCE_MIN, SPAWN_SPEED_VARIANCE_MAX
            )
            vehicle.desired_speed_ms = desired_ms
            # Arrancar en reposo: elimina colisiones inducidas por velocidad
            # inicial sobre un líder más lento; IDM acelerará en el primer tick.
            vehicle.velocity = 0.0
            vehicle.progress_on_edge = progress
            # Comportamiento en semáforo amarillo: 40% de vehículos lo se saltan
            vehicle.yellow_runs_light = random.random() > YELLOW_BRAKE_PROBABILITY
            # Escalonar la primera evaluación MOBIL para repartir carga entre ticks:
            # cada vehículo arranca con un cooldown aleatorio ∈ [0, interval-1].
            from app.core.constants import MOBIL_EVAL_INTERVAL_TICKS
            vehicle.mobil_cooldown_ticks = random.randint(
                0, MOBIL_EVAL_INTERVAL_TICKS - 1
            )

            spawned.append(vehicle)
            edges_used.add((first_node, second_node))
            # Publicar la ocupación del nuevo vehículo para que los siguientes
            # intentos vean el hueco ya tomado (misma arista y carril).
            first_edge_key = (first_node, second_node)
            lane_map = occupants_by_edge_lane.setdefault(first_edge_key, {})
            lane_map.setdefault(lane, []).append((progress, profile.length_m))

        if len(spawned) < count:
            logger.warning(
                "Spawn incompleto: %d/%d vehículos tras %d intentos (red saturada)",
                len(spawned), count, attempts,
            )
        logger.info(
            "Spawn batch: %d vehículos, %d aristas iniciales únicas",
            len(spawned), len(edges_used),
        )

        return spawned

    def _find_free_spawn_slot(
        self,
        *,
        first_node: int,
        second_node: int,
        first_edge_len: float,
        n_lanes: int,
        vehicle_length: float,
        occupants_by_edge_lane: dict[
            tuple[int, int], dict[int, list[tuple[float, float]]]
        ],
    ) -> tuple[int, float] | None:
        """
        Busca un par (lane, progress) libre en la primera arista de la ruta.

        Intenta primero la ventana restringida [SPAWN_INITIAL_PROGRESS_MIN,
        SPAWN_INITIAL_PROGRESS_MAX] y, si no hay hueco en ningún carril,
        amplía a [0.0, 0.9]. Devuelve None si ninguna combinación es viable.

        Lee la ocupación del índice `occupants_by_edge_lane` mantenido por el
        batch: no itera la lista global de vehículos.
        """
        occupants_by_lane = occupants_by_edge_lane.get(
            (first_node, second_node), {}
        )

        lane_order = list(range(n_lanes))
        random.shuffle(lane_order)

        windows = [
            (SPAWN_INITIAL_PROGRESS_MIN, SPAWN_INITIAL_PROGRESS_MAX),
            (0.0, 0.9),
        ]

        for window_min, window_max in windows:
            for lane in lane_order:
                progress = _find_free_progress(
                    occupants=occupants_by_lane.get(lane, []),
                    window=(window_min, window_max),
                    vehicle_length=vehicle_length,
                    edge_length=first_edge_len,
                )
                if progress is not None:
                    return lane, progress
        return None

    def _node_spawn_weights(
        self,
        nodes: list[int],
        edge_occupancy: dict[tuple[int, int], int],
        *,
        outgoing: bool,
    ) -> list[float]:
        """
        Calcula pesos de muestreo por nodo inversamente proporcionales a la
        densidad vehicular de sus aristas adyacentes.

        `outgoing=True` considera salientes (nodos de entrada: peso alto si
        las calles que salen del nodo están poco ocupadas).
        `outgoing=False` considera entrantes (nodos de salida).

        Un nodo sin aristas (o aristas ya llenas) mantiene un peso mínimo
        de 0.05 para que nunca quede excluido del muestreo.
        """
        g = self._graph.graph
        weights: list[float] = []
        for node in nodes:
            edges = g.out_edges(node) if outgoing else g.in_edges(node)
            loads: list[float] = []
            for u, v in edges:
                length_m = max(
                    float(g[u][v].get(ATTR_LENGTH, 50.0)), MIN_EDGE_LENGTH_M
                )
                # Densidad normalizada (vehículos por cada 100 m).
                density = edge_occupancy.get((u, v), 0) / max(length_m / 100.0, 1.0)
                # Normalizar suponiendo que 10 veh/100m es "lleno".
                loads.append(min(density / 10.0, 1.0))
            avg_load = sum(loads) / len(loads) if loads else 0.0
            weights.append(max(1.0 - avg_load, 0.05))
        return weights

    # -------------------------------------------------------------------------
    # Spawn síncrono (mantenido para tests y uso interno)
    # -------------------------------------------------------------------------

    def spawn(self, count: int = 1) -> list[SimVehicle]:
        """
        Genera hasta `count` vehículos sincrónicamente.

        Preferir spawn_background() para uso en producción.
        """
        entry_nodes = self.get_entry_nodes()
        exit_nodes = self.get_exit_nodes()

        if not entry_nodes:
            raise ValueError("El grafo no tiene nodos navegables para spawning")
        if not exit_nodes:
            raise ValueError("El grafo no tiene nodos navegables para destinos")

        entry_nodes, exit_nodes = self._filter_to_scc(entry_nodes, exit_nodes)

        if count <= 0:
            return []

        spawned = self._spawn_sync_batch(entry_nodes, exit_nodes, count)
        for vehicle in spawned:
            self._vehicles[vehicle.id] = vehicle

        logger.info(
            "Spawned %d/%d vehículos (activos: %d)",
            len(spawned),
            count,
            self.active_count,
        )
        return spawned

    # -------------------------------------------------------------------------
    # Acceso y gestión de vehículos
    # -------------------------------------------------------------------------

    def get_vehicle(self, vehicle_id: str) -> SimVehicle | None:
        return self._vehicles.get(vehicle_id)

    def get_all_vehicles(self) -> list[SimVehicle]:
        return list(self._vehicles.values())

    def remove_vehicle(self, vehicle_id: str) -> bool:
        if vehicle_id in self._vehicles:
            del self._vehicles[vehicle_id]
            return True
        return False

    def reroute_vehicle(self, vehicle_id: str, end_node_id: int) -> bool:
        """
        Recalcula la ruta de un vehículo hacia un nuevo nodo destino.

        Toma como punto de partida el nodo de inicio del segmento actual
        para que el vehículo continúe desde donde está.

        Returns:
            True si se reroute con éxito, False si el vehículo no existe
            o no se pudo calcular la ruta.
        """
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None:
            return False

        node_path = vehicle.route.node_path
        ei = vehicle.current_edge_index
        start_node = node_path[ei] if ei < len(node_path) else node_path[-1]

        new_route = compute_route(self._graph, start_node, end_node_id)
        if new_route is None:
            return False

        vehicle.route = new_route
        vehicle.end_node_id = end_node_id
        vehicle.current_edge_index = 0
        vehicle.progress_on_edge = 0.0
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_first_edge_speed_kmh(route: RouteInfo, graph: "RoadNetworkGraph") -> float:  # type: ignore[name-defined]
    """
    Devuelve la velocidad máxima (km/h) del primer tramo de una ruta.

    Usado para inicializar la velocidad deseada de cada vehículo basándose
    en el límite real de la vía donde se spawnea.
    """
    if len(route.node_path) < 2:
        return 50.0
    attrs = graph.get_edge_attributes(route.node_path[0], route.node_path[1])
    speed = float(attrs.get(ATTR_MAX_SPEED, 50.0))
    return speed if speed > 0 else 50.0


def _weighted_choice(items: list[int], weights: list[float]) -> int:
    """random.choices con validación mínima — cae a choice uniforme si los pesos suman 0."""
    if not weights or sum(weights) <= 0.0:
        return random.choice(items)
    return random.choices(items, weights=weights, k=1)[0]


def _find_free_progress(
    occupants: list[tuple[float, float]],
    window: tuple[float, float],
    vehicle_length: float,
    edge_length: float,
) -> float | None:
    """
    Encuentra un ``progress_on_edge`` en ``window`` que mantenga separación
    ≥ 2·max(length) respecto a cada ocupante. Devuelve None si no hay hueco.

    ``occupants`` = lista de ``(progress, length_m)`` para el carril dado.
    """
    w_min, w_max = window
    if w_max <= w_min:
        return None

    # Intervalos prohibidos alrededor de cada ocupante.
    forbidden: list[tuple[float, float]] = []
    for occ_progress, occ_len in occupants:
        sep = max(vehicle_length, occ_len) * 2.0 / edge_length
        forbidden.append((occ_progress - sep, occ_progress + sep))
    forbidden.sort()

    # Escanea huecos disponibles en la ventana tras restar los prohibidos.
    free_intervals: list[tuple[float, float]] = []
    cursor = w_min
    for lo, hi in forbidden:
        if hi < cursor:
            continue
        if lo > w_max:
            break
        if lo > cursor:
            free_intervals.append((cursor, min(lo, w_max)))
        cursor = max(cursor, hi)
        if cursor >= w_max:
            break
    if cursor < w_max:
        free_intervals.append((cursor, w_max))

    if not free_intervals:
        return None
    lo, hi = random.choice(free_intervals)
    return random.uniform(lo, hi)


# Pre-compute weight list to avoid re-summing on each spawn (called thousands of times).
_PROFILE_LIST: list[VehicleTypeProfile] = list(PROFILES.values())
_PROFILE_WEIGHTS: list[float] = [p.spawn_weight for p in _PROFILE_LIST]


def _pick_vehicle_profile() -> VehicleTypeProfile:
    """Elige un perfil de vehículo con distribución ponderada por spawn_weight."""
    return random.choices(_PROFILE_LIST, weights=_PROFILE_WEIGHTS, k=1)[0]

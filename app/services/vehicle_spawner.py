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
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_TYPE,
    ATTR_ROUNDABOUT_ID,
    DEFAULT_MAX_SPEED_MS,
    KMH_TO_MS,
    MIN_EDGE_LENGTH_M,
    ROUNDABOUT_SATURATION_VEH_PER_100M,
    SPAWN_INITIAL_PROGRESS_MAX,
    SPAWN_INITIAL_PROGRESS_MIN,
    SPAWN_INITIAL_VELOCITY_MAX,
    SPAWN_INITIAL_VELOCITY_MIN,
    SPAWN_MAX_ENTRIES_PER_ROUNDABOUT_PER_TICK,
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
        nodes = self._get_nodes_by_type(NodeType.ENTRY_POINT.value)
        if not nodes:
            nodes = self._get_nodes_by_type(NodeType.INTERSECTION.value)
        if not nodes:
            nodes = list(self._graph.graph.nodes())
        return nodes

    def get_exit_nodes(self) -> list[int]:
        nodes = self._get_nodes_by_type(NodeType.EXIT_POINT.value)
        if not nodes:
            nodes = self._get_nodes_by_type(NodeType.INTERSECTION.value)
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

        available_slots = self._max_vehicles - self.active_count
        actual_count = min(count, available_slots)

        if actual_count <= 0:
            return 0

        asyncio.create_task(
            self._spawn_task(entry_nodes, exit_nodes, actual_count, on_complete)
        )
        return actual_count

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

        Usa random.choice en lugar de shuffle+nested-loop para evitar
        el coste O(E×X) por vehículo cuando hay muchos nodos.
        Las rutas se almacenan en caché por par (start, end): con pocos
        nodos de entrada/salida, a partir del primer spawn todos los
        vehículos son cache-hits y el coste de cálculo es O(1).
        """
        spawned: list[SimVehicle] = []
        # Con nodos filtrados al SCC, la gran mayoría de pares tienen ruta
        # válida. El único motivo de fallo es start==end, que ocurre con
        # probabilidad 1/N. max_attempts = count * 2 es más que suficiente.
        max_attempts = count * 2

        # ── Coordinación de entrada en rotonda (Fase 5) ───────────────────────
        # Precalcular ocupación por rotonda (vehículos cuya arista actual
        # pertenece a un anillo) y contador de entradas en este batch para
        # limitar cuántos vehículos entran a cada rotonda por tick.
        occupancy_by_rid: dict[int, int] = {}
        for v in self._vehicles.values():
            if v.status == VehicleStatus.FINISHED:
                continue
            np_ = v.route.node_path
            ei = v.current_edge_index
            if ei >= len(np_) - 1:
                continue
            attrs = self._graph.get_edge_attributes(np_[ei], np_[ei + 1])
            if not attrs.get(ATTR_IS_ROUNDABOUT):
                continue
            rid = attrs.get(ATTR_ROUNDABOUT_ID)
            if rid is not None:
                occupancy_by_rid[int(rid)] = occupancy_by_rid.get(int(rid), 0) + 1
        entered_this_batch: dict[int, int] = {}

        for _ in range(max_attempts):
            if len(spawned) >= count:
                break

            start = random.choice(entry_nodes)
            end = random.choice(exit_nodes)
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

            # Rechazar si la primera rotonda de la ruta está saturada o ya
            # aceptó una entrada en este batch.
            first_rid = _find_first_roundabout_in_route(route, self._graph)
            if first_rid is not None:
                ring_len_m = self._graph.get_roundabout_length(first_rid)
                load = occupancy_by_rid.get(first_rid, 0) / max(ring_len_m / 100.0, 1.0)
                if load > ROUNDABOUT_SATURATION_VEH_PER_100M:
                    continue
                if (
                    entered_this_batch.get(first_rid, 0)
                    >= SPAWN_MAX_ENTRIES_PER_ROUNDABOUT_PER_TICK
                ):
                    continue
                entered_this_batch[first_rid] = (
                    entered_this_batch.get(first_rid, 0) + 1
                )

            node_attrs = self._graph.get_node_attributes(start)
            # Tipo de vehículo con probabilidad según spawn_weight del perfil
            profile = _pick_vehicle_profile()
            # Carril inicial: 0 (derecha) — el MOBIL decidirá si cambia
            first_node = route.node_path[0]
            second_node = route.node_path[1]
            first_edge_attrs = self._graph.get_edge_attributes(first_node, second_node)
            n_lanes = max(int(first_edge_attrs.get(ATTR_LANES, 1)), 1)
            # Arrancar en el carril derecho; MOBIL reubicará según convenga
            initial_lane = random.randint(0, n_lanes - 1)

            vehicle = SimVehicle(
                id=self._next_id(),
                start_node_id=start,
                end_node_id=end,
                route=route,
                longitude=node_attrs.get(ATTR_LONGITUDE, 0.0),
                latitude=node_attrs.get(ATTR_LATITUDE, 0.0),
                vtype=profile.vtype,
                lane=initial_lane,
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
            # Velocidad y posición inicial distribuidas para evitar aglomeración
            vehicle.velocity = vehicle.desired_speed_ms * random.uniform(
                SPAWN_INITIAL_VELOCITY_MIN, SPAWN_INITIAL_VELOCITY_MAX
            )
            vehicle.progress_on_edge = random.uniform(
                SPAWN_INITIAL_PROGRESS_MIN, SPAWN_INITIAL_PROGRESS_MAX
            )
            # Comportamiento en semáforo amarillo: 40% de vehículos lo se saltan
            vehicle.yellow_runs_light = random.random() > YELLOW_BRAKE_PROBABILITY
            # Escalonar la primera evaluación MOBIL para repartir carga entre ticks:
            # cada vehículo arranca con un cooldown aleatorio ∈ [0, interval-1].
            from app.core.constants import MOBIL_EVAL_INTERVAL_TICKS
            vehicle.mobil_cooldown_ticks = random.randint(
                0, MOBIL_EVAL_INTERVAL_TICKS - 1
            )

            # ── Spawn collision avoidance ──────────────────────────────────────
            # Verificar que la posición de spawn no choca con vehículos existentes
            # en la misma arista y mismo carril.
            first_edge_len = max(
                float(first_edge_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M
            )
            min_sep_progress = (vehicle.length_m * 2.0) / first_edge_len
            conflict = False
            all_candidates = list(self._vehicles.values()) + spawned
            for existing in all_candidates:
                if existing.status == VehicleStatus.FINISHED:
                    continue
                enp = existing.route.node_path
                eei = existing.current_edge_index
                if (
                    eei < len(enp) - 1
                    and enp[eei] == first_node
                    and enp[eei + 1] == second_node
                    and getattr(existing, "lane", 0) == vehicle.lane
                    and abs(existing.progress_on_edge - vehicle.progress_on_edge) < min_sep_progress
                ):
                    conflict = True
                    break
            if conflict:
                continue  # intentar con otro par entrada/salida

            spawned.append(vehicle)

        return spawned

    # -------------------------------------------------------------------------
    # Spawn síncrono (mantenido para tests y uso interno)
    # -------------------------------------------------------------------------

    def spawn(self, count: int = 1) -> list[SimVehicle]:
        """
        Genera hasta `count` vehículos sincrónicamente, respetando MAX_VEHICLES.

        Preferir spawn_background() para uso en producción.
        """
        entry_nodes = self.get_entry_nodes()
        exit_nodes = self.get_exit_nodes()

        if not entry_nodes:
            raise ValueError("El grafo no tiene nodos navegables para spawning")
        if not exit_nodes:
            raise ValueError("El grafo no tiene nodos navegables para destinos")

        entry_nodes, exit_nodes = self._filter_to_scc(entry_nodes, exit_nodes)

        available_slots = self._max_vehicles - self.active_count
        actual_count = min(count, available_slots)

        if actual_count <= 0:
            return []

        spawned = self._spawn_sync_batch(entry_nodes, exit_nodes, actual_count)
        for vehicle in spawned:
            self._vehicles[vehicle.id] = vehicle

        logger.info(
            "Spawned %d/%d vehículos (activos: %d/%d)",
            len(spawned),
            count,
            self.active_count,
            self._max_vehicles,
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


def _find_first_roundabout_in_route(
    route: RouteInfo, graph: "RoadNetworkGraph"  # type: ignore[name-defined]
) -> int | None:
    """
    Devuelve el ``roundabout_id`` de la primera rotonda que atraviesa la ruta,
    o ``None`` si la ruta no entra en ninguna. Se usa para coordinar spawns y
    evitar saturación concurrente en el mismo anillo.
    """
    np_ = route.node_path
    for i in range(len(np_) - 1):
        attrs = graph.get_edge_attributes(np_[i], np_[i + 1])
        if attrs.get(ATTR_IS_ROUNDABOUT):
            rid = attrs.get(ATTR_ROUNDABOUT_ID)
            if rid is not None:
                return int(rid)
    return None


# Pre-compute weight list to avoid re-summing on each spawn (called thousands of times).
_PROFILE_LIST: list[VehicleTypeProfile] = list(PROFILES.values())
_PROFILE_WEIGHTS: list[float] = [p.spawn_weight for p in _PROFILE_LIST]


def _pick_vehicle_profile() -> VehicleTypeProfile:
    """Elige un perfil de vehículo con distribución ponderada por spawn_weight."""
    return random.choices(_PROFILE_LIST, weights=_PROFILE_WEIGHTS, k=1)[0]

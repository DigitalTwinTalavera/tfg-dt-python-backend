"""
Spawner de vehículos y gestor de ciclo de vida.

Genera vehículos en nodos de entrada con rutas calculadas por NetworkX,
gestiona las transiciones de estado (IDLE → MOVING → FINISHED → eliminado)
y aplica el límite MAX_VEHICLES.
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
from app.core.constants import ATTR_LATITUDE, ATTR_LONGITUDE, ATTR_NODE_TYPE
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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
            "route_edges": self.route.edge_ids,
            "route_length_m": round(self.route.length_m, 1),
            "status": self.status.value,
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
                route = compute_route(self._graph, start, end)
                if route is not None:
                    self._route_cache[key] = route

            if route is None:
                continue

            node_attrs = self._graph.get_node_attributes(start)
            vehicle = SimVehicle(
                id=self._next_id(),
                start_node_id=start,
                end_node_id=end,
                route=route,
                longitude=node_attrs.get(ATTR_LONGITUDE, 0.0),
                latitude=node_attrs.get(ATTR_LATITUDE, 0.0),
            )
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


class VehicleLifecycleManager:
    """
    Gestiona las transiciones de estado de los vehículos:
      IDLE → MOVING  (primer tick)
      MOVING → FINISHED  (ruta completada)
      FINISHED → eliminado  (cleanup)
    """

    def __init__(self, spawner: VehicleSpawner) -> None:
        self._spawner = spawner

    def activate_idle_vehicles(self) -> int:
        """Cambia todos los vehículos IDLE a MOVING. Devuelve el nº cambiados."""
        count = 0
        for v in self._spawner.vehicles.values():
            if v.status == VehicleStatus.IDLE:
                v.status = VehicleStatus.MOVING
                count += 1
        return count

    def mark_finished(self, vehicle_id: str) -> bool:
        """Marca un vehículo como FINISHED."""
        v = self._spawner.get_vehicle(vehicle_id)
        if v is None:
            return False
        v.status = VehicleStatus.FINISHED
        return True

    def cleanup_finished(self) -> int:
        """Elimina todos los vehículos FINISHED. Devuelve el nº eliminados."""
        finished_ids = [
            vid
            for vid, v in self._spawner.vehicles.items()
            if v.status == VehicleStatus.FINISHED
        ]
        for vid in finished_ids:
            self._spawner.remove_vehicle(vid)
        return len(finished_ids)

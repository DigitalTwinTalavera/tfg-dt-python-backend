"""
Spawner de vehículos y gestor de ciclo de vida.

Genera vehículos en nodos de entrada con rutas calculadas por NetworkX,
gestiona las transiciones de estado (IDLE → MOVING → FINISHED → eliminado)
y aplica el límite MAX_VEHICLES.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from uuid import UUID, uuid4

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
    y ruta calculada por shortest path (peso = tiempo de viaje).
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
        self._counter += 1
        return f"v_{self._counter:03d}"

    def _get_nodes_by_type(self, node_type: str) -> list[int]:
        """Devuelve los IDs de nodos con un tipo dado."""
        return [
            nid
            for nid, attrs in self._graph.graph.nodes(data=True)
            if attrs.get(ATTR_NODE_TYPE) == node_type
        ]

    def get_entry_nodes(self) -> list[int]:
        return self._get_nodes_by_type(NodeType.ENTRY_POINT.value)

    def get_exit_nodes(self) -> list[int]:
        return self._get_nodes_by_type(NodeType.EXIT_POINT.value)

    def spawn(self, count: int = 1) -> list[SimVehicle]:
        """
        Genera hasta `count` vehículos, respetando MAX_VEHICLES.

        Cada vehículo se coloca en un entry_point aleatorio con destino
        a un exit_point aleatorio, calculando la ruta más corta.

        Returns:
            Lista de vehículos creados.

        Raises:
            ValueError: Si no hay nodos de entrada/salida disponibles.
        """
        entry_nodes = self.get_entry_nodes()
        exit_nodes = self.get_exit_nodes()

        if not entry_nodes:
            raise ValueError("No hay nodos de entrada (entry_point) en el grafo")
        if not exit_nodes:
            raise ValueError("No hay nodos de salida (exit_point) en el grafo")

        available_slots = self._max_vehicles - self.active_count
        actual_count = min(count, available_slots)

        if actual_count <= 0:
            return []

        spawned: list[SimVehicle] = []

        for _ in range(actual_count):
            vehicle = self._try_spawn_one(entry_nodes, exit_nodes)
            if vehicle is not None:
                spawned.append(vehicle)

        logger.info(
            "Spawned %d/%d vehículos (activos: %d/%d)",
            len(spawned),
            count,
            self.active_count,
            self._max_vehicles,
        )
        return spawned

    def _try_spawn_one(
        self,
        entry_nodes: list[int],
        exit_nodes: list[int],
    ) -> SimVehicle | None:
        """Intenta crear un vehículo con ruta válida."""
        random.shuffle(entry_nodes)
        random.shuffle(exit_nodes)

        for start in entry_nodes:
            for end in exit_nodes:
                if start == end:
                    continue
                route = compute_route(self._graph, start, end)
                if route is not None:
                    node_attrs = self._graph.get_node_attributes(start)
                    vehicle = SimVehicle(
                        id=self._next_id(),
                        start_node_id=start,
                        end_node_id=end,
                        route=route,
                        longitude=node_attrs.get(ATTR_LONGITUDE, 0.0),
                        latitude=node_attrs.get(ATTR_LATITUDE, 0.0),
                    )
                    self._vehicles[vehicle.id] = vehicle
                    return vehicle
        return None

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

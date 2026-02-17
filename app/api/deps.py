"""
Dependencias de inyección para los endpoints de la API.
"""

from app.core.simulation_engine import SimulationEngine, simulation_engine
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import VehicleLifecycleManager, VehicleSpawner

# Singletons a nivel de aplicación
_graph = RoadNetworkGraph()
_spawner = VehicleSpawner(graph=_graph)
_lifecycle = VehicleLifecycleManager(spawner=_spawner)


def get_simulation_engine() -> SimulationEngine:
    """Devuelve la instancia singleton del motor de simulación."""
    return simulation_engine


def get_road_network_graph() -> RoadNetworkGraph:
    """Devuelve la instancia singleton del grafo de red vial."""
    return _graph


def get_vehicle_spawner() -> VehicleSpawner:
    """Devuelve la instancia singleton del spawner de vehículos."""
    return _spawner


def get_vehicle_lifecycle_manager() -> VehicleLifecycleManager:
    """Devuelve la instancia singleton del gestor de ciclo de vida."""
    return _lifecycle

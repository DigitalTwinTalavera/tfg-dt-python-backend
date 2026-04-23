"""
Dependencias de inyección para los endpoints de la API.
"""

from app.api.websocket.manager import connection_manager
from app.core.broadcaster import SimulationBroadcaster
from app.core.simulation_config import SimulationConfig
from app.core.simulation_engine import SimulationEngine, simulation_engine
from app.services.incident_manager import IncidentManager
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import VehicleSpawner
from app.services.zone_manager import ZoneManager

# Singletons a nivel de aplicación
_config = SimulationConfig()
_graph = RoadNetworkGraph()
_spawner = VehicleSpawner(graph=_graph)
_broadcaster = SimulationBroadcaster(
    connection_manager=connection_manager,
    vehicle_spawner=_spawner,
)
_incident_manager = IncidentManager(
    spawner=_spawner, broadcaster=_broadcaster, graph=_graph
)
_zone_manager = ZoneManager(graph=_graph, broadcaster=_broadcaster)
# El spawner consulta el zone_manager en el hot path de spawn/reroute.
_spawner.zone_manager = _zone_manager

# Inyectar dependencias en el engine
simulation_engine.set_config(_config)
simulation_engine.set_spawner(_spawner)
simulation_engine.set_broadcaster(_broadcaster)
simulation_engine.set_incident_manager(_incident_manager)
simulation_engine.set_zone_manager(_zone_manager)


def get_simulation_engine() -> SimulationEngine:
    """Devuelve la instancia singleton del motor de simulación."""
    return simulation_engine


def get_simulation_config() -> SimulationConfig:
    """Devuelve la configuración activa del motor de simulación."""
    config = simulation_engine.get_config()
    if config is None:
        return _config
    return config


def get_road_network_graph() -> RoadNetworkGraph:
    """Devuelve la instancia singleton del grafo de red vial."""
    return _graph


def get_vehicle_spawner() -> VehicleSpawner:
    """Devuelve la instancia singleton del spawner de vehículos."""
    return _spawner


def get_broadcaster() -> SimulationBroadcaster:
    """Devuelve la instancia singleton del broadcaster."""
    return _broadcaster


def get_incident_manager() -> IncidentManager:
    """Devuelve la instancia singleton del gestor de incidentes."""
    return _incident_manager


def get_zone_manager() -> ZoneManager:
    """Devuelve la instancia singleton del gestor de zonas."""
    return _zone_manager

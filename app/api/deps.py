"""
Dependencias de inyección para los endpoints de la API.
"""

from app.core.simulation_engine import SimulationEngine, simulation_engine


def get_simulation_engine() -> SimulationEngine:
    """Devuelve la instancia singleton del motor de simulación."""
    return simulation_engine

"""
Modelos de física vehicular para la simulación de tráfico.

- IDM (Intelligent Driver Model): comportamiento longitudinal
- MOBIL: decisiones de cambio de carril
- VehiclePhysics: integración completa con geometría de aristas
"""

from app.core.physics.idm import IDMModel
from app.core.physics.mobil import LaneChangeDecision, MOBILModel
from app.core.physics.parameters import IDMParameters, MOBILParameters
from app.core.physics.vehicle_physics import VehiclePhysics, VehicleStateUpdate

__all__ = [
    "IDMModel",
    "IDMParameters",
    "LaneChangeDecision",
    "MOBILModel",
    "MOBILParameters",
    "VehiclePhysics",
    "VehicleStateUpdate",
]

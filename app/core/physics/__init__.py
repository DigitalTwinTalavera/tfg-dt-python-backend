"""
Modelos de física vehicular para la simulación de tráfico.

- IDM (Intelligent Driver Model): comportamiento longitudinal
- MOBIL: decisiones de cambio de carril
"""

from app.core.physics.idm import IDMModel
from app.core.physics.mobil import LaneChangeDecision, MOBILModel
from app.core.physics.parameters import IDMParameters, MOBILParameters
from app.core.physics.vehicle_types import (
    CAR_PROFILE,
    MOTO_PROFILE,
    PROFILES,
    TRUCK_PROFILE,
    VehicleType,
    VehicleTypeProfile,
    get_profile,
)

__all__ = [
    "IDMModel",
    "IDMParameters",
    "LaneChangeDecision",
    "MOBILModel",
    "MOBILParameters",
    "VehicleType",
    "VehicleTypeProfile",
    "CAR_PROFILE",
    "MOTO_PROFILE",
    "TRUCK_PROFILE",
    "PROFILES",
    "get_profile",
]

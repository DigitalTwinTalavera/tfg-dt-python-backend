"""
Tipos de vehículo con parámetros físicos y de IDM propios.

Permite simular una flota heterogénea: coches, motos y camiones con distintas
aceleraciones, dimensiones y velocidades deseadas. El tipo se decide al
spawnear y se transmite al cliente para que pueda dibujar la malla adecuada.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.physics.parameters import IDMParameters


class VehicleType(str, Enum):
    CAR = "car"
    MOTORCYCLE = "moto"
    TRUCK = "truck"


@dataclass(frozen=True)
class VehicleTypeProfile:
    """Perfil físico + de comportamiento de un tipo de vehículo."""

    vtype: VehicleType
    length_m: float
    width_m: float
    max_speed_ms: float   # techo absoluto; el deseado puede ser menor
    idm: IDMParameters
    # Probabilidad de spawnear este tipo (se normaliza al sumarse).
    spawn_weight: float
    # Aceleración lateral máxima tolerada en curvas (m/s²). Menor para camiones.
    lateral_accel_max_ms2: float = 2.5
    # Radio mínimo de giro (m). Usado para descartar rotondas demasiado cerradas
    # en el cálculo de ruta para camiones.
    min_turn_radius_m: float = 5.0


# ---------------------------------------------------------------------------
# Perfiles por defecto
# ---------------------------------------------------------------------------

CAR_PROFILE = VehicleTypeProfile(
    vtype=VehicleType.CAR,
    length_m=4.5,
    width_m=1.9,
    max_speed_ms=36.11,   # 130 km/h
    idm=IDMParameters(
        v0=13.89,  # 50 km/h
        s0=2.0,
        T=1.4,
        a=1.4,
        b=2.5,
        delta=4.0,
    ),
    spawn_weight=0.75,
    lateral_accel_max_ms2=2.5,
    min_turn_radius_m=5.0,
)

MOTO_PROFILE = VehicleTypeProfile(
    vtype=VehicleType.MOTORCYCLE,
    length_m=2.1,
    width_m=0.8,
    max_speed_ms=44.44,   # 160 km/h
    idm=IDMParameters(
        v0=15.28,  # 55 km/h — las motos tienden a superar el límite
        s0=1.0,
        T=1.0,
        a=2.0,
        b=3.5,
        delta=4.0,
    ),
    spawn_weight=0.15,
    lateral_accel_max_ms2=3.0,
    min_turn_radius_m=3.0,
)

TRUCK_PROFILE = VehicleTypeProfile(
    vtype=VehicleType.TRUCK,
    length_m=10.0,
    width_m=2.5,
    max_speed_ms=25.0,    # 90 km/h
    idm=IDMParameters(
        v0=11.11,  # 40 km/h — los camiones circulan más lentos en ciudad
        s0=3.0,
        T=2.4,
        a=0.7,
        b=2.0,
        delta=4.0,
    ),
    spawn_weight=0.10,
    lateral_accel_max_ms2=1.8,
    min_turn_radius_m=10.0,
)


PROFILES: dict[VehicleType, VehicleTypeProfile] = {
    CAR_PROFILE.vtype: CAR_PROFILE,
    MOTO_PROFILE.vtype: MOTO_PROFILE,
    TRUCK_PROFILE.vtype: TRUCK_PROFILE,
}


def get_profile(vtype: VehicleType) -> VehicleTypeProfile:
    """Perfil del tipo solicitado; CAR como fallback."""
    return PROFILES.get(vtype, CAR_PROFILE)

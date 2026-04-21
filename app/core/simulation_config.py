"""
Configuración centralizada de la simulación.

SimulationConfig es un modelo Pydantic que agrupa todos los parámetros
configurables y puede actualizarse en caliente mientras la simulación corre.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from app.core.physics.parameters import IDMParameters, MOBILParameters


# =============================================================================
# Sub-modelos Pydantic para física vehicular
# =============================================================================


class IDMConfig(BaseModel):
    """
    Parámetros del Intelligent Driver Model (IDM) como modelo Pydantic.

    Attrs:
        v0: Velocidad deseada en espacio libre (m/s). Por defecto ~50 km/h.
        s0: Distancia mínima de separación en reposo (m).
        T:  Tiempo de seguimiento seguro (s).
        a:  Aceleración máxima (m/s²).
        b:  Deceleración confortable (m/s²).
        delta: Exponente de aceleración (sin dimensión).
    """

    v0: float = Field(default=13.89, gt=0, description="Velocidad deseada (m/s)")
    s0: float = Field(default=2.0, gt=0, description="Separación mínima (m)")
    T: float = Field(default=1.5, gt=0, description="Tiempo de seguimiento seguro (s)")
    a: float = Field(default=1.0, gt=0, description="Aceleración máxima (m/s²)")
    b: float = Field(default=1.5, gt=0, description="Deceleración confortable (m/s²)")
    delta: float = Field(default=4.0, gt=0, description="Exponente de aceleración")

    def to_parameters(self) -> IDMParameters:
        """Convierte a IDMParameters (frozen dataclass) para los modelos de física."""
        return IDMParameters(
            v0=self.v0,
            s0=self.s0,
            T=self.T,
            a=self.a,
            b=self.b,
            delta=self.delta,
        )


class MOBILConfig(BaseModel):
    """
    Parámetros del modelo MOBIL para cambio de carril como modelo Pydantic.

    Attrs:
        politeness:    Factor de cortesía (0=agresivo, 1=altruista).
        b_safe:        Deceleración máxima segura para el nuevo seguidor (m/s²).
        a_threshold:   Umbral mínimo de ganancia para cambiar de carril (m/s²).
    """

    politeness: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Factor de cortesía [0, 1]"
    )
    b_safe: float = Field(default=4.0, gt=0, description="Deceleración segura (m/s²)")
    a_threshold: float = Field(
        default=0.2, ge=0.0, description="Umbral de ganancia para cambio de carril (m/s²)"
    )

    def to_parameters(self) -> MOBILParameters:
        """Convierte a MOBILParameters (frozen dataclass) para los modelos de física."""
        return MOBILParameters(
            politeness=self.politeness,
            b_safe=self.b_safe,
            a_threshold=self.a_threshold,
        )


# =============================================================================
# Configuración principal de simulación
# =============================================================================


class SimulationConfig(BaseModel):
    """
    Configuración centralizada de la simulación.

    Todos los parámetros pueden actualizarse en caliente via PUT /simulation/config
    sin necesidad de reiniciar la simulación.
    """

    tick_rate: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Ticks por segundo (1–100)",
    )
    auto_spawn: bool = Field(
        default=True,
        description="Activar generación automática de vehículos en cada tick",
    )
    spawn_rate: int = Field(
        default=10,
        ge=0,
        le=600,
        description="Vehículos generados por minuto (0 = desactivado)",
    )
    max_vehicles: int = Field(
        default=1_000_000_000,
        ge=1,
        description="Número máximo de vehículos activos simultáneamente (default ilimitado)",
    )
    idm: IDMConfig = Field(
        default_factory=IDMConfig,
        description="Parámetros del modelo IDM",
    )
    mobil: MOBILConfig = Field(
        default_factory=MOBILConfig,
        description="Parámetros del modelo MOBIL",
    )

    @property
    def tick_interval_ms(self) -> float:
        """Intervalo entre ticks en milisegundos, derivado de tick_rate."""
        return 1000.0 / self.tick_rate

    @property
    def ticks_between_spawns(self) -> int:
        """
        Número de ticks entre cada spawn automático.

        Calculado como: (tick_rate * 60) / spawn_rate
        Ejemplo: tick_rate=10, spawn_rate=10 => 600/10 = 60 ticks (cada 6s)

        Returns:
            Ticks entre spawns, mínimo 1. Si spawn_rate == 0, devuelve maxsize.
        """
        if self.spawn_rate <= 0:
            return int(1e9)
        return max(1, math.ceil((self.tick_rate * 60) / self.spawn_rate))

    def to_idm_parameters(self) -> IDMParameters:
        """Convierte la config IDM al dataclass de física."""
        return self.idm.to_parameters()

    def to_mobil_parameters(self) -> MOBILParameters:
        """Convierte la config MOBIL al dataclass de física."""
        return self.mobil.to_parameters()

    def to_dict(self) -> dict:
        """Serialización completa para respuestas API."""
        return {
            "tick_rate": self.tick_rate,
            "tick_interval_ms": round(self.tick_interval_ms, 3),
            "auto_spawn": self.auto_spawn,
            "spawn_rate": self.spawn_rate,
            "ticks_between_spawns": self.ticks_between_spawns,
            "max_vehicles": self.max_vehicles,
            "idm": self.idm.model_dump(),
            "mobil": self.mobil.model_dump(),
        }

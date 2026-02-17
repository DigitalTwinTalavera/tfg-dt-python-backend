"""
Parámetros configurables para los modelos de física vehicular.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IDMParameters:
    """
    Parámetros del Intelligent Driver Model (IDM).

    Attrs:
        v0: Velocidad deseada en espacio libre (m/s). Por defecto ~50 km/h.
        s0: Distancia mínima de separación en reposo (m).
        T: Tiempo de seguimiento seguro (s).
        a: Aceleración máxima (m/s²).
        b: Deceleración confortable (m/s²).
        delta: Exponente de aceleración (sin dimensión).
    """

    v0: float = 13.89   # ~50 km/h
    s0: float = 2.0     # metros
    T: float = 1.5      # segundos
    a: float = 1.0      # m/s²
    b: float = 1.5      # m/s²
    delta: float = 4.0  # exponente


@dataclass(frozen=True)
class MOBILParameters:
    """
    Parámetros del modelo MOBIL para cambio de carril.

    Attrs:
        politeness: Factor de cortesía (0=agresivo, 1=altruista).
        b_safe: Deceleración máxima segura para el nuevo seguidor (m/s²).
        a_threshold: Umbral mínimo de ganancia para cambiar de carril (m/s²).
    """

    politeness: float = 0.5
    b_safe: float = 4.0    # m/s²
    a_threshold: float = 0.2  # m/s²

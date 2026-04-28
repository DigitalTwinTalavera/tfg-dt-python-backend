"""
Datatipos compartidos entre los módulos de física vehicular.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NeighborInfo:
    """
    Información sobre el vehículo líder (o semáforo virtual) que precede a un ego.

    Attrs:
        gap_m:       Distancia bumper-to-bumper en metros (≥ 0.01 m).
        velocity_ms: Velocidad del líder en m/s (0.0 para semáforo en rojo).
        leader_id:   ID del vehículo líder real, o None si es un líder virtual (semáforo).
    """

    gap_m: float
    velocity_ms: float
    leader_id: str | None = None

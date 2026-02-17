"""
Integración de física vehicular: IDM + geometría de aristas.

Actualiza la posición del vehículo a lo largo de la LineString de la arista,
calcula heading desde la tangente y gestiona transiciones entre aristas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point

from app.core.physics.idm import IDMModel
from app.core.physics.parameters import IDMParameters


@dataclass
class VehicleStateUpdate:
    """Resultado de un paso de física para un vehículo."""

    longitude: float
    latitude: float
    velocity: float
    acceleration: float
    heading: float
    current_edge_index: int
    progress_on_edge: float  # 0.0 a 1.0
    route_completed: bool


@dataclass
class NeighborInfo:
    """Información de un vehículo vecino (líder)."""

    distance: float  # metros hasta el ego
    velocity: float  # m/s


class VehiclePhysics:
    """
    Motor de física vehicular.

    Integra IDM para aceleración longitudinal con interpolación
    sobre geometrías LineString de las aristas del grafo.
    """

    def __init__(self, idm_params: IDMParameters | None = None) -> None:
        self.idm = IDMModel(params=idm_params or IDMParameters())

    def update(
        self,
        velocity: float,
        current_edge_index: int,
        progress_on_edge: float,
        edge_geometries: list[LineString],
        edge_speed_limits: list[float],
        dt: float,
        leader: NeighborInfo | None = None,
    ) -> VehicleStateUpdate:
        """
        Ejecuta un paso de simulación para un vehículo.

        Args:
            velocity: Velocidad actual (m/s).
            current_edge_index: Índice de la arista actual en la ruta.
            progress_on_edge: Progreso en la arista actual [0.0, 1.0].
            edge_geometries: Lista de LineStrings para cada arista de la ruta.
            edge_speed_limits: Límite de velocidad de cada arista (m/s).
            dt: Delta time (s).
            leader: Info del vehículo líder, None si carretera libre.

        Returns:
            VehicleStateUpdate con la nueva posición y estado.
        """
        if not edge_geometries:
            return VehicleStateUpdate(
                longitude=0.0,
                latitude=0.0,
                velocity=0.0,
                acceleration=0.0,
                heading=0.0,
                current_edge_index=current_edge_index,
                progress_on_edge=progress_on_edge,
                route_completed=True,
            )

        # Clamp edge index
        edge_idx = min(current_edge_index, len(edge_geometries) - 1)
        current_geom = edge_geometries[edge_idx]
        edge_length = current_geom.length

        # Velocidad deseada = límite de velocidad de la arista actual
        v0 = edge_speed_limits[edge_idx] if edge_idx < len(edge_speed_limits) else self.idm.params.v0

        # Calcular aceleración IDM
        if leader is not None:
            accel = self.idm.calculate_acceleration(
                v=velocity,
                v0=v0,
                s=leader.distance,
                v_lead=leader.velocity,
            )
        else:
            accel = self.idm.calculate_acceleration(v=velocity, v0=v0)

        # Integrar velocidad y posición
        new_velocity = max(0.0, velocity + accel * dt)
        distance_traveled = max(0.0, velocity * dt + 0.5 * accel * dt * dt)

        # Avanzar sobre la geometría
        edge_idx, progress, route_completed = self._advance_on_route(
            edge_idx=edge_idx,
            progress=progress_on_edge,
            distance=distance_traveled,
            edge_geometries=edge_geometries,
        )

        # Interpolar posición y heading
        current_geom = edge_geometries[min(edge_idx, len(edge_geometries) - 1)]
        point = self._interpolate_point(current_geom, progress)
        heading = self._calculate_heading(current_geom, progress)

        return VehicleStateUpdate(
            longitude=point.x,
            latitude=point.y,
            velocity=new_velocity,
            acceleration=accel,
            heading=heading,
            current_edge_index=edge_idx,
            progress_on_edge=progress,
            route_completed=route_completed,
        )

    def _advance_on_route(
        self,
        edge_idx: int,
        progress: float,
        distance: float,
        edge_geometries: list[LineString],
    ) -> tuple[int, float, bool]:
        """
        Avanza la distancia dada sobre la ruta, transitando entre aristas.

        Returns:
            (nuevo_edge_idx, nuevo_progreso, ruta_completada)
        """
        remaining = distance

        while remaining > 0 and edge_idx < len(edge_geometries):
            geom = edge_geometries[edge_idx]
            edge_length = geom.length

            if edge_length <= 0:
                edge_idx += 1
                progress = 0.0
                continue

            distance_on_edge = progress * edge_length
            distance_to_end = edge_length - distance_on_edge

            if remaining < distance_to_end:
                progress = (distance_on_edge + remaining) / edge_length
                remaining = 0.0
            else:
                remaining -= distance_to_end
                edge_idx += 1
                progress = 0.0

        # Ruta completada
        if edge_idx >= len(edge_geometries):
            edge_idx = len(edge_geometries) - 1
            progress = 1.0
            return edge_idx, progress, True

        return edge_idx, progress, False

    @staticmethod
    def _interpolate_point(geom: LineString, progress: float) -> Point:
        """Interpola un punto a lo largo de la LineString."""
        clamped = max(0.0, min(1.0, progress))
        return geom.interpolate(clamped, normalized=True)

    @staticmethod
    def _calculate_heading(geom: LineString, progress: float) -> float:
        """
        Calcula el heading (grados, 0=Norte, 90=Este) desde la tangente
        de la geometría en el punto dado.
        """
        length = geom.length
        if length <= 0:
            return 0.0

        # Puntos para tangente (pequeño delta)
        delta = 0.001
        p0 = max(0.0, min(1.0, progress - delta))
        p1 = max(0.0, min(1.0, progress + delta))

        if p0 == p1:
            p1 = min(1.0, p0 + delta)

        pt0 = geom.interpolate(p0, normalized=True)
        pt1 = geom.interpolate(p1, normalized=True)

        dx = pt1.x - pt0.x
        dy = pt1.y - pt0.y

        if dx == 0 and dy == 0:
            return 0.0

        # atan2 da ángulo desde eje X; convertir a heading (0=Norte, CW)
        angle_rad = math.atan2(dx, dy)
        heading = math.degrees(angle_rad) % 360.0

        return round(heading, 2)

"""
Motor de analíticas de tráfico.

Calcula las *métricas clave de dominio* del tráfico simulado —distintas de la
instrumentación de rendimiento de :mod:`app.core.instrumentation`, que mide el
coste del propio motor (latencia de tick, serialización, etc.)—:

  - **Tiempo medio de viaje**: tiempo de simulación que tarda cada vehículo en
    completar su ruta, junto con su velocidad media de trayecto. Se registra
    por evento, al finalizar cada vehículo.
  - **Nivel de congestión**: para cada arista con tráfico, el cociente entre su
    ocupación (vehículos circulando por ella) y su capacidad estimada
    (longitud · carriles / hueco medio por vehículo). Se agrega en media,
    máximo y número de aristas saturadas.
  - **Impacto de incidentes**: número de vehículos activos cuya ruta restante
    atraviesa una arista bloqueada por un incidente.

Los escalares agregados se publican además como *gauges* en el
``MetricsRegistry``, de modo que afloran tanto en ``/api/simulation/metrics``
(JSON, vía :meth:`snapshot`) como en ``/metrics`` (Prometheus).

El cálculo de emisiones de CO_2 queda fuera de este módulo: requiere un modelo
de factores de emisión (p. ej. COPERT) y un censo del parque vehicular que no
son objeto del presente trabajo (véase el capítulo de trabajo futuro).
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from app.core.constants import (
    ANALYTICS_TRIP_BUFFER_SIZE,
    ATTR_LANES,
    ATTR_LENGTH,
    CONGESTION_RATIO_THRESHOLD,
    CONGESTION_VEHICLE_SLOT_M,
    MIN_EDGE_LENGTH_M,
)
from app.core.instrumentation import registry
from app.models.enums import VehicleStatus

if TYPE_CHECKING:
    from app.services.network_graph import RoadNetworkGraph
    from app.services.vehicle_spawner import SimVehicle


def _current_edge(vehicle: "SimVehicle") -> tuple[int, int] | None:
    """Arista (u, v) por la que circula el vehículo, o None si ya no aplica."""
    node_path = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(node_path) - 1:
        return None
    return (node_path[ei], node_path[ei + 1])


class TrafficAnalytics:
    """
    Acumulador de métricas clave de tráfico.

    Mantiene un ring buffer de tiempos de viaje (eventos puntuales) y el último
    cálculo agregado de congestión e impacto de incidentes (muestreado cada
    ``ANALYTICS_INTERVAL_TICKS`` por el motor). No es thread-safe por sí mismo:
    todas sus llamadas se hacen desde el bucle de tick en el hilo principal del
    event loop, fuera del cómputo paralelo de física.
    """

    def __init__(self, buffer_size: int = ANALYTICS_TRIP_BUFFER_SIZE) -> None:
        self._travel_times_s: deque[float] = deque(maxlen=buffer_size)
        self._trip_speeds_kmh: deque[float] = deque(maxlen=buffer_size)
        self._trips_completed: int = 0
        # Último agregado de congestión / impacto (para el snapshot JSON).
        self._congestion: dict[str, float] = {}
        self._incidents: dict[str, float] = {}

    # ---- Tiempo de viaje (por evento, al finalizar un vehículo) ----
    def record_trip(self, travel_time_s: float, route_length_m: float) -> None:
        """Registra un viaje completado: su duración y velocidad media."""
        if travel_time_s <= 0.0:
            return
        self._travel_times_s.append(travel_time_s)
        speed_kmh = (route_length_m / travel_time_s) * 3.6
        self._trip_speeds_kmh.append(speed_kmh)
        self._trips_completed += 1
        registry.inc("traffic.trips_completed")

    # ---- Congestión + impacto de incidentes (muestreado cada N ticks) ----
    def sample(
        self,
        vehicles: "dict[str, SimVehicle]",
        graph: "RoadNetworkGraph",
        blocked_edges: "dict[tuple[int, int], object | None]",
    ) -> None:
        """
        Recalcula los agregados de congestión e impacto de incidentes a partir
        del estado vivo. Una sola pasada O(N) sobre los vehículos activos.
        """
        occupancy: dict[tuple[int, int], int] = {}
        affected_by_incident = 0
        has_blocked = bool(blocked_edges)

        for v in vehicles.values():
            if v.status == VehicleStatus.FINISHED:
                continue
            edge = _current_edge(v)
            if edge is not None:
                occupancy[edge] = occupancy.get(edge, 0) + 1
            # Impacto de incidentes: ¿la ruta restante pisa una arista bloqueada?
            if has_blocked and self._route_hits_blocked(v, blocked_edges):
                affected_by_incident += 1

        # Congestión: ratio ocupación/capacidad por arista ocupada.
        ratios: list[float] = []
        for edge, count in occupancy.items():
            attrs = graph.get_edge_attributes(*edge)
            length = max(float(attrs.get(ATTR_LENGTH, 0.0)), MIN_EDGE_LENGTH_M)
            lanes = max(int(attrs.get(ATTR_LANES, 1)), 1)
            capacity = max(length * lanes / CONGESTION_VEHICLE_SLOT_M, 1.0)
            ratios.append(count / capacity)

        if ratios:
            mean_ratio = sum(ratios) / len(ratios)
            max_ratio = max(ratios)
            congested = sum(1 for r in ratios if r >= CONGESTION_RATIO_THRESHOLD)
        else:
            mean_ratio = max_ratio = 0.0
            congested = 0

        self._congestion = {
            "occupied_edges": len(occupancy),
            "congestion_mean": round(mean_ratio, 4),
            "congestion_max": round(max_ratio, 4),
            "congested_edges": congested,
        }
        self._incidents = {
            "blocked_edges": len(blocked_edges),
            "vehicles_affected": affected_by_incident,
        }

        # Reflejar los escalares en el registry (para Prometheus).
        registry.gauge("traffic.congestion_mean", mean_ratio)
        registry.gauge("traffic.congestion_max", max_ratio)
        registry.gauge("traffic.congested_edges", float(congested))
        registry.gauge("traffic.vehicles_affected", float(affected_by_incident))
        mean_tt = self._mean(self._travel_times_s)
        registry.gauge("traffic.travel_time_mean_s", mean_tt)

    @staticmethod
    def _route_hits_blocked(
        vehicle: "SimVehicle",
        blocked_edges: "dict[tuple[int, int], object | None]",
    ) -> bool:
        node_path = vehicle.route.node_path
        ei = vehicle.current_edge_index
        for i in range(ei, len(node_path) - 1):
            if (node_path[i], node_path[i + 1]) in blocked_edges:
                return True
        return False

    @staticmethod
    def _mean(values: "deque[float]") -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        k = (len(s) - 1) * q
        f = int(k)
        if f + 1 >= len(s):
            return s[-1]
        return s[f] + (s[f + 1] - s[f]) * (k - f)

    # ---- Snapshot para /api/simulation/metrics ----
    def snapshot(self) -> dict:
        """Bloque `traffic` con las métricas clave ya agregadas."""
        tt = list(self._travel_times_s)
        sp = list(self._trip_speeds_kmh)
        return {
            "travel_time_s": {
                "trips_completed": self._trips_completed,
                "mean": round(self._mean(self._travel_times_s), 2),
                "p50": round(self._percentile(tt, 0.50), 2),
                "p95": round(self._percentile(tt, 0.95), 2),
                "max": round(max(tt), 2) if tt else 0.0,
            },
            "trip_speed_kmh": {
                "mean": round(self._mean(self._trip_speeds_kmh), 2),
                "p50": round(self._percentile(sp, 0.50), 2),
            },
            "congestion": dict(self._congestion),
            "incidents": dict(self._incidents),
        }

    def reset(self) -> None:
        """Limpia los acumuladores (al reiniciar la simulación)."""
        self._travel_times_s.clear()
        self._trip_speeds_kmh.clear()
        self._trips_completed = 0
        self._congestion = {}
        self._incidents = {}


# Instancia singleton a nivel de aplicación, en paralelo a `registry`.
traffic_analytics = TrafficAnalytics()

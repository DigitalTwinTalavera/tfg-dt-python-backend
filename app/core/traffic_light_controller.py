"""
Controlador de semáforos con ciclos de tiempo fijo y fase por dirección.

Cada nodo TRAFFIC_LIGHT agrupa sus aristas entrantes en dos grupos según el
**eje de aproximación** (p. ej. N-S vs E-O). Cada grupo recibe un offset de
fase: cuando un eje está en verde el otro está en rojo, rotando cada ciclo.

Ciclo por grupo: GREEN (30s) → YELLOW (5s) → RED (35s) → GREEN …
Offset del grupo 1: (TL_GREEN + TL_YELLOW) segundos respecto al grupo 0.

El controlador es stateful y debe avanzarse con tick(dt) en cada tick.
Es completamente síncrono: no hace I/O.
"""

from __future__ import annotations

import math
import random

from app.core.constants import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_NODE_TYPE,
    TL_GREEN_SECONDS,
    TL_PHASE_GREEN,
    TL_PHASE_RED,
    TL_PHASE_YELLOW,
    TL_RED_SECONDS,
    TL_YELLOW_SECONDS,
)
from app.models.enums import NodeType
from app.services.network_graph import RoadNetworkGraph

_CYCLE: float = TL_GREEN_SECONDS + TL_YELLOW_SECONDS + TL_RED_SECONDS
_GROUP_OFFSET_S: float = TL_GREEN_SECONDS + TL_YELLOW_SECONDS  # cuando A acaba amarillo, B empieza verde


def _bearing(u_lon: float, u_lat: float, v_lon: float, v_lat: float) -> float:
    """Ángulo (rad) del vector u→v en el plano lon/lat. Rango (-π, π]."""
    return math.atan2(v_lat - u_lat, v_lon - u_lon)


def _axis_group(anchor: float, bearing: float) -> int:
    """Devuelve 0 si `bearing` se alinea con el eje de `anchor` (±45°), 1 si es perpendicular.

    Dos bearings opuestos (diferencia ~π) se consideran el mismo eje (grupo 0).
    """
    # Diferencia angular mínima en [0, π]
    diff = abs((bearing - anchor + math.pi) % (2.0 * math.pi) - math.pi)
    # Cerca de 0 o π → mismo eje
    if diff < math.pi / 4.0 or diff > 3.0 * math.pi / 4.0:
        return 0
    return 1


class TrafficLightController:
    """
    Gestor de ciclos de semáforos con fase por dirección de aproximación.

    Para cada nodo TRAFFIC_LIGHT:
      * `_lights[nid]`: timer del ciclo (0 ≤ t < _CYCLE), con offset aleatorio inicial.
      * `_edge_groups[nid]`: mapa arista-entrante → índice de grupo (0 o 1).
      * Fase vista por el vehículo = timer del nodo + offset del grupo, módulo ciclo.
    """

    def __init__(self, graph: RoadNetworkGraph) -> None:
        self._lights: dict[int, float] = {}
        self._edge_groups: dict[int, dict[tuple[int, int], int]] = {}

        nx_graph = graph.graph
        for nid, attrs in nx_graph.nodes(data=True):
            if attrs.get(ATTR_NODE_TYPE) != NodeType.TRAFFIC_LIGHT.value:
                continue

            # Timer inicial aleatorio — evita sincronización artificial
            self._lights[nid] = random.uniform(0.0, _CYCLE)

            # Agrupar aristas entrantes por eje
            in_edges: list[tuple[int, int]] = list(nx_graph.in_edges(nid))
            if not in_edges:
                self._edge_groups[nid] = {}
                continue

            v_lon = float(attrs.get(ATTR_LONGITUDE, 0.0))
            v_lat = float(attrs.get(ATTR_LATITUDE, 0.0))

            bearings: list[float] = []
            for (u, _v) in in_edges:
                u_attrs = nx_graph.nodes[u]
                u_lon = float(u_attrs.get(ATTR_LONGITUDE, 0.0))
                u_lat = float(u_attrs.get(ATTR_LATITUDE, 0.0))
                bearings.append(_bearing(u_lon, u_lat, v_lon, v_lat))

            anchor = bearings[0]
            self._edge_groups[nid] = {
                edge: _axis_group(anchor, b)
                for edge, b in zip(in_edges, bearings, strict=True)
            }

        # Overrides
        self._overrides: dict[int, str] = {}
        self._global_override: str | None = None

    @property
    def light_count(self) -> int:
        return len(self._lights)

    def tick(self, dt: float) -> None:
        for nid in self._lights:
            self._lights[nid] = (self._lights[nid] + dt) % _CYCLE

    # ------------------------------------------------------------------
    # Phase queries
    # ------------------------------------------------------------------

    def _phase_from_time(self, t: float) -> str:
        if t < TL_GREEN_SECONDS:
            return TL_PHASE_GREEN
        if t < TL_GREEN_SECONDS + TL_YELLOW_SECONDS:
            return TL_PHASE_YELLOW
        return TL_PHASE_RED

    def get_phase(self, node_id: int) -> str:
        """Fase del grupo 0 del nodo (compatibilidad con APIs legadas)."""
        if self._global_override is not None:
            return self._global_override
        if node_id in self._overrides:
            return self._overrides[node_id]
        return self._phase_from_time(self._lights.get(node_id, 0.0))

    def get_phase_for_edge(self, node_id: int, incoming_edge: tuple[int, int]) -> str:
        """Fase que ve un vehículo aproximándose a `node_id` por `incoming_edge`.

        Si la arista no está registrada (p. ej. nodo sin semáforo), devuelve 'green'
        — el llamador ya filtra nodos TL antes de usar esta API.
        """
        if self._global_override is not None:
            return self._global_override
        if node_id in self._overrides:
            return self._overrides[node_id]
        if node_id not in self._lights:
            return TL_PHASE_GREEN

        group = self._edge_groups.get(node_id, {}).get(incoming_edge, 0)
        offset = group * _GROUP_OFFSET_S
        t_eff = (self._lights[node_id] + offset) % _CYCLE
        return self._phase_from_time(t_eff)

    def is_red(self, node_id: int) -> bool:
        return self.get_phase(node_id) == TL_PHASE_RED

    def is_blocking(self, node_id: int) -> bool:
        phase = self.get_phase(node_id)
        return phase in (TL_PHASE_RED, TL_PHASE_YELLOW)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_snapshot(self) -> dict[int, dict[str, str]]:
        """Estado actual por arista entrante.

        Formato: `{node_id: {"u_v": phase, ...}}` donde `"u_v"` es la arista
        entrante serializada.

        Nota: muchos TLs de OSM están a mitad de una calle (no son endpoint
        de arista del DiGraph) → no tienen aristas entrantes registradas.
        Para esos emitimos una entrada sintética `"*"` con la fase del grupo 0
        para que el cliente pueda pintar al menos una esfera por nodo.
        """
        snap: dict[int, dict[str, str]] = {}
        for nid in self._lights:
            phases: dict[str, str] = {}
            edge_groups = self._edge_groups.get(nid, {})
            if edge_groups:
                for edge in edge_groups:
                    key = f"{edge[0]}_{edge[1]}"
                    phases[key] = self.get_phase_for_edge(nid, edge)
            else:
                # TL sin aristas entrantes en el DiGraph — fase uniforme
                phases["*"] = self.get_phase(nid)
            snap[nid] = phases
        return snap

    def get_flat_snapshot(self) -> dict[int, str]:
        """Snapshot plano `{node_id: phase}` usando el grupo 0. Útil para workers."""
        return {nid: self.get_phase(nid) for nid in self._lights}

    # ------------------------------------------------------------------
    # Override control
    # ------------------------------------------------------------------

    def set_all_override(self, phase: str) -> None:
        self._global_override = phase

    def set_override(self, node_id: int, phase: str) -> None:
        self._overrides[node_id] = phase

    def clear_override_for_node(self, node_id: int) -> bool:
        """Retira el override de un nodo concreto. Devuelve True si había uno."""
        return self._overrides.pop(node_id, None) is not None

    def has_override_for_node(self, node_id: int) -> bool:
        return node_id in self._overrides

    def knows_node(self, node_id: int) -> bool:
        return node_id in self._lights

    def clear_overrides(self) -> None:
        self._overrides.clear()
        self._global_override = None

    def get_override_mode(self) -> str:
        if self._global_override is not None:
            return f"global_{self._global_override}"
        if self._overrides:
            return f"per_node({len(self._overrides)})"
        return "normal"

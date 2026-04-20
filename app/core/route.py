"""
Cálculo y representación de rutas sobre el grafo de red vial.
Convierte caminos de nodos (NetworkX) en listas de aristas con métricas.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.constants import ATTR_EDGE_ID, ATTR_LENGTH
from app.services.network_graph import RoadNetworkGraph


@dataclass(frozen=True)
class RouteInfo:
    """Información de una ruta calculada entre dos nodos."""

    start_node_id: int
    end_node_id: int
    node_path: list[int]
    edge_ids: list[int]
    length_m: float

    def to_dict(self) -> dict:
        return {
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
            "route_edges": self.edge_ids,
            "route_length_m": round(self.length_m, 1),
        }


def compute_route(
    graph: RoadNetworkGraph,
    start_node_id: int,
    end_node_id: int,
    *,
    blocked_edges: dict[tuple[int, int], object | None] | None = None,
) -> RouteInfo | None:
    """
    Calcula la ruta más corta (por tiempo de viaje) entre dos nodos.

    Convierte el path de nodos devuelto por NetworkX en una lista ordenada
    de edge IDs y calcula la longitud total en metros.

    Args:
        blocked_edges: Aristas con colisión activa; A* las penaliza fuertemente
            en vez de eliminarlas del grafo (preserva conectividad cuando no
            hay alternativa).

    Returns:
        RouteInfo con la ruta calculada, o None si no existe camino.
    """
    node_path = graph.get_shortest_path_astar_safe(
        start_node_id, end_node_id, blocked_edges=blocked_edges
    )
    if node_path is None or len(node_path) < 2:
        return None

    edge_ids: list[int] = []
    total_length = 0.0

    for i in range(len(node_path) - 1):
        edge_data = graph.get_edge_attributes(node_path[i], node_path[i + 1])
        edge_id = edge_data.get(ATTR_EDGE_ID)
        if edge_id is not None:
            edge_ids.append(edge_id)
        total_length += edge_data.get(ATTR_LENGTH, 0.0)

    return RouteInfo(
        start_node_id=start_node_id,
        end_node_id=end_node_id,
        node_path=node_path,
        edge_ids=edge_ids,
        length_m=total_length,
    )

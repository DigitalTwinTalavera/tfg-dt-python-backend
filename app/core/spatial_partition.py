"""
Partición espacial uniforme para bucketing de vehículos por zona.

Cuadrícula NxN sobre el bounding box geográfico del grafo. Cada arista se
asigna a la celda que contiene su centroide al cargar el grafo; cada vehículo
se asigna a la celda de su arista actual (lookup O(1) durante el tick).

El bucketing por celda permite que cada ``ThreadPoolExecutor`` task procese
la física de un subconjunto disjunto de vehículos sin coordinación entre
hilos (excepto el lock global de colisiones).

Con N=8 (64 celdas) frente a ``os.cpu_count()`` hilos, el LPT scheduling
(buckets ordenados DESC por tamaño) absorbe hotspots urbanos sin que un
único bucket pesado limite el wall-clock del tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.constants import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    MAX_BUCKET_SIZE,
    ZONE_GRID_CELLS_PER_AXIS,
)

if TYPE_CHECKING:
    from app.services.network_graph import RoadNetworkGraph
    from app.services.vehicle_spawner import SimVehicle


class SpatialGrid:
    """Partición uniforme NxN sobre el bbox del grafo."""

    __slots__ = (
        "_cells_per_axis",
        "_lon_min",
        "_lat_min",
        "_cell_w",
        "_cell_h",
        "_edge_cell",
    )

    def __init__(
        self,
        graph: "RoadNetworkGraph",
        cells_per_axis: int = ZONE_GRID_CELLS_PER_AXIS,
    ) -> None:
        self._cells_per_axis = cells_per_axis

        lons: list[float] = []
        lats: list[float] = []
        for _, attrs in graph.graph.nodes(data=True):
            lons.append(float(attrs.get(ATTR_LONGITUDE, 0.0)))
            lats.append(float(attrs.get(ATTR_LATITUDE, 0.0)))

        if not lons:
            self._lon_min = 0.0
            self._lat_min = 0.0
            self._cell_w = 1.0
            self._cell_h = 1.0
        else:
            self._lon_min = min(lons)
            self._lat_min = min(lats)
            lon_range = max(lons) - self._lon_min
            lat_range = max(lats) - self._lat_min
            self._cell_w = (lon_range or 1.0) / cells_per_axis
            self._cell_h = (lat_range or 1.0) / cells_per_axis

        self._edge_cell: dict[tuple[int, int], int] = {}
        for u, v in graph.graph.edges():
            lon_u, lat_u = graph.node_lonlat(u)
            lon_v, lat_v = graph.node_lonlat(v)
            self._edge_cell[(u, v)] = self._compute_cell(
                (lon_u + lon_v) * 0.5, (lat_u + lat_v) * 0.5
            )

    def _compute_cell(self, lon: float, lat: float) -> int:
        col = int((lon - self._lon_min) / self._cell_w)
        row = int((lat - self._lat_min) / self._cell_h)
        # Clamp para que coordenadas justo en el borde superior caigan en la
        # última celda en vez de overshoot fuera del grid.
        cap = self._cells_per_axis - 1
        if col < 0:
            col = 0
        elif col > cap:
            col = cap
        if row < 0:
            row = 0
        elif row > cap:
            row = cap
        return row * self._cells_per_axis + col

    def cell_for_edge(self, edge_key: tuple[int, int]) -> int:
        """Celda asignada a la arista. 0 si la arista no está mapeada."""
        return self._edge_cell.get(edge_key, 0)

    def bucket_vehicles(
        self,
        vehicles: list["SimVehicle"],
    ) -> list[list["SimVehicle"]]:
        """
        Agrupa vehículos por celda de su arista actual y devuelve los buckets
        no vacíos ordenados DESC por tamaño (LPT scheduling).

        Vehículos sin arista válida (ruta vacía o ya consumida) van a la
        celda 0 como bucket residual.

        Si un bucket excede ``MAX_BUCKET_SIZE`` se parte en sub-buckets
        ordenando por (edge_key, progress_on_edge); el `edge_index` global de
        `vehicle_physics` garantiza que el líder cross-sub-bucket sigue siendo
        visible aunque dos vehículos del mismo edge caigan en sub-buckets
        distintos.
        """
        buckets: dict[int, list[SimVehicle]] = {}
        for v in vehicles:
            np_ = v.route.node_path
            ei = v.current_edge_index
            if ei < len(np_) - 1:
                cell = self._edge_cell.get((np_[ei], np_[ei + 1]), 0)
            else:
                cell = 0
            buckets.setdefault(cell, []).append(v)

        out: list[list[SimVehicle]] = []
        for cell_bucket in buckets.values():
            if len(cell_bucket) <= MAX_BUCKET_SIZE:
                out.append(cell_bucket)
                continue
            cell_bucket.sort(
                key=lambda v: (
                    v.route.node_path[v.current_edge_index]
                    if v.current_edge_index < len(v.route.node_path) - 1
                    else -1,
                    v.route.node_path[v.current_edge_index + 1]
                    if v.current_edge_index + 1 < len(v.route.node_path)
                    else -1,
                    v.progress_on_edge,
                )
            )
            target = max(MAX_BUCKET_SIZE * 3 // 4, 1)
            for start in range(0, len(cell_bucket), target):
                out.append(cell_bucket[start : start + target])

        return sorted(out, key=len, reverse=True)


_GRID: SpatialGrid | None = None
_GRID_GRAPH_ID: int = 0


def ensure_grid(graph: "RoadNetworkGraph") -> SpatialGrid:
    """Singleton lazy del grid, keyed por id del grafo (mismo patrón que
    ``vehicle_physics._ensure_executor``).

    Si el grafo se rebuilda en runtime, la primera llamada tras el rebuild
    reconstruye el grid.
    """
    global _GRID, _GRID_GRAPH_ID
    gid = id(graph)
    if _GRID is None or _GRID_GRAPH_ID != gid:
        _GRID = SpatialGrid(graph)
        _GRID_GRAPH_ID = gid
    return _GRID

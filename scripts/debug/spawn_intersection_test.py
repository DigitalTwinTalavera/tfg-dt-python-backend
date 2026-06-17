"""Diagnóstico en proceso del arbitraje de cruces y del clamp de colisión.

Construye en memoria un cruce de 4 brazos (N/S/E/O → centro → N/S/E/O),
spawnea 4 vehículos que convergen al mismo nodo central simultáneamente y
ejecuta `update_vehicles` durante N ticks. Reporta:

  - Mínimo gap observado entre cada par convergente.
  - Si se ha disparado alguna colisión (status COLLISION).
  - Distancia recorrida total y velocidades finales.

Uso:
    cd tfg-dt-python-backend
    ./myenv/bin/python -m scripts.debug.spawn_intersection_test
    ./myenv/bin/python -m scripts.debug.spawn_intersection_test --ticks 100

Sirve como regresión y como evidencia cuantitativa de que los fixes
(hard clamp + intersection arbitration) impiden el rebase. Para verificar
visualmente con el cliente Godot, hacer correr la simulación normal con la
demo: el comportamiento debería notarse en cualquier cruce sin TL.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_WAYPOINTS,
    ATTR_WEIGHT,
)
from app.core.route import RouteInfo
from app.core.vehicle_physics import update_vehicles
from app.models.enums import NodeType, VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle


CENTER = 0
NORTH = 1
SOUTH = 2
EAST = 3
WEST = 4
N_END = 5
S_END = 6
E_END = 7
W_END = 8


@dataclass
class TickReport:
    tick: int
    sim_time: float
    min_gap_m: float
    velocities: dict[str, float]
    collisions: list[str]


def _build_4way_graph() -> RoadNetworkGraph:
    """Cruce centrado en (0, 0) con 4 brazos de 50 m cada uno + salida."""
    rng = RoadNetworkGraph()
    g = rng.graph

    # Cada brazo a 50 m del centro (0.00045° ≈ 50 m).
    d = 0.00045
    nodes = {
        CENTER: (0.0, 0.0),
        NORTH: (0.0, d),
        SOUTH: (0.0, -d),
        EAST: (d, 0.0),
        WEST: (-d, 0.0),
        N_END: (0.0, 2 * d),
        S_END: (0.0, -2 * d),
        E_END: (2 * d, 0.0),
        W_END: (-2 * d, 0.0),
    }
    for nid, (lon, lat) in nodes.items():
        g.add_node(
            nid,
            **{
                ATTR_NODE_ID: nid,
                ATTR_LONGITUDE: lon,
                ATTR_LATITUDE: lat,
                # Cruce sin semáforo / sin signo: el árbitro genérico se aplica.
                ATTR_NODE_TYPE: NodeType.INTERSECTION.value,
            },
        )

    def add(eid: int, u: int, v: int, length: float = 50.0) -> None:
        lu, la = nodes[u]
        lv, lb = nodes[v]
        g.add_edge(
            u, v,
            **{
                ATTR_EDGE_ID: eid,
                ATTR_LENGTH: length,
                ATTR_MAX_SPEED: 50,
                ATTR_WEIGHT: length / 30.0,
                ATTR_ONE_WAY: True,
                ATTR_LANES: 1,
                ATTR_WAYPOINTS: [(lu, la), (lv, lb)],
            },
        )

    # Brazos entrantes al centro.
    add(101, NORTH, CENTER)
    add(102, SOUTH, CENTER)
    add(103, EAST, CENTER)
    add(104, WEST, CENTER)
    # Salidas desde el centro hacia los respectivos extremos opuestos.
    add(201, CENTER, S_END)  # N→S
    add(202, CENTER, N_END)  # S→N
    add(203, CENTER, W_END)  # E→W
    add(204, CENTER, E_END)  # W→E
    return rng


def _make_vehicle(vid: str, source: int, dest: int) -> SimVehicle:
    """Vehículo en arista (source, CENTER) con progress=0.5 (a 25 m del cruce)
    y velocidad 12 m/s. Esto es suficiente para que las TTCs sean cercanas."""
    route = RouteInfo(
        start_node_id=source,
        end_node_id=dest,
        node_path=[source, CENTER, dest],
        edge_ids=[100, 200],  # placeholder
        length_m=100.0,
    )
    return SimVehicle(
        id=vid,
        start_node_id=source,
        end_node_id=dest,
        route=route,
        status=VehicleStatus.MOVING,
        current_edge_index=0,
        progress_on_edge=0.5,
        velocity=12.0,
        desired_speed_ms=13.89,
    )


def _gap_between(
    v1: SimVehicle,
    v2: SimVehicle,
    graph: RoadNetworkGraph,
) -> float:
    """Distancia euclidiana aproximada entre los dos vehículos (en metros)."""
    # Reproyección equirectangular alrededor del centro (suficiente a esta escala).
    lon1, lat1 = v1.longitude, v1.latitude
    lon2, lat2 = v2.longitude, v2.latitude
    cos_lat = math.cos(math.radians((lat1 + lat2) * 0.5))
    dx = (lon2 - lon1) * cos_lat * 111_320.0
    dy = (lat2 - lat1) * 111_320.0
    return math.hypot(dx, dy)


def _set_initial_position(v: SimVehicle, graph: RoadNetworkGraph) -> None:
    """Inicializa lon/lat a partir de progress_on_edge y los waypoints del edge."""
    np_ = v.route.node_path
    edge_attrs = graph.get_edge_attributes(np_[0], np_[1])
    waypoints = edge_attrs.get(ATTR_WAYPOINTS) or []
    if len(waypoints) < 2:
        return
    p = v.progress_on_edge
    lon1, lat1 = waypoints[0]
    lon2, lat2 = waypoints[-1]
    v.longitude = lon1 + (lon2 - lon1) * p
    v.latitude = lat1 + (lat2 - lat1) * p


def _scenario_symmetric() -> "dict[str, SimVehicle]":
    """4 vehículos perfectamente simétricos. Caso patológico del priority-to-right:
    todos tienen algún contendiente al que ceder → deadlock conservador. No
    colisión, no rebase. Este es el comportamiento esperado del fix; los TLs
    o stop signs son la solución realista en cruces tan equilibrados."""
    return {
        "v_N": _make_vehicle("v_N", NORTH, S_END),
        "v_S": _make_vehicle("v_S", SOUTH, N_END),
        "v_E": _make_vehicle("v_E", EAST, W_END),
        "v_W": _make_vehicle("v_W", WEST, E_END),
    }


def _scenario_asymmetric() -> "dict[str, SimVehicle]":
    """2 vehículos, llegada escalonada (TTC delta > 0.5 s): el más cercano
    pasa primero, el otro cede. Caso típico en mapa real."""
    a = _make_vehicle("v_a", NORTH, S_END)
    b = _make_vehicle("v_b", EAST, W_END)
    a.progress_on_edge = 0.85  # ~7.5 m del centro
    b.progress_on_edge = 0.50  # ~25 m del centro
    return {"v_a": a, "v_b": b}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=80, help="Ticks a simular")
    parser.add_argument("--dt", type=float, default=0.1, help="Tamaño del tick (s)")
    parser.add_argument(
        "--scenario",
        choices=("symmetric", "asymmetric"),
        default="asymmetric",
        help="symmetric=4 brazos simultáneos (caso patológico, deadlock OK); "
             "asymmetric=2 brazos con llegada escalonada (caso realista)",
    )
    args = parser.parse_args()

    graph = _build_4way_graph()
    if args.scenario == "symmetric":
        vehicles = _scenario_symmetric()
    else:
        vehicles = _scenario_asymmetric()
    for v in vehicles.values():
        _set_initial_position(v, graph)

    print(f"# Diagnóstico de cruce no señalizado — escenario={args.scenario}, "
          f"{args.ticks} ticks @ dt={args.dt}s")
    print(f"# Vehículos convergen al nodo central {CENTER}.")
    velocity_cols = " ".join(f"{vid:>5}" for vid in vehicles)
    print(f"# {'tick':>4} {'sim_t':>6} {'min_gap':>9} {velocity_cols} {'colisiones':>10}")

    overall_min_gap = float("inf")
    collisions_seen: set[str] = set()
    for tick in range(args.ticks):
        update_vehicles(vehicles, graph, dt=args.dt, tick_count=tick)

        # Mínimo gap observado entre todos los pares.
        ids = list(vehicles.keys())
        local_min = float("inf")
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                g = _gap_between(vehicles[ids[i]], vehicles[ids[j]], graph)
                if g < local_min:
                    local_min = g
        overall_min_gap = min(overall_min_gap, local_min)

        # Estado / colisiones.
        for vid, v in vehicles.items():
            if v.status == VehicleStatus.COLLISION:
                collisions_seen.add(vid)

        if tick % 5 == 0 or tick == args.ticks - 1:
            vels = " ".join(f"{v.velocity:5.2f}" for v in vehicles.values())
            print(
                f"  {tick:>4d} {tick * args.dt:6.2f} {local_min:9.2f} "
                f"{vels} {','.join(sorted(collisions_seen)) or '-':>10}"
            )

    print()
    print(f"Mínimo gap observado en toda la simulación: {overall_min_gap:.2f} m")
    if collisions_seen:
        print(f"⚠️  Vehículos en COLLISION al final: {sorted(collisions_seen)}")
    else:
        print("✓ Ningún vehículo terminó en estado COLLISION.")
    if overall_min_gap < 0.0:
        print("⚠️  Solapamiento geométrico detectado.")
    elif overall_min_gap < 1.0:
        print(f"⚠️  Gap mínimo bajo ({overall_min_gap:.2f} m). Margen ajustado pero no overlap.")
    else:
        print("✓ El gap mínimo se mantuvo por encima de 1 m durante toda la simulación.")


if __name__ == "__main__":
    main()

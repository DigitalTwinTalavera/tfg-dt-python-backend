"""
Test de escalado del spawner con muchos vehículos activos.

Regresión de rendimiento: ``_spawn_sync_batch`` antes iteraba todos los
vehículos activos dentro de ``_find_free_spawn_slot`` (O(N · intentos)).
Con >2000 vehículos activos, un batch adicional bloqueaba el hilo de spawn
durante >1 s. Ahora mantiene un índice por arista y cada intento sólo
inspecciona los ocupantes de la arista candidata.

Este test fija un presupuesto (0.5 s) que el implementación lineal viola
cómodamente y la indexada cumple de sobra (~<100 ms en la máquina de CI).
"""

from __future__ import annotations

import time

import pytest

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_TYPE,
    ATTR_WEIGHT,
)
from app.models.enums import NodeType
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import VehicleSpawner


def _build_wide_graph(num_entry: int = 20, num_exit: int = 20) -> RoadNetworkGraph:
    """
    Grafo sintético con aristas largas (10 km, 4 carriles) para que
    quepan miles de vehículos sin saturar. Estructura:
        ENTRY_i → INTERSECTION_i → EXIT_j  para cada combinación (i, j).
    """
    g = RoadNetworkGraph()

    entry_ids: list[int] = []
    exit_ids: list[int] = []
    inter_ids: list[int] = []

    edge_id = 1
    for i in range(num_entry):
        eid = 1000 + i
        iid = 2000 + i
        g.graph.add_node(eid, **{
            ATTR_NODE_TYPE: NodeType.ENTRY_POINT.value,
            ATTR_LONGITUDE: -3.7 + i * 0.001,
            ATTR_LATITUDE: 40.4 + i * 0.001,
        })
        g.graph.add_node(iid, **{
            ATTR_NODE_TYPE: NodeType.INTERSECTION.value,
            ATTR_LONGITUDE: -3.65 + i * 0.001,
            ATTR_LATITUDE: 40.45 + i * 0.001,
        })
        g.graph.add_edge(eid, iid, **{
            ATTR_EDGE_ID: edge_id,
            ATTR_LENGTH: 10_000.0,
            ATTR_WEIGHT: 500.0,
            ATTR_LANES: 4,
            ATTR_MAX_SPEED: 50.0,
        })
        edge_id += 1
        entry_ids.append(eid)
        inter_ids.append(iid)

    for j in range(num_exit):
        xid = 3000 + j
        g.graph.add_node(xid, **{
            ATTR_NODE_TYPE: NodeType.EXIT_POINT.value,
            ATTR_LONGITUDE: -3.5 + j * 0.001,
            ATTR_LATITUDE: 40.3 + j * 0.001,
        })
        exit_ids.append(xid)

    # Cada intersección alcanza cada salida (aristas más cortas, 2 km).
    for iid in inter_ids:
        for xid in exit_ids:
            g.graph.add_edge(iid, xid, **{
                ATTR_EDGE_ID: edge_id,
                ATTR_LENGTH: 2_000.0,
                ATTR_WEIGHT: 100.0,
                ATTR_LANES: 2,
                ATTR_MAX_SPEED: 50.0,
            })
            edge_id += 1

    return g


@pytest.mark.unit
def test_spawn_scales_with_many_active_vehicles():
    """
    Spawnea 2000 vehículos y luego añade un batch de 500 más. El segundo
    batch debe completarse muy por debajo del presupuesto. Antes del
    índice por arista, esto bloqueaba >1 s.
    """
    graph = _build_wide_graph(num_entry=20, num_exit=20)
    spawner = VehicleSpawner(graph=graph, max_vehicles=10_000)

    # Precarga: 2000 vehículos activos.
    initial = spawner.spawn(count=2000)
    assert len(initial) >= 1500, (
        "El grafo no aloja suficientes vehículos; ajustar la fixture"
    )

    # Medir el batch adicional.
    t0 = time.monotonic()
    extra = spawner.spawn(count=500)
    elapsed = time.monotonic() - t0

    assert len(extra) >= 400, (
        f"Spawn adicional dio sólo {len(extra)}/500 vehículos"
    )
    # Presupuesto holgado: con el índice por arista tarda ~<100 ms;
    # el comportamiento O(N²) previo tardaba >1 s.
    assert elapsed < 0.5, (
        f"Spawn de 500 sobre 2000 activos tardó {elapsed*1000:.0f} ms "
        f"(presupuesto 500 ms) — regresión de rendimiento del spawner"
    )

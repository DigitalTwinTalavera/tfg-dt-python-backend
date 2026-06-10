#!/usr/bin/env python3
"""Compara el wire format binario del tick frente a JSON: bytes y tiempo de
serialización para la misma flota. Valida el factor de compresión y de
velocidad citado en la memoria."""
from __future__ import annotations
import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import logging; logging.disable(logging.WARNING)

import app.api.deps as deps
from app.db.database import async_session_factory
from app.api.websocket.messages import build_tick_binary_from_vehicles, build_vehicle_state
from app.core.constants import BROADCAST_CHUNK_SIZE


def _json_payload(vehicles, tick, sim_time):
    """Equivalente JSON al tick binario: lista de estados por vehículo."""
    states = [
        build_vehicle_state(
            vehicle_id=v.id, longitude=v.longitude, latitude=v.latitude,
            velocity=v.velocity, acceleration=v.acceleration, heading=v.heading,
            status=v.status.value, current_edge_index=v.current_edge_index,
            progress_on_edge=v.progress_on_edge, lane=v.lane,
            vtype=v.vtype.value,
        ) for v in vehicles
    ]
    msg = {"type": "tick", "tick": tick, "sim_time": sim_time, "vehicles": states}
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


async def main():
    async with async_session_factory() as s:
        await deps._graph.build_from_database(s)
    spawner = deps.get_vehicle_spawner()
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    spawner.spawn(count=N)
    # avanzar unos ticks para que tengan posición/velocidad realista
    from app.core.vehicle_physics import update_vehicles_parallel
    for _ in range(30):
        await update_vehicles_parallel(spawner.vehicles, spawner.graph, 1/3,
                                       blocked_edges=spawner.blocked_edges,
                                       closed_lanes=spawner.closed_lanes)
    vehicles = spawner.get_all_vehicles()
    n = len(vehicles)

    REP = 50
    # Binario (chunked como en producción)
    t0 = time.perf_counter()
    for _ in range(REP):
        cs = BROADCAST_CHUNK_SIZE
        total = (n + cs - 1) // cs
        bin_bytes = 0
        for idx, i in enumerate(range(0, n, cs)):
            chunk = vehicles[i:i+cs]
            bin_bytes += len(build_tick_binary_from_vehicles(
                tick=1, sim_time=10.0, vehicles=chunk, chunk_index=idx, chunk_total=total))
    bin_ms = (time.perf_counter() - t0) * 1000 / REP

    # JSON
    t0 = time.perf_counter()
    for _ in range(REP):
        json_bytes = len(_json_payload(vehicles, 1, 10.0))
    json_ms = (time.perf_counter() - t0) * 1000 / REP

    # MessagePack (binario estándar autodescriptivo; mismo payload lógico que
    # el JSON, con floats en precisión simple para una comparación justa)
    import msgpack

    def _msgpack_payload(vehicles, tick, sim_time):
        states = [
            build_vehicle_state(
                vehicle_id=v.id, longitude=v.longitude, latitude=v.latitude,
                velocity=v.velocity, acceleration=v.acceleration, heading=v.heading,
                status=v.status.value, current_edge_index=v.current_edge_index,
                progress_on_edge=v.progress_on_edge, lane=v.lane,
                vtype=v.vtype.value,
            ) for v in vehicles
        ]
        msg = {"type": "tick", "tick": tick, "sim_time": sim_time, "vehicles": states}
        return msgpack.packb(msg, use_single_float=True)

    t0 = time.perf_counter()
    for _ in range(REP):
        mp_bytes = len(_msgpack_payload(vehicles, 1, 10.0))
    mp_ms = (time.perf_counter() - t0) * 1000 / REP

    print(f"N={n} vehículos, chunk_size={BROADCAST_CHUNK_SIZE}")
    print(f"  BINARIO: {bin_bytes:>9} B  ({bin_bytes/n:5.1f} B/veh)  serialize={bin_ms:6.2f} ms")
    print(f"  MSGPACK: {mp_bytes:>9} B  ({mp_bytes/n:5.1f} B/veh)  serialize={mp_ms:6.2f} ms")
    print(f"  JSON:    {json_bytes:>9} B  ({json_bytes/n:5.1f} B/veh)  serialize={json_ms:6.2f} ms")
    print(f"  Factor tamaño JSON/bin:    {json_bytes/bin_bytes:.2f}x menos ancho de banda")
    print(f"  Factor tiempo JSON/bin:    {json_ms/bin_ms:.2f}x mas rapido el binario")
    print(f"  Factor tamaño msgpack/bin: {mp_bytes/bin_bytes:.2f}x")
    print(f"  Factor tiempo msgpack/bin: {mp_ms/bin_ms:.2f}x")

asyncio.run(main())

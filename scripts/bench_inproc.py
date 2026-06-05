#!/usr/bin/env python3
"""
Benchmark in-process del backend de simulación.

Conduce el motor REAL (SimulationEngine + VehicleSpawner + broadcaster +
ZoneManager, cableados como en app/api/deps.py) sin pasar por uvicorn/HTTP,
para poder medir el coste por tick bajo distintas cardinalidades de flota en
un único proceso Python free-threaded (3.14t, PYTHON_GIL=0).

Por cada N:
  1. Limpia la flota y reinicia el motor (estado RUNNING).
  2. Spawnea N vehículos (rutas A* reales).
  3. Warm-up: ticks + reposición de la flota a N durante --warmup-s.
  4. registry.reset_histograms() para descartar el transitorio de arranque.
  5. Medición: ticks back-to-back durante --measure-s reponiendo a N cada
     ~2 s; los ticks se ejecutan a la MÁXIMA velocidad (sin sleep de
     deadline) para medir el coste/techo de throughput real por tick.
  6. Vuelca el snapshot completo del registry (histogramas/gauges/counters).

Los ticks se conducen manualmente (no engine.start()) para reponer la flota
SÓLO entre ticks: bajo free-threading no es seguro mutar el dict de vehículos
mientras los workers de física lo iteran.

Uso:
    PYTHON_GIL=0 .venv/bin/python scripts/bench_inproc.py \
        --vehicles 50,100,500,1000,2000,4000 \
        --warmup-s 20 --measure-s 60 --out reports/bench/inproc.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Importar deps cablea el engine singleton con spawner/broadcaster/zone/incident.
import app.api.deps as deps  # noqa: E402
from app.api.websocket.manager import connection_manager  # noqa: E402
from app.core.instrumentation import registry  # noqa: E402
from app.core.simulation_engine import SimulationState  # noqa: E402
from app.db.database import async_session_factory  # noqa: E402
from app.core.constants import VEHICLE_PHYSICS_PARALLEL_THRESHOLD  # noqa: E402


class _FakeWS:
    """Cliente WS de mentira: hace que connection_count>0 para que el
    broadcaster serialice de verdad (ws.serialize_ms / ws.bytes_per_tick)
    sin enviar nada por red."""

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def send_json(self, msg) -> None:
        return None

    async def send_text(self, msg) -> None:
        return None


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _await_pending_broadcast(engine) -> None:
    task = engine._prev_broadcast_task
    if task is not None:
        try:
            await task
        except Exception:
            pass
        engine._prev_broadcast_task = None


async def run_stage(engine, spawner, n: int, dt: float, warmup_s: float,
                    measure_s: float, topup_every_s: float) -> dict:
    # --- reset de estado entre cardinalidades ---
    spawner._vehicles.clear()
    spawner.blocked_edges.clear()
    spawner.closed_lanes.clear()
    spawner.clear_route_cache()
    engine._tick_count = 0
    engine._simulation_time = 0.0
    engine._tl_controller = None
    engine._vehicles_active = 0
    engine._prev_broadcast_task = None
    engine._start_wall_time = time.monotonic()
    engine._state = SimulationState.RUNNING
    if engine._broadcaster is not None:
        engine._broadcaster.reset()

    async def one_tick() -> float:
        await _await_pending_broadcast(engine)
        t0 = time.perf_counter_ns()
        await engine._tick(dt)
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        registry.record("tick_total_ms", elapsed_ms)
        engine._tick_count += 1
        engine._simulation_time += dt
        registry.gauge("vehicles_active", engine._vehicles_active)
        registry.gauge("tick_count", engine._tick_count)
        if elapsed_ms > dt * 1000.0 * 1.2:
            registry.inc("slow_tick_count")
        return elapsed_ms

    def topup() -> None:
        active = spawner.active_count
        if active < n:
            try:
                spawner.spawn(count=n - active)
            except Exception as e:
                _log(f"  spawn topup failed: {type(e).__name__}: {e}")

    # --- spawn inicial ---
    t_spawn = time.monotonic()
    spawner.spawn(count=n)
    _log(f"  N={n}: spawn inicial {spawner.active_count} veh en {time.monotonic()-t_spawn:.1f}s")

    # Pacing por deadline igual que SimulationEngine._run_loop: el tiempo de
    # simulación avanza ~en tiempo real (sim≈wall), de modo que la flota se
    # mantiene con reposición periódica y la dinámica vehicular es realista.
    # Si un tick se pasa del presupuesto no se duerme (corre a tope, como el
    # motor real bajo carga). tick_total_ms se mide SOLO alrededor del tick.
    interval = dt

    async def paced_loop(duration_s: float, collect: list[int] | None) -> tuple[int, float]:
        nonlocal_start_tick = engine._tick_count
        t0 = time.monotonic()
        t_end = t0 + duration_s
        next_deadline = time.monotonic()
        last_topup = -1e9
        while time.monotonic() < t_end:
            now = time.monotonic()
            if now - last_topup >= topup_every_s:
                await _await_pending_broadcast(engine)
                topup()
                last_topup = now
            await one_tick()
            if collect is not None:
                collect.append(spawner.active_count)
            next_deadline += interval
            now = time.monotonic()
            if now - next_deadline > 2 * interval:
                next_deadline = now + interval
            sleep_time = next_deadline - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        return engine._tick_count - nonlocal_start_tick, time.monotonic() - t0

    # --- warm-up ---
    await paced_loop(warmup_s, None)
    _log(f"  N={n}: warm-up done, active={spawner.active_count}, ticks={engine._tick_count}, "
         f"phys_path={'parallel' if spawner.active_count>=VEHICLE_PHYSICS_PARALLEL_THRESHOLD else 'serial'}")

    # --- limpiar transitorio ---
    registry.reset_histograms()
    for c in ("slow_tick_count", "phys.collisions", "ws.bytes"):
        registry._counters.pop(c, None)

    # --- medición ---
    actives: list[int] = []
    ticks_done, measure_wall = await paced_loop(measure_s, actives)

    snap = registry.snapshot()

    # --- teardown del stage ---
    engine._state = SimulationState.STOPPED
    await _await_pending_broadcast(engine)

    eff_rate = ticks_done / measure_wall if measure_wall > 0 else 0.0
    result = {
        "n_target": n,
        "active_median": statistics.median(actives) if actives else 0,
        "active_mean": round(statistics.fmean(actives), 1) if actives else 0,
        "active_min": min(actives) if actives else 0,
        "active_max": max(actives) if actives else 0,
        "ticks_measured": ticks_done,
        "measure_wall_s": round(measure_wall, 2),
        "dt_s": dt,
        "effective_tick_rate_hz": round(eff_rate, 3),
        "histograms_ms": snap["histograms_ms"],
        "gauges": snap["gauges"],
        "counters": snap["counters"],
    }
    h = snap["histograms_ms"]
    tt = h.get("tick_total_ms", {})
    pt = h.get("physics_total_ms", {})
    _log(f"  N={n}: DONE active~{result['active_median']:.0f} ticks={ticks_done} "
         f"tick p50={tt.get('p50',0):.1f}/p95={tt.get('p95',0):.1f}ms "
         f"phys p50={pt.get('p50',0):.1f}ms eff_rate={eff_rate:.2f}Hz "
         f"bytes/tick={h.get('ws.bytes_per_tick',{}).get('p50',0):.0f}")
    return result


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", default="50,100,500,1000,2000,4000")
    ap.add_argument("--warmup-s", type=float, default=20.0)
    ap.add_argument("--measure-s", type=float, default=60.0)
    ap.add_argument("--topup-every-s", type=float, default=2.0)
    ap.add_argument("--tick-hz", type=float, default=3.0)
    ap.add_argument("--out", type=Path, default=Path("reports/bench/inproc.json"))
    args = ap.parse_args()

    import logging
    logging.disable(logging.WARNING)  # silenciar INFO/WARNING del backend en el bench

    sizes = [int(x) for x in args.vehicles.split(",") if x.strip()]
    dt = 1.0 / args.tick_hz

    _log(f"GIL enabled: {sys._is_gil_enabled()}  python {sys.version.split()[0]}")
    _log(f"cpu_count={os.cpu_count()}  parallel_threshold={VEHICLE_PHYSICS_PARALLEL_THRESHOLD}")
    _log(f"sizes={sizes} dt={dt:.4f}s warmup={args.warmup_s}s measure={args.measure_s}s")

    engine = deps.get_simulation_engine()
    spawner = deps.get_vehicle_spawner()
    config = deps.get_simulation_config()
    config.auto_spawn = False
    config.tick_rate = int(round(args.tick_hz))
    engine.set_config(config)

    # cliente WS falso para forzar serialización
    connection_manager._active_connections.append(_FakeWS())

    # construir grafo desde la BD
    t0 = time.monotonic()
    async with async_session_factory() as session:
        gstats = await deps._graph.build_from_database(session)
    _log(f"grafo: {gstats.node_count} nodos, {gstats.edge_count} aristas, "
         f"connected={gstats.is_connected}, build={time.monotonic()-t0:.1f}s")
    try:
        await deps._zone_manager.load_from_db()
    except Exception:
        pass

    meta = {
        "python": sys.version.split()[0],
        "gil_enabled": sys._is_gil_enabled(),
        "cpu_count": os.cpu_count(),
        "parallel_threshold": VEHICLE_PHYSICS_PARALLEL_THRESHOLD,
        "split_sample_every": os.getenv("SIM_SPLIT_SAMPLE_EVERY", "1"),
        "prom_disabled": os.getenv("SIM_PROM_DISABLED", "0"),
        "tick_hz": args.tick_hz,
        "dt_s": dt,
        "warmup_s": args.warmup_s,
        "measure_s": args.measure_s,
        "graph_nodes": gstats.node_count,
        "graph_edges": gstats.edge_count,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    stages: list[dict] = []
    for n in sizes:
        st = await run_stage(engine, spawner, n, dt,
                             args.warmup_s, args.measure_s, args.topup_every_s)
        stages.append(st)
        # volcado incremental (sobrevive a un kill por timeout)
        args.out.write_text(json.dumps({"meta": meta, "stages": stages}, indent=2))
        _log(f"  -> escrito {args.out} ({len(stages)} stages)")

    _log("FIN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""
Casos de estudio what-if EN VIVO (motor IDM/MOBIL real + analítica de dominio).

A diferencia de scripts/bench_cases.py — que es ESTÁTICO: genera pares O-D y
compara rutas A* sin/con restricción sin mover vehículos —, este script ejecuta
cada escenario con la flota CIRCULANDO sobre el motor real (SimulationEngine +
VehicleSpawner + ZoneManager + IncidentManager, cableados como en
app/api/deps.py) y mide el efecto con el motor de analíticas de dominio
(app/core/analytics.py, §5.5.3 de la memoria).

Por cada caso (zbe | flood) se ejecutan dos fases como MUNDOS GEMELOS: ambas
parten de un mundo recién reiniciado con la MISMA semilla, el mismo warm-up
(--warmup-s) y la misma ventana (--measure-s); la única diferencia es que en
la fase escenario la restricción está activa desde antes del primer spawn.
La demanda es idéntica y las dos fases tienen la misma «edad» de mundo, de
modo que la deriva emergente (colisiones→incidentes) afecta por igual a ambas
y el delta es atribuible solo a la restricción.

  FASE BASE      — mundo limpio; flota de N vehículos sin restricción.
  FASE ESCENARIO — mundo limpio gemelo con la restricción activa desde t=0:
                     * zbe   → se crea la ZBE real (Trinidad + Casco Histórico,
                               ~0.44 km²) con enforcement=force_reroute para
                               car+truck vía ZoneManager.create().
                     * flood → se cortan las mismas calles anegadas que en el
                               bench estático (13 calles → 104 edge IDs vía
                               ST_DWithin) proyectándolas en
                               spawner.blocked_edges[(u,v)] = None, la misma
                               proyección que hace IncidentManager
                               (_apply_to_graph_state) al bloquear todos los
                               carriles, sin ensuciar dt_incidents con 104 filas.

Métricas por fase (ventana de medición, motor de analíticas de dominio):
  * travel_time_mean_s / p50 / p95 y trips_completed — viajes COMPLETADOS
    dentro de la ventana (traffic_analytics.record_trip, invocado por el
    engine al finalizar cada vehículo). El ring buffer del singleton se
    redimensiona antes de abrir la ventana para no perder viajes.
  * trip_speed_kmh_mean — velocidad media de los viajes completados.
  * fleet_speed_ms_mean — media (sobre ticks) de la velocidad instantánea
    media de la flota activa (m/s).
  * reroute_events / vehicles_rerouted — se detectan comparando la IDENTIDAD
    del objeto RouteInfo de cada vehículo entre ticks (todo reroute con éxito
    reemplaza vehicle.route por un RouteInfo nuevo; cubre los pases inmediato,
    urgente, periódico, dead-wall y el live-reroute del ZoneManager sin tocar
    el motor). Se cuentan durante TODA la fase (transitorio + medición),
    granularidad 1 evento/vehículo/tick.
  * saturated_edges_peak — pico de aristas con ocupación/capacidad > 1.0,
    usando la MISMA capacidad que el motor de analíticas
    (longitud·carriles/CONGESTION_VEHICLE_SLOT_M); definición ad hoc porque
    TrafficAnalytics solo expone el conteo con umbral 0.6.
  * congested_edges_peak / congestion_max_peak — pico del criterio nativo del
    módulo de analíticas (ratio >= CONGESTION_RATIO_THRESHOLD = 0.6).
  * vehicles_affected_peak — pico de vehículos cuya ruta restante pisa una
    arista bloqueada (bloque `incidents` del motor de analíticas).
  * delta = escenario − base para las métricas numéricas.

Determinismo y mecánica (mismo patrón que scripts/bench_inproc.py):
  * Los ticks se conducen MANUALMENTE con engine._tick(dt): bajo free-threading
    no es seguro mutar el dict de vehículos (top-up/spawn) mientras los workers
    de física lo iteran, así que la reposición se hace SOLO entre ticks.
  * La flota se repone a N cada --topup-every-s, igual que bench_inproc.
  * random.seed(seed) (y numpy si está instalado, por si alguna dependencia lo
    usa; la física no) se fija al inicio de la fase base Y de la fase
    escenario, de modo que el flujo de demanda sea comparable entre fases.
  * El bucketing paralelo de la física no garantiza reproducibilidad bit a bit
    (ver update_vehicles_parallel), pero la demanda O-D sí es la misma.

Notas / limitaciones:
  * Los accidentes autodetectados son permanentes por diseño (los cierra el
    operador); --accident-ttl-s les impone un TTL que simula ese despeje. Sin
    él, en un run desatendido se acumulan hasta autobloquear la red.
  * Los viajes completados en la ventana pueden haber empezado durante el
    warm-up (semántica de ventana en régimen).
  * Requiere la BD con el grafo cargado (load_osm) y NO debe lanzarse mientras
    corre otro benchmark contra el mismo backend/BD.

Uso:
    PYTHON_GIL=0 .venv/bin/python scripts/bench_cases_live.py \
        --case both --vehicles 1000 --warmup-s 120 --measure-s 420 \
        --accident-ttl-s 120 --seed 42 --tick-hz 3 \
        --out reports/bench/cases_live.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import statistics
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Importar deps cablea el engine singleton con spawner/broadcaster/zone/incident.
import app.api.deps as deps  # noqa: E402
from app.core.analytics import _current_edge, traffic_analytics  # noqa: E402
from app.core.constants import (  # noqa: E402
    ANALYTICS_INTERVAL_TICKS,
    ANALYTICS_TRIP_BUFFER_SIZE,
    ATTR_LANES,
    ATTR_LENGTH,
    CONGESTION_RATIO_THRESHOLD,
    CONGESTION_VEHICLE_SLOT_M,
    MIN_EDGE_LENGTH_M,
    PERIODIC_REROUTE_ASTAR_CAP_PER_TICK,
)
from app.core.simulation_engine import SimulationState  # noqa: E402
from app.db.database import async_session_factory, engine as db_engine  # noqa: E402
from app.models.enums import VehicleStatus, ZoneEnforcement, ZoneType  # noqa: E402
from sqlalchemy import text  # noqa: E402

# ---------------------------------------------------------------------------
# Geometría de los escenarios — COPIADA de scripts/bench_cases.py (no se puede
# importar: ese módulo ejecuta asyncio.run(main()) a nivel de módulo). Mantener
# sincronizada a mano si cambia el bench estático.
# ---------------------------------------------------------------------------

# Perímetro REAL de la ZBE (Trinidad + Casco Histórico) dibujado en el cliente.
# WKT en lon/lat (EPSG:4326), tal cual lo almacena PostGIS.
ZBE_WKT = (
    "POLYGON(("
    "-4.832959 39.956703,-4.832446 39.956535,-4.827789 39.958225,"
    "-4.825591 39.960075,-4.8277 39.960739,-4.830302 39.962399,"
    "-4.83018 39.963161,-4.832466 39.965141,-4.833056 39.965229,"
    "-4.833297 39.964001,-4.834359 39.963921,-4.833306 39.962608,"
    "-4.834325 39.961929,-4.835838 39.961243,-4.836168 39.960571,"
    "-4.83578 39.959312,-4.835608 39.958084,-4.835629 39.95792,"
    "-4.832959 39.956703))"
)
ZBE_RESTRICTED = ["car", "truck"]   # solo las motos quedan exentas

# Calles REALES cortadas por la inundación. Cada tramo: (nombre, lon0,lat0,lon1,lat1).
FLOOD_STREETS = [
    ("Marqués de Mirasol",          -4.8285, 39.9619, -4.8258, 39.9632),
    ("Portiña de San Miguel",       -4.8280, 39.9605, -4.8270, 39.9620),
    ("Portiña del Salvador",        -4.8275, 39.9590, -4.8280, 39.9605),
    ("Calle Mula",                  -4.8340, 39.9570, -4.8330, 39.9580),
    ("Cristo de la Salud",          -4.8250, 39.9640, -4.8235, 39.9655),
    ("Calle Entretorres",           -4.8310, 39.9565, -4.8300, 39.9575),
    ("Las Hilanderas",              -4.8450, 39.9585, -4.8440, 39.9595),
    ("Calle Grisetas",              -4.8420, 39.9590, -4.8410, 39.9595),
    ("Calle San Martín",            -4.8315, 39.9575, -4.8325, 39.9585),
    ("Calle Bancaleros",            -4.8410, 39.9580, -4.8400, 39.9585),
    ("Santa Lucía",                 -4.8340, 39.9610, -4.8330, 39.9620),
    ("Callejón Ideal",              -4.8260, 39.9630, -4.8255, 39.9635),
    ("Av. Real Fábrica de Sedas",   -4.8480, 39.9565, -4.8550, 39.9550),
]
FLOOD_DWITHIN_M = 20  # mismo spatial-join que el bench estático (104 edge IDs)

# Métricas numéricas sobre las que se calcula el delta escenario − base.
DELTA_KEYS = (
    "travel_time_mean_s",
    "travel_time_p50_s",
    "travel_time_p95_s",
    "trip_speed_kmh_mean",
    "fleet_speed_ms_mean",
    "trips_completed",
    "reroute_events",
    "vehicles_rerouted",
    "saturated_edges_peak",
    "congested_edges_peak",
    "congestion_max_peak",
    "vehicles_affected_peak",
)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _seed_all(seed: int) -> None:
    """Fija la semilla del random global (el spawner usa `random` de stdlib).

    numpy solo se siembra si está instalado: la física IDM/MOBIL no lo usa,
    pero alguna dependencia podría — coste cero y determinismo defensivo.
    """
    random.seed(seed)
    try:
        import numpy as _np
        _np.random.seed(seed)
    except Exception:
        pass


def _arm_analytics(buffer_size: int) -> None:
    """Resetea el motor de analíticas y redimensiona su ring buffer de viajes.

    Acceso privado deliberado (estilo bench, como bench_inproc con
    registry._counters): el singleton conserva por defecto los últimos
    ANALYTICS_TRIP_BUFFER_SIZE (2048) viajes; para que la media de la ventana
    cubra TODOS los viajes completados durante --measure-s ampliamos el buffer
    antes de abrir la ventana. `trips_completed` del snapshot cuenta desde
    este reset.
    """
    traffic_analytics.reset()
    traffic_analytics._travel_times_s = deque(maxlen=buffer_size)
    traffic_analytics._trip_speeds_kmh = deque(maxlen=buffer_size)


async def _await_pending_broadcast(engine) -> None:
    task = engine._prev_broadcast_task
    if task is not None:
        try:
            await task
        except Exception:
            pass
        engine._prev_broadcast_task = None


def _reset_world(engine, spawner, incident_mgr) -> None:
    """Estado limpio entre casos (mismo patrón que bench_inproc.run_stage)."""
    spawner._vehicles.clear()
    spawner.blocked_edges.clear()
    spawner.closed_lanes.clear()
    spawner.clear_route_cache()
    # Accidentes en memoria del caso anterior (IDs sintéticos, sin flush a BD).
    incident_mgr._active.clear()
    engine._tick_count = 0
    engine._simulation_time = 0.0
    engine._tl_controller = None
    engine._vehicles_active = 0
    engine._prev_broadcast_task = None
    engine._start_wall_time = time.monotonic()
    engine._state = SimulationState.RUNNING
    if engine._broadcaster is not None:
        engine._broadcaster.reset()
    traffic_analytics.reset()


class _PhaseMetrics:
    """
    Colector de métricas de dominio de una fase.

    Se crea al ARRANCAR la fase (antes de su warm-up) para que el tracker de
    reroutes cubra también el transitorio de activación; las métricas de
    ventana (velocidades, picos de congestión, viajes) solo se acumulan tras
    open_window(), que se llama al terminar el warm-up.

    Detección de reroutes: todo reroute con éxito sustituye `vehicle.route`
    por un RouteInfo NUEVO (app/core/physics/rerouting.py y
    VehicleSpawner.reroute_vehicle), así que basta comparar la identidad del
    objeto entre ticks. Se retiene la referencia anterior en el dict, lo que
    además impide falsos negativos por reutilización de id() tras GC.
    """

    def __init__(self, spawner) -> None:
        self._routes: dict[str, object] = {
            vid: v.route for vid, v in spawner.vehicles.items()
        }
        self.window = False
        self.reroute_events = 0
        self.rerouted_ids: set[str] = set()
        self.fleet_speed_means: list[float] = []
        self.actives: list[int] = []
        self.saturated_edges_peak = 0
        self.congested_edges_peak = 0
        self.congestion_max_peak = 0.0
        self.vehicles_affected_peak = 0

    def open_window(self) -> None:
        self.window = True

    def on_tick(self, spawner, graph, sampled: bool) -> None:
        """Una pasada O(N) por tick; la ocupación solo en ticks muestreados."""
        vehicles = spawner.vehicles
        routes = self._routes
        n_active = 0
        speed_sum = 0.0
        collect_occ = self.window and sampled
        occupancy: dict[tuple[int, int], int] = {}

        for vid, v in vehicles.items():
            prev = routes.get(vid)
            if prev is None:
                routes[vid] = v.route          # alta nueva (top-up)
            elif v.route is not prev:          # RouteInfo reemplazado → reroute
                self.reroute_events += 1
                self.rerouted_ids.add(vid)
                routes[vid] = v.route
            if v.status == VehicleStatus.FINISHED:
                continue
            n_active += 1
            speed_sum += v.velocity
            if collect_occ:
                edge = _current_edge(v)
                if edge is not None:
                    occupancy[edge] = occupancy.get(edge, 0) + 1

        if self.window:
            self.actives.append(n_active)
            if n_active:
                self.fleet_speed_means.append(speed_sum / n_active)

        # Poda perezosa de vehículos ya retirados (los IDs no se reutilizan).
        if len(routes) > 2 * max(len(vehicles), 1):
            for vid in [k for k in routes if k not in vehicles]:
                del routes[vid]

        if collect_occ:
            # Saturación estricta (ratio > 1.0) con la MISMA capacidad que
            # TrafficAnalytics.sample (app/core/analytics.py): longitud ·
            # carriles / CONGESTION_VEHICLE_SLOT_M. Ad hoc porque el módulo
            # solo agrega el conteo con umbral CONGESTION_RATIO_THRESHOLD.
            saturated = 0
            for edge, count in occupancy.items():
                attrs = graph.get_edge_attributes(*edge)
                length = max(float(attrs.get(ATTR_LENGTH, 0.0)), MIN_EDGE_LENGTH_M)
                lanes = max(int(attrs.get(ATTR_LANES, 1)), 1)
                capacity = max(length * lanes / CONGESTION_VEHICLE_SLOT_M, 1.0)
                if count / capacity > 1.0:
                    saturated += 1
            if saturated > self.saturated_edges_peak:
                self.saturated_edges_peak = saturated

            # Picos del criterio NATIVO del motor de analíticas (sample() se
            # ejecutó dentro de engine._tick en este mismo tick). Acceso a los
            # dicts internos para no pagar el sort de percentiles del snapshot.
            cong = traffic_analytics._congestion
            inc = traffic_analytics._incidents
            ce = int(cong.get("congested_edges", 0))
            if ce > self.congested_edges_peak:
                self.congested_edges_peak = ce
            cm = float(cong.get("congestion_max", 0.0))
            if cm > self.congestion_max_peak:
                self.congestion_max_peak = cm
            va = int(inc.get("vehicles_affected", 0))
            if va > self.vehicles_affected_peak:
                self.vehicles_affected_peak = va


async def _one_tick(engine, dt: float) -> bool:
    """Un tick manual del motor real. Devuelve True si en este tick el engine
    ejecutó traffic_analytics.sample() (cada ANALYTICS_INTERVAL_TICKS)."""
    await _await_pending_broadcast(engine)
    pre = engine._tick_count
    await engine._tick(dt)
    engine._tick_count += 1
    engine._simulation_time += dt
    return pre % ANALYTICS_INTERVAL_TICKS == 0


def _topup(spawner, n: int) -> None:
    active = spawner.active_count
    if active < n:
        try:
            spawner.spawn(count=n - active)
        except Exception as e:
            _log(f"  spawn topup failed: {type(e).__name__}: {e}")


async def _paced_loop(engine, spawner, graph, *, n: int, dt: float,
                      duration_s: float, topup_every_s: float,
                      metrics: _PhaseMetrics | None,
                      incident_mgr=None,
                      accident_ttl_s: float | None = None) -> tuple[int, float]:
    """Bucle por deadline (sim≈wall) igual que bench_inproc: top-up SOLO entre
    ticks; si un tick se pasa de presupuesto no se duerme."""
    start_tick = engine._tick_count
    t0 = time.monotonic()
    t_end = t0 + duration_s
    next_deadline = time.monotonic()
    last_topup = -1e9
    while time.monotonic() < t_end:
        now = time.monotonic()
        if now - last_topup >= topup_every_s:
            await _await_pending_broadcast(engine)
            _topup(spawner, n)
            last_topup = now
        sampled = await _one_tick(engine, dt)
        if accident_ttl_s is not None and incident_mgr is not None:
            # Los accidentes autodetectados son PERMANENTES por diseño
            # (INCIDENT_DEFAULT_DURATION_S["accident"] = None: los cierra el
            # operador por la API). En un run desatendido se acumulan hasta
            # autobloquear la red, así que el bench les fija un TTL y deja
            # que la expiración del propio engine (incident_manager.tick +
            # process_expired) los retire — simula la intervención del
            # operador que el diseño asume.
            for inc in incident_mgr.list_active():
                if inc.duration_s is None and inc.type == "accident":
                    inc.duration_s = accident_ttl_s
        if metrics is not None:
            metrics.on_tick(spawner, graph, sampled)
        next_deadline += dt
        now = time.monotonic()
        if now - next_deadline > 2 * dt:
            next_deadline = now + dt
        sleep_time = next_deadline - now
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
    return engine._tick_count - start_tick, time.monotonic() - t0


async def _run_phase(engine, spawner, graph, *, n: int, dt: float,
                     warmup_s: float, measure_s: float, topup_every_s: float,
                     trip_buffer: int, label: str,
                     incident_mgr=None,
                     accident_ttl_s: float | None = None) -> dict:
    """Warm-up + ventana de medición. Devuelve las métricas de la fase."""
    metrics = _PhaseMetrics(spawner)

    if warmup_s > 0:
        await _paced_loop(engine, spawner, graph, n=n, dt=dt,
                          duration_s=warmup_s, topup_every_s=topup_every_s,
                          metrics=metrics, incident_mgr=incident_mgr,
                          accident_ttl_s=accident_ttl_s)
    _log(f"  {label}: warm-up done ({warmup_s:.0f}s), active={spawner.active_count}, "
         f"reroutes_transitorio={metrics.reroute_events}")

    _arm_analytics(trip_buffer)
    metrics.open_window()
    ticks, wall = await _paced_loop(engine, spawner, graph, n=n, dt=dt,
                                    duration_s=measure_s,
                                    topup_every_s=topup_every_s,
                                    metrics=metrics, incident_mgr=incident_mgr,
                                    accident_ttl_s=accident_ttl_s)

    snap = traffic_analytics.snapshot()
    tt = snap["travel_time_s"]
    ts = snap["trip_speed_kmh"]
    result = {
        "ticks": ticks,
        "wall_s": round(wall, 2),
        "sim_s": round(ticks * dt, 1),
        "effective_tick_rate_hz": round(ticks / wall, 3) if wall > 0 else 0.0,
        "active_mean": round(statistics.fmean(metrics.actives), 1) if metrics.actives else 0,
        "active_median": statistics.median(metrics.actives) if metrics.actives else 0,
        "fleet_speed_ms_mean": round(statistics.fmean(metrics.fleet_speed_means), 3)
        if metrics.fleet_speed_means else 0.0,
        "trips_completed": tt["trips_completed"],
        "travel_time_mean_s": tt["mean"],
        "travel_time_p50_s": tt["p50"],
        "travel_time_p95_s": tt["p95"],
        "trip_speed_kmh_mean": ts["mean"],
        # Reroutes de TODA la fase (transitorio incluido), ver _PhaseMetrics.
        "reroute_events": metrics.reroute_events,
        "vehicles_rerouted": len(metrics.rerouted_ids),
        "saturated_edges_peak": metrics.saturated_edges_peak,
        "congested_edges_peak": metrics.congested_edges_peak,
        "congestion_max_peak": round(metrics.congestion_max_peak, 4),
        "vehicles_affected_peak": metrics.vehicles_affected_peak,
        "blocked_edges_active": len(spawner.blocked_edges),
        # Bloque completo del motor de analíticas al cierre de la ventana.
        "analytics": snap,
    }
    _log(f"  {label}: DONE active~{result['active_median']:.0f} "
         f"trips={result['trips_completed']} tt_mean={result['travel_time_mean_s']:.1f}s "
         f"v_flota={result['fleet_speed_ms_mean']:.2f}m/s "
         f"reroutes={result['vehicles_rerouted']} "
         f"saturadas_pico={result['saturated_edges_peak']}")
    return result


def _delta(base: dict, scenario: dict) -> dict:
    return {k: round(scenario[k] - base[k], 3) for k in DELTA_KEYS}


# ---------------------------------------------------------------------------
# Activación / retirada de escenarios
# ---------------------------------------------------------------------------

async def _activate_zbe(zone_mgr):
    """Crea la ZBE real vía ZoneManager (PostGIS ST_Intersects → caché de
    aristas restringidas, invalidación de caché de rutas y live-reroute capado
    a 500; el batch periódico del motor cubre el resto de la flota)."""
    zone = await zone_mgr.create(
        name="ZBE Talavera (Trinidad + Casco Histórico) [bench live]",
        zone_type=ZoneType.ZBE,
        geometry_wkt=ZBE_WKT,
        restricted_vtypes=ZBE_RESTRICTED,
        enforcement=ZoneEnforcement.FORCE_REROUTE,
        active=True,
    )
    # Área real de la geometría vía PostGIS (igual que bench_cases.py).
    async with async_session_factory() as s:
        area_km2 = float((await s.execute(text(
            "SELECT ST_Area(geometry::geography)/1e6 FROM dt_zones WHERE id=:i"
        ), {"i": zone.id})).scalar_one())
    restricted = zone_mgr.restricted_edges_by_vtype().get(ZBE_RESTRICTED[0], set())
    setup = {
        "tipo": "zbe",
        "zone_id": zone.id,
        "area_km2": round(area_km2, 3),
        "aristas_restringidas_uv": len(restricted),
        "restringidos": list(ZBE_RESTRICTED),
        "enforcement": ZoneEnforcement.FORCE_REROUTE.value,
    }
    _log(f"  ZBE activa: ~{area_km2:.3f} km², "
         f"{len(restricted)} aristas (u,v) restringidas para car+truck")
    return zone, setup


async def _flood_edges(graph) -> tuple[set[tuple[int, int]], set[int], list]:
    """Map-matching de las 13 calles anegadas a aristas de la red.

    Mismo spatial-join que scripts/bench_cases.py: aristas cuya geometría
    discurre a < FLOOD_DWITHIN_M del tramo entrada→salida de cada calle
    (en el bench estático: 104 edge IDs). edge_id (BD) → aristas dirigidas
    (u,v) del grafo (1 o 2 sentidos).
    """
    G = graph.graph
    eid2uv: dict[int, list[tuple[int, int]]] = {}
    for u, v, data in G.edges(data=True):
        eid2uv.setdefault(int(data.get("edge_id", 0)), []).append((u, v))

    B: set[tuple[int, int]] = set()
    ids_cut: set[int] = set()
    matched: list[tuple[str, int]] = []
    async with async_session_factory() as s:
        for (name, lo0, la0, lo1, la1) in FLOOD_STREETS:
            ids = (await s.execute(text(
                """
                SELECT id FROM dt_edges
                WHERE ST_DWithin(
                    geometry::geography,
                    ST_SetSRID(ST_MakeLine(ST_MakePoint(:x0,:y0),
                                           ST_MakePoint(:x1,:y1)),4326)::geography,
                    :d)
                """
            ), {"x0": lo0, "y0": la0, "x1": lo1, "y1": la1, "d": FLOOD_DWITHIN_M})).scalars().all()
            ids = [int(e) for e in ids]
            uvs = [uv for eid in ids for uv in eid2uv.get(eid, [])]
            B.update(uvs)
            ids_cut.update(ids)
            matched.append((name, len(ids)))
            _log(f"  corte: {name:28s} -> {len(ids)} aristas")
    _log(f"  TOTAL: {len(ids_cut)} edge IDs / {len(B)} aristas dirigidas "
         f"(D={FLOOD_DWITHIN_M} m)")
    return B, ids_cut, matched


def _activate_flood(spawner, flood_uv: set[tuple[int, int]]) -> None:
    """Corta las aristas anegadas en el estado VIVO.

    Misma proyección que IncidentManager._apply_to_graph_state cuando un
    incidente cierra todos los carriles (spawner.blocked_edges[edge] = None),
    sin persistir 104 incidentes en BD. El motor las trata igual: A* las
    penaliza en spawns/reroutes, el IDM frena ante ellas y el batch periódico
    de rerouting reencamina a los vehículos cuya ruta pendiente las pise.
    """
    for e in flood_uv:
        spawner.blocked_edges[e] = None
    # Las rutas cacheadas del spawner pueden atravesar los cortes: invalidar
    # para que los top-ups recalculen con blocked_edges.
    spawner.clear_route_cache()


def _deactivate_flood(spawner, flood_uv: set[tuple[int, int]]) -> None:
    """Retira SOLO los cortes de inundación (los bloqueos por accidente del
    propio tráfico, si los hubiera, se conservan)."""
    for e in flood_uv:
        spawner.blocked_edges.pop(e, None)
    spawner.clear_route_cache()


# ---------------------------------------------------------------------------
# Caso completo: base + escenario + delta
# ---------------------------------------------------------------------------

async def run_case(case: str, engine, spawner, zone_mgr, incident_mgr, graph,
                   args, dt: float, trip_buffer: int, case_entry: dict,
                   dump) -> None:
    """Diseño de MUNDOS GEMELOS: las dos fases parten de un mundo recién
    reiniciado con la MISMA semilla, mismo warm-up y misma ventana; la única
    diferencia es que en la fase escenario la restricción está activa desde
    antes del primer spawn. Así la demanda es idéntica, ambas fases tienen la
    misma «edad» de mundo (la degradación emergente por colisiones afecta por
    igual a las dos) y el delta es atribuible a la restricción, no al paso del
    tiempo. (El diseño anterior —continuar el mismo mundo y activar sobre la
    flota viva— confundía el efecto de la restricción con la deriva temporal.)
    """
    _log(f"=== CASO {case.upper()} (N={args.vehicles}, seed={args.seed}, "
         f"mundos gemelos) ===")

    flood_uv: set[tuple[int, int]] | None = None
    if case == "flood":
        flood_uv, ids_cut, matched = await _flood_edges(graph)
        case_entry["setup"] = {
            "tipo": "flood",
            "calles_cortadas": len(FLOOD_STREETS),
            "aristas_cortadas_ids": len(ids_cut),
            "aristas_cortadas_uv": len(flood_uv),
            "dwithin_m": FLOOD_DWITHIN_M,
            "calles": matched,
        }

    async def _spawn_and_phase(label: str) -> dict:
        t_spawn = time.monotonic()
        spawner.spawn(count=args.vehicles)
        _log(f"  {label}: spawn inicial {spawner.active_count} veh "
             f"en {time.monotonic() - t_spawn:.1f}s")
        return await _run_phase(
            engine, spawner, graph, n=args.vehicles, dt=dt,
            warmup_s=args.warmup_s, measure_s=args.measure_s,
            topup_every_s=args.topup_every_s, trip_buffer=trip_buffer,
            label=label,
            incident_mgr=incident_mgr, accident_ttl_s=args.accident_ttl_s,
        )

    # --- FASE BASE: mundo limpio, sin restricción ---
    _reset_world(engine, spawner, incident_mgr)
    _seed_all(args.seed)
    case_entry["base"] = await _spawn_and_phase(f"{case}/base")
    engine._state = SimulationState.STOPPED
    await _await_pending_broadcast(engine)
    dump()

    # --- FASE ESCENARIO: mundo limpio gemelo, restricción activa desde t=0 ---
    _reset_world(engine, spawner, incident_mgr)
    _seed_all(args.seed)
    zone = None
    if case == "zbe":
        zone, setup = await _activate_zbe(zone_mgr)
        case_entry["setup"] = setup
    else:
        assert flood_uv is not None
        _activate_flood(spawner, flood_uv)

    try:
        case_entry["scenario"] = await _spawn_and_phase(f"{case}/escenario")
    finally:
        # --- teardown del escenario ---
        if zone is not None:
            await zone_mgr.delete(zone.id)
        if flood_uv is not None:
            _deactivate_flood(spawner, flood_uv)
        engine._state = SimulationState.STOPPED
        await _await_pending_broadcast(engine)

    case_entry["delta"] = _delta(case_entry["base"], case_entry["scenario"])
    _log(f"  delta {case}: " + ", ".join(
        f"{k}={case_entry['delta'][k]:+g}"
        for k in ("travel_time_mean_s", "fleet_speed_ms_mean",
                  "vehicles_rerouted", "saturated_edges_peak")
    ))


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Casos de estudio ZBE/inundación con la flota circulando "
                    "(motor real) y métricas del motor de analíticas de dominio."
    )
    ap.add_argument("--case", choices=("zbe", "flood", "both"), default="both")
    ap.add_argument("--vehicles", type=int, default=3000)
    ap.add_argument("--warmup-s", type=float, default=120.0,
                    help="warm-up de CADA mundo gemelo (base y escenario); en "
                         "el escenario absorbe también el transitorio de "
                         "reroutes, capados a "
                         f"{PERIODIC_REROUTE_ASTAR_CAP_PER_TICK} A*/tick")
    ap.add_argument("--measure-s", type=float, default=300.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tick-hz", type=float, default=3.0)
    ap.add_argument("--topup-every-s", type=float, default=2.0)
    ap.add_argument("--accident-ttl-s", type=float, default=120.0,
                    help="TTL (s, tiempo de simulación) impuesto a los "
                         "accidentes autodetectados, que por diseño son "
                         "permanentes hasta que el operador los cierra; "
                         "simula esa intervención. <=0 = permanentes")
    ap.add_argument("--out", type=Path, default=Path("reports/bench/cases_live.json"))
    args = ap.parse_args()
    if args.accident_ttl_s is not None and args.accident_ttl_s <= 0:
        args.accident_ttl_s = None

    logging.disable(logging.WARNING)  # silenciar INFO/WARNING del backend en el bench

    dt = 1.0 / args.tick_hz
    # Buffer de viajes con margen holgado sobre lo esperable en --measure-s.
    trip_buffer = max(ANALYTICS_TRIP_BUFFER_SIZE, args.vehicles * 10)

    gil_enabled = getattr(sys, "_is_gil_enabled", lambda: True)()
    _log(f"GIL enabled: {gil_enabled}  python {sys.version.split()[0]}")
    _log(f"case={args.case} N={args.vehicles} dt={dt:.4f}s warmup={args.warmup_s}s "
         f"accident_ttl={args.accident_ttl_s}s measure={args.measure_s}s "
         f"seed={args.seed}")

    engine = deps.get_simulation_engine()
    spawner = deps.get_vehicle_spawner()
    zone_mgr = deps.get_zone_manager()
    incident_mgr = deps.get_incident_manager()
    config = deps.get_simulation_config()
    config.auto_spawn = False           # la flota se repone manualmente a N
    config.tick_rate = int(round(args.tick_hz))
    engine.set_config(config)

    # Construir grafo desde la BD.
    t0 = time.monotonic()
    async with async_session_factory() as session:
        gstats = await deps._graph.build_from_database(session)
    graph = deps._graph
    _log(f"grafo: {gstats.node_count} nodos, {gstats.edge_count} aristas, "
         f"build={time.monotonic() - t0:.1f}s")

    # Limpiar zonas residuales de runs anteriores (igual que bench_cases.py)
    # para que la fase BASE corra de verdad sin restricciones.
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM dt_zones"))
        await s.commit()
    await zone_mgr.load_from_db()

    meta = {
        "script": "bench_cases_live",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "gil_enabled": gil_enabled,
        "cpu_count": os.cpu_count(),
        "case": args.case,
        "vehicles": args.vehicles,
        "warmup_s": args.warmup_s,
        "twin_worlds": True,
        "measure_s": args.measure_s,
        "seed": args.seed,
        "tick_hz": args.tick_hz,
        "dt_s": dt,
        "topup_every_s": args.topup_every_s,
        "accident_ttl_s": args.accident_ttl_s,
        "trip_buffer": trip_buffer,
        "graph_nodes": gstats.node_count,
        "graph_edges": gstats.edge_count,
        # Definiciones usadas por las métricas de congestión/saturación.
        "analytics_interval_ticks": ANALYTICS_INTERVAL_TICKS,
        "congestion_vehicle_slot_m": CONGESTION_VEHICLE_SLOT_M,
        "congestion_ratio_threshold": CONGESTION_RATIO_THRESHOLD,
        "saturated_ratio_threshold": 1.0,
    }

    cases = ["zbe", "flood"] if args.case == "both" else [args.case]
    out = {"meta": meta, "cases": {}}
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def dump() -> None:
        # Volcado incremental (sobrevive a un kill por timeout).
        args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        _log(f"  -> escrito {args.out}")

    for case in cases:
        case_entry: dict = {"setup": {}, "base": None, "scenario": None, "delta": None}
        out["cases"][case] = case_entry
        await run_case(case, engine, spawner, zone_mgr, incident_mgr, graph,
                       args, dt, trip_buffer, case_entry, dump)
        dump()

    await db_engine.dispose()
    _log("FIN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

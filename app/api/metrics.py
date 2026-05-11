"""
Endpoints de exposición de métricas de rendimiento.

  - GET /metrics  → Prometheus exposition format (texto plano).
  - GET /api/simulation/metrics  → JSON estructurado con percentiles y
    derivadas legibles a ojo (bytes/s, ratios) para curl/jq.

El backend escribe en `app.core.instrumentation.registry` desde el tick
loop, la física de vehículos y el broadcaster. Estos endpoints son
sólo lectura: snapshot del estado actual del registry, sin reset.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_simulation_engine
from app.core.instrumentation import registry
from app.core.simulation_engine import SimulationEngine

logger = logging.getLogger(__name__)

prometheus_router = APIRouter()
json_router = APIRouter()


@prometheus_router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus exposition format. Apto para scraping cada 5–15 s."""
    body = registry.prometheus_text()
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@json_router.get("/simulation/metrics")
async def simulation_metrics(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """
    Snapshot JSON con percentiles + métricas derivadas (bytes/s, etc.).

    Útil para:
      - El script `scripts/load_test.py` (sample cada 10 s y compara entre
        tamaños de flota).
      - Inspección manual con `curl -s ... | jq`.
    """
    snap = registry.snapshot()
    uptime_s = engine.uptime_seconds or 1e-9

    counters = snap.get("counters", {})
    ws_bytes_total = counters.get("ws.bytes", 0.0)
    derived = {
        "ws_bytes_per_second": ws_bytes_total / uptime_s,
        "uptime_seconds": uptime_s,
        "tick_count": engine.tick_count,
        "vehicles_active": engine.vehicles_active,
        "tick_rate_hz": engine.tick_rate,
    }

    physics_stats = snap["histograms_ms"].get("phys.idm_ms")
    n_active_gauge = snap.get("gauges", {}).get("phys.n_active_in_loop")
    if physics_stats and n_active_gauge:
        # Indicador de O(n²): cuánto crece el coste/vehículo al subir N. El
        # propio script de carga lo recalcula entre tamaños; aquí damos el
        # valor instantáneo del último snapshot para inspección manual.
        derived["phys_idm_us_per_vehicle"] = (
            physics_stats["p50"] * 1000.0 / max(n_active_gauge, 1)
        )

    return {
        "schema": "v1",
        "derived": derived,
        "histograms_ms": snap["histograms_ms"],
        "counters": snap["counters"],
        "gauges": snap["gauges"],
    }

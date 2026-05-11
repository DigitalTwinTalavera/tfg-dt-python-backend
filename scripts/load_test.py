#!/usr/bin/env python3
"""
Script de carga para el backend de simulación de tráfico.

Para cada tamaño de flota N:
  1. POST /api/simulation/start
  2. POST /api/simulation/vehicles/spawn  (count=N)
  3. Espera `--warmup-s` para llegar a régimen estacionario
  4. Cada 10 s hace GET /api/simulation/metrics y guarda snapshot
  5. POST /api/simulation/stop

Al final imprime una tabla resumen con `tick_p95`, `physics_p95`,
`bytes/s` y, sobre todo, `physics_us/vehicle` y el ratio frente al
tamaño anterior — la señal directa de O(n²): si physics/veh crece al
duplicar N, el coste por vehículo no es constante.

Uso:
    python scripts/load_test.py \\
        --url http://localhost:8000 \\
        --vehicles 500,1000,2000,4000 \\
        --duration-s 300 --warmup-s 30 \\
        --metrics-out reports/load.json

No depende de ningún módulo del proyecto: solo stdlib. Pensado para
correr desde fuera (mientras `uvicorn app.main:app` está sirviendo).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _http(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8") or "{}"
        return json.loads(text)


def _get(url: str, timeout: float = 30.0) -> dict:
    return _http("GET", url, timeout=timeout)


def _post(url: str, body: dict | None = None, timeout: float = 60.0) -> dict:
    return _http("POST", url, body=body, timeout=timeout)


@dataclass
class StageResult:
    n_target: int
    samples: list[dict] = field(default_factory=list)

    def add(self, snap: dict) -> None:
        self.samples.append(snap)

    def summary(self) -> dict:
        if not self.samples:
            return {"n_target": self.n_target, "samples": 0}

        def col(path: list[str]) -> list[float]:
            out: list[float] = []
            for s in self.samples:
                v: Any = s
                for p in path:
                    v = v.get(p) if isinstance(v, dict) else None
                    if v is None:
                        break
                if isinstance(v, (int, float)):
                    out.append(float(v))
            return out

        def median(xs: list[float]) -> float:
            return statistics.median(xs) if xs else 0.0

        tick_p95 = col(["histograms_ms", "tick_total_ms", "p95"])
        physics_p95 = col(["histograms_ms", "phys.physics_total_ms", "p95"])
        if not physics_p95:
            physics_p95 = col(["histograms_ms", "phys.parallel_total_ms", "p95"])
        broadcast_p95 = col(["histograms_ms", "ws.serialize_ms", "p95"])
        active = col(["derived", "vehicles_active"])
        bytes_per_s = col(["derived", "ws_bytes_per_second"])

        n_active_med = median(active)
        physics_med = median(physics_p95)
        physics_us_per_veh = (
            physics_med * 1000.0 / max(n_active_med, 1.0)
            if n_active_med > 0 else 0.0
        )

        return {
            "n_target": self.n_target,
            "samples": len(self.samples),
            "n_active_p50": round(n_active_med, 1),
            "tick_ms_p95": round(median(tick_p95), 2),
            "physics_ms_p95": round(physics_med, 2),
            "broadcast_ms_p95": round(median(broadcast_p95), 2),
            "ws_bytes_per_s": round(median(bytes_per_s), 0),
            "physics_us_per_vehicle": round(physics_us_per_veh, 2),
        }


def run_stage(base: str, n: int, duration_s: float, warmup_s: float, sample_every_s: float) -> StageResult:
    print(f"[stage N={n}] start", flush=True)
    _post(f"{base}/api/simulation/start")

    print(f"[stage N={n}] spawn {n} vehicles", flush=True)
    _post(f"{base}/api/simulation/vehicles/spawn", {"count": n})

    # Espera a que el spawner ponga al menos el 80% en régimen activo o agote
    # warmup_s. Evita que las muestras midan el transitorio del A*.
    t_warm_start = time.monotonic()
    while time.monotonic() - t_warm_start < warmup_s:
        try:
            status = _get(f"{base}/api/simulation/status")
            if status.get("vehicles_active", 0) >= int(0.8 * n):
                break
        except Exception:
            pass
        time.sleep(2.0)
    print(
        f"[stage N={n}] warmup done after {time.monotonic() - t_warm_start:.0f}s, "
        f"sampling for {duration_s:.0f}s",
        flush=True,
    )

    result = StageResult(n_target=n)
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        try:
            snap = _get(f"{base}/api/simulation/metrics")
            result.add(snap)
            print(
                f"  [tick={snap['derived'].get('tick_count')}] "
                f"active={snap['derived'].get('vehicles_active')} "
                f"tick_p95={snap['histograms_ms'].get('tick_total_ms', {}).get('p95', 0):.1f}ms "
                f"phys_p95={snap['histograms_ms'].get('phys.physics_total_ms', {}).get('p95', 0):.1f}ms",
                flush=True,
            )
        except Exception as e:
            print(f"  [warn] fetch failed: {e}", flush=True)
        time.sleep(sample_every_s)

    print(f"[stage N={n}] stop", flush=True)
    try:
        _post(f"{base}/api/simulation/stop")
    except Exception:
        pass
    # Pequeña pausa para que el backend libere recursos antes del siguiente
    # stage (el ProcessPoolExecutor se reutiliza, pero los vehículos colgados
    # se limpian al inicio del próximo start).
    time.sleep(2.0)
    return result


def print_table(summaries: list[dict]) -> None:
    cols = [
        ("N",            lambda s: f"{s['n_target']:>5}"),
        ("active",       lambda s: f"{s.get('n_active_p50', 0):>6.0f}"),
        ("tick_p95",     lambda s: f"{s.get('tick_ms_p95', 0):>8.1f} ms"),
        ("physics_p95",  lambda s: f"{s.get('physics_ms_p95', 0):>8.1f} ms"),
        ("bcast_p95",    lambda s: f"{s.get('broadcast_ms_p95', 0):>7.1f} ms"),
        ("bytes/s",      lambda s: f"{s.get('ws_bytes_per_s', 0):>10.0f}"),
        ("us/veh",       lambda s: f"{s.get('physics_us_per_vehicle', 0):>7.2f}"),
    ]
    headers = "  ".join(name.ljust(11) for name, _ in cols)
    print()
    print(headers)
    print("-" * len(headers))
    prev_us_veh: float | None = None
    for s in summaries:
        row = "  ".join(getter(s).ljust(11) for _, getter in cols)
        suffix = ""
        cur = s.get("physics_us_per_vehicle", 0.0) or 0.0
        if prev_us_veh is not None and prev_us_veh > 0:
            ratio = cur / prev_us_veh
            suffix = f"  Δ_per_veh={ratio:.2f}×"
            if ratio > 1.3:
                suffix += "  ⚠ posible O(n²)"
        prev_us_veh = cur
        print(row + suffix)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--vehicles", default="500,1000,2000,4000",
                    help="Lista separada por comas de tamaños de flota a probar.")
    ap.add_argument("--duration-s", type=float, default=300.0,
                    help="Duración del muestreo por stage (s). Default 300.")
    ap.add_argument("--warmup-s", type=float, default=30.0,
                    help="Tiempo máximo de espera al spawn antes de muestrear. Default 30.")
    ap.add_argument("--sample-every-s", type=float, default=10.0,
                    help="Intervalo entre samples /metrics dentro del stage. Default 10.")
    ap.add_argument("--metrics-out", type=Path, default=None,
                    help="Si se da, vuelca el JSON crudo de samples + summary aquí.")
    args = ap.parse_args()

    sizes = [int(x.strip()) for x in args.vehicles.split(",") if x.strip()]
    base = args.url.rstrip("/")

    print(f"Load test against {base}: sizes={sizes}, duration={args.duration_s}s, warmup={args.warmup_s}s")

    all_results: list[StageResult] = []
    summaries: list[dict] = []
    try:
        for n in sizes:
            result = run_stage(
                base=base,
                n=n,
                duration_s=args.duration_s,
                warmup_s=args.warmup_s,
                sample_every_s=args.sample_every_s,
            )
            all_results.append(result)
            summaries.append(result.summary())
            print_table(summaries)
    except KeyboardInterrupt:
        print("Interrumpido — devolviendo lo medido hasta ahora.")

    if args.metrics_out:
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "config": {
                "url": base,
                "sizes": sizes,
                "duration_s": args.duration_s,
                "warmup_s": args.warmup_s,
                "sample_every_s": args.sample_every_s,
                "started_at_unix": int(time.time()),
            },
            "stages": [
                {"n_target": r.n_target, "samples": r.samples} for r in all_results
            ],
            "summary": summaries,
        }
        args.metrics_out.write_text(json.dumps(out, indent=2))
        print(f"Métricas crudas escritas en {args.metrics_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

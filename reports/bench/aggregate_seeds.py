#!/usr/bin/env python3
"""Agrega los JSON de bench_cases_live por semilla: media ± sd por métrica y
fase, deltas pareados (escenario − base por semilla) y viajes agrupados."""
import json
import statistics
import sys
from pathlib import Path

SEEDS = [42, 43, 44, 45, 46]
METRICS = [
    "fleet_speed_ms_mean",
    "saturated_edges_peak",
    "congestion_max_peak",
    "vehicles_affected_peak",
    "trips_completed",
    "travel_time_mean_s",
    "travel_time_p50_s",
]


def fmt(xs):
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return f"{m:8.3f} ± {sd:6.3f}"


def main():
    base = Path(__file__).parent
    runs = {}
    for s in SEEDS:
        p = base / f"cases_live_seed{s}.json"
        if p.exists():
            runs[s] = json.load(open(p))
    print(f"semillas disponibles: {sorted(runs)}")
    for case in ("zbe", "flood"):
        print(f"\n================ CASO {case.upper()} ================")
        for phase in ("base", "scenario"):
            print(f"--- {phase} ---")
            for m in METRICS:
                xs = [runs[s]["cases"][case][phase][m] for s in runs
                      if runs[s]["cases"].get(case, {}).get(phase)]
                if xs:
                    print(f"  {m:28s} {fmt(xs)}   {[round(x,2) for x in xs]}")
        print("--- delta pareado (esc − base por semilla) ---")
        for m in METRICS:
            ds = []
            for s in runs:
                c = runs[s]["cases"].get(case, {})
                if c.get("base") and c.get("scenario"):
                    ds.append(c["scenario"][m] - c["base"][m])
            if ds:
                print(f"  {m:28s} {fmt(ds)}   {[round(d,2) for d in ds]}")
        # viajes agrupados entre semillas (media ponderada por n)
        for phase in ("base", "scenario"):
            n_tot, tt_w = 0, 0.0
            for s in runs:
                ph = runs[s]["cases"].get(case, {}).get(phase)
                if ph and ph["trips_completed"]:
                    n_tot += ph["trips_completed"]
                    tt_w += ph["trips_completed"] * ph["travel_time_mean_s"]
            if n_tot:
                print(f"  viajes agrupados {phase:9s}: n={n_tot:4d}  tt_media={tt_w/n_tot:7.1f}s")
            else:
                print(f"  viajes agrupados {phase:9s}: n=0")


if __name__ == "__main__":
    sys.exit(main())

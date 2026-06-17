#!/usr/bin/env python3
"""Fusiona los bloques perf_*.json y produce un resumen + análisis de paralelismo."""
import json, glob, sys
from pathlib import Path

base = Path("reports/bench")
blocks = sorted(base.glob("perf_[a-d].json"))
meta = None
stages = []
for b in blocks:
    d = json.load(open(b))
    meta = d["meta"]
    stages.extend(d["stages"])
stages.sort(key=lambda s: s["n_target"])

WORKER_SUBS = ["phys.idm_ms", "phys.leader_find_ms", "phys.mobil_ms",
               "phys.sign_check_ms", "phys.round_yield_ms",
               "phys.intersection_yield_ms", "phys.collision_check_ms"]

def g(h, k, p="p50"):
    return h.get(k, {}).get(p, 0.0)

rows = []
for s in stages:
    h = s["histograms_ms"]
    N = s["active_median"]
    tick = h.get("tick_total_ms", {})
    phys = h.get("physics_total_ms", {})
    wc = g(h, "phys.idm_workers_wallclock_ms")
    cpu_sum = sum(g(h, k) for k in WORKER_SUBS)
    par = (cpu_sum / wc) if wc > 0 else 0.0
    ticks = s["ticks_measured"]
    slow = s["counters"].get("slow_tick_count", 0)
    bpt = g(h, "ws.bytes_per_tick")
    row = {
        "N": int(round(N)),
        "tick_p50": tick.get("p50", 0), "tick_p95": tick.get("p95", 0), "tick_p99": tick.get("p99", 0),
        "phys_p50": phys.get("p50", 0), "phys_p95": phys.get("p95", 0),
        "idm_p50": g(h, "phys.idm_ms"),
        "leader_p50": g(h, "phys.leader_find_ms"),
        "mobil_p50": g(h, "phys.mobil_ms"),
        "workers_wc_p50": wc, "cpu_sum_p50": cpu_sum, "parallelism": par,
        "serialize_p50": g(h, "ws.serialize_ms"), "serialize_p95": g(h, "ws.serialize_ms", "p95"),
        "bytes_per_tick": bpt, "bytes_per_veh": bpt / N if N else 0,
        "eff_hz": s["effective_tick_rate_hz"],
        "slow_frac": slow / ticks if ticks else 0,
        "path": s["gauges"].get("phys.path"),
        "n_buckets": s["gauges"].get("phys.n_buckets"),
        "n_workers": s["gauges"].get("phys.n_workers"),
    }
    rows.append(row)

print("META:", json.dumps(meta))
print()
hdr = ("  N   path tick_p50 tick_p95 tick_p99  phys_p50  idm_p50 wrk_wc cpu_sum  par×  "
       "ser_p50 bytes/tk B/veh  eff_hz slow%")
print(hdr)
print("-" * len(hdr))
for r in rows:
    print(f"{r['N']:>5} {str(r['path']):>4} "
          f"{r['tick_p50']:8.1f} {r['tick_p95']:8.1f} {r['tick_p99']:8.1f} "
          f"{r['phys_p50']:9.1f} {r['idm_p50']:8.2f} {r['workers_wc_p50']:6.1f} {r['cpu_sum_p50']:7.1f} "
          f"{r['parallelism']:5.2f} {r['serialize_p50']:7.2f} {r['bytes_per_tick']:8.0f} "
          f"{r['bytes_per_veh']:5.1f} {r['eff_hz']:6.2f} {100*r['slow_frac']:4.0f}")

# Guardar consolidado
out = {"meta": meta, "stages": stages, "summary_rows": rows}
Path("reports/bench/perf_3hz.json").write_text(json.dumps(out, indent=2))
print("\n-> reports/bench/perf_3hz.json escrito con", len(stages), "stages")

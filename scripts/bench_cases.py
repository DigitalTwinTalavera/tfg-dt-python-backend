#!/usr/bin/env python3
"""
Casos de estudio what-if (datos agregados reales, sobre el A*/zonas del sistema).

CASO 1 - ZBE: zona de bajas emisiones (polígono central ~0.6 km²) que
restringe `truck` con enforcement force_reroute. Se generan M pares O-D y se
compara, para CADA par, la ruta sin restricción (lo que hace un coche) con la
ruta del camión bajo la ZBE. Métricas: % de rutas que cruzan el casco, cuántas
evita el camión, y el rodeo medio.

CASO 2 - INUNDACIÓN: se cortan las K aristas centrales más transitadas
(arterias) y se mide cuántas rutas quedan afectadas, cuántas se reencaminan con
éxito y el rodeo medio.

Determinista (seed fija). Usa compute_route real con restricted_edges /
blocked_edges, exactamente como el motor.
"""
from __future__ import annotations
import asyncio, json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import logging; logging.disable(logging.WARNING)

import app.api.deps as deps
from app.db.database import async_session_factory, engine
from app.core.route import compute_route
from app.core.constants import ATTR_LONGITUDE, ATTR_LATITUDE
from app.models.enums import ZoneType, ZoneEnforcement
from sqlalchemy import text

random.seed(20260605)
M = 3000          # pares O-D de muestra
K_FLOOD = 6       # arterias cortadas


def edges_of(route):
    return [(route.node_path[i], route.node_path[i + 1]) for i in range(len(route.node_path) - 1)]


async def main():
    async with async_session_factory() as s:
        g = await deps._graph.build_from_database(s)
    graph = deps._graph
    spawner = deps.get_vehicle_spawner()
    zone_mgr = deps.get_zone_manager()

    # nodos navegables (SCC) para O-D con ruta garantizada
    entries = spawner.get_entry_nodes(); exits = spawner.get_exit_nodes()
    entries, exits = spawner._filter_to_scc(entries, exits)
    entries, exits = spawner._filter_out_roundabout_nodes(entries, exits)
    print(f"grafo {g.node_count} nodos / {g.edge_count} aristas; entries={len(entries)} exits={len(exits)}", flush=True)

    G = graph.graph
    lons = [G.nodes[n].get(ATTR_LONGITUDE) for n in G.nodes()]
    lats = [G.nodes[n].get(ATTR_LATITUDE) for n in G.nodes()]
    cx = sum(lons) / len(lons); cy = sum(lats) / len(lats)

    # ---- limpiar zonas previas ----
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM dt_zones"))
        await s.commit()
    await zone_mgr.load_from_db()

    # polígono central ~0.6 km² (half 0.00457 lon, 0.00351 lat alrededor del centroide)
    dlon, dlat = 0.00457, 0.00351
    x0, x1, y0, y1 = cx - dlon, cx + dlon, cy - dlat, cy + dlat
    wkt = (f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))")
    area_km2 = (2 * dlon * 85.3) * (2 * dlat * 111.32)

    zone = await zone_mgr.create(
        name="Casco Histórico (ZBE prueba)", zone_type=ZoneType.ZBE,
        geometry_wkt=wkt, restricted_vtypes=["truck"],
        enforcement=ZoneEnforcement.FORCE_REROUTE, active=True,
    )
    Z = zone_mgr.restricted_edges_by_vtype().get("truck", set())
    print(f"ZBE polígono ~{area_km2:.3f} km², aristas dentro restringidas a truck: {len(Z)}", flush=True)

    # ---- muestrear O-D y comparar ----
    pairs = [(random.choice(entries), random.choice(exits)) for _ in range(M)]
    base_cross = 0; truck_cross = 0; rerouted = 0; trapped = 0
    detours = []; base_lens = []; both_ok = 0
    edge_use = {}  # para el caso inundación: aristas más usadas
    for (a, b) in pairs:
        if a == b:
            continue
        rb = compute_route(graph, a, b)                       # coche / sin restricción
        if rb is None:
            continue
        base_lens.append(rb.length_m)
        eb = edges_of(rb)
        for e in eb:
            edge_use[e] = edge_use.get(e, 0) + 1
        bcross = any(e in Z for e in eb)
        base_cross += bcross
        rt = compute_route(graph, a, b, restricted_edges=Z)   # camión bajo ZBE
        if rt is None:
            continue
        both_ok += 1
        tcross = any(e in Z for e in edges_of(rt))
        truck_cross += tcross
        if bcross:
            if not tcross:
                rerouted += 1
                detours.append(rt.length_m - rb.length_m)
            else:
                trapped += 1  # no hay alternativa: A* penaliza pero no elimina

    import statistics as st
    zbe = {
        "M": M, "rutas_validas": both_ok,
        "area_km2": round(area_km2, 3), "aristas_zona": len(Z),
        "pct_base_cruzan_casco": round(100 * base_cross / both_ok, 1),
        "pct_truck_cruzan_casco": round(100 * truck_cross / both_ok, 1),
        "trucks_reencaminados": rerouted,
        "trucks_sin_alternativa": trapped,
        "pct_evitan_de_los_que_cruzaban": round(100 * rerouted / max(base_cross, 1), 1),
        "rodeo_medio_m": round(st.mean(detours), 1) if detours else 0,
        "rodeo_mediana_m": round(st.median(detours), 1) if detours else 0,
        "long_media_base_m": round(st.mean(base_lens), 1) if base_lens else 0,
        "rodeo_pct": round(100 * st.mean(detours) / st.mean(base_lens), 1) if detours and base_lens else 0,
    }
    print("ZBE:", json.dumps(zbe, ensure_ascii=False), flush=True)

    # ---- limpiar zona ----
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM dt_zones"))
        await s.commit()

    # =================== CASO 2: INUNDACIÓN ===================
    # Arterias = K aristas más usadas por las rutas base (las que más tráfico
    # soportan). Cortarlas (blocked_edges) simula calles anegadas.
    top = sorted(edge_use.items(), key=lambda kv: kv[1], reverse=True)[:K_FLOOD]
    B = {e for e, _ in top}
    blocked = {e: None for e in B}
    flow_base = sum(c for _, c in top)
    affected = 0; reroute_ok = 0; reroute_fail = 0
    fdetours = []; still_uses = 0
    for (a, b) in pairs:
        if a == b:
            continue
        rb = compute_route(graph, a, b)
        if rb is None:
            continue
        eb = edges_of(rb)
        if not any(e in B for e in eb):
            continue
        affected += 1
        rr = compute_route(graph, a, b, blocked_edges=blocked)
        if rr is None:
            reroute_fail += 1
            continue
        reroute_ok += 1
        if any(e in B for e in edges_of(rr)):
            still_uses += 1  # sin alternativa: penalizado pero usado
        else:
            fdetours.append(rr.length_m - rb.length_m)
    flood = {
        "M": M, "arterias_cortadas": K_FLOOD,
        "flujo_base_en_arterias_rutas": flow_base,
        "rutas_afectadas": affected,
        "pct_afectadas": round(100 * affected / both_ok, 1),
        "reencaminadas_ok": reroute_ok,
        "sin_ruta_alternativa": reroute_fail,
        "siguen_usando_corte_penalizado": still_uses,
        "rodeo_medio_m": round(st.mean(fdetours), 1) if fdetours else 0,
        "rodeo_mediana_m": round(st.median(fdetours), 1) if fdetours else 0,
        "rodeo_pct": round(100 * st.mean(fdetours) / st.mean(base_lens), 1) if fdetours and base_lens else 0,
    }
    print("FLOOD:", json.dumps(flood, ensure_ascii=False), flush=True)

    out = {"centroid": [round(cx, 5), round(cy, 5)], "zbe": zbe, "flood": flood,
           "graph": {"nodes": g.node_count, "edges": g.edge_count}}
    Path("reports/bench/cases.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print("-> reports/bench/cases.json", flush=True)
    await engine.dispose()

asyncio.run(main())

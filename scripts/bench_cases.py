#!/usr/bin/env python3
"""
Casos de estudio what-if (datos agregados reales, sobre el A*/zonas del sistema).

CASO 1 - ZBE: zona de bajas emisiones con el perímetro REAL del Casco Histórico
de Talavera (polígono dibujado en el cliente, ~0.44 km²) que restringe `car` y
`truck` con enforcement force_reroute; solo las motos pueden cruzar. Se generan M
pares O-D y se compara, para CADA par, la ruta sin restricción (red libre, lo que
hace una moto) con la ruta del tráfico general (coche/camión) bajo la ZBE.
Métricas: % de rutas que cruzan el casco, cuántas evita el tráfico restringido y
el rodeo medio.

CASO 2 - INUNDACIÓN: se cortan las calles REALES anegadas por la crecida del Tajo
(cada calle definida por un tramo entrada→salida en lat/lon; el tramo se engancha
a la red por nodo más cercano + camino mínimo) y se mide cuántas rutas quedan
afectadas, cuántas se reencaminan con éxito y el rodeo medio.

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

# Perímetro REAL de la ZBE (Casco Histórico) dibujado en el cliente. WKT en
# lon/lat (EPSG:4326), tal cual lo almacena PostGIS.
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
    # edge_id (DB) -> aristas dirigidas (u,v) del grafo (1 o 2 sentidos)
    eid2uv: dict[int, list[tuple[int, int]]] = {}
    for u, v, data in G.edges(data=True):
        eid2uv.setdefault(int(data.get("edge_id", 0)), []).append((u, v))

    # ---- limpiar zonas previas ----
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM dt_zones"))
        await s.commit()
    await zone_mgr.load_from_db()

    # =================== CASO 1: ZBE (perímetro real) ===================
    zone = await zone_mgr.create(
        name="ZBE Casco Histórico (Talavera)", zone_type=ZoneType.ZBE,
        geometry_wkt=ZBE_WKT, restricted_vtypes=ZBE_RESTRICTED,
        enforcement=ZoneEnforcement.FORCE_REROUTE, active=True,
    )
    # área real de la geometría vía PostGIS (geografía → m²)
    async with async_session_factory() as s:
        area_km2 = float((await s.execute(text(
            "SELECT ST_Area(geometry::geography)/1e6 FROM dt_zones WHERE id=:i"
        ), {"i": zone.id})).scalar_one())
    Z = zone_mgr.restricted_edges_by_vtype().get(ZBE_RESTRICTED[0], set())
    print(f"ZBE perímetro real ~{area_km2:.3f} km², aristas restringidas (car+truck): {len(Z)}", flush=True)

    # ---- muestrear O-D y comparar ----
    pairs = [(random.choice(entries), random.choice(exits)) for _ in range(M)]
    base_cross = 0; rest_cross = 0; rerouted = 0; trapped = 0
    detours = []; base_lens = []; both_ok = 0
    for (a, b) in pairs:
        if a == b:
            continue
        rb = compute_route(graph, a, b)                       # red libre (moto)
        if rb is None:
            continue
        base_lens.append(rb.length_m)
        eb = edges_of(rb)
        bcross = any(e in Z for e in eb)
        base_cross += bcross
        rr = compute_route(graph, a, b, restricted_edges=Z)   # coche/camión bajo ZBE
        if rr is None:
            continue
        both_ok += 1
        tcross = any(e in Z for e in edges_of(rr))
        rest_cross += tcross
        if bcross:
            if not tcross:
                rerouted += 1
                detours.append(rr.length_m - rb.length_m)
            else:
                trapped += 1  # origen/destino dentro: A* penaliza pero no elimina

    import statistics as st
    zbe = {
        "M": M, "rutas_validas": both_ok,
        "area_km2": round(area_km2, 3), "aristas_zona": len(Z),
        "restringidos": ZBE_RESTRICTED,
        "pct_base_cruzan_casco": round(100 * base_cross / both_ok, 1),
        "pct_restringidos_cruzan_casco": round(100 * rest_cross / both_ok, 1),
        "reencaminados": rerouted,
        "sin_alternativa": trapped,
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

    # =================== CASO 2: INUNDACIÓN (calles reales) ===================
    # Cada calle (tramo entrada→salida) se map-matchea a la red seleccionando en
    # PostGIS las aristas cuya geometría discurre a menos de FLOOD_DWITHIN_M del
    # tramo; esas aristas se cortan en ambos sentidos (blocked_edges), simulando
    # la calle anegada. Mismo enfoque de spatial-join que usa la ZBE.
    FLOOD_DWITHIN_M = 20
    B = set(); ids_cut = set(); matched = []
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
            B.update(uvs); ids_cut.update(ids)
            matched.append((name, len(ids)))
    blocked = {e: None for e in B}
    for name, ne in matched:
        print(f"  corte: {name:28s} -> {ne} aristas", flush=True)
    print(f"  TOTAL aristas dirigidas cortadas: {len(B)} (D={FLOOD_DWITHIN_M} m)", flush=True)

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
        "M": M, "calles_cortadas": len(FLOOD_STREETS),
        "aristas_cortadas": len(ids_cut), "dwithin_m": FLOOD_DWITHIN_M,
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

    out = {"zbe": zbe, "flood": flood, "calles": matched,
           "graph": {"nodes": g.node_count, "edges": g.edge_count}}
    Path("reports/bench/cases.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print("-> reports/bench/cases.json", flush=True)
    await engine.dispose()

asyncio.run(main())

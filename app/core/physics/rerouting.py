"""
Reruteo dinámico de vehículos.

Funciones que recalculan la ruta de los vehículos MOVING cuando aparecen
bloqueos (cierres, ZBE, colisiones) o de forma periódica. La política se
divide en tres niveles:

1. **Inmediato** — `_reroute_affected_by_new_blocks`: tras procesar bloqueos
   nuevos en el tick, recorre todos los MOVING y re-rutea a los que tocan
   alguna arista recién bloqueada.
2. **Urgente** — durante `URGENT_REROUTE_TTL_TICKS` ticks tras un bloqueo
   nuevo, `_periodic_reroute_batch` usa `URGENT_REROUTE_BATCH_SIZE` para
   cubrir la flota más rápido.
3. **Periódico** — cada tick `_periodic_reroute_batch` revisa
   `PERIODIC_REROUTE_BATCH_SIZE` vehículos arrancando desde un cursor
   rotatorio. Cobertura completa cada `ceil(N / batch)` ticks.

Todas las funciones respetan el cap `PERIODIC_REROUTE_ASTAR_CAP_PER_TICK`
para evitar spikes de tick en activaciones de ZBE que afecten a cientos
de vehículos.
"""

from __future__ import annotations

import logging

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LENGTH,
    PERIODIC_REROUTE_ASTAR_CAP_PER_TICK,
    PERIODIC_REROUTE_BATCH_SIZE,
    URGENT_REROUTE_BATCH_SIZE,
    URGENT_REROUTE_TTL_TICKS,
)
from app.models.enums import VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle

logger = logging.getLogger(__name__)


## Tick del último bloqueo nuevo detectado. Mientras `tick_count - _last_new_block_tick`
## sea menor que URGENT_REROUTE_TTL_TICKS, el batch periódico usa el tamaño urgente.
## Inicializado a un valor muy negativo para que la primera comprobación falle.
_last_new_block_tick: int = -10_000_000


def _mark_new_blocks_detected(tick_count: int) -> None:
    """Activa la ventana de reroute urgente durante URGENT_REROUTE_TTL_TICKS."""
    global _last_new_block_tick
    _last_new_block_tick = tick_count


def _maybe_reroute_around_blocks(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    blocked_edges: dict[tuple[int, int], object | None],
    trigger_blocks: set[tuple[int, int]] | None = None,
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
    astar_budget: list[int] | None = None,
) -> bool:
    """
    Re-rutea un vehículo individual si su ruta pendiente toca alguna arista
    de ``trigger_blocks`` (subconjunto relevante — típicamente bloques recién
    creados o el conjunto total). Si se pasa ``None`` se usa ``blocked_edges``
    completo (modo periódico / dead-wall).

    La arista CURRENT (ei → ei+1) NO se re-rutea: el vehículo ya está sobre
    ella y comprometido a su geometría. Se re-planifica desde NEXT node
    (``node_path[ei+1]``) hacia el destino; se mantiene progreso, carril y
    prefijo [0..ei].

    Si A* devuelve una ruta que aún contiene algún bloqueo conocido (no hay
    alternativa real), no se muta: seguir con el camino original penalizado
    es equivalente y evita churn.

    ``restricted_edges_by_vtype`` (vtype_str → set[(u,v)]) se inyecta desde el
    ``ZoneManager``. Si el vtype del vehículo tiene restricciones, se pasan a
    A* para que rodee la zona, y se descartan rutas que metan al vehículo en
    una zona donde su ruta original no entraba (mejora estricta).

    Returns:
        True si el vehículo fue re-ruteado; False en cualquier otro caso.
    """
    if vehicle.status != VehicleStatus.MOVING:
        return False
    np_ = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(np_) - 1:
        return False

    # Resolución temprana del set restringido del vtype: necesario tanto para
    # validar el reroute como para AMPLIAR el trigger de detección. Sin esto,
    # un vehículo con ruta pre-existente que cruza una ZBE jamás dispara
    # reroute (bloqueos ≠ ZBE) y el cliente lo ve "ignorando" la zona.
    restricted: set[tuple[int, int]] | None = None
    if restricted_edges_by_vtype is not None:
        vt = getattr(vehicle.vtype, "value", None)
        if vt is not None:
            r = restricted_edges_by_vtype.get(vt)
            if r:
                restricted = r

    # check_set incluye los bloqueos (cierres/colisiones) Y las restricciones
    # ZBE del vtype del vehículo. Si la ruta pendiente toca cualquiera, vale
    # la pena recalcular.
    if trigger_blocks is not None:
        check_set: set[tuple[int, int]] = set(trigger_blocks)
    else:
        check_set = set(blocked_edges.keys())
    if restricted:
        check_set = check_set | restricted
    if not check_set:
        return False

    # ¿Alguna arista PENDIENTE (a partir de ei+1) toca el conjunto de trigger?
    hit = False
    for i in range(ei + 1, len(np_) - 1):
        if (np_[i], np_[i + 1]) in check_set:
            hit = True
            break
    if not hit:
        return False

    # Cap de A* por tick: si el batch ya consumió su presupuesto, no llamar a
    # compute_route. El vehículo será revisado en el siguiente tick por el
    # cursor rotatorio. Sin esto, una activación de ZBE que afecte a muchos
    # vehículos genera spikes de tick (>200 ms) y tirones visibles.
    if astar_budget is not None and astar_budget[0] <= 0:
        return False

    from app.core.route import RouteInfo, compute_route

    pivot_node = np_[ei + 1]
    end_node = vehicle.route.end_node_id

    if astar_budget is not None:
        astar_budget[0] -= 1

    new_route = compute_route(
        graph,
        pivot_node,
        end_node,
        blocked_edges=blocked_edges,
        restricted_edges=restricted,
    )
    if new_route is None or len(new_route.node_path) < 2:
        return False

    nnp = new_route.node_path
    # Si la nueva ruta sigue atravesando un bloqueo conocido, no aporta.
    blocked_set = set(blocked_edges.keys())
    if any((nnp[i], nnp[i + 1]) in blocked_set for i in range(len(nnp) - 1)):
        return False
    # Si la nueva ruta introduce un cruce de zona restringida que la vieja
    # NO tenía, descartar (no empeorar). Si ambas cruzan, aceptar el reroute
    # — A* eligió el menos malo dada la penalización ZBE_EDGE_PENALTY_FACTOR.
    if restricted:
        new_hits = any(
            (nnp[i], nnp[i + 1]) in restricted for i in range(len(nnp) - 1)
        )
        if new_hits:
            old_hits = any(
                (np_[i], np_[i + 1]) in restricted
                for i in range(ei + 1, len(np_) - 1)
            )
            if not old_hits:
                return False

    # Concatenar prefijo [0..ei] + nueva ruta (que empieza en pivot=np_[ei+1]).
    prefix = np_[: ei + 1]
    combined = list(prefix) + list(nnp)

    # Recalcular edge_ids y length_m del path completo.
    total_len = 0.0
    edge_ids: list[int] = []
    for i in range(len(combined) - 1):
        ea = graph.get_edge_attributes(combined[i], combined[i + 1])
        eid = ea.get(ATTR_EDGE_ID)
        if eid is not None:
            edge_ids.append(eid)
        total_len += ea.get(ATTR_LENGTH, 0.0)

    vehicle.route = RouteInfo(
        start_node_id=vehicle.route.start_node_id,
        end_node_id=end_node,
        node_path=combined,
        edge_ids=edge_ids,
        length_m=total_len,
    )
    return True


def _reroute_affected_by_new_blocks(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    new_blocks: set[tuple[int, int]],
    blocked_edges: dict[tuple[int, int], object | None],
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> int:
    """
    Re-rutea a todos los vehículos MOVING cuya cola de ruta pase por alguna
    arista recién bloqueada. Se ejecuta una vez por tick tras procesar todas
    las colisiones nuevas.

    Returns:
        Número de vehículos re-ruteados.
    """
    if not new_blocks:
        return 0
    # Cap también este pase: con un cierre que afecte a 500+ vehículos, sin
    # cap el A* fan-out colapsa el tick. Los no servidos los recoge el
    # periodic batch en los siguientes ticks.
    astar_budget = [PERIODIC_REROUTE_ASTAR_CAP_PER_TICK]
    rerouted = 0
    for vehicle in vehicles.values():
        if astar_budget[0] <= 0:
            break
        if _maybe_reroute_around_blocks(
            vehicle,
            graph,
            blocked_edges,
            trigger_blocks=new_blocks,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
            astar_budget=astar_budget,
        ):
            rerouted += 1
    return rerouted


def _periodic_reroute_all(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    blocked_edges: dict[tuple[int, int], object | None],
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> int:
    """
    Plan D1 — reroute proactivo. Recorre TODOS los MOVING y re-planifica a los
    que siguen enrutados por aristas bloqueadas. Se llama periódicamente (ver
    ``PERIODIC_REROUTE_TICK_INTERVAL``). Idempotente: si una ruta ya es limpia,
    `_maybe_reroute_around_blocks` sale sin mutar.

    Returns:
        Número de vehículos re-ruteados en esta pasada.
    """
    if not blocked_edges:
        return 0
    blocked_set = set(blocked_edges.keys())
    rerouted = 0
    for vehicle in vehicles.values():
        if _maybe_reroute_around_blocks(
            vehicle,
            graph,
            blocked_edges,
            trigger_blocks=blocked_set,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
        ):
            rerouted += 1
    return rerouted


def _periodic_reroute_batch(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
    blocked_edges: dict[tuple[int, int], object | None],
    tick_count: int,
    restricted_edges_by_vtype: dict[str, set[tuple[int, int]]] | None = None,
) -> int:
    """
    Versión amortizada de ``_periodic_reroute_all``. En lugar de revisar los N
    vehículos en un único tick (→ picos de 1-1.5 s con 3500+ vehículos), cada
    tick procesa ``PERIODIC_REROUTE_BATCH_SIZE`` vehículos arrancando desde un
    cursor rotatorio derivado del ``tick_count``. Cada vehículo es visitado
    cada ``ceil(N / batch)`` ticks, cobertura idéntica a la versión all-in-one
    pero con latencia constante por tick (≤ 5-10 ms típicamente).

    Durante la ventana urgente posterior a un nuevo bloqueo (TTL en ticks,
    ver ``URGENT_REROUTE_TTL_TICKS``) se usa ``URGENT_REROUTE_BATCH_SIZE``
    para cubrir la flota más rápido — clave con 1500+ vehículos donde la
    cobertura normal tarda 30 ticks (3 s).

    El reroute inmediato cuando aparece un nuevo bloqueo se mantiene vía
    ``_reroute_affected_by_new_blocks`` — esto es el safety net para los
    vehículos que esa pasada no atrapó.

    También dispara cuando hay restricciones ZBE activas aunque no haya
    aristas bloqueadas: vehículos con ruta pre-existente que cruza una zona
    recién creada deben ser rerouteados periódicamente (el one-shot de
    `_reroute_live_vehicles` está capado a 500 → con flotas grandes el
    resto se queda con la ruta vieja sin esta cobertura).
    """
    if PERIODIC_REROUTE_BATCH_SIZE <= 0:
        return 0
    has_zbe = bool(restricted_edges_by_vtype) and any(
        bool(s) for s in restricted_edges_by_vtype.values()
    )
    if not blocked_edges and not has_zbe:
        return 0
    # snapshot del orden: dict.values() en Python 3.7+ es orden de inserción,
    # estable mientras no haya inserciones/borrados dentro del batch.
    vehicles_list = list(vehicles.values())
    n = len(vehicles_list)
    if n == 0:
        return 0
    is_urgent = (tick_count - _last_new_block_tick) < URGENT_REROUTE_TTL_TICKS
    effective_batch = URGENT_REROUTE_BATCH_SIZE if is_urgent else PERIODIC_REROUTE_BATCH_SIZE
    batch_size = min(effective_batch, n)
    start = (tick_count * batch_size) % n
    blocked_set = set(blocked_edges.keys()) if blocked_edges else set()
    # Presupuesto de A* (compute_route) por tick. Lista de un int para mutar
    # por referencia desde la función auxiliar. Vehículos con hit pero sin
    # presupuesto vuelven a ser candidatos en el siguiente tick.
    astar_budget = [PERIODIC_REROUTE_ASTAR_CAP_PER_TICK]
    rerouted = 0
    for i in range(batch_size):
        if astar_budget[0] <= 0:
            break  # Presupuesto agotado: parar el batch — el cursor rotatorio
                   # cubrirá el resto en ticks sucesivos.
        idx = start + i
        if idx >= n:
            idx -= n
        # trigger_blocks=None deja que `_maybe_reroute_around_blocks` use
        # blocked_set ∪ restricted_edges del vtype como check_set.
        if _maybe_reroute_around_blocks(
            vehicles_list[idx],
            graph,
            blocked_edges,
            trigger_blocks=blocked_set if blocked_set else None,
            restricted_edges_by_vtype=restricted_edges_by_vtype,
            astar_budget=astar_budget,
        ):
            rerouted += 1
    return rerouted

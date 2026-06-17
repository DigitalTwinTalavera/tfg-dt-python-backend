"""
Arbitración de cruces no señalizados (priority-to-the-right + TTC tiebreak).

Genera un líder virtual estacionado en la línea de stop del cruce cuando el
ego debe ceder ante otro vehículo que también se aproxima al mismo nodo por
otra arista. Se evalúa exclusivamente para nodos:

  - SIN semáforo (TRAFFIC_LIGHT)
  - SIN señal STOP/YIELD (gestionados por `traffic_signs._check_stop_yield_sign`)
  - SIN entrada a rotonda (gestionada por `_find_roundabout_yield_leader`)
  - con grado de entrada ≥ 2 (cruce real, no continuación de calle)

El árbitro aplica, en orden:

  1. **Tiempo a la línea de stop**: si la TTC del contendiente es menor que la
     del ego en más de `INTERSECTION_TTC_PRIORITY_DELTA_S` segundos, el ego
     cede. Si el ego llega claramente antes (mismo delta) ignora a ese
     contendiente.
  2. **Prioridad por la derecha**: con TTCs cercanas, gana el que tiene al otro
     a su izquierda (regla por defecto en España). El bearing relativo se
     calcula a partir del heading de aproximación de cada vehículo.
  3. **Desempate por id**: en simetría perfecta (TTCs iguales y ambos en la
     "izquierda" del otro por error de redondeo o cruces de cuatro brazos
     equilibrados) gana el id menor; el resto cede.

El líder virtual se sitúa al mismo gap que la línea de stop, igual que la
lógica de STOP/YIELD existente, para que el IDM frene de forma natural.
"""

from __future__ import annotations

import math

from app.core.constants import (
    ATTR_IS_ROUNDABOUT,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_NODE_TYPE,
    ATTR_WAYPOINTS,
    INTERSECTION_DETECTION_ZONE_M,
    INTERSECTION_RIGHT_BEARING_MAX_DEG,
    INTERSECTION_RIGHT_BEARING_MIN_DEG,
    INTERSECTION_TTC_PRIORITY_DELTA_S,
    MIN_EDGE_LENGTH_M,
    VEHICLE_LENGTH_M,
)
from app.core.physics.neighbor import NeighborInfo
from app.models.enums import NodeType
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle


# Tipos del árbol de aproximaciones por nodo: (vehicle, dist_to_node, ttc_s,
# approach_heading_deg, edge_key).
ApproachInfo = tuple["SimVehicle", float, float, float, tuple[int, int]]
IntersectionArmIndex = "dict[int, list[ApproachInfo]]"


# Tipos de nodo cuya prioridad ya está gestionada en otra parte y por tanto
# NO deben pasar por el árbitro genérico.
_HANDLED_ELSEWHERE = {
    NodeType.TRAFFIC_LIGHT.value,
    NodeType.STOP_SIGN.value,
    NodeType.YIELD_SIGN.value,
    NodeType.ROUNDABOUT.value,
}


def _node_type_of(graph: RoadNetworkGraph, node_id: int) -> str | None:
    attrs = graph.get_node_attributes(node_id)
    raw = attrs.get(ATTR_NODE_TYPE)
    if raw is None:
        return None
    if isinstance(raw, NodeType):
        return raw.value
    return str(raw)


def _approach_bearing_deg(
    edge_attrs: dict,
    graph: RoadNetworkGraph,
    start_node_id: int,
    end_node_id: int,
) -> float:
    """
    Bearing de aproximación al `end_node` siguiendo la geometría real de la
    arista. Se toma el último segmento de waypoints; si no hay, se usa la
    recta start_node→end_node como fallback.

    Devuelve el bearing en grados (0=Norte, 90=Este, 180=Sur, 270=Oeste),
    consistente con el resto del simulador (ver `_position_along_waypoints`).
    """
    waypoints = edge_attrs.get(ATTR_WAYPOINTS) or []
    if len(waypoints) >= 2:
        lon1, lat1 = waypoints[-2]
        lon2, lat2 = waypoints[-1]
    else:
        s_attrs = graph.get_node_attributes(start_node_id)
        e_attrs = graph.get_node_attributes(end_node_id)
        lon1 = float(s_attrs.get(ATTR_LONGITUDE, 0.0))
        lat1 = float(s_attrs.get(ATTR_LATITUDE, 0.0))
        lon2 = float(e_attrs.get(ATTR_LONGITUDE, 0.0))
        lat2 = float(e_attrs.get(ATTR_LATITUDE, 0.0))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    return math.degrees(math.atan2(dlon, dlat)) % 360.0


def _is_contender_on_right(
    ego_bearing_deg: float,
    contender_bearing_deg: float,
) -> bool:
    """
    ¿Llega el contendiente desde la derecha del ego?

    El contendiente "viene de" la dirección opuesta a su bearing de aproximación
    (si entra al cruce yendo Oeste, viene del Este). Calculamos el bearing
    relativo al ego y comprobamos si cae en la ventana lateral derecha
    (INTERSECTION_RIGHT_BEARING_MIN_DEG, INTERSECTION_RIGHT_BEARING_MAX_DEG).
    """
    arrival_dir = (contender_bearing_deg + 180.0) % 360.0
    relative = (arrival_dir - ego_bearing_deg) % 360.0
    return (
        INTERSECTION_RIGHT_BEARING_MIN_DEG
        < relative
        < INTERSECTION_RIGHT_BEARING_MAX_DEG
    )


def _is_eligible_intersection_node(
    graph: RoadNetworkGraph,
    node_id: int,
) -> bool:
    """
    Un nodo es elegible para arbitraje genérico si:
      - su tipo NO está en `_HANDLED_ELSEWHERE` (TL/STOP/YIELD/ROUNDABOUT);
      - tiene grado de entrada ≥ 2 (cruce real, no simple continuación).

    El cálculo del grado se hace sobre el DiGraph subyacente. Como la red
    es estática durante un tick, se pueden cachear estos resultados; aquí los
    dejamos sin cachear para mantener el módulo simple.
    """
    kind = _node_type_of(graph, node_id)
    if kind in _HANDLED_ELSEWHERE:
        return False
    if graph.graph.in_degree(node_id) < 2:
        return False
    return True


def _build_intersection_arm_index(
    vehicles: dict[str, SimVehicle],
    graph: RoadNetworkGraph,
) -> "IntersectionArmIndex":
    """
    Mapa {end_node_id → [ApproachInfo,...]} con vehículos aproximándose a un
    cruce no señalizado. Se construye una vez por tick en el proceso principal.

    Filtros aplicados:
      - vehículo activo (no FINISHED/COLLISION/PAUSED implícito por el caller)
      - distancia restante a end_node < INTERSECTION_DETECTION_ZONE_M
      - end_node es elegible (`_is_eligible_intersection_node`)
      - la siguiente arista del ego NO es de rotonda (entrada a rotonda
        gestionada por `_find_roundabout_yield_leader`).
    """
    from app.models.enums import VehicleStatus

    arms: "IntersectionArmIndex" = {}
    # Cache local de elegibilidad por nodo (la red es estática durante un tick).
    eligible_cache: dict[int, bool] = {}

    for v in vehicles.values():
        if v.status == VehicleStatus.FINISHED or v.status == VehicleStatus.COLLISION:
            continue
        np_ = v.route.node_path
        ei = v.current_edge_index
        if ei >= len(np_) - 1:
            continue
        start_n = np_[ei]
        end_n = np_[ei + 1]
        cur_attrs = graph.get_edge_attributes(start_n, end_n)
        # No arbitrar dentro del anillo: la circulación tiene su propia lógica.
        if cur_attrs.get(ATTR_IS_ROUNDABOUT):
            continue
        # Si la siguiente arista es de rotonda, lo gestiona el yield específico.
        if ei + 2 <= len(np_) - 1:
            nxt_attrs = graph.get_edge_attributes(np_[ei + 1], np_[ei + 2])
            if nxt_attrs.get(ATTR_IS_ROUNDABOUT):
                continue
        # Distancia hasta el end_node y filtro de zona.
        edge_len = max(float(cur_attrs.get(ATTR_LENGTH, 1.0)), MIN_EDGE_LENGTH_M)
        dist_to_node = (1.0 - v.progress_on_edge) * edge_len
        if dist_to_node > INTERSECTION_DETECTION_ZONE_M:
            continue
        # Elegibilidad del nodo (cachear).
        cached = eligible_cache.get(end_n)
        if cached is None:
            cached = _is_eligible_intersection_node(graph, end_n)
            eligible_cache[end_n] = cached
        if not cached:
            continue
        # TTC con velocidad mínima 1 m/s (mismo criterio que YIELD existente:
        # evita TTCs explosivas con vehículos parados que ya están cediendo).
        ttc = dist_to_node / max(v.velocity, 1.0)
        approach_bearing = _approach_bearing_deg(cur_attrs, graph, start_n, end_n)
        arms.setdefault(end_n, []).append(
            (v, dist_to_node, ttc, approach_bearing, (start_n, end_n))
        )
    return arms


def _check_intersection_yield(
    vehicle: SimVehicle,
    graph: RoadNetworkGraph,
    arm_index: "IntersectionArmIndex",
) -> NeighborInfo | None:
    """
    Devuelve un líder virtual estacionado en la línea de stop si el ego debe
    ceder ante algún vehículo aproximándose al mismo nodo por otra arista.

    El árbol de decisión se aplica contendiente a contendiente: el primero que
    impone ceder devuelve el líder. Si ningún contendiente impone ceder
    devuelve `None` y el ego cruza libremente.
    """
    np_ = vehicle.route.node_path
    ei = vehicle.current_edge_index
    if ei >= len(np_) - 1:
        return None
    end_n = np_[ei + 1]
    contenders = arm_index.get(end_n)
    if not contenders or len(contenders) < 2:
        return None  # ego es el único brazo aproximándose

    # Localiza la entrada del propio ego dentro de los contenders.
    ego_entry: ApproachInfo | None = None
    for entry in contenders:
        if entry[0].id == vehicle.id:
            ego_entry = entry
            break
    if ego_entry is None:
        return None  # ego no estaba en zona (no debería pasar si se llamó tras build)
    _, ego_dist, ego_ttc, ego_bearing, ego_edge_key = ego_entry

    must_yield = False
    for entry in contenders:
        other, _, other_ttc, other_bearing, other_edge_key = entry
        if other.id == vehicle.id:
            continue
        if other_edge_key == ego_edge_key:
            continue  # mismo edge → conflicto rear-end, ya cubierto por _find_leader

        # 1) Prioridad clara por orden de llegada.
        if other_ttc + INTERSECTION_TTC_PRIORITY_DELTA_S < ego_ttc:
            must_yield = True
            break
        if ego_ttc + INTERSECTION_TTC_PRIORITY_DELTA_S < other_ttc:
            continue  # ego claramente antes que este contendiente

        # 2) TTCs cercanos: prioridad por la derecha.
        if _is_contender_on_right(ego_bearing, other_bearing):
            must_yield = True
            break

        # 3) Desempate determinista por id (mantiene el sistema desbloqueable
        # cuando la simetría es perfecta — p.ej. cuatro vehículos llegando a
        # un cruce en aspa con TTC iguales y posiciones angulares idénticas
        # módulo 90°). El de id mayor cede; el menor gana.
        if other.id < vehicle.id:
            must_yield = True
            break

    if not must_yield:
        return None

    # Líder virtual en la línea de stop, con margen para que el frontal del ego
    # quede al borde del cruce. Mismo patrón que STOP_SIGN: gap = dist - L_veh.
    gap = max(ego_dist - VEHICLE_LENGTH_M, 0.2)
    return NeighborInfo(gap_m=gap, velocity_ms=0.0, leader_id=None)

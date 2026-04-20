"""
Tipos de mensajes WebSocket y funciones builder para la comunicación
en tiempo real con el cliente Godot.

Cada builder genera un dict serializable a JSON listo para broadcast.
"""

from __future__ import annotations

from typing import Any


# =============================================================================
# Message Types
# =============================================================================

MSG_TYPE_TICK = "tick"
MSG_TYPE_SIM_STATE = "sim_state"
MSG_TYPE_VEHICLE_SPAWNED = "vehicle_spawned"
MSG_TYPE_VEHICLES_BATCH_SPAWNED = "vehicles_batch_spawned"
MSG_TYPE_VEHICLE_FINISHED = "vehicle_finished"
MSG_TYPE_VEHICLE_UPDATE = "vehicle_update"
MSG_TYPE_MAP_SWITCHED = "map_switched"
MSG_TYPE_TRAFFIC_LIGHT = "traffic_light"
MSG_TYPE_VEHICLE_COLLISION = "vehicle_collision"


# =============================================================================
# Builders
# =============================================================================


def build_tick_message(
    tick: int,
    sim_time: float,
    vehicles: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Mensaje de tick con el estado de todos los vehículos activos.

    Args:
        tick: Número de tick actual.
        sim_time: Tiempo de simulación en segundos.
        vehicles: Lista de dicts con estado de cada vehículo.
    """
    return {
        "type": MSG_TYPE_TICK,
        "tick": tick,
        "sim_time": round(sim_time, 3),
        "vehicles": vehicles,
        "count": len(vehicles),
    }


def build_vehicle_state(
    vehicle_id: str,
    longitude: float,
    latitude: float,
    velocity: float,
    acceleration: float,
    heading: float,
    status: str,
    current_edge_index: int,
    progress_on_edge: float,
) -> dict[str, Any]:
    """Estado completo de un vehículo para incluir en un mensaje tick."""
    return {
        "id": vehicle_id,
        "lon": round(longitude, 7),
        "lat": round(latitude, 7),
        "v": round(velocity, 2),
        "a": round(acceleration, 2),
        "h": round(heading, 1),
        "status": status,
        "edge_idx": current_edge_index,
        "progress": round(progress_on_edge, 4),
    }


def build_sim_state_message(state: str) -> dict[str, Any]:
    """Mensaje de cambio de estado de la simulación."""
    return {
        "type": MSG_TYPE_SIM_STATE,
        "state": state,
    }


def build_vehicle_spawned_message(
    vehicle_id: str,
    start_node_id: int,
    end_node_id: int,
    route_edges: list[int],
) -> dict[str, Any]:
    """Mensaje de vehículo recién generado."""
    return {
        "type": MSG_TYPE_VEHICLE_SPAWNED,
        "vehicle_id": vehicle_id,
        "start_node_id": start_node_id,
        "end_node_id": end_node_id,
        "route_edges": route_edges,
    }


def build_vehicle_finished_message(vehicle_id: str) -> dict[str, Any]:
    """Mensaje de vehículo que completó su ruta."""
    return {
        "type": MSG_TYPE_VEHICLE_FINISHED,
        "vehicle_id": vehicle_id,
    }


def build_vehicles_batch_spawned_message(vehicles: list[Any]) -> dict[str, Any]:
    """
    Mensaje de lote de vehículos recién generados, con posición inicial y ruta.

    Enviado por el broadcaster tras completar un spawn masivo en background.
    El cliente lo usa para registrar y renderizar todos los vehículos de golpe,
    sin esperar a los mensajes de tick individuales.

    Args:
        vehicles: Lista de SimVehicle con los vehículos creados.
    """
    return {
        "type": MSG_TYPE_VEHICLES_BATCH_SPAWNED,
        "count": len(vehicles),
        "vehicles": [
            {
                "id": v.id,
                "lon": round(v.longitude, 7),
                "lat": round(v.latitude, 7),
                "h": round(v.heading, 1),
                "status": v.status.value,
                "route_edges": v.route.edge_ids,
            }
            for v in vehicles
        ],
    }


def build_vehicle_collision_message(
    vehicle_id_1: str,
    vehicle_id_2: str,
    node_from: int,
    node_to: int,
) -> dict[str, Any]:
    """Mensaje de colisión entre dos vehículos, indicando el tramo bloqueado."""
    return {
        "type": MSG_TYPE_VEHICLE_COLLISION,
        "vehicle_id_1": vehicle_id_1,
        "vehicle_id_2": vehicle_id_2,
        "blocked_edge": [node_from, node_to],
    }


def build_map_switched_message(
    map_name: str,
    nodes: int,
    edges: int,
) -> dict[str, Any]:
    """Mensaje notificando que el mapa activo ha cambiado."""
    return {
        "type": MSG_TYPE_MAP_SWITCHED,
        "map": map_name,
        "nodes": nodes,
        "edges": edges,
    }


def build_traffic_lights_message(
    states: dict[int, dict[str, str]],
) -> dict[str, Any]:
    """
    Snapshot de estados de semáforos por arista de aproximación.

    Args:
        states: {node_id: {"u_v": phase}} del TrafficLightController.get_snapshot().
                Cada nodo puede tener fases distintas por eje (N-S vs E-O).

    Returns:
        Dict con type='traffic_light', version=2 y states anidados.
    """
    return {
        "type": MSG_TYPE_TRAFFIC_LIGHT,
        "version": 2,
        "states": {
            str(nid): {str(k): v for k, v in edges.items()}
            for nid, edges in states.items()
        },
    }

"""
Tipos de mensajes WebSocket y funciones builder para la comunicación
en tiempo real con el cliente Godot.

Cada builder genera un dict serializable a JSON listo para broadcast.
"""

from __future__ import annotations

import struct
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
MSG_TYPE_INCIDENT = "incident"
MSG_TYPE_ZONE = "zone"


# =============================================================================
# Builders
# =============================================================================


# =============================================================================
# Tick binario — wire format compacto usado para tick messages.
# El resto de mensajes siguen yendo en JSON (poco volumen, fácil debug).
#
# Layout (little-endian):
#   Header (18 bytes):
#     magic        u8     0x01 (TICK_BINARY)
#     version      u8     0x01
#     tick         u32
#     sim_time     f32
#     chunk_index  u16
#     chunk_total  u16
#     n_vehicles   u32
#   Per-vehicle (variable, ~30-35 B con id "v_NNN"):
#     id_len       u8       (longitud del id en bytes UTF-8)
#     id           id_len bytes
#     lon          f32
#     lat          f32
#     h            f32      (heading en grados)
#     v            f32      (velocidad m/s)
#     a            f32      (aceleración m/s²)
#     edge_idx     u32      (current_edge_index)
#     progress     f32      (progreso en arista 0..1)
#     status       u8       (mapeo en _STATUS_TO_INT)
#     lane         u8
#     vtype        u8       (mapeo en _VTYPE_TO_INT)
#
# Cliente Godot detecta el primer byte: 0x01 → binario; 0x7B ('{') → JSON.
# =============================================================================

TICK_BINARY_MAGIC: int = 0x01
TICK_BINARY_VERSION: int = 0x01

# Mapeos enum → int. Mantener sincronizados con el cliente Godot
# (Config.WireProtocol.STATUS_*, VTYPE_*).
_STATUS_TO_INT: dict[str, int] = {
    "idle": 0,
    "moving": 1,
    "stopped": 2,
    "waiting": 3,
    "collision": 4,
    "paused": 5,
    "finished": 6,
}
_VTYPE_TO_INT: dict[str, int] = {
    "car": 0,
    "moto": 1,
    "truck": 2,
}

# struct para el header (little-endian).
_TICK_HEADER_STRUCT = struct.Struct("<BBIfHHI")
# struct para los campos numéricos del vehículo (después del id).
# Orden: lon, lat, h, v, a (5×f32), edge_idx (u32), progress (f32),
#        status, lane, vtype (3×u8). Total = 5*4 + 4 + 4 + 3 = 31 B.
_TICK_VEHICLE_STRUCT = struct.Struct("<fffffIfBBB")


def build_tick_binary(
    tick: int,
    sim_time: float,
    vehicles: list[dict[str, Any]],
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> bytes:
    """
    Versión binaria de `build_tick_message`. Produce un buffer de bytes
    listo para `broadcast_bytes` (sin pasar por JSON).

    Reduce ~4× los bytes en el wire (130 B/veh → 33 B/veh) y el coste de
    serialización es ~5× menor que orjson para listas grandes. Indispensable
    para 5000+ vehículos: con JSON el broadcast bloqueaba >10 ms cada tick.
    """
    n = len(vehicles)
    # Pre-allocar el buffer es difícil (id_len varía). Usar bytearray y
    # extender con el header + per-vehicle. struct.pack devuelve bytes
    # nuevos por llamada, pero el coste de N pequeños pack es despreciable
    # frente al ahorro de NO pasar por JSON.
    buf = bytearray()
    buf.extend(_TICK_HEADER_STRUCT.pack(
        TICK_BINARY_MAGIC,
        TICK_BINARY_VERSION,
        tick & 0xFFFFFFFF,
        sim_time,
        chunk_index,
        chunk_total,
        n,
    ))
    for vs in vehicles:
        vid_bytes = vs["id"].encode("utf-8")
        buf.append(len(vid_bytes) & 0xFF)
        buf.extend(vid_bytes)
        buf.extend(_TICK_VEHICLE_STRUCT.pack(
            vs["lon"],
            vs["lat"],
            vs["h"],
            vs["v"],
            vs["a"],
            int(vs["edge_idx"]) & 0xFFFFFFFF,
            vs["progress"],
            _STATUS_TO_INT.get(vs["status"], 0),
            int(vs["lane"]) & 0xFF,
            _VTYPE_TO_INT.get(vs["vtype"], 0),
        ))
    return bytes(buf)


def build_tick_message(
    tick: int,
    sim_time: float,
    vehicles: list[dict[str, Any]],
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> dict[str, Any]:
    """
    Mensaje de tick con el estado de todos los vehículos activos.

    Args:
        tick: Número de tick actual.
        sim_time: Tiempo de simulación en segundos.
        vehicles: Lista de dicts con estado de cada vehículo.
        chunk_index: Índice (0-based) de este fragmento dentro del tick.
        chunk_total: Número total de fragmentos que componen el tick.

    El cliente usa `chunk_index` y `chunk_total` para aplicar todos los
    fragmentos del mismo tick atómicamente, sin jitter visible cuando el
    tick se divide por tamaño de WebSocket.
    """
    return {
        "type": MSG_TYPE_TICK,
        "tick": tick,
        "sim_time": round(sim_time, 3),
        "vehicles": vehicles,
        "count": len(vehicles),
        "chunk_index": chunk_index,
        "chunk_total": chunk_total,
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
    lane: int = 0,
    vtype: str = "car",
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
        "lane": lane,
        "vtype": vtype,
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
    lane: int = 0,
    vtype: str = "car",
) -> dict[str, Any]:
    """Mensaje de vehículo recién generado."""
    return {
        "type": MSG_TYPE_VEHICLE_SPAWNED,
        "vehicle_id": vehicle_id,
        "start_node_id": start_node_id,
        "end_node_id": end_node_id,
        "route_edges": route_edges,
        "lane": lane,
        "vtype": vtype,
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
                "lane": getattr(v, "lane", 0),
                "vtype": v.vtype.value if hasattr(v, "vtype") else "car",
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


def build_incident_message(
    action: str,
    incident: dict[str, Any],
) -> dict[str, Any]:
    """
    Notificación de incidente de tráfico.

    Args:
        action: "created" | "updated" | "cleared".
        incident: Dict serializable (IncidentManager.to_public_dict()).
    """
    return {
        "type": MSG_TYPE_INCIDENT,
        "action": action,
        "incident": incident,
    }


def build_zone_message(
    action: str,
    zone: dict[str, Any],
) -> dict[str, Any]:
    """
    Notificación de zona de control (ZBE / restringida / peatonal).

    Args:
        action: "created" | "updated" | "cleared".
        zone: Dict con los datos públicos de la zona (ZoneManager.to_public_dict()).
    """
    return {
        "type": MSG_TYPE_ZONE,
        "action": action,
        "zone": zone,
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

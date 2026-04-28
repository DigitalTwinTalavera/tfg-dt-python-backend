"""
Endpoints de control de la simulación y gestión de vehículos.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_broadcaster, get_simulation_config, get_simulation_engine, get_vehicle_spawner
from app.core.constants import KMH_TO_MS
from app.models.enums import VehicleStatus
from app.core.broadcaster import SimulationBroadcaster
from app.core.exceptions import SimulationStateError
from app.core.simulation_config import SimulationConfig
from app.core.simulation_engine import SimulationEngine
from app.services.vehicle_spawner import VehicleSpawner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulation")


@asynccontextmanager
async def _state_error_to_409() -> AsyncIterator[None]:
    """Convierte un SimulationStateError en HTTP 409 (Conflict)."""
    try:
        yield
    except SimulationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _vehicle_or_404(spawner: VehicleSpawner, vehicle_id: str):
    """Devuelve el SimVehicle o lanza HTTP 404."""
    vehicle = spawner.get_vehicle(vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail=f"Vehículo '{vehicle_id}' no encontrado")
    return vehicle


# =========================================================================
# Request schemas
# =========================================================================


class SpawnRequest(BaseModel):
    count: int = Field(default=1, ge=1, description="Número de vehículos a generar")


class SpeedRequest(BaseModel):
    desired_speed_kmh: float = Field(ge=0.0, le=300.0, description="Nueva velocidad deseada en km/h")


class RerouteRequest(BaseModel):
    end_node_id: int = Field(description="Nodo de destino para la nueva ruta")


# =========================================================================
# Simulation control endpoints
# =========================================================================


@router.post("/start")
async def start_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Inicia la simulación."""
    async with _state_error_to_409():
        await engine.start()
    return {
        "status": "started",
        "tick_rate": engine.tick_rate,
        "tick_interval_ms": engine.tick_interval_ms,
    }


@router.post("/stop")
async def stop_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Detiene la simulación."""
    async with _state_error_to_409():
        await engine.stop()
    return {"status": "stopped"}


@router.post("/pause")
async def pause_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Pausa la simulación."""
    async with _state_error_to_409():
        await engine.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Reanuda la simulación pausada."""
    async with _state_error_to_409():
        await engine.resume()
    return {"status": "resumed"}


@router.get("/status")
async def get_simulation_status(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Devuelve el estado actual de la simulación."""
    return engine.get_status()


# =========================================================================
# Config endpoints
# =========================================================================


@router.get("/config")
async def get_config(
    config: SimulationConfig = Depends(get_simulation_config),
) -> dict:
    """Devuelve la configuración activa de la simulación."""
    return config.to_dict()


@router.put("/config")
async def update_config(
    body: SimulationConfig,
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """
    Actualiza la configuración de la simulación en caliente.

    Los cambios toman efecto en el próximo tick sin necesidad de reiniciar.
    """
    engine.set_config(body)
    return {
        "status": "updated",
        "config": body.to_dict(),
    }


# =========================================================================
# Vehicle endpoints
# =========================================================================


@router.post("/vehicles/spawn")
async def spawn_vehicles(
    body: SpawnRequest,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """
    Inicia el spawn de vehículos en background y devuelve inmediatamente.

    Las rutas se calculan con A* + caché en un hilo de fondo. Al terminar,
    el broadcaster envía un mensaje WS `vehicles_batch_spawned` con todos
    los vehículos creados para que el cliente los renderice de golpe.
    """
    try:
        requested = await spawner.spawn_background(
            count=body.count,
            on_complete=broadcaster.broadcast_vehicles_batch_spawned,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "spawning",
        "requested": requested,
    }


@router.get("/vehicles")
async def list_vehicles(
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Lista todos los vehículos activos."""
    vehicles = spawner.get_all_vehicles()
    return {
        "count": len(vehicles),
        "vehicles": [v.to_dict() for v in vehicles],
    }


@router.get("/vehicles/{vehicle_id}")
async def get_vehicle(
    vehicle_id: str,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Obtiene un vehículo por su ID."""
    return _vehicle_or_404(spawner, vehicle_id).to_dict()


@router.delete("/vehicles/{vehicle_id}")
async def delete_vehicle(
    vehicle_id: str,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Elimina un vehículo de la simulación."""
    removed = spawner.remove_vehicle(vehicle_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Vehículo '{vehicle_id}' no encontrado")
    return {"status": "deleted", "vehicle_id": vehicle_id}


@router.post("/vehicles/{vehicle_id}/pause")
async def pause_vehicle(
    vehicle_id: str,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Pausa manualmente un vehículo (deja de moverse)."""
    vehicle = _vehicle_or_404(spawner, vehicle_id)
    vehicle.status = VehicleStatus.PAUSED
    vehicle.velocity = 0.0
    return {"status": "paused", "vehicle_id": vehicle_id}


@router.post("/vehicles/{vehicle_id}/resume")
async def resume_vehicle(
    vehicle_id: str,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Reanuda un vehículo pausado manualmente."""
    vehicle = _vehicle_or_404(spawner, vehicle_id)
    if vehicle.status != VehicleStatus.PAUSED:
        raise HTTPException(status_code=409, detail=f"Vehículo '{vehicle_id}' no está pausado")
    vehicle.status = VehicleStatus.MOVING
    return {"status": "resumed", "vehicle_id": vehicle_id}


@router.put("/vehicles/{vehicle_id}/speed")
async def set_vehicle_speed(
    vehicle_id: str,
    body: SpeedRequest,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Cambia la velocidad deseada de un vehículo."""
    vehicle = _vehicle_or_404(spawner, vehicle_id)
    vehicle.desired_speed_ms = body.desired_speed_kmh * KMH_TO_MS
    return {
        "status": "updated",
        "vehicle_id": vehicle_id,
        "desired_speed_kmh": body.desired_speed_kmh,
        "desired_speed_ms": round(vehicle.desired_speed_ms, 3),
    }


@router.post("/vehicles/{vehicle_id}/reroute")
async def reroute_vehicle(
    vehicle_id: str,
    body: RerouteRequest,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """Reasigna la ruta de un vehículo hacia un nuevo nodo destino."""
    success = spawner.reroute_vehicle(vehicle_id, body.end_node_id)
    if not success:
        # Si el vehículo no existe → 404; si existe pero no hay ruta → 422.
        _vehicle_or_404(spawner, vehicle_id)
        raise HTTPException(
            status_code=422,
            detail=f"No se pudo calcular ruta desde vehículo '{vehicle_id}' a nodo {body.end_node_id}",
        )
    return {"status": "rerouted", "vehicle_id": vehicle_id, "end_node_id": body.end_node_id}


# =========================================================================
# Collision management endpoints
# =========================================================================


@router.get("/collisions")
async def list_collisions(
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
) -> dict:
    """
    Lista los vehículos actualmente en estado de colisión.

    Para cada choque se devuelve el id del vehículo, su posición (lon/lat) y
    la arista donde ocurrió (start/end node). El cliente (UI del operador)
    utiliza este endpoint para mostrar un panel de incidencias y ofrecer el
    botón de retirada manual.
    """
    # Emparejar los vehículos por arista compartida (asumimos 2 por choque).
    by_edge: dict[tuple[int, int], list] = {}
    for v in spawner.vehicles.values():
        if v.status != VehicleStatus.COLLISION:
            continue
        np_ = v.route.node_path
        ei = v.current_edge_index
        if ei >= len(np_) - 1:
            continue
        by_edge.setdefault((np_[ei], np_[ei + 1]), []).append(v)

    collisions: list[dict] = []
    for (u, w), vs in by_edge.items():
        for v in vs:
            partner_id = next((o.id for o in vs if o.id != v.id), "")
            collisions.append(
                {
                    "vehicle_id": v.id,
                    "partner_id": partner_id,
                    "longitude": v.longitude,
                    "latitude": v.latitude,
                    "edge": [u, w],
                }
            )
    return {"count": len(collisions), "collisions": collisions}


@router.post("/vehicles/{vehicle_id}/clear-collision")
async def clear_vehicle_collision(
    vehicle_id: str,
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """
    Retira manualmente un vehículo colisionado (gemelo digital: simula la
    intervención de grúa/emergencias). Si tras retirarlo ninguna otra
    colisión comparte la misma arista, se libera el bloqueo → A* vuelve a
    usarla sin penalización.
    """
    vehicle = _vehicle_or_404(spawner, vehicle_id)
    if vehicle.status != VehicleStatus.COLLISION:
        raise HTTPException(
            status_code=409,
            detail=f"Vehículo '{vehicle_id}' no está en estado de colisión",
        )

    np_ = vehicle.route.node_path
    ei = vehicle.current_edge_index
    edge_key: tuple[int, int] | None = None
    if ei < len(np_) - 1:
        edge_key = (np_[ei], np_[ei + 1])

    spawner.remove_vehicle(vehicle_id)
    # El cliente Godot sólo retira el vehículo del MultiMesh cuando recibe
    # vehicle_finished; sin este broadcast el coche quedaría pintado en la
    # escena aun habiendo sido borrado del backend.
    await broadcaster.broadcast_vehicle_finished(vehicle_id)

    released = False
    if edge_key is not None and edge_key in spawner.blocked_edges:
        still_blocked = False
        for other in spawner.vehicles.values():
            if other.status != VehicleStatus.COLLISION:
                continue
            onp = other.route.node_path
            oei = other.current_edge_index
            if oei < len(onp) - 1 and (onp[oei], onp[oei + 1]) == edge_key:
                still_blocked = True
                break
        if not still_blocked:
            spawner.blocked_edges.pop(edge_key, None)
            released = True

    return {
        "status": "cleared",
        "vehicle_id": vehicle_id,
        "edge_released": released,
        "edge": {"start_node_id": edge_key[0], "end_node_id": edge_key[1]}
        if edge_key
        else None,
    }


@router.post("/collisions/clear-all")
async def clear_all_collisions(
    spawner: VehicleSpawner = Depends(get_vehicle_spawner),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """
    Retira de una sola vez todos los vehículos en estado de colisión y
    libera las aristas bloqueadas por ellos. Equivalente a pulsar "Retirar"
    en cada fila del panel de colisiones.
    """
    to_clear: list[tuple[str, tuple[int, int] | None]] = []
    for v in list(spawner.vehicles.values()):
        if v.status != VehicleStatus.COLLISION:
            continue
        np_ = v.route.node_path
        ei = v.current_edge_index
        edge_key = (np_[ei], np_[ei + 1]) if ei < len(np_) - 1 else None
        to_clear.append((v.id, edge_key))

    edges_to_release: set[tuple[int, int]] = set()
    for vid, edge_key in to_clear:
        spawner.remove_vehicle(vid)
        await broadcaster.broadcast_vehicle_finished(vid)
        if edge_key is not None:
            edges_to_release.add(edge_key)

    released: list[list[int]] = []
    for edge_key in edges_to_release:
        if edge_key in spawner.blocked_edges:
            spawner.blocked_edges.pop(edge_key, None)
            released.append([edge_key[0], edge_key[1]])

    return {
        "status": "cleared_all",
        "count": len(to_clear),
        "edges_released": released,
    }


# =========================================================================
# Traffic light endpoints
# =========================================================================


def _get_tl_or_404(engine: SimulationEngine):
    """Helper: devuelve el TrafficLightController o lanza 404."""
    tl = engine.get_tl_controller()
    if tl is None:
        raise HTTPException(
            status_code=404,
            detail="Controlador de semáforos no disponible (¿simulación iniciada?)",
        )
    return tl


@router.get("/traffic-lights")
async def get_traffic_lights(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Devuelve el snapshot actual de todos los semáforos y el modo de override."""
    tl = engine.get_tl_controller()
    if tl is None:
        return {"mode": "normal", "count": 0, "states": {}}
    return {
        "mode": tl.get_override_mode(),
        "count": tl.light_count,
        "states": {
            str(nid): {str(ek): ph for ek, ph in edges.items()}
            for nid, edges in tl.get_snapshot().items()
        },
    }


@router.post("/traffic-lights/all-green")
async def tl_all_green(
    engine: SimulationEngine = Depends(get_simulation_engine),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """Fuerza todos los semáforos en verde. Broadcast inmediato al cliente."""
    tl = _get_tl_or_404(engine)
    tl.set_all_override("green")
    await broadcaster.broadcast_traffic_lights(tl.get_snapshot())
    return {"mode": tl.get_override_mode(), "count": tl.light_count}


@router.post("/traffic-lights/all-red")
async def tl_all_red(
    engine: SimulationEngine = Depends(get_simulation_engine),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """Fuerza todos los semáforos en rojo. Broadcast inmediato al cliente."""
    tl = _get_tl_or_404(engine)
    tl.set_all_override("red")
    await broadcaster.broadcast_traffic_lights(tl.get_snapshot())
    return {"mode": tl.get_override_mode(), "count": tl.light_count}


@router.post("/traffic-lights/normal")
async def tl_normal(
    engine: SimulationEngine = Depends(get_simulation_engine),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """Elimina todos los overrides; los semáforos vuelven al ciclo de tiempo fijo."""
    tl = _get_tl_or_404(engine)
    tl.clear_overrides()
    await broadcaster.broadcast_traffic_lights(tl.get_snapshot())
    return {"mode": tl.get_override_mode(), "count": tl.light_count}


class TLNodeOverrideRequest(BaseModel):
    phase: str = Field(description="Fase forzada: 'red', 'yellow' o 'green'")


@router.post("/traffic-lights/{node_id}/override")
async def tl_node_override(
    node_id: int,
    body: TLNodeOverrideRequest,
    engine: SimulationEngine = Depends(get_simulation_engine),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """
    Fuerza una fase concreta en el semáforo del nodo indicado.

    Los demás nodos siguen con su ciclo normal (o su propio override previo).
    """
    phase = body.phase.lower()
    if phase not in {"red", "yellow", "green"}:
        raise HTTPException(
            status_code=422,
            detail="phase debe ser 'red', 'yellow' o 'green'",
        )
    tl = _get_tl_or_404(engine)
    if not tl.knows_node(node_id):
        raise HTTPException(
            status_code=404,
            detail=f"Nodo {node_id} no es un semáforo conocido",
        )
    tl.set_override(node_id, phase)
    await broadcaster.broadcast_traffic_lights(tl.get_snapshot())
    return {
        "status": "overridden",
        "node_id": node_id,
        "phase": phase,
        "mode": tl.get_override_mode(),
    }


@router.delete("/traffic-lights/{node_id}/override")
async def tl_node_override_clear(
    node_id: int,
    engine: SimulationEngine = Depends(get_simulation_engine),
    broadcaster: SimulationBroadcaster = Depends(get_broadcaster),
) -> dict:
    """Retira el override del nodo y deja que vuelva a su ciclo."""
    tl = _get_tl_or_404(engine)
    if not tl.knows_node(node_id):
        raise HTTPException(
            status_code=404,
            detail=f"Nodo {node_id} no es un semáforo conocido",
        )
    had = tl.clear_override_for_node(node_id)
    await broadcaster.broadcast_traffic_lights(tl.get_snapshot())
    return {
        "status": "cleared" if had else "noop",
        "node_id": node_id,
        "mode": tl.get_override_mode(),
    }

"""
Endpoints de control de la simulación y gestión de vehículos.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_broadcaster, get_simulation_config, get_simulation_engine, get_vehicle_spawner
from app.core.broadcaster import SimulationBroadcaster
from app.core.exceptions import SimulationStateError
from app.core.simulation_config import SimulationConfig
from app.core.simulation_engine import SimulationEngine
from app.services.vehicle_spawner import VehicleSpawner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulation")


# =========================================================================
# Request schemas
# =========================================================================


class SpawnRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=10000, description="Número de vehículos a generar")


# =========================================================================
# Simulation control endpoints
# =========================================================================


@router.post("/start")
async def start_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Inicia la simulación."""
    try:
        await engine.start()
    except SimulationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
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
    try:
        await engine.stop()
    except SimulationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "stopped"}


@router.post("/pause")
async def pause_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Pausa la simulación."""
    try:
        await engine.pause()
    except SimulationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "paused"}


@router.post("/resume")
async def resume_simulation(
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """Reanuda la simulación pausada."""
    try:
        await engine.resume()
    except SimulationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
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
    vehicle = spawner.get_vehicle(vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail=f"Vehículo '{vehicle_id}' no encontrado")
    return vehicle.to_dict()


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

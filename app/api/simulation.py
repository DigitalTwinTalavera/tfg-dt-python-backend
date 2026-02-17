"""
Endpoints de control de la simulación.
Permite iniciar, detener, pausar, reanudar y consultar el estado.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_simulation_engine
from app.core.exceptions import SimulationStateError
from app.core.simulation_engine import SimulationEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulation")


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

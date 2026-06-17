"""
Endpoints REST para la gestión de incidentes de tráfico.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_incident_manager, get_simulation_engine
from app.core.simulation_engine import SimulationEngine
from app.models.enums import IncidentType
from app.services.incident_manager import IncidentManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/incidents", tags=["Incidents"])


# =========================================================================
# Request / Response schemas
# =========================================================================


class IncidentCreateRequest(BaseModel):
    type: IncidentType = Field(description="Tipo de incidente")
    edge: tuple[int, int] = Field(
        description="Arista afectada como par (start_node_id, end_node_id)"
    )
    lanes_affected: list[int] = Field(
        default_factory=list,
        description="Índices de carril cerrados. Vacío = evento sólo informativo.",
    )
    duration_s: float | None = Field(
        default=None,
        description=(
            "Duración (s) hasta auto-cierre. null = permanente (retirada "
            "manual). Si se omite se usa el default del tipo."
        ),
    )
    severity: int = Field(default=1, ge=1, le=3, description="1=bajo, 3=alto")
    description: str = Field(default="", max_length=255)


class IncidentExtendRequest(BaseModel):
    extra_s: float = Field(gt=0.0, description="Segundos adicionales a sumar al TTL")


# =========================================================================
# Endpoints
# =========================================================================


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_incident(
    body: IncidentCreateRequest,
    mgr: IncidentManager = Depends(get_incident_manager),
    engine: SimulationEngine = Depends(get_simulation_engine),
) -> dict:
    """
    Crea un nuevo incidente sobre la arista indicada.

    Si ``lanes_affected`` cubre todos los carriles de la arista, se bloquea
    entera y A* la evita. Si es subconjunto, MOBIL empuja a los vehículos
    fuera de los carriles cerrados.
    """
    duration = body.duration_s
    if duration is None and body.duration_s is None:
        # Sentinel para que el manager use el default del tipo
        duration = -1.0
    try:
        incident = await mgr.create(
            type_=body.type,
            edge=tuple(body.edge),
            lanes_affected=body.lanes_affected,
            duration_s=duration if duration is not None else None,
            severity=body.severity,
            description=body.description,
            sim_time=engine.simulation_time,
        )
    except Exception as e:
        logger.exception("Error creando incidente")
        raise HTTPException(status_code=500, detail=f"Error creando incidente: {e}")
    edge_tuple = (body.edge[0], body.edge[1])
    return {"status": "created", "incident": mgr.to_public_dict(incident, edge_tuple)}


@router.get("")
async def list_incidents(
    mgr: IncidentManager = Depends(get_incident_manager),
) -> dict:
    """Lista los incidentes actualmente activos."""
    active = mgr.list_active()
    return {
        "count": len(active),
        "incidents": [
            mgr.to_public_dict(inc, mgr._edge_of(inc)) for inc in active
        ],
    }


@router.get("/{incident_id}")
async def get_incident(
    incident_id: int,
    mgr: IncidentManager = Depends(get_incident_manager),
) -> dict:
    incident = mgr.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incidente {incident_id} no encontrado")
    return mgr.to_public_dict(incident, mgr._edge_of(incident))


@router.delete("/{incident_id}")
async def clear_incident(
    incident_id: int,
    mgr: IncidentManager = Depends(get_incident_manager),
) -> dict:
    ok = await mgr.clear(incident_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Incidente {incident_id} no encontrado o ya cerrado")
    return {"status": "cleared", "incident_id": incident_id}


@router.post("/{incident_id}/extend")
async def extend_incident(
    incident_id: int,
    body: IncidentExtendRequest,
    mgr: IncidentManager = Depends(get_incident_manager),
) -> dict:
    incident = await mgr.extend(incident_id, body.extra_s)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incidente {incident_id} no activo")
    return {
        "status": "extended",
        "incident": mgr.to_public_dict(incident, mgr._edge_of(incident)),
    }

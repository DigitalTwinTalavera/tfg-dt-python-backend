"""
Endpoints REST para la gestión de zonas de control (ZBE, restringidas, peatonales).

La geometría se transmite como WKT (POLYGON((lon lat, ...))) para simplificar
el cliente. PostGIS convierte al SRID 4326 al persistir.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_zone_manager
from app.models.enums import ZoneEnforcement, ZoneType
from app.services.zone_manager import ZoneManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zones", tags=["Zones"])


# =========================================================================
# Request schemas
# =========================================================================


class ZoneCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    zone_type: ZoneType = Field(default=ZoneType.ZBE)
    geometry_wkt: str = Field(
        description=(
            "Polígono en WKT: 'POLYGON((lon1 lat1, lon2 lat2, ..., lon1 lat1))'"
        )
    )
    restricted_vtypes: list[str] = Field(
        default_factory=list,
        description="Tipos de vehículo restringidos: 'car', 'moto', 'truck'",
    )
    enforcement: ZoneEnforcement = Field(default=ZoneEnforcement.FORCE_REROUTE)
    active: bool = True


class ZoneUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    enforcement: ZoneEnforcement | None = None
    restricted_vtypes: list[str] | None = None


# =========================================================================
# Endpoints
# =========================================================================


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_zone(
    body: ZoneCreateRequest,
    mgr: ZoneManager = Depends(get_zone_manager),
) -> dict:
    try:
        zone = await mgr.create(
            name=body.name,
            zone_type=body.zone_type,
            geometry_wkt=body.geometry_wkt,
            restricted_vtypes=body.restricted_vtypes,
            enforcement=body.enforcement,
            active=body.active,
        )
    except Exception as e:
        logger.exception("Error creando zona")
        raise HTTPException(status_code=400, detail=f"Error creando zona: {e}")
    return {"status": "created", "zone": await mgr.to_public_dict(zone)}


@router.get("")
async def list_zones(
    mgr: ZoneManager = Depends(get_zone_manager),
) -> dict:
    zones = mgr.list_all()
    return {
        "count": len(zones),
        "zones": [await mgr.to_public_dict(z) for z in zones],
    }


@router.get("/{zone_id}")
async def get_zone(
    zone_id: int,
    mgr: ZoneManager = Depends(get_zone_manager),
) -> dict:
    zone = mgr.get(zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"Zona {zone_id} no encontrada")
    return await mgr.to_public_dict(zone)


@router.put("/{zone_id}")
async def update_zone(
    zone_id: int,
    body: ZoneUpdateRequest,
    mgr: ZoneManager = Depends(get_zone_manager),
) -> dict:
    zone = await mgr.update(
        zone_id,
        name=body.name,
        active=body.active,
        enforcement=body.enforcement,
        restricted_vtypes=body.restricted_vtypes,
    )
    if zone is None:
        raise HTTPException(status_code=404, detail=f"Zona {zone_id} no encontrada")
    return {"status": "updated", "zone": await mgr.to_public_dict(zone)}


@router.delete("/{zone_id}")
async def delete_zone(
    zone_id: int,
    mgr: ZoneManager = Depends(get_zone_manager),
) -> dict:
    ok = await mgr.delete(zone_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Zona {zone_id} no encontrada")
    return {"status": "deleted", "zone_id": zone_id}

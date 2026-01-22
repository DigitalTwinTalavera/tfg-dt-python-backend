"""
Pydantic schemas for vehicle entities in the traffic simulation.
Provides validation and serialization for API requests and responses.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import (
    DEFAULT_ACCELERATION,
    DEFAULT_HEADING,
    DEFAULT_VELOCITY,
    LATITUDE_MAX,
    LATITUDE_MIN,
    LONGITUDE_MAX,
    LONGITUDE_MIN,
    MAX_ACCELERATION,
    MAX_HEADING,
    MAX_VELOCITY,
    MIN_ACCELERATION,
    MIN_HEADING,
    MIN_VELOCITY,
)
from app.models.enums import VehicleStatus


# ============================================================================
# Vehicle Schemas
# ============================================================================


class VehicleBase(BaseModel):
    """Base schema for vehicle attributes."""

    velocity: float = Field(
        default=DEFAULT_VELOCITY,
        ge=MIN_VELOCITY,
        le=MAX_VELOCITY,
        description="Current velocity in m/s",
    )
    acceleration: float = Field(
        default=DEFAULT_ACCELERATION,
        ge=MIN_ACCELERATION,
        le=MAX_ACCELERATION,
        description="Current acceleration in m/s²",
    )
    heading: float = Field(
        default=DEFAULT_HEADING,
        ge=MIN_HEADING,
        lt=MAX_HEADING,
        description="Direction in degrees (0=North, 90=East)",
    )
    status: VehicleStatus = Field(
        default=VehicleStatus.IDLE, description="Current status of the vehicle"
    )
    current_edge_id: Optional[int] = Field(
        None, description="ID of the edge the vehicle is currently on"
    )
    route_edges: Optional[list[int]] = Field(
        None, description="Ordered list of edge IDs forming the planned route"
    )


class VehicleCreate(VehicleBase):
    """Schema for creating a new vehicle."""

    longitude: float = Field(
        ..., ge=LONGITUDE_MIN, le=LONGITUDE_MAX, description="Longitude in degrees"
    )
    latitude: float = Field(
        ..., ge=LATITUDE_MIN, le=LATITUDE_MAX, description="Latitude in degrees"
    )


class VehicleUpdate(BaseModel):
    """Schema for updating a vehicle. All fields are optional."""

    longitude: Optional[float] = Field(
        None, ge=LONGITUDE_MIN, le=LONGITUDE_MAX, description="Longitude in degrees"
    )
    latitude: Optional[float] = Field(
        None, ge=LATITUDE_MIN, le=LATITUDE_MAX, description="Latitude in degrees"
    )
    velocity: Optional[float] = Field(
        None,
        ge=MIN_VELOCITY,
        le=MAX_VELOCITY,
        description="Current velocity in m/s",
    )
    acceleration: Optional[float] = Field(
        None,
        ge=MIN_ACCELERATION,
        le=MAX_ACCELERATION,
        description="Current acceleration in m/s²",
    )
    heading: Optional[float] = Field(
        None,
        ge=MIN_HEADING,
        lt=MAX_HEADING,
        description="Direction in degrees (0=North, 90=East)",
    )
    status: Optional[VehicleStatus] = Field(
        None, description="Current status of the vehicle"
    )
    current_edge_id: Optional[int] = Field(
        None, description="ID of the edge the vehicle is currently on"
    )
    route_edges: Optional[list[int]] = Field(
        None, description="Ordered list of edge IDs forming the planned route"
    )


class VehicleStateUpdate(BaseModel):
    """
    Schema for high-frequency vehicle state updates during simulation.
    Optimized for position and physics updates only.
    """

    longitude: float = Field(
        ..., ge=LONGITUDE_MIN, le=LONGITUDE_MAX, description="Longitude in degrees"
    )
    latitude: float = Field(
        ..., ge=LATITUDE_MIN, le=LATITUDE_MAX, description="Latitude in degrees"
    )
    velocity: Optional[float] = Field(
        None,
        ge=MIN_VELOCITY,
        le=MAX_VELOCITY,
        description="Current velocity in m/s",
    )
    acceleration: Optional[float] = Field(
        None,
        ge=MIN_ACCELERATION,
        le=MAX_ACCELERATION,
        description="Current acceleration in m/s²",
    )
    heading: Optional[float] = Field(
        None,
        ge=MIN_HEADING,
        lt=MAX_HEADING,
        description="Direction in degrees",
    )


class VehicleResponse(VehicleBase):
    """Schema for vehicle responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Unique vehicle identifier")
    longitude: float = Field(..., description="Current longitude in degrees")
    latitude: float = Field(..., description="Current latitude in degrees")
    created_at: datetime = Field(..., description="Timestamp of creation")
    updated_at: datetime = Field(..., description="Timestamp of last update")

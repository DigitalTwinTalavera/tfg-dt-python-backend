"""
Pydantic schemas for the application.
"""

from app.core.schemas.network_schema import (
    EdgeCreate,
    EdgeResponse,
    EdgeUpdate,
    NodeCreate,
    NodeResponse,
    NodeUpdate,
)
from app.core.schemas.vehicle_schema import (
    VehicleCreate,
    VehicleResponse,
    VehicleStateUpdate,
    VehicleUpdate,
)

__all__ = [
    "NodeCreate",
    "NodeResponse",
    "NodeUpdate",
    "EdgeCreate",
    "EdgeResponse",
    "EdgeUpdate",
    "VehicleCreate",
    "VehicleResponse",
    "VehicleStateUpdate",
    "VehicleUpdate",
]

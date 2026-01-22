"""
SQLAlchemy model for vehicles in the traffic simulation.
Uses GeoAlchemy2 for PostGIS geometry types with EPSG:4326 (WGS84).
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    DEFAULT_ACCELERATION,
    DEFAULT_HEADING,
    DEFAULT_VELOCITY,
    IDX_VEHICLES_CURRENT_EDGE,
    IDX_VEHICLES_POSITION,
    IDX_VEHICLES_STATUS,
    IDX_VEHICLES_UPDATED_AT,
    SRID_WGS84,
    TABLE_EDGES,
    TABLE_VEHICLES,
    VEHICLE_STATUS_MAX_LENGTH,
)
from app.db.database import Base
from app.models.enums import VehicleStatus


class VehicleModel(Base):
    """
    Represents a vehicle in the traffic simulation.

    Attributes:
        id: Unique identifier (UUID)
        position: PostGIS Point geometry (EPSG:4326 - WGS84) for current location
        velocity: Current velocity in m/s
        acceleration: Current acceleration in m/s²
        heading: Direction of movement in degrees (0-360, 0=North, 90=East)
        status: Current status of the vehicle (idle, moving, stopped, waiting, finished)
        current_edge_id: ID of the edge the vehicle is currently on (nullable)
        route_edges: Ordered list of edge IDs forming the vehicle's planned route (JSONB)
        created_at: Timestamp of creation
        updated_at: Timestamp of last update (indexed for high-frequency queries)
    """

    __tablename__ = TABLE_VEHICLES

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    position: Mapped[str] = mapped_column(
        Geometry(geometry_type="POINT", srid=SRID_WGS84), nullable=False
    )
    velocity: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_VELOCITY
    )
    acceleration: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_ACCELERATION
    )
    heading: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_HEADING
    )
    status: Mapped[str] = mapped_column(
        String(VEHICLE_STATUS_MAX_LENGTH),
        nullable=False,
        default=VehicleStatus.IDLE.value,
    )
    current_edge_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey(f"{TABLE_EDGES}.id", ondelete="SET NULL"),
        nullable=True,
    )
    route_edges: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationship to current edge
    current_edge: Mapped[Optional["EdgeModel"]] = relationship(  # noqa: F821
        "EdgeModel", foreign_keys=[current_edge_id]
    )

    __table_args__ = (
        Index(IDX_VEHICLES_POSITION, position, postgresql_using="gist"),
        Index(IDX_VEHICLES_STATUS, status),
        Index(IDX_VEHICLES_CURRENT_EDGE, current_edge_id),
        Index(IDX_VEHICLES_UPDATED_AT, updated_at),
    )

    def __repr__(self) -> str:
        return f"<VehicleModel(id={self.id}, status={self.status}, velocity={self.velocity})>"

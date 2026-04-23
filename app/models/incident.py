"""
Modelo SQLAlchemy para incidentes de tráfico (accidentes, obras, averías, eventos).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import TABLE_EDGES, TYPE_FIELD_MAX_LENGTH
from app.db.database import Base
from app.models.enums import IncidentStatus, IncidentType

TABLE_INCIDENTS = "dt_incidents"

IDX_INCIDENTS_STATUS = "idx_dt_incidents_status"
IDX_INCIDENTS_EDGE = "idx_dt_incidents_edge_id"


class IncidentModel(Base):
    """
    Incidente de tráfico sobre una arista concreta.

    Un incidente afecta a uno o más carriles de una arista durante una
    duración opcional. Si cubre todos los carriles, la arista queda
    bloqueada. Si es parcial, MOBIL empuja a los vehículos a otros carriles.
    """

    __tablename__ = TABLE_INCIDENTS

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(
        String(TYPE_FIELD_MAX_LENGTH),
        nullable=False,
        default=IncidentType.ACCIDENT.value,
    )
    edge_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{TABLE_EDGES}.id", ondelete="CASCADE"),
        nullable=False,
    )
    lanes_affected: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    start_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    severity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(TYPE_FIELD_MAX_LENGTH),
        nullable=False,
        default=IncidentStatus.ACTIVE.value,
    )
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    edge: Mapped["EdgeModel"] = relationship(  # noqa: F821
        "EdgeModel", foreign_keys=[edge_id]
    )

    __table_args__ = (
        Index(IDX_INCIDENTS_STATUS, status),
        Index(IDX_INCIDENTS_EDGE, edge_id),
    )

    def __repr__(self) -> str:
        return (
            f"<IncidentModel(id={self.id}, type={self.type}, edge={self.edge_id}, "
            f"lanes={self.lanes_affected}, status={self.status})>"
        )

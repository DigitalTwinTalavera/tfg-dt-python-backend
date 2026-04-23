"""
Modelo SQLAlchemy para zonas de control de tráfico (ZBE, peatonal, restringida).

La geometría se almacena como POLYGON en EPSG:4326 (WGS84) y se consulta
vía PostGIS con ``ST_Intersects`` para determinar qué aristas están dentro.
"""

from datetime import datetime
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import SRID_WGS84, TYPE_FIELD_MAX_LENGTH
from app.db.database import Base
from app.models.enums import ZoneEnforcement, ZoneType

TABLE_ZONES = "dt_zones"

IDX_ZONES_GEOMETRY = "idx_dt_zones_geometry_gist"
IDX_ZONES_ACTIVE = "idx_dt_zones_active"


class ZoneModel(Base):
    """
    Zona poligonal con posibles restricciones por tipo de vehículo.

    Ejemplo: una ZBE que prohíbe ``truck`` con enforcement ``force_reroute``
    hará que el A* penalice las aristas internas a la zona sólo para camiones.
    """

    __tablename__ = TABLE_ZONES

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    zone_type: Mapped[str] = mapped_column(
        String(TYPE_FIELD_MAX_LENGTH),
        nullable=False,
        default=ZoneType.ZBE.value,
    )
    geometry: Mapped[str] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=SRID_WGS84), nullable=False
    )
    restricted_vtypes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    enforcement: Mapped[str] = mapped_column(
        String(TYPE_FIELD_MAX_LENGTH),
        nullable=False,
        default=ZoneEnforcement.FORCE_REROUTE.value,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(IDX_ZONES_GEOMETRY, geometry, postgresql_using="gist"),
        Index(IDX_ZONES_ACTIVE, active),
    )

    def __repr__(self) -> str:
        return (
            f"<ZoneModel(id={self.id}, name={self.name}, type={self.zone_type}, "
            f"enforcement={self.enforcement}, active={self.active})>"
        )

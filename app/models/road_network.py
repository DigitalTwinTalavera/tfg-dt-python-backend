"""
SQLAlchemy models for road network (nodes and edges).
Uses GeoAlchemy2 for PostGIS geometry types with EPSG:4326 (WGS84).
"""

from datetime import datetime
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    DEFAULT_IS_ACTIVE,
    DEFAULT_LANES,
    DEFAULT_MAX_SPEED_KMH,
    DEFAULT_ONE_WAY,
    EDGE_NAME_MAX_LENGTH,
    IDX_EDGES_ACTIVE,
    IDX_EDGES_END_NODE,
    IDX_EDGES_GEOMETRY,
    IDX_EDGES_ROAD_TYPE,
    IDX_EDGES_START_NODE,
    IDX_NODES_ACTIVE,
    IDX_NODES_POSITION,
    IDX_NODES_TYPE,
    NODE_NAME_MAX_LENGTH,
    SRID_WGS84,
    TABLE_EDGES,
    TABLE_NODES,
    TYPE_FIELD_MAX_LENGTH,
)
from app.db.database import Base
from app.models.enums import NodeType, RoadType


class NodeModel(Base):
    """
    Represents a node (intersection, traffic light, etc.) in the road network.

    Attributes:
        id: Unique identifier
        name: Optional human-readable name
        node_type: Type of node (intersection, traffic_light, etc.)
        position: PostGIS Point geometry (EPSG:4326 - WGS84)
        is_active: Whether the node is currently active
        metadata_json: Optional JSON metadata for additional properties
        created_at: Timestamp of creation
        updated_at: Timestamp of last update
    """

    __tablename__ = TABLE_NODES

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(
        String(NODE_NAME_MAX_LENGTH), nullable=True
    )
    node_type: Mapped[str] = mapped_column(
        String(TYPE_FIELD_MAX_LENGTH),
        nullable=False,
        default=NodeType.INTERSECTION.value,
    )
    position: Mapped[str] = mapped_column(
        Geometry(geometry_type="POINT", srid=SRID_WGS84), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=DEFAULT_IS_ACTIVE, nullable=False
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    outgoing_edges: Mapped[list["EdgeModel"]] = relationship(
        "EdgeModel",
        foreign_keys="EdgeModel.start_node_id",
        back_populates="start_node",
        cascade="all, delete-orphan",
    )
    incoming_edges: Mapped[list["EdgeModel"]] = relationship(
        "EdgeModel",
        foreign_keys="EdgeModel.end_node_id",
        back_populates="end_node",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(IDX_NODES_POSITION, position, postgresql_using="gist"),
        Index(IDX_NODES_TYPE, node_type),
        Index(IDX_NODES_ACTIVE, is_active),
    )

    def __repr__(self) -> str:
        return f"<NodeModel(id={self.id}, name={self.name}, type={self.node_type})>"


class EdgeModel(Base):
    """
    Represents an edge (road segment) connecting two nodes in the road network.

    Attributes:
        id: Unique identifier
        name: Optional human-readable name (e.g., street name)
        start_node_id: ID of the starting node
        end_node_id: ID of the ending node
        road_type: Type of road (motorway, primary, etc.)
        geometry: PostGIS LineString geometry (EPSG:4326 - WGS84)
        length: Length of the edge in meters
        max_speed: Maximum allowed speed in km/h
        lanes: Number of lanes
        one_way: Whether the road is one-way
        is_active: Whether the edge is currently active
        metadata_json: Optional JSON metadata for additional properties
        created_at: Timestamp of creation
        updated_at: Timestamp of last update
    """

    __tablename__ = TABLE_EDGES

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(
        String(EDGE_NAME_MAX_LENGTH), nullable=True
    )
    start_node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{TABLE_NODES}.id", ondelete="CASCADE"), nullable=False
    )
    end_node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{TABLE_NODES}.id", ondelete="CASCADE"), nullable=False
    )
    road_type: Mapped[str] = mapped_column(
        String(TYPE_FIELD_MAX_LENGTH),
        nullable=False,
        default=RoadType.RESIDENTIAL.value,
    )
    geometry: Mapped[str] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=SRID_WGS84), nullable=False
    )
    length: Mapped[float] = mapped_column(Float, nullable=False)
    max_speed: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_MAX_SPEED_KMH, nullable=False
    )
    lanes: Mapped[int] = mapped_column(Integer, default=DEFAULT_LANES, nullable=False)
    one_way: Mapped[bool] = mapped_column(
        Boolean, default=DEFAULT_ONE_WAY, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=DEFAULT_IS_ACTIVE, nullable=False
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    start_node: Mapped["NodeModel"] = relationship(
        "NodeModel", foreign_keys=[start_node_id], back_populates="outgoing_edges"
    )
    end_node: Mapped["NodeModel"] = relationship(
        "NodeModel", foreign_keys=[end_node_id], back_populates="incoming_edges"
    )

    __table_args__ = (
        Index(IDX_EDGES_GEOMETRY, geometry, postgresql_using="gist"),
        Index(IDX_EDGES_ROAD_TYPE, road_type),
        Index(IDX_EDGES_START_NODE, start_node_id),
        Index(IDX_EDGES_END_NODE, end_node_id),
        Index(IDX_EDGES_ACTIVE, is_active),
    )

    def __repr__(self) -> str:
        return f"<EdgeModel(id={self.id}, name={self.name}, type={self.road_type})>"

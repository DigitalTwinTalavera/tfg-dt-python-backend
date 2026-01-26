"""
Pydantic schemas for road network entities (nodes and edges).
Provides validation and serialization for API requests and responses.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import (
    DEFAULT_IS_ACTIVE,
    DEFAULT_LANES,
    DEFAULT_MAX_SPEED_KMH,
    DEFAULT_ONE_WAY,
    EDGE_NAME_MAX_LENGTH,
    LATITUDE_MAX,
    LATITUDE_MIN,
    LONGITUDE_MAX,
    LONGITUDE_MIN,
    MAX_LANES,
    MAX_SPEED_KMH,
    MIN_GEOMETRY_POINTS,
    MIN_LANES,
    MIN_SPEED_KMH,
    NODE_NAME_MAX_LENGTH,
)
from app.models.enums import NodeType, RoadType


# ============================================================================
# Coordinate Schema (reusable)
# ============================================================================


class Coordinate(BaseModel):
    """Represents a geographic coordinate (WGS84 - EPSG:4326)."""

    longitude: float = Field(
        ..., ge=LONGITUDE_MIN, le=LONGITUDE_MAX, description="Longitude in degrees"
    )
    latitude: float = Field(
        ..., ge=LATITUDE_MIN, le=LATITUDE_MAX, description="Latitude in degrees"
    )


# ============================================================================
# Node Schemas
# ============================================================================


class NodeBase(BaseModel):
    """Base schema for node attributes."""

    name: Optional[str] = Field(
        None, max_length=NODE_NAME_MAX_LENGTH, description="Node name"
    )
    node_type: NodeType = Field(
        default=NodeType.INTERSECTION, description="Type of node"
    )
    is_active: bool = Field(
        default=DEFAULT_IS_ACTIVE, description="Whether the node is active"
    )
    metadata_json: Optional[str] = Field(None, description="Optional JSON metadata")


class NodeCreate(NodeBase):
    """Schema for creating a new node."""

    longitude: float = Field(
        ..., ge=LONGITUDE_MIN, le=LONGITUDE_MAX, description="Longitude in degrees"
    )
    latitude: float = Field(
        ..., ge=LATITUDE_MIN, le=LATITUDE_MAX, description="Latitude in degrees"
    )


class NodeUpdate(BaseModel):
    """Schema for updating a node. All fields are optional."""

    name: Optional[str] = Field(
        None, max_length=NODE_NAME_MAX_LENGTH, description="Node name"
    )
    node_type: Optional[NodeType] = Field(None, description="Type of node")
    longitude: Optional[float] = Field(
        None, ge=LONGITUDE_MIN, le=LONGITUDE_MAX, description="Longitude in degrees"
    )
    latitude: Optional[float] = Field(
        None, ge=LATITUDE_MIN, le=LATITUDE_MAX, description="Latitude in degrees"
    )
    is_active: Optional[bool] = Field(None, description="Whether the node is active")
    metadata_json: Optional[str] = Field(None, description="Optional JSON metadata")


class NodeResponse(NodeBase):
    """Schema for node responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Unique node identifier")
    longitude: float = Field(..., description="Longitude in degrees")
    latitude: float = Field(..., description="Latitude in degrees")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class GeoJSONPoint(BaseModel):
    """GeoJSON Point representation for Godot client compatibility."""

    type: str = Field(default="Point", description="GeoJSON geometry type")
    coordinates: list[float] = Field(..., description="[longitude, latitude]")


class NodeGeoJSONResponse(BaseModel):
    """Node response with GeoJSON position for Godot client."""

    id: int = Field(..., description="Unique node identifier")
    name: Optional[str] = Field(None, description="Node name")
    node_type: str = Field(..., description="Type of node")
    is_active: bool = Field(..., description="Whether the node is active")
    position: GeoJSONPoint = Field(..., description="GeoJSON Point position")
    metadata_json: Optional[str] = Field(None, description="Optional JSON metadata")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class NodeListResponse(BaseModel):
    """Paginated list of nodes for Godot client."""

    items: list[NodeGeoJSONResponse] = Field(..., description="List of nodes")
    total: int = Field(..., description="Total number of nodes")


# ============================================================================
# Edge Schemas
# ============================================================================


class EdgeBase(BaseModel):
    """Base schema for edge attributes."""

    name: Optional[str] = Field(
        None, max_length=EDGE_NAME_MAX_LENGTH, description="Edge/road name"
    )
    road_type: RoadType = Field(
        default=RoadType.RESIDENTIAL, description="Type of road"
    )
    max_speed: int = Field(
        default=DEFAULT_MAX_SPEED_KMH,
        ge=MIN_SPEED_KMH,
        le=MAX_SPEED_KMH,
        description="Maximum speed in km/h",
    )
    lanes: int = Field(
        default=DEFAULT_LANES,
        ge=MIN_LANES,
        le=MAX_LANES,
        description="Number of lanes",
    )
    one_way: bool = Field(
        default=DEFAULT_ONE_WAY, description="Whether road is one-way"
    )
    is_active: bool = Field(
        default=DEFAULT_IS_ACTIVE, description="Whether edge is active"
    )
    metadata_json: Optional[str] = Field(None, description="Optional JSON metadata")


class EdgeCreate(EdgeBase):
    """Schema for creating a new edge."""

    start_node_id: int = Field(..., description="ID of the start node")
    end_node_id: int = Field(..., description="ID of the end node")
    geometry_coordinates: list[Coordinate] = Field(
        ...,
        min_length=MIN_GEOMETRY_POINTS,
        description="List of coordinates forming the LineString geometry",
    )
    length: float = Field(..., gt=0, description="Length in meters")


class EdgeUpdate(BaseModel):
    """Schema for updating an edge. All fields are optional."""

    name: Optional[str] = Field(
        None, max_length=EDGE_NAME_MAX_LENGTH, description="Edge/road name"
    )
    road_type: Optional[RoadType] = Field(None, description="Type of road")
    geometry_coordinates: Optional[list[Coordinate]] = Field(
        None,
        min_length=MIN_GEOMETRY_POINTS,
        description="List of coordinates forming the geometry",
    )
    length: Optional[float] = Field(None, gt=0, description="Length in meters")
    max_speed: Optional[int] = Field(
        None, ge=MIN_SPEED_KMH, le=MAX_SPEED_KMH, description="Maximum speed in km/h"
    )
    lanes: Optional[int] = Field(
        None, ge=MIN_LANES, le=MAX_LANES, description="Number of lanes"
    )
    one_way: Optional[bool] = Field(None, description="Whether road is one-way")
    is_active: Optional[bool] = Field(None, description="Whether edge is active")
    metadata_json: Optional[str] = Field(None, description="Optional JSON metadata")


class EdgeResponse(EdgeBase):
    """Schema for edge responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Unique edge identifier")
    start_node_id: int = Field(..., description="ID of the start node")
    end_node_id: int = Field(..., description="ID of the end node")
    geometry_coordinates: list[Coordinate] = Field(
        ..., description="List of coordinates forming the geometry"
    )
    length: float = Field(..., description="Length in meters")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class GeoJSONLineString(BaseModel):
    """GeoJSON LineString representation for Godot client compatibility."""

    type: str = Field(default="LineString", description="GeoJSON geometry type")
    coordinates: list[list[float]] = Field(
        ..., description="List of [longitude, latitude] pairs"
    )


class EdgeGeoJSONResponse(BaseModel):
    """Edge response with GeoJSON geometry for Godot client."""

    id: int = Field(..., description="Unique edge identifier")
    name: Optional[str] = Field(None, description="Edge/road name")
    start_node_id: int = Field(..., description="ID of the start node")
    end_node_id: int = Field(..., description="ID of the end node")
    road_type: str = Field(..., description="Type of road")
    geometry: GeoJSONLineString = Field(..., description="GeoJSON LineString geometry")
    length: float = Field(..., description="Length in meters")
    max_speed: int = Field(..., description="Maximum speed in km/h")
    lanes: int = Field(..., description="Number of lanes")
    one_way: bool = Field(..., description="Whether road is one-way")
    is_active: bool = Field(..., description="Whether edge is active")
    metadata_json: Optional[str] = Field(None, description="Optional JSON metadata")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class EdgeListResponse(BaseModel):
    """Paginated list of edges for Godot client."""

    items: list[EdgeGeoJSONResponse] = Field(..., description="List of edges")
    total: int = Field(..., description="Total number of edges")

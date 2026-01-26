"""
API endpoints for map operations, including OSM data import and data retrieval.
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from geoalchemy2.functions import ST_AsGeoJSON, ST_X, ST_Y
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    DEFAULT_PAGE_LIMIT,
    OSM_BATCH_SIZE,
    OSM_DATA_DIRECTORY,
    OSM_SUPPORTED_FORMATS,
    TAG_MAP,
)
from app.core.schemas.network_schema import (
    EdgeGeoJSONResponse,
    EdgeListResponse,
    GeoJSONLineString,
    GeoJSONPoint,
    NodeGeoJSONResponse,
    NodeListResponse,
)
from app.db.database import get_db_session
from app.models.road_network import EdgeModel, NodeModel
from app.services.osm_loader import OSMLoader, OSMLoadStats

router = APIRouter(prefix="/map", tags=[TAG_MAP])


class ImportRequest(BaseModel):
    """Request body for OSM import endpoint."""

    filepath: str = Field(
        ...,
        description="Path to OSM file on the server (relative to data/ directory)",
        examples=["talavera.osm", "spain/madrid.osm"],
    )
    clear_existing: bool = Field(
        default=False,
        description="If true, delete all existing nodes and edges before import",
    )
    batch_size: int = Field(
        default=OSM_BATCH_SIZE,
        ge=100,
        le=10000,
        description="Number of entities to insert per batch",
    )


class ImportResponse(BaseModel):
    """Response body for OSM import endpoint."""

    status: str = Field(..., description="Import status")
    nodes_parsed: int = Field(..., description="Total nodes found in file")
    nodes_imported: int = Field(..., description="Nodes imported to database")
    ways_parsed: int = Field(..., description="Total ways found in file")
    edges_imported: int = Field(..., description="Edges imported to database")
    ways_skipped: int = Field(..., description="Ways skipped (wrong type)")
    errors: int = Field(..., description="Number of errors during import")
    duration_seconds: float = Field(..., description="Import duration in seconds")


class ImportStatusResponse(BaseModel):
    """Response for import status check."""

    available: bool = Field(..., description="Whether import endpoint is available")
    supported_formats: list[str] = Field(..., description="Supported file formats")
    data_directory: str = Field(..., description="Server data directory path")


@router.get("/nodes", response_model=NodeListResponse)
async def get_nodes(
    skip: int = Query(0, ge=0, description="Number of nodes to skip"),
    limit: int = Query(
        DEFAULT_PAGE_LIMIT, ge=1, le=10000, description="Maximum nodes to return"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> NodeListResponse:
    """
    Get paginated list of nodes with GeoJSON-compatible positions.

    Returns nodes with their positions in GeoJSON Point format for
    compatibility with the Godot client.

    Args:
        skip: Number of records to skip (offset)
        limit: Maximum number of records to return
        session: Database session (injected)

    Returns:
        Paginated list of nodes with total count
    """
    # Get total count
    count_stmt = select(func.count()).select_from(NodeModel)
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    # Get nodes with coordinates extracted
    stmt = (
        select(
            NodeModel.id,
            NodeModel.name,
            NodeModel.node_type,
            NodeModel.is_active,
            NodeModel.metadata_json,
            NodeModel.created_at,
            NodeModel.updated_at,
            ST_X(NodeModel.position).label("longitude"),
            ST_Y(NodeModel.position).label("latitude"),
        )
        .offset(skip)
        .limit(limit)
        .order_by(NodeModel.id)
    )

    result = await session.execute(stmt)
    rows = result.fetchall()

    items = [
        NodeGeoJSONResponse(
            id=row.id,
            name=row.name,
            node_type=row.node_type,
            is_active=row.is_active,
            position=GeoJSONPoint(coordinates=[row.longitude, row.latitude]),
            metadata_json=row.metadata_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]

    return NodeListResponse(items=items, total=total)


@router.get("/edges", response_model=EdgeListResponse)
async def get_edges(
    skip: int = Query(0, ge=0, description="Number of edges to skip"),
    limit: int = Query(
        DEFAULT_PAGE_LIMIT, ge=1, le=10000, description="Maximum edges to return"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> EdgeListResponse:
    """
    Get paginated list of edges with GeoJSON-compatible geometries.

    Returns edges with their geometries in GeoJSON LineString format for
    compatibility with the Godot client.

    Args:
        skip: Number of records to skip (offset)
        limit: Maximum number of records to return
        session: Database session (injected)

    Returns:
        Paginated list of edges with total count
    """
    # Get total count
    count_stmt = select(func.count()).select_from(EdgeModel)
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    # Get edges with geometry as GeoJSON
    stmt = (
        select(
            EdgeModel.id,
            EdgeModel.name,
            EdgeModel.start_node_id,
            EdgeModel.end_node_id,
            EdgeModel.road_type,
            EdgeModel.length,
            EdgeModel.max_speed,
            EdgeModel.lanes,
            EdgeModel.one_way,
            EdgeModel.is_active,
            EdgeModel.metadata_json,
            EdgeModel.created_at,
            EdgeModel.updated_at,
            ST_AsGeoJSON(EdgeModel.geometry).label("geometry_json"),
        )
        .offset(skip)
        .limit(limit)
        .order_by(EdgeModel.id)
    )

    result = await session.execute(stmt)
    rows = result.fetchall()

    items = []
    for row in rows:
        # Parse GeoJSON geometry
        geom_data = json.loads(row.geometry_json)
        coordinates = geom_data.get("coordinates", [])

        items.append(
            EdgeGeoJSONResponse(
                id=row.id,
                name=row.name,
                start_node_id=row.start_node_id,
                end_node_id=row.end_node_id,
                road_type=row.road_type,
                geometry=GeoJSONLineString(coordinates=coordinates),
                length=row.length,
                max_speed=row.max_speed,
                lanes=row.lanes,
                one_way=row.one_way,
                is_active=row.is_active,
                metadata_json=row.metadata_json,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )

    return EdgeListResponse(items=items, total=total)


@router.get("/import/status", response_model=ImportStatusResponse)
async def get_import_status() -> ImportStatusResponse:
    """
    Check if the import functionality is available and get configuration info.

    Returns information about supported formats and the data directory.
    """
    return ImportStatusResponse(
        available=True,
        supported_formats=OSM_SUPPORTED_FORMATS,
        data_directory=f"{OSM_DATA_DIRECTORY}/",
    )


@router.post(
    "/import",
    response_model=ImportResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Import completed successfully"},
        400: {"description": "Invalid request or file format"},
        404: {"description": "File not found"},
        500: {"description": "Import failed due to server error"},
    },
)
async def import_osm_data(
    request: ImportRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ImportResponse:
    """
    Import OSM data from a file on the server into the database.

    The file must be located in the server's data directory.
    Only .osm (XML) format is currently supported.

    **Note**: This endpoint does not have authentication yet.
    In production, it should be restricted to admin users.

    Args:
        request: Import configuration including file path and options
        session: Database session (injected)

    Returns:
        Statistics about the import process

    Raises:
        HTTPException: If file not found or import fails
    """
    # Construct full path (relative to data directory)
    data_dir = Path(OSM_DATA_DIRECTORY)
    filepath = data_dir / request.filepath

    # Security check: prevent path traversal
    try:
        filepath = filepath.resolve()
        if not str(filepath).startswith(str(data_dir.resolve())):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file path: path traversal not allowed",
            )
    except HTTPException:
        raise
    except (OSError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file path: {e}",
        )

    # Check file exists
    if not filepath.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.filepath}",
        )

    try:
        loader = OSMLoader(
            session,
            batch_size=request.batch_size,
        )

        stats = await loader.load_from_file(
            str(filepath),
            clear_existing=request.clear_existing,
        )

        return ImportResponse(
            status="completed",
            nodes_parsed=stats.nodes_parsed,
            nodes_imported=stats.nodes_imported,
            ways_parsed=stats.ways_parsed,
            edges_imported=stats.edges_imported,
            ways_skipped=stats.ways_skipped,
            errors=stats.errors,
            duration_seconds=round(stats.duration_seconds, 2),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )

"""
API endpoints for map operations, including OSM data import.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    OSM_DATA_DIRECTORY,
    OSM_SUPPORTED_FORMATS,
    TAG_MAP,
)
from app.db.database import get_db_session
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
        default=1000,
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
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path",
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

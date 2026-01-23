"""
Repository for EdgeModel database operations.
Provides node-based queries and edge-specific operations.
"""

from typing import Optional

from geoalchemy2.functions import (
    ST_DWithin,
    ST_Intersects,
    ST_MakeEnvelope,
    ST_MakePoint,
    ST_SetSRID,
)
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    DEFAULT_PAGE_LIMIT,
    MAX_BULK_OPERATION_LIMIT,
    SRID_WGS84,
)
from app.db.repositories.base import BaseRepository
from app.models.enums import RoadType
from app.models.road_network import EdgeModel


class EdgeRepository(BaseRepository[EdgeModel]):
    """
    Repository for edge operations with node-based and spatial query support.

    Extends BaseRepository with methods for finding edges by nodes,
    road type filtering, and spatial bounding box queries.
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the EdgeRepository.

        Args:
            session: AsyncSession for database operations
        """
        super().__init__(session)

    async def find_by_start_node(
        self,
        start_node_id: int,
        *,
        active_only: bool = True,
    ) -> list[EdgeModel]:
        """
        Find all edges starting from a specific node.

        Args:
            start_node_id: The starting node's ID
            active_only: If True, only return active edges

        Returns:
            List of edges starting from the node
        """
        stmt = select(EdgeModel).where(EdgeModel.start_node_id == start_node_id)

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_end_node(
        self,
        end_node_id: int,
        *,
        active_only: bool = True,
    ) -> list[EdgeModel]:
        """
        Find all edges ending at a specific node.

        Args:
            end_node_id: The ending node's ID
            active_only: If True, only return active edges

        Returns:
            List of edges ending at the node
        """
        stmt = select(EdgeModel).where(EdgeModel.end_node_id == end_node_id)

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_nodes(
        self,
        start_node_id: int,
        end_node_id: int,
        *,
        active_only: bool = True,
    ) -> Optional[EdgeModel]:
        """
        Find an edge connecting two specific nodes.

        Args:
            start_node_id: The starting node's ID
            end_node_id: The ending node's ID
            active_only: If True, only return if edge is active

        Returns:
            Edge if found, None otherwise
        """
        stmt = select(EdgeModel).where(
            and_(
                EdgeModel.start_node_id == start_node_id,
                EdgeModel.end_node_id == end_node_id,
            )
        )

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_connected_to_node(
        self,
        node_id: int,
        *,
        active_only: bool = True,
    ) -> list[EdgeModel]:
        """
        Find all edges connected to a node (either as start or end).

        Args:
            node_id: The node's ID
            active_only: If True, only return active edges

        Returns:
            List of edges connected to the node
        """
        stmt = select(EdgeModel).where(
            (EdgeModel.start_node_id == node_id) | (EdgeModel.end_node_id == node_id)
        )

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_road_type(
        self,
        road_type: RoadType,
        *,
        active_only: bool = True,
        skip: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[EdgeModel]:
        """
        Get all edges of a specific road type.

        Args:
            road_type: The road type to filter by
            active_only: If True, only return active edges
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of edges matching the road type
        """
        stmt = select(EdgeModel).where(EdgeModel.road_type == road_type.value)

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        stmt = stmt.offset(skip).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_in_bounding_box(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        *,
        active_only: bool = True,
    ) -> list[EdgeModel]:
        """
        Find all edges that intersect with a bounding box.

        Args:
            min_lon: Minimum longitude (west)
            min_lat: Minimum latitude (south)
            max_lon: Maximum longitude (east)
            max_lat: Maximum latitude (north)
            active_only: If True, only return active edges

        Returns:
            List of edges intersecting the bounding box
        """
        bbox = ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, SRID_WGS84)
        stmt = select(EdgeModel).where(ST_Intersects(EdgeModel.geometry, bbox))

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_near_point(
        self,
        longitude: float,
        latitude: float,
        radius_meters: float,
        *,
        active_only: bool = True,
    ) -> list[EdgeModel]:
        """
        Find all edges within a specified distance from a point.

        Args:
            longitude: Reference point longitude
            latitude: Reference point latitude
            radius_meters: Search radius in meters
            active_only: If True, only return active edges

        Returns:
            List of edges within the radius
        """
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)
        stmt = select(EdgeModel).where(
            ST_DWithin(
                EdgeModel.geometry,
                point,
                radius_meters,
                use_spheroid=True,
            )
        )

        if active_only:
            stmt = stmt.where(EdgeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_with_nodes(self, edge_id: int) -> Optional[EdgeModel]:
        """
        Get an edge with its connected nodes eagerly loaded.

        Args:
            edge_id: The edge's primary key

        Returns:
            Edge with nodes if found, None otherwise
        """
        stmt = (
            select(EdgeModel)
            .where(EdgeModel.id == edge_id)
            .options(
                selectinload(EdgeModel.start_node),
                selectinload(EdgeModel.end_node),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_active_status(
        self,
        edge_id: int,
        is_active: bool,
    ) -> Optional[EdgeModel]:
        """
        Set the active status of an edge.

        Args:
            edge_id: The edge's primary key
            is_active: New active status

        Returns:
            Updated edge if found, None otherwise
        """
        return await self.update(edge_id, {"is_active": is_active})

    async def count_by_road_type(self, active_only: bool = True) -> dict[str, int]:
        """
        Count edges grouped by their road type.

        Args:
            active_only: If True, only count active edges

        Returns:
            Dictionary mapping road type to count
        """
        counts: dict[str, int] = {}
        for road_type in RoadType:
            filters = {"road_type": road_type.value}
            if active_only:
                filters["is_active"] = True
            counts[road_type.value] = await self.count(filters)
        return counts

    async def get_total_length(
        self,
        road_type: Optional[RoadType] = None,
        active_only: bool = True,
    ) -> float:
        """
        Calculate the total length of edges in meters.

        Args:
            road_type: Optional filter by road type
            active_only: If True, only include active edges

        Returns:
            Total length in meters
        """
        edges = await self.list(
            skip=0,
            limit=MAX_BULK_OPERATION_LIMIT,
            filters={"road_type": road_type.value} if road_type else None,
        )

        if active_only:
            edges = [e for e in edges if e.is_active]

        return sum(e.length for e in edges)

    async def find_path_edges(
        self,
        edge_ids: list[int],
        *,
        preserve_order: bool = True,
    ) -> list[EdgeModel]:
        """
        Retrieve multiple edges by their IDs, optionally preserving order.

        Useful for loading edges that form a route.

        Args:
            edge_ids: List of edge IDs
            preserve_order: If True, return edges in the same order as edge_ids

        Returns:
            List of edges
        """
        if not edge_ids:
            return []

        stmt = select(EdgeModel).where(EdgeModel.id.in_(edge_ids))
        result = await self._session.execute(stmt)
        edges = list(result.scalars().all())

        if preserve_order:
            edge_map = {e.id: e for e in edges}
            return [edge_map[eid] for eid in edge_ids if eid in edge_map]

        return edges

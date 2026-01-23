"""
Repository for NodeModel database operations.
Provides spatial queries and node-specific operations.
"""

from typing import Optional

from geoalchemy2.functions import ST_DWithin, ST_Distance, ST_MakePoint, ST_SetSRID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import DEFAULT_PAGE_LIMIT, SRID_WGS84
from app.db.repositories.base import BaseRepository
from app.models.enums import NodeType
from app.models.road_network import NodeModel


class NodeRepository(BaseRepository[NodeModel]):
    """
    Repository for node operations with spatial query support.

    Extends BaseRepository with methods for spatial queries,
    such as finding nodes within a radius or nearest neighbors.
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the NodeRepository.

        Args:
            session: AsyncSession for database operations
        """
        super().__init__(session)

    async def find_within_radius(
        self,
        longitude: float,
        latitude: float,
        radius_meters: float,
        *,
        node_type: Optional[NodeType] = None,
        active_only: bool = True,
    ) -> list[NodeModel]:
        """
        Find all nodes within a specified radius of a point.

        Uses PostGIS ST_DWithin for efficient spatial indexing.

        Args:
            longitude: Center point longitude
            latitude: Center point latitude
            radius_meters: Search radius in meters
            node_type: Optional filter by node type
            active_only: If True, only return active nodes

        Returns:
            List of nodes within the radius
        """
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)
        stmt = select(NodeModel).where(
            ST_DWithin(
                NodeModel.position,
                point,
                radius_meters,
                use_spheroid=True,
            )
        )

        if node_type is not None:
            stmt = stmt.where(NodeModel.node_type == node_type.value)

        if active_only:
            stmt = stmt.where(NodeModel.is_active.is_(True))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_nearest(
        self,
        longitude: float,
        latitude: float,
        *,
        limit: int = 1,
        node_type: Optional[NodeType] = None,
        active_only: bool = True,
    ) -> list[NodeModel]:
        """
        Find the nearest nodes to a specified point.

        Uses PostGIS ST_Distance for accurate distance calculation.

        Args:
            longitude: Reference point longitude
            latitude: Reference point latitude
            limit: Maximum number of nodes to return
            node_type: Optional filter by node type
            active_only: If True, only return active nodes

        Returns:
            List of nearest nodes, ordered by distance
        """
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)
        distance = ST_Distance(NodeModel.position, point, use_spheroid=True)

        stmt = select(NodeModel).order_by(distance)

        if node_type is not None:
            stmt = stmt.where(NodeModel.node_type == node_type.value)

        if active_only:
            stmt = stmt.where(NodeModel.is_active.is_(True))

        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_type(
        self,
        node_type: NodeType,
        *,
        active_only: bool = True,
        skip: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[NodeModel]:
        """
        Get all nodes of a specific type.

        Args:
            node_type: The type of nodes to retrieve
            active_only: If True, only return active nodes
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of nodes matching the type
        """
        stmt = select(NodeModel).where(NodeModel.node_type == node_type.value)

        if active_only:
            stmt = stmt.where(NodeModel.is_active.is_(True))

        stmt = stmt.offset(skip).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_with_edges(self, node_id: int) -> Optional[NodeModel]:
        """
        Get a node with its connected edges eagerly loaded.

        Args:
            node_id: The node's primary key

        Returns:
            Node with edges if found, None otherwise
        """
        stmt = (
            select(NodeModel)
            .where(NodeModel.id == node_id)
            .options(
                selectinload(NodeModel.outgoing_edges),
                selectinload(NodeModel.incoming_edges),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_connected_nodes(
        self,
        node_id: int,
        *,
        outgoing: bool = True,
        incoming: bool = True,
    ) -> list[NodeModel]:
        """
        Get all nodes directly connected to a specified node.

        Args:
            node_id: The source node's primary key
            outgoing: Include nodes reachable via outgoing edges
            incoming: Include nodes reachable via incoming edges

        Returns:
            List of connected nodes
        """
        from app.models.road_network import EdgeModel

        connected_ids: set[int] = set()

        if outgoing:
            stmt = select(EdgeModel.end_node_id).where(
                EdgeModel.start_node_id == node_id
            )
            result = await self._session.execute(stmt)
            connected_ids.update(row[0] for row in result.fetchall())

        if incoming:
            stmt = select(EdgeModel.start_node_id).where(
                EdgeModel.end_node_id == node_id
            )
            result = await self._session.execute(stmt)
            connected_ids.update(row[0] for row in result.fetchall())

        if not connected_ids:
            return []

        stmt = select(NodeModel).where(NodeModel.id.in_(connected_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_active_status(
        self,
        node_id: int,
        is_active: bool,
    ) -> Optional[NodeModel]:
        """
        Set the active status of a node.

        Args:
            node_id: The node's primary key
            is_active: New active status

        Returns:
            Updated node if found, None otherwise
        """
        return await self.update(node_id, {"is_active": is_active})

    async def count_by_type(self, active_only: bool = True) -> dict[str, int]:
        """
        Count nodes grouped by their type.

        Args:
            active_only: If True, only count active nodes

        Returns:
            Dictionary mapping node type to count
        """
        counts: dict[str, int] = {}
        for node_type in NodeType:
            filters = {"node_type": node_type.value}
            if active_only:
                filters["is_active"] = True
            counts[node_type.value] = await self.count(filters)
        return counts

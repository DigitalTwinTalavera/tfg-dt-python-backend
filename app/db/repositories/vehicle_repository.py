"""
Repository for VehicleModel database operations.
Provides bulk updates and vehicle-specific operations optimized for simulation.
"""

from typing import Optional
from uuid import UUID

from geoalchemy2.functions import (
    ST_DWithin,
    ST_Distance,
    ST_MakePoint,
    ST_SetSRID,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    DEFAULT_PAGE_LIMIT,
    MAX_BULK_OPERATION_LIMIT,
    SRID_WGS84,
)
from app.db.repositories.base import BaseRepository
from app.models.enums import VehicleStatus
from app.models.vehicle import VehicleModel


class VehicleRepository(BaseRepository[VehicleModel]):
    """
    Repository for vehicle operations with bulk update support.

    Extends BaseRepository with methods optimized for high-frequency
    simulation updates, including bulk position updates and status queries.
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the VehicleRepository.

        Args:
            session: AsyncSession for database operations
        """
        super().__init__(session)

    async def get_by_status(
        self,
        status: VehicleStatus,
        *,
        skip: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> list[VehicleModel]:
        """
        Get all vehicles with a specific status.

        Args:
            status: The vehicle status to filter by
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of vehicles with the specified status
        """
        stmt = (
            select(VehicleModel)
            .where(VehicleModel.status == status.value)
            .offset(skip)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_vehicles(self) -> list[VehicleModel]:
        """
        Get all vehicles that are not in FINISHED status.

        Returns:
            List of active vehicles
        """
        stmt = select(VehicleModel).where(
            VehicleModel.status != VehicleStatus.FINISHED.value
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_moving_vehicles(self) -> list[VehicleModel]:
        """
        Get all vehicles that are currently moving.

        Returns:
            List of moving vehicles
        """
        stmt = select(VehicleModel).where(
            VehicleModel.status == VehicleStatus.MOVING.value
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_vehicles_on_edge(self, edge_id: int) -> list[VehicleModel]:
        """
        Get all vehicles currently on a specific edge.

        Args:
            edge_id: The edge's ID

        Returns:
            List of vehicles on the edge
        """
        stmt = select(VehicleModel).where(VehicleModel.current_edge_id == edge_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_within_radius(
        self,
        longitude: float,
        latitude: float,
        radius_meters: float,
        *,
        active_only: bool = True,
    ) -> list[VehicleModel]:
        """
        Find all vehicles within a specified radius of a point.

        Args:
            longitude: Center point longitude
            latitude: Center point latitude
            radius_meters: Search radius in meters
            active_only: If True, only return non-FINISHED vehicles

        Returns:
            List of vehicles within the radius
        """
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)
        stmt = select(VehicleModel).where(
            ST_DWithin(
                VehicleModel.position,
                point,
                radius_meters,
                use_spheroid=True,
            )
        )

        if active_only:
            stmt = stmt.where(VehicleModel.status != VehicleStatus.FINISHED.value)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_nearest(
        self,
        longitude: float,
        latitude: float,
        *,
        limit: int = 1,
        active_only: bool = True,
    ) -> list[VehicleModel]:
        """
        Find the nearest vehicles to a specified point.

        Args:
            longitude: Reference point longitude
            latitude: Reference point latitude
            limit: Maximum number of vehicles to return
            active_only: If True, only return non-FINISHED vehicles

        Returns:
            List of nearest vehicles, ordered by distance
        """
        point = ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)
        distance = ST_Distance(VehicleModel.position, point, use_spheroid=True)

        stmt = select(VehicleModel).order_by(distance)

        if active_only:
            stmt = stmt.where(VehicleModel.status != VehicleStatus.FINISHED.value)

        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_position(
        self,
        vehicle_id: UUID,
        longitude: float,
        latitude: float,
        *,
        velocity: Optional[float] = None,
        acceleration: Optional[float] = None,
        heading: Optional[float] = None,
    ) -> Optional[VehicleModel]:
        """
        Update a vehicle's position and optionally its physics state.

        Optimized for high-frequency updates during simulation.

        Args:
            vehicle_id: The vehicle's UUID
            longitude: New longitude
            latitude: New latitude
            velocity: Optional new velocity in m/s
            acceleration: Optional new acceleration in m/s²
            heading: Optional new heading in degrees

        Returns:
            Updated vehicle if found, None otherwise
        """
        vehicle = await self.get(vehicle_id)
        if vehicle is None:
            return None

        vehicle.position = ST_SetSRID(ST_MakePoint(longitude, latitude), SRID_WGS84)

        if velocity is not None:
            vehicle.velocity = velocity
        if acceleration is not None:
            vehicle.acceleration = acceleration
        if heading is not None:
            vehicle.heading = heading

        await self._session.flush()
        await self._session.refresh(vehicle)
        return vehicle

    async def update_status(
        self,
        vehicle_id: UUID,
        status: VehicleStatus,
    ) -> Optional[VehicleModel]:
        """
        Update a vehicle's status.

        Args:
            vehicle_id: The vehicle's UUID
            status: New status

        Returns:
            Updated vehicle if found, None otherwise
        """
        return await self.update(vehicle_id, {"status": status.value})

    async def update_route(
        self,
        vehicle_id: UUID,
        route_edges: list[int],
        *,
        current_edge_id: Optional[int] = None,
    ) -> Optional[VehicleModel]:
        """
        Update a vehicle's route.

        Args:
            vehicle_id: The vehicle's UUID
            route_edges: Ordered list of edge IDs forming the route
            current_edge_id: Optional current edge ID

        Returns:
            Updated vehicle if found, None otherwise
        """
        values: dict = {"route_edges": route_edges}
        if current_edge_id is not None:
            values["current_edge_id"] = current_edge_id

        return await self.update(vehicle_id, values)

    async def bulk_update_positions(
        self,
        updates: list[dict],
    ) -> int:
        """
        Bulk update vehicle positions for simulation efficiency.

        Each update dict should contain:
        - vehicle_id: UUID
        - longitude: float
        - latitude: float
        - velocity: Optional[float]
        - acceleration: Optional[float]
        - heading: Optional[float]

        Args:
            updates: List of position update dictionaries

        Returns:
            Number of vehicles updated
        """
        updated_count = 0
        for upd in updates:
            vehicle_id = upd.get("vehicle_id")
            if vehicle_id is None:
                continue

            result = await self.update_position(
                vehicle_id=vehicle_id,
                longitude=upd["longitude"],
                latitude=upd["latitude"],
                velocity=upd.get("velocity"),
                acceleration=upd.get("acceleration"),
                heading=upd.get("heading"),
            )
            if result is not None:
                updated_count += 1

        return updated_count

    async def bulk_update_status(
        self,
        vehicle_ids: list[UUID],
        status: VehicleStatus,
    ) -> int:
        """
        Update the status of multiple vehicles at once.

        Args:
            vehicle_ids: List of vehicle UUIDs
            status: New status for all vehicles

        Returns:
            Number of vehicles updated
        """
        if not vehicle_ids:
            return 0

        stmt = (
            update(VehicleModel)
            .where(VehicleModel.id.in_(vehicle_ids))
            .values(status=status.value)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def mark_finished(self, vehicle_ids: list[UUID]) -> int:
        """
        Mark multiple vehicles as finished (completed their routes).

        Args:
            vehicle_ids: List of vehicle UUIDs

        Returns:
            Number of vehicles marked as finished
        """
        return await self.bulk_update_status(vehicle_ids, VehicleStatus.FINISHED)

    async def count_by_status(self) -> dict[str, int]:
        """
        Count vehicles grouped by their status.

        Returns:
            Dictionary mapping status to count
        """
        counts: dict[str, int] = {}
        for status in VehicleStatus:
            counts[status.value] = await self.count({"status": status.value})
        return counts

    async def count_active(self) -> int:
        """
        Count vehicles that are not in FINISHED status.

        Returns:
            Number of active vehicles
        """
        total = await self.count()
        finished = await self.count({"status": VehicleStatus.FINISHED.value})
        return total - finished

    async def delete_finished_vehicles(self) -> int:
        """
        Delete all vehicles with FINISHED status.

        Useful for cleanup after simulation runs.

        Returns:
            Number of vehicles deleted
        """
        finished = await self.get_by_status(
            VehicleStatus.FINISHED, limit=MAX_BULK_OPERATION_LIMIT
        )
        vehicle_ids = [v.id for v in finished]
        if not vehicle_ids:
            return 0
        return await self.bulk_delete(vehicle_ids)

    async def get_vehicles_by_ids(self, vehicle_ids: list[UUID]) -> list[VehicleModel]:
        """
        Get multiple vehicles by their IDs.

        Args:
            vehicle_ids: List of vehicle UUIDs

        Returns:
            List of vehicles found
        """
        if not vehicle_ids:
            return []

        stmt = select(VehicleModel).where(VehicleModel.id.in_(vehicle_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

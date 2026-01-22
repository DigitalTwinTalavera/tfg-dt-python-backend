"""
Vehicle Manager service for managing vehicles in the traffic simulation.
Provides CRUD operations and position updates for vehicles.
"""

from typing import Optional
from uuid import UUID

from geoalchemy2.functions import ST_SetSRID, ST_MakePoint
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SRID_WGS84
from app.core.schemas.vehicle_schema import VehicleCreate, VehicleStateUpdate
from app.models.enums import VehicleStatus
from app.models.vehicle import VehicleModel


class VehicleManager:
    """
    Manager class for vehicle operations in the simulation.

    Provides methods for adding, updating, querying, and removing vehicles.
    Designed for high-frequency position updates during simulation.
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the VehicleManager with a database session.

        Args:
            session: AsyncSession for database operations
        """
        self._session = session

    async def add_vehicle(self, vehicle_data: VehicleCreate) -> VehicleModel:
        """
        Add a new vehicle to the simulation.

        Args:
            vehicle_data: VehicleCreate schema with vehicle properties

        Returns:
            VehicleModel: The created vehicle instance
        """
        vehicle = VehicleModel(
            position=ST_SetSRID(
                ST_MakePoint(vehicle_data.longitude, vehicle_data.latitude),
                SRID_WGS84,
            ),
            velocity=vehicle_data.velocity,
            acceleration=vehicle_data.acceleration,
            heading=vehicle_data.heading,
            status=vehicle_data.status.value,
            current_edge_id=vehicle_data.current_edge_id,
            route_edges=vehicle_data.route_edges,
        )
        self._session.add(vehicle)
        await self._session.flush()
        await self._session.refresh(vehicle)
        return vehicle

    async def update_position(
        self,
        vehicle_id: UUID,
        new_position: VehicleStateUpdate,
    ) -> Optional[VehicleModel]:
        """
        Update a vehicle's position and physics state.
        Optimized for high-frequency updates during simulation.

        Args:
            vehicle_id: UUID of the vehicle to update
            new_position: VehicleStateUpdate with new position and optional physics

        Returns:
            VehicleModel if found and updated, None otherwise
        """
        stmt = select(VehicleModel).where(VehicleModel.id == vehicle_id)
        result = await self._session.execute(stmt)
        vehicle = result.scalar_one_or_none()

        if vehicle is None:
            return None

        # Update position
        vehicle.position = ST_SetSRID(
            ST_MakePoint(new_position.longitude, new_position.latitude),
            SRID_WGS84,
        )

        # Update physics if provided
        if new_position.velocity is not None:
            vehicle.velocity = new_position.velocity
        if new_position.acceleration is not None:
            vehicle.acceleration = new_position.acceleration
        if new_position.heading is not None:
            vehicle.heading = new_position.heading

        await self._session.flush()
        await self._session.refresh(vehicle)
        return vehicle

    async def get_all_active_vehicles(self) -> list[VehicleModel]:
        """
        Get all active vehicles in the simulation.
        Active vehicles are those not in FINISHED status.

        Returns:
            List of VehicleModel instances that are active
        """
        stmt = select(VehicleModel).where(
            VehicleModel.status != VehicleStatus.FINISHED.value
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_vehicle_by_id(self, vehicle_id: UUID) -> Optional[VehicleModel]:
        """
        Get a vehicle by its ID.

        Args:
            vehicle_id: UUID of the vehicle

        Returns:
            VehicleModel if found, None otherwise
        """
        stmt = select(VehicleModel).where(VehicleModel.id == vehicle_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def remove_vehicle(self, vehicle_id: UUID) -> bool:
        """
        Remove a vehicle from the simulation.

        Args:
            vehicle_id: UUID of the vehicle to remove

        Returns:
            True if vehicle was found and removed, False otherwise
        """
        vehicle = await self.get_vehicle_by_id(vehicle_id)
        if vehicle is None:
            return False

        await self._session.delete(vehicle)
        await self._session.flush()
        return True

    async def update_vehicle_status(
        self,
        vehicle_id: UUID,
        new_status: VehicleStatus,
    ) -> Optional[VehicleModel]:
        """
        Update a vehicle's status.

        Args:
            vehicle_id: UUID of the vehicle
            new_status: New VehicleStatus value

        Returns:
            VehicleModel if found and updated, None otherwise
        """
        vehicle = await self.get_vehicle_by_id(vehicle_id)
        if vehicle is None:
            return None

        vehicle.status = new_status.value
        await self._session.flush()
        await self._session.refresh(vehicle)
        return vehicle

    async def update_vehicle_route(
        self,
        vehicle_id: UUID,
        route_edges: list[int],
        current_edge_id: Optional[int] = None,
    ) -> Optional[VehicleModel]:
        """
        Update a vehicle's route.

        Args:
            vehicle_id: UUID of the vehicle
            route_edges: Ordered list of edge IDs forming the route
            current_edge_id: Optional ID of the current edge

        Returns:
            VehicleModel if found and updated, None otherwise
        """
        vehicle = await self.get_vehicle_by_id(vehicle_id)
        if vehicle is None:
            return None

        vehicle.route_edges = route_edges
        if current_edge_id is not None:
            vehicle.current_edge_id = current_edge_id

        await self._session.flush()
        await self._session.refresh(vehicle)
        return vehicle

    async def get_vehicles_on_edge(self, edge_id: int) -> list[VehicleModel]:
        """
        Get all vehicles currently on a specific edge.

        Args:
            edge_id: ID of the edge

        Returns:
            List of VehicleModel instances on the edge
        """
        stmt = select(VehicleModel).where(VehicleModel.current_edge_id == edge_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_active_vehicles(self) -> int:
        """
        Count active vehicles in the simulation.

        Returns:
            Number of active vehicles
        """
        vehicles = await self.get_all_active_vehicles()
        return len(vehicles)

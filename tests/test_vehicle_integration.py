"""
Integration tests for VehicleManager with database.
Tests actual database operations using a test database session.
"""

import pytest
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.schemas.vehicle_schema import VehicleCreate, VehicleStateUpdate
from app.models.enums import VehicleStatus
from app.services.vehicle_manager import VehicleManager


# Use the same database for integration tests
TEST_DATABASE_URL = settings.database_url


# ============================================================================
# Helper function to create session and manager
# ============================================================================


async def create_test_session():
    """Create a test database session."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    session = async_session_maker()
    return session, engine


# ============================================================================
# VehicleManager Integration Tests
# ============================================================================


class TestVehicleManagerIntegration:
    """Integration tests for VehicleManager."""

    @pytest.mark.asyncio
    async def test_add_vehicle(self):
        """Test adding a new vehicle."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(
                longitude=-4.8306,
                latitude=39.9634,
                velocity=15.0,
                acceleration=1.0,
                heading=90.0,
                status=VehicleStatus.MOVING,
            )

            vehicle = await manager.add_vehicle(vehicle_data)

            assert vehicle is not None
            assert vehicle.id is not None
            assert vehicle.velocity == 15.0
            assert vehicle.acceleration == 1.0
            assert vehicle.heading == 90.0
            assert vehicle.status == VehicleStatus.MOVING.value
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_add_vehicle_minimal(self):
        """Test adding a vehicle with minimal data."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(longitude=-4.8306, latitude=39.9634)

            vehicle = await manager.add_vehicle(vehicle_data)

            assert vehicle is not None
            assert vehicle.velocity == 0.0
            assert vehicle.acceleration == 0.0
            assert vehicle.heading == 0.0
            assert vehicle.status == VehicleStatus.IDLE.value
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_vehicle_by_id(self):
        """Test retrieving a vehicle by ID."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(
                longitude=-4.8306, latitude=39.9634, velocity=15.0
            )

            created = await manager.add_vehicle(vehicle_data)
            retrieved = await manager.get_vehicle_by_id(created.id)

            assert retrieved is not None
            assert retrieved.id == created.id
            assert retrieved.velocity == created.velocity
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_vehicle_by_id_not_found(self):
        """Test retrieving a non-existent vehicle."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            random_id = uuid4()

            vehicle = await manager.get_vehicle_by_id(random_id)

            assert vehicle is None
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_position(self):
        """Test updating a vehicle's position."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(
                longitude=-4.8306, latitude=39.9634, velocity=15.0
            )
            vehicle = await manager.add_vehicle(vehicle_data)

            new_position = VehicleStateUpdate(
                longitude=-4.8400,
                latitude=39.9700,
                velocity=20.0,
                heading=180.0,
            )
            updated = await manager.update_position(vehicle.id, new_position)

            assert updated is not None
            assert updated.velocity == 20.0
            assert updated.heading == 180.0
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_position_not_found(self):
        """Test updating position for non-existent vehicle."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            random_id = uuid4()
            new_position = VehicleStateUpdate(longitude=-4.8400, latitude=39.9700)

            result = await manager.update_position(random_id, new_position)

            assert result is None
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_all_active_vehicles(self):
        """Test getting all active vehicles."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)

            # Add multiple vehicles with different statuses
            vehicles_data = [
                VehicleCreate(
                    longitude=-4.8306, latitude=39.9634, status=VehicleStatus.IDLE
                ),
                VehicleCreate(
                    longitude=-4.8400, latitude=39.9700, status=VehicleStatus.MOVING
                ),
                VehicleCreate(
                    longitude=-4.8500, latitude=39.9800, status=VehicleStatus.FINISHED
                ),
            ]

            for data in vehicles_data:
                await manager.add_vehicle(data)

            active = await manager.get_all_active_vehicles()

            # Should exclude FINISHED vehicles from newly added
            active_statuses = [v.status for v in active]
            assert VehicleStatus.FINISHED.value not in active_statuses
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_remove_vehicle(self):
        """Test removing a vehicle."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(longitude=-4.8306, latitude=39.9634)
            vehicle = await manager.add_vehicle(vehicle_data)
            vehicle_id = vehicle.id

            result = await manager.remove_vehicle(vehicle_id)

            assert result is True

            # Verify vehicle is removed
            retrieved = await manager.get_vehicle_by_id(vehicle_id)
            assert retrieved is None
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_remove_vehicle_not_found(self):
        """Test removing a non-existent vehicle."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            random_id = uuid4()

            result = await manager.remove_vehicle(random_id)

            assert result is False
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_vehicle_status(self):
        """Test updating a vehicle's status."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(
                longitude=-4.8306, latitude=39.9634, status=VehicleStatus.MOVING
            )
            vehicle = await manager.add_vehicle(vehicle_data)

            updated = await manager.update_vehicle_status(
                vehicle.id, VehicleStatus.STOPPED
            )

            assert updated is not None
            assert updated.status == VehicleStatus.STOPPED.value
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_vehicle_route(self):
        """Test updating a vehicle's route (without current_edge_id FK)."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)
            vehicle_data = VehicleCreate(longitude=-4.8306, latitude=39.9634)
            vehicle = await manager.add_vehicle(vehicle_data)
            route = [1, 2, 3, 4, 5]

            # Update route without setting current_edge_id (FK constraint)
            updated = await manager.update_vehicle_route(
                vehicle.id, route_edges=route
            )

            assert updated is not None
            assert updated.route_edges == route
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_count_active_vehicles(self):
        """Test counting active vehicles."""
        session, engine = await create_test_session()
        try:
            manager = VehicleManager(session)

            # Add some vehicles
            for i in range(3):
                data = VehicleCreate(
                    longitude=-4.8306 + i * 0.01,
                    latitude=39.9634,
                    status=VehicleStatus.MOVING,
                )
                await manager.add_vehicle(data)

            count = await manager.count_active_vehicles()

            assert count >= 3
        finally:
            await session.rollback()
            await session.close()
            await engine.dispose()

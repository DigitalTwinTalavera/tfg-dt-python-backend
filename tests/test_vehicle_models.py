"""
Unit tests for vehicle models, enums, and schemas.
"""

import pytest
from pydantic import ValidationError

from app.core.constants import (
    DEFAULT_ACCELERATION,
    DEFAULT_HEADING,
    DEFAULT_VELOCITY,
    MAX_ACCELERATION,
    MAX_HEADING,
    MAX_VELOCITY,
    MIN_ACCELERATION,
    MIN_VELOCITY,
)
from app.core.schemas.vehicle_schema import (
    VehicleCreate,
    VehicleResponse,
    VehicleStateUpdate,
    VehicleUpdate,
)
from app.models.enums import VehicleStatus


# ============================================================================
# VehicleStatus Enum Tests
# ============================================================================


class TestVehicleStatusEnum:
    """Tests for VehicleStatus enum."""

    def test_vehicle_status_values(self):
        """Test all expected VehicleStatus values exist."""
        expected_values = {"idle", "moving", "stopped", "waiting", "finished"}
        actual_values = {status.value for status in VehicleStatus}
        assert actual_values == expected_values

    def test_vehicle_status_is_string_enum(self):
        """Test VehicleStatus inherits from str."""
        assert isinstance(VehicleStatus.IDLE.value, str)
        assert VehicleStatus.MOVING == "moving"


# ============================================================================
# VehicleCreate Schema Tests
# ============================================================================


class TestVehicleCreateSchema:
    """Tests for VehicleCreate schema."""

    def test_vehicle_create_valid_minimal(self):
        """Test creating a vehicle with minimal required fields."""
        vehicle = VehicleCreate(
            longitude=-4.8306,
            latitude=39.9634,
        )
        assert vehicle.longitude == -4.8306
        assert vehicle.latitude == 39.9634
        assert vehicle.velocity == DEFAULT_VELOCITY
        assert vehicle.acceleration == DEFAULT_ACCELERATION
        assert vehicle.heading == DEFAULT_HEADING
        assert vehicle.status == VehicleStatus.IDLE
        assert vehicle.current_edge_id is None
        assert vehicle.route_edges is None

    def test_vehicle_create_valid_full(self):
        """Test creating a vehicle with all fields."""
        vehicle = VehicleCreate(
            longitude=-4.8306,
            latitude=39.9634,
            velocity=15.5,
            acceleration=2.0,
            heading=90.0,
            status=VehicleStatus.MOVING,
            current_edge_id=1,
            route_edges=[1, 2, 3],
        )
        assert vehicle.velocity == 15.5
        assert vehicle.acceleration == 2.0
        assert vehicle.heading == 90.0
        assert vehicle.status == VehicleStatus.MOVING
        assert vehicle.current_edge_id == 1
        assert vehicle.route_edges == [1, 2, 3]

    def test_vehicle_create_invalid_longitude_too_low(self):
        """Test validation fails for longitude below -180."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-181, latitude=39.9634)

    def test_vehicle_create_invalid_longitude_too_high(self):
        """Test validation fails for longitude above 180."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=181, latitude=39.9634)

    def test_vehicle_create_invalid_latitude_too_low(self):
        """Test validation fails for latitude below -90."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-4.8306, latitude=-91)

    def test_vehicle_create_invalid_latitude_too_high(self):
        """Test validation fails for latitude above 90."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-4.8306, latitude=91)

    def test_vehicle_create_invalid_velocity_negative(self):
        """Test validation fails for negative velocity."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-4.8306, latitude=39.9634, velocity=-1)

    def test_vehicle_create_invalid_velocity_too_high(self):
        """Test validation fails for velocity above max."""
        with pytest.raises(ValidationError):
            VehicleCreate(
                longitude=-4.8306, latitude=39.9634, velocity=MAX_VELOCITY + 1
            )

    def test_vehicle_create_invalid_acceleration_too_low(self):
        """Test validation fails for acceleration below min."""
        with pytest.raises(ValidationError):
            VehicleCreate(
                longitude=-4.8306, latitude=39.9634, acceleration=MIN_ACCELERATION - 1
            )

    def test_vehicle_create_invalid_acceleration_too_high(self):
        """Test validation fails for acceleration above max."""
        with pytest.raises(ValidationError):
            VehicleCreate(
                longitude=-4.8306, latitude=39.9634, acceleration=MAX_ACCELERATION + 1
            )

    def test_vehicle_create_invalid_heading_negative(self):
        """Test validation fails for negative heading."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-4.8306, latitude=39.9634, heading=-1)

    def test_vehicle_create_invalid_heading_too_high(self):
        """Test validation fails for heading >= 360."""
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-4.8306, latitude=39.9634, heading=MAX_HEADING)


# ============================================================================
# VehicleUpdate Schema Tests
# ============================================================================


class TestVehicleUpdateSchema:
    """Tests for VehicleUpdate schema."""

    def test_vehicle_update_all_optional(self):
        """Test that all fields are optional for updates."""
        update = VehicleUpdate()
        assert update.longitude is None
        assert update.latitude is None
        assert update.velocity is None
        assert update.acceleration is None
        assert update.heading is None
        assert update.status is None
        assert update.current_edge_id is None
        assert update.route_edges is None

    def test_vehicle_update_partial(self):
        """Test partial update with some fields."""
        update = VehicleUpdate(velocity=20.0, status=VehicleStatus.MOVING)
        assert update.velocity == 20.0
        assert update.status == VehicleStatus.MOVING
        assert update.longitude is None

    def test_vehicle_update_invalid_velocity(self):
        """Test validation fails for invalid velocity in update."""
        with pytest.raises(ValidationError):
            VehicleUpdate(velocity=-5.0)


# ============================================================================
# VehicleStateUpdate Schema Tests
# ============================================================================


class TestVehicleStateUpdateSchema:
    """Tests for VehicleStateUpdate schema (high-frequency updates)."""

    def test_state_update_valid_minimal(self):
        """Test state update with only position."""
        update = VehicleStateUpdate(longitude=-4.8306, latitude=39.9634)
        assert update.longitude == -4.8306
        assert update.latitude == 39.9634
        assert update.velocity is None
        assert update.acceleration is None
        assert update.heading is None

    def test_state_update_valid_full(self):
        """Test state update with all physics."""
        update = VehicleStateUpdate(
            longitude=-4.8306,
            latitude=39.9634,
            velocity=15.0,
            acceleration=1.5,
            heading=45.0,
        )
        assert update.velocity == 15.0
        assert update.acceleration == 1.5
        assert update.heading == 45.0

    def test_state_update_invalid_position(self):
        """Test validation fails for invalid position."""
        with pytest.raises(ValidationError):
            VehicleStateUpdate(longitude=200, latitude=39.9634)


# ============================================================================
# VehicleResponse Schema Tests
# ============================================================================


class TestVehicleResponseSchema:
    """Tests for VehicleResponse schema."""

    def test_vehicle_response_from_dict(self):
        """Test creating VehicleResponse from dictionary."""
        from datetime import datetime
        from uuid import uuid4

        vehicle_id = uuid4()
        now = datetime.now()
        data = {
            "id": vehicle_id,
            "longitude": -4.8306,
            "latitude": 39.9634,
            "velocity": 15.0,
            "acceleration": 1.0,
            "heading": 90.0,
            "status": VehicleStatus.MOVING,
            "current_edge_id": 1,
            "route_edges": [1, 2, 3],
            "created_at": now,
            "updated_at": now,
        }
        response = VehicleResponse(**data)
        assert response.id == vehicle_id
        assert response.longitude == -4.8306
        assert response.velocity == 15.0
        assert response.status == VehicleStatus.MOVING


# ============================================================================
# Vehicle Fixtures Tests
# ============================================================================


class TestVehicleFixtures:
    """Tests for vehicle sample data helpers."""

    def test_vehicle_status_transitions(self):
        """Test that status transitions make logical sense."""
        # A vehicle can be in these states
        valid_statuses = [s for s in VehicleStatus]
        assert len(valid_statuses) == 5

        # IDLE -> MOVING is valid start
        vehicle = VehicleCreate(longitude=-4.8306, latitude=39.9634)
        assert vehicle.status == VehicleStatus.IDLE

    def test_velocity_bounds(self):
        """Test velocity bounds are reasonable."""
        assert MIN_VELOCITY == 0.0
        assert MAX_VELOCITY == 200.0  # ~720 km/h

    def test_acceleration_bounds(self):
        """Test acceleration bounds are reasonable."""
        assert MIN_ACCELERATION == -50.0  # Hard braking
        assert MAX_ACCELERATION == 20.0  # Sports car

    def test_heading_bounds(self):
        """Test heading bounds are 0-360 degrees."""
        # Valid heading
        vehicle = VehicleCreate(longitude=-4.8306, latitude=39.9634, heading=359.9)
        assert vehicle.heading == 359.9

        # Heading 360 should fail (lt, not le)
        with pytest.raises(ValidationError):
            VehicleCreate(longitude=-4.8306, latitude=39.9634, heading=360.0)

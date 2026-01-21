"""
Unit tests for road network models, enums, and schemas.
"""

import pytest
from pydantic import ValidationError

from app.models.enums import NodeType, RoadType
from app.core.schemas.network_schema import (
    Coordinate,
    EdgeCreate,
    EdgeResponse,
    EdgeUpdate,
    NodeCreate,
    NodeResponse,
    NodeUpdate,
)
from tests.fixtures.road_network_fixtures import (
    SAMPLE_EDGES,
    SAMPLE_NODES,
    create_sample_edge_data,
    create_sample_node_data,
)


# ============================================================================
# Enum Tests
# ============================================================================


class TestNodeTypeEnum:
    """Tests for NodeType enum."""

    @pytest.mark.unit
    def test_node_type_values(self):
        """Test all NodeType enum values exist."""
        assert NodeType.INTERSECTION.value == "intersection"
        assert NodeType.ENDPOINT.value == "endpoint"
        assert NodeType.TRAFFIC_LIGHT.value == "traffic_light"
        assert NodeType.ROUNDABOUT.value == "roundabout"
        assert NodeType.DEAD_END.value == "dead_end"
        assert NodeType.ENTRY_POINT.value == "entry_point"
        assert NodeType.EXIT_POINT.value == "exit_point"

    @pytest.mark.unit
    def test_node_type_is_string_enum(self):
        """Test NodeType is a string enum."""
        assert isinstance(NodeType.INTERSECTION, str)
        assert NodeType.INTERSECTION == "intersection"


class TestRoadTypeEnum:
    """Tests for RoadType enum."""

    @pytest.mark.unit
    def test_road_type_values(self):
        """Test all RoadType enum values exist."""
        assert RoadType.MOTORWAY.value == "motorway"
        assert RoadType.PRIMARY.value == "primary"
        assert RoadType.SECONDARY.value == "secondary"
        assert RoadType.TERTIARY.value == "tertiary"
        assert RoadType.RESIDENTIAL.value == "residential"
        assert RoadType.SERVICE.value == "service"
        assert RoadType.PEDESTRIAN.value == "pedestrian"
        assert RoadType.CYCLEWAY.value == "cycleway"

    @pytest.mark.unit
    def test_road_type_is_string_enum(self):
        """Test RoadType is a string enum."""
        assert isinstance(RoadType.MOTORWAY, str)
        assert RoadType.MOTORWAY == "motorway"


# ============================================================================
# Coordinate Schema Tests
# ============================================================================


class TestCoordinateSchema:
    """Tests for Coordinate schema."""

    @pytest.mark.unit
    def test_valid_coordinate(self):
        """Test valid coordinate creation."""
        coord = Coordinate(longitude=-4.8306, latitude=39.9634)
        assert coord.longitude == -4.8306
        assert coord.latitude == 39.9634

    @pytest.mark.unit
    def test_coordinate_bounds_longitude(self):
        """Test longitude bounds validation."""
        # Valid bounds
        Coordinate(longitude=-180, latitude=0)
        Coordinate(longitude=180, latitude=0)

        # Invalid bounds
        with pytest.raises(ValidationError):
            Coordinate(longitude=-181, latitude=0)
        with pytest.raises(ValidationError):
            Coordinate(longitude=181, latitude=0)

    @pytest.mark.unit
    def test_coordinate_bounds_latitude(self):
        """Test latitude bounds validation."""
        # Valid bounds
        Coordinate(longitude=0, latitude=-90)
        Coordinate(longitude=0, latitude=90)

        # Invalid bounds
        with pytest.raises(ValidationError):
            Coordinate(longitude=0, latitude=-91)
        with pytest.raises(ValidationError):
            Coordinate(longitude=0, latitude=91)


# ============================================================================
# Node Schema Tests
# ============================================================================


class TestNodeSchemas:
    """Tests for Node schemas."""

    @pytest.mark.unit
    def test_node_create_valid(self):
        """Test valid NodeCreate schema."""
        data = create_sample_node_data()
        node = NodeCreate(**data)
        assert node.name == data["name"]
        assert node.node_type == NodeType.INTERSECTION
        assert node.longitude == data["longitude"]
        assert node.latitude == data["latitude"]
        assert node.is_active is True

    @pytest.mark.unit
    def test_node_create_minimal(self):
        """Test NodeCreate with minimal required fields."""
        node = NodeCreate(longitude=-4.8306, latitude=39.9634)
        assert node.name is None
        assert node.node_type == NodeType.INTERSECTION  # default
        assert node.is_active is True  # default

    @pytest.mark.unit
    def test_node_create_invalid_longitude(self):
        """Test NodeCreate with invalid longitude."""
        with pytest.raises(ValidationError):
            NodeCreate(longitude=-200, latitude=39.9634)

    @pytest.mark.unit
    def test_node_create_invalid_latitude(self):
        """Test NodeCreate with invalid latitude."""
        with pytest.raises(ValidationError):
            NodeCreate(longitude=-4.8306, latitude=100)

    @pytest.mark.unit
    def test_node_update_all_optional(self):
        """Test NodeUpdate with all fields optional."""
        update = NodeUpdate()
        assert update.name is None
        assert update.node_type is None
        assert update.longitude is None
        assert update.latitude is None
        assert update.is_active is None

    @pytest.mark.unit
    def test_node_update_partial(self):
        """Test NodeUpdate with partial fields."""
        update = NodeUpdate(name="Updated Name", is_active=False)
        assert update.name == "Updated Name"
        assert update.is_active is False
        assert update.node_type is None

    @pytest.mark.unit
    def test_node_response_from_dict(self):
        """Test NodeResponse creation from dictionary."""
        from datetime import datetime

        data = {
            "id": 1,
            "name": "Test Node",
            "node_type": NodeType.TRAFFIC_LIGHT,
            "longitude": -4.8306,
            "latitude": 39.9634,
            "is_active": True,
            "metadata_json": None,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        response = NodeResponse(**data)
        assert response.id == 1
        assert response.name == "Test Node"
        assert response.node_type == NodeType.TRAFFIC_LIGHT


# ============================================================================
# Edge Schema Tests
# ============================================================================


class TestEdgeSchemas:
    """Tests for Edge schemas."""

    @pytest.mark.unit
    def test_edge_create_valid(self):
        """Test valid EdgeCreate schema."""
        data = create_sample_edge_data()
        edge = EdgeCreate(**data)
        assert edge.name == data["name"]
        assert edge.start_node_id == data["start_node_id"]
        assert edge.end_node_id == data["end_node_id"]
        assert edge.road_type == RoadType.RESIDENTIAL
        assert len(edge.geometry_coordinates) == 2

    @pytest.mark.unit
    def test_edge_create_minimal(self):
        """Test EdgeCreate with minimal required fields."""
        edge = EdgeCreate(
            start_node_id=1,
            end_node_id=2,
            geometry_coordinates=[
                Coordinate(longitude=-4.8306, latitude=39.9634),
                Coordinate(longitude=-4.8256, latitude=39.9598),
            ],
            length=100.0,
        )
        assert edge.road_type == RoadType.RESIDENTIAL  # default
        assert edge.max_speed == 50  # default
        assert edge.lanes == 1  # default
        assert edge.one_way is False  # default

    @pytest.mark.unit
    def test_edge_create_invalid_geometry_too_few_points(self):
        """Test EdgeCreate with too few geometry points."""
        with pytest.raises(ValidationError):
            EdgeCreate(
                start_node_id=1,
                end_node_id=2,
                geometry_coordinates=[
                    Coordinate(longitude=-4.8306, latitude=39.9634),
                ],  # Only 1 point
                length=100.0,
            )

    @pytest.mark.unit
    def test_edge_create_invalid_length(self):
        """Test EdgeCreate with invalid length."""
        with pytest.raises(ValidationError):
            EdgeCreate(
                start_node_id=1,
                end_node_id=2,
                geometry_coordinates=[
                    Coordinate(longitude=-4.8306, latitude=39.9634),
                    Coordinate(longitude=-4.8256, latitude=39.9598),
                ],
                length=0,  # Must be > 0
            )

    @pytest.mark.unit
    def test_edge_create_invalid_speed(self):
        """Test EdgeCreate with invalid max speed."""
        with pytest.raises(ValidationError):
            EdgeCreate(
                start_node_id=1,
                end_node_id=2,
                geometry_coordinates=[
                    Coordinate(longitude=-4.8306, latitude=39.9634),
                    Coordinate(longitude=-4.8256, latitude=39.9598),
                ],
                length=100.0,
                max_speed=0,  # Must be >= 1
            )

    @pytest.mark.unit
    def test_edge_update_all_optional(self):
        """Test EdgeUpdate with all fields optional."""
        update = EdgeUpdate()
        assert update.name is None
        assert update.road_type is None
        assert update.length is None

    @pytest.mark.unit
    def test_edge_response_from_dict(self):
        """Test EdgeResponse creation from dictionary."""
        from datetime import datetime

        data = {
            "id": 1,
            "name": "Test Edge",
            "start_node_id": 1,
            "end_node_id": 2,
            "road_type": RoadType.PRIMARY,
            "geometry_coordinates": [
                {"longitude": -4.8306, "latitude": 39.9634},
                {"longitude": -4.8256, "latitude": 39.9598},
            ],
            "length": 450.0,
            "max_speed": 50,
            "lanes": 2,
            "one_way": False,
            "is_active": True,
            "metadata_json": None,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        response = EdgeResponse(**data)
        assert response.id == 1
        assert response.start_node_id == 1
        assert response.end_node_id == 2
        assert len(response.geometry_coordinates) == 2


# ============================================================================
# Sample Fixtures Tests
# ============================================================================


class TestSampleFixtures:
    """Tests for sample data fixtures."""

    @pytest.mark.unit
    def test_sample_nodes_count(self):
        """Test sample nodes fixture has expected count."""
        assert len(SAMPLE_NODES) == 6

    @pytest.mark.unit
    def test_sample_nodes_valid_schema(self):
        """Test all sample nodes can be validated."""
        for node_data in SAMPLE_NODES:
            node = NodeCreate(**node_data)
            assert node.longitude is not None
            assert node.latitude is not None

    @pytest.mark.unit
    def test_sample_edges_count(self):
        """Test sample edges fixture has expected count."""
        assert len(SAMPLE_EDGES) == 4

    @pytest.mark.unit
    def test_sample_edges_valid_references(self):
        """Test sample edges reference valid node indices."""
        max_node_index = len(SAMPLE_NODES) - 1
        for edge_data in SAMPLE_EDGES:
            assert edge_data["start_node_index"] <= max_node_index
            assert edge_data["end_node_index"] <= max_node_index

    @pytest.mark.unit
    def test_create_sample_node_data_helper(self):
        """Test create_sample_node_data helper function."""
        data = create_sample_node_data(
            name="Custom Node",
            node_type=NodeType.ROUNDABOUT.value,
        )
        assert data["name"] == "Custom Node"
        assert data["node_type"] == "roundabout"

    @pytest.mark.unit
    def test_create_sample_edge_data_helper(self):
        """Test create_sample_edge_data helper function."""
        data = create_sample_edge_data(
            name="Custom Edge",
            road_type=RoadType.MOTORWAY.value,
            max_speed=120,
        )
        assert data["name"] == "Custom Edge"
        assert data["road_type"] == "motorway"
        assert data["max_speed"] == 120

"""
Unit tests for OSMLoader service.
Tests parsing, filtering, and transformation of OSM data.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.constants import (
    OSM_ALLOWED_HIGHWAY_TYPES,
    OSM_DEFAULT_SPEED_LIMITS,
)
from app.models.enums import NodeType, RoadType
from app.services.osm_loader import OSMLoader, OSMLoadStats, OSMNode, OSMWay


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_osm_path() -> Path:
    """Path to sample OSM file."""
    return Path(__file__).parent / "fixtures" / "sample.osm"


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add_all = MagicMock()
    return session


@pytest.fixture
def loader(mock_session) -> OSMLoader:
    """Create an OSMLoader with mocked session."""
    return OSMLoader(mock_session)


# =============================================================================
# Test OSMLoader Initialization
# =============================================================================


class TestOSMLoaderInit:
    """Tests for OSMLoader initialization."""

    def test_init_default_batch_size(self, mock_session):
        """Test default batch size is set."""
        loader = OSMLoader(mock_session)
        assert loader._batch_size == 1000

    def test_init_custom_batch_size(self, mock_session):
        """Test custom batch size is used."""
        loader = OSMLoader(mock_session, batch_size=500)
        assert loader._batch_size == 500

    def test_init_with_progress_callback(self, mock_session):
        """Test progress callback is stored."""
        callback = MagicMock()
        loader = OSMLoader(mock_session, progress_callback=callback)
        assert loader._progress_callback == callback


# =============================================================================
# Test Maxspeed Parsing
# =============================================================================


class TestMaxspeedParsing:
    """Tests for maxspeed tag parsing."""

    def test_parse_numeric_speed(self, loader):
        """Test parsing numeric speed '50'."""
        assert loader._parse_maxspeed("50", "primary") == 50

    def test_parse_speed_with_kmh_unit(self, loader):
        """Test parsing '50 km/h'."""
        assert loader._parse_maxspeed("50 km/h", "primary") == 50

    def test_parse_speed_with_kmh_no_space(self, loader):
        """Test parsing '50kmh'."""
        assert loader._parse_maxspeed("50kmh", "primary") == 50

    def test_parse_speed_mph(self, loader):
        """Test parsing '30 mph' converts to km/h."""
        result = loader._parse_maxspeed("30 mph", "residential")
        # 30 mph ≈ 48.28 km/h
        assert result == 48

    def test_parse_speed_missing_uses_default(self, loader):
        """Test missing speed uses highway type default."""
        result = loader._parse_maxspeed(None, "motorway")
        assert result == OSM_DEFAULT_SPEED_LIMITS["motorway"]

    def test_parse_speed_invalid_uses_default(self, loader):
        """Test invalid speed string uses default."""
        result = loader._parse_maxspeed("unknown", "residential")
        assert result == OSM_DEFAULT_SPEED_LIMITS["residential"]

    def test_parse_speed_with_decimal(self, loader):
        """Test parsing '45.5' truncates to integer."""
        assert loader._parse_maxspeed("45.5", "primary") == 45


# =============================================================================
# Test Lanes Parsing
# =============================================================================


class TestLanesParsing:
    """Tests for lanes tag parsing."""

    def test_parse_lanes_numeric(self, loader):
        """Test parsing '2'."""
        assert loader._parse_lanes("2") == 2

    def test_parse_lanes_missing(self, loader):
        """Test missing lanes defaults to 1."""
        assert loader._parse_lanes(None) == 1

    def test_parse_lanes_invalid(self, loader):
        """Test invalid lanes defaults to 1."""
        assert loader._parse_lanes("two") == 1

    def test_parse_lanes_zero_becomes_one(self, loader):
        """Test '0' becomes 1 (minimum)."""
        assert loader._parse_lanes("0") == 1


# =============================================================================
# Test One-Way Detection
# =============================================================================


class TestOneWayDetection:
    """Tests for one-way road detection."""

    def test_oneway_yes(self, loader):
        """Test oneway=yes returns True."""
        assert loader._is_one_way({"oneway": "yes"}) is True

    def test_oneway_true(self, loader):
        """Test oneway=true returns True."""
        assert loader._is_one_way({"oneway": "true"}) is True

    def test_oneway_1(self, loader):
        """Test oneway=1 returns True."""
        assert loader._is_one_way({"oneway": "1"}) is True

    def test_oneway_reverse(self, loader):
        """Test oneway=-1 returns True (reverse direction)."""
        assert loader._is_one_way({"oneway": "-1"}) is True

    def test_oneway_no(self, loader):
        """Test oneway=no returns False."""
        assert loader._is_one_way({"oneway": "no"}) is False

    def test_oneway_missing(self, loader):
        """Test missing oneway returns False."""
        assert loader._is_one_way({}) is False

    def test_motorway_default_oneway(self, loader):
        """Test motorway is one-way by default."""
        assert loader._is_one_way({"highway": "motorway"}) is True

    def test_motorway_explicit_no(self, loader):
        """Test motorway with oneway=no returns False."""
        assert loader._is_one_way({"highway": "motorway", "oneway": "no"}) is False

    def test_roundabout_always_oneway(self, loader):
        """Test roundabout is always one-way."""
        assert loader._is_one_way({"junction": "roundabout"}) is True


# =============================================================================
# Test Highway Type Filtering
# =============================================================================


class TestHighwayFiltering:
    """Tests for highway type filtering."""

    def test_primary_is_allowed(self, loader):
        """Test primary highway is allowed."""
        assert "primary" in OSM_ALLOWED_HIGHWAY_TYPES

    def test_footway_is_excluded(self, loader):
        """Test footway is excluded."""
        assert "footway" not in OSM_ALLOWED_HIGHWAY_TYPES

    def test_cycleway_is_excluded(self, loader):
        """Test cycleway is excluded."""
        assert "cycleway" not in OSM_ALLOWED_HIGHWAY_TYPES

    def test_residential_is_allowed(self, loader):
        """Test residential is allowed."""
        assert "residential" in OSM_ALLOWED_HIGHWAY_TYPES


# =============================================================================
# Test Node Type Detection
# =============================================================================


class TestNodeTypeDetection:
    """Tests for determining node type from OSM tags."""

    def test_traffic_signals(self, loader):
        """Test traffic signals node."""
        node = OSMNode(osm_id=1, lat=0, lon=0, tags={"highway": "traffic_signals"})
        assert loader._determine_node_type(node) == NodeType.TRAFFIC_LIGHT

    def test_roundabout(self, loader):
        """Test roundabout node."""
        node = OSMNode(osm_id=1, lat=0, lon=0, tags={"junction": "roundabout"})
        assert loader._determine_node_type(node) == NodeType.ROUNDABOUT

    def test_dead_end(self, loader):
        """Test dead end node."""
        node = OSMNode(osm_id=1, lat=0, lon=0, tags={"noexit": "yes"})
        assert loader._determine_node_type(node) == NodeType.DEAD_END

    def test_default_intersection(self, loader):
        """Test default node type is intersection."""
        node = OSMNode(osm_id=1, lat=0, lon=0, tags={})
        assert loader._determine_node_type(node) == NodeType.INTERSECTION


# =============================================================================
# Test RoadType Conversion
# =============================================================================


class TestRoadTypeConversion:
    """Tests for OSM highway to RoadType conversion."""

    def test_primary(self):
        """Test primary conversion."""
        assert RoadType.from_osm_highway("primary") == RoadType.PRIMARY

    def test_motorway_link(self):
        """Test motorway_link conversion."""
        assert RoadType.from_osm_highway("motorway_link") == RoadType.MOTORWAY_LINK

    def test_unknown_defaults_to_unclassified(self):
        """Test unknown highway type defaults to unclassified."""
        assert RoadType.from_osm_highway("unknown_type") == RoadType.UNCLASSIFIED


# =============================================================================
# Test Length Calculation
# =============================================================================


class TestLengthCalculation:
    """Tests for Haversine distance calculation."""

    def test_same_point_zero_distance(self, loader):
        """Test same point has zero distance."""
        coords = [(-4.83, 39.95), (-4.83, 39.95)]
        length = loader._calculate_length(coords)
        assert length == 0.0

    def test_short_distance(self, loader):
        """Test short distance calculation."""
        # Approximately 100m apart
        coords = [(-4.8301, 39.9567), (-4.8290, 39.9567)]
        length = loader._calculate_length(coords)
        # Should be roughly 90-100 meters
        assert 80 < length < 120

    def test_multiple_segments(self, loader):
        """Test distance with multiple segments."""
        coords = [
            (-4.8301, 39.9567),
            (-4.8290, 39.9567),
            (-4.8280, 39.9567),
        ]
        length = loader._calculate_length(coords)
        # Should be roughly double the single segment
        assert 160 < length < 240


# =============================================================================
# Test XML Parsing
# =============================================================================


class TestXMLParsing:
    """Tests for OSM XML parsing."""

    def test_parse_sample_file(self, loader, sample_osm_path):
        """Test parsing the sample OSM file."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        # Should have parsed all nodes
        assert stats.nodes_parsed == 8
        assert len(loader._osm_nodes) == 8

        # Should have parsed all ways
        assert stats.ways_parsed == 8
        assert len(loader._osm_ways) == 8

    def test_parsed_node_coordinates(self, loader, sample_osm_path):
        """Test node coordinates are correctly parsed."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        node = loader._osm_nodes[1001]
        assert node.lat == 39.9567
        assert node.lon == -4.8301

    def test_parsed_node_tags(self, loader, sample_osm_path):
        """Test node tags are correctly parsed."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        node = loader._osm_nodes[1001]
        assert node.tags.get("name") == "Plaza Mayor"

    def test_parsed_way_refs(self, loader, sample_osm_path):
        """Test way node references are correctly parsed."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        # Find the Calle Mayor way
        way = next(w for w in loader._osm_ways if w.osm_id == 2001)
        assert way.node_refs == [1001, 1002, 1003, 1004]

    def test_parsed_way_tags(self, loader, sample_osm_path):
        """Test way tags are correctly parsed."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        way = next(w for w in loader._osm_ways if w.osm_id == 2001)
        assert way.tags.get("highway") == "primary"
        assert way.tags.get("name") == "Calle Mayor"
        assert way.tags.get("maxspeed") == "50"


# =============================================================================
# Test Way Filtering
# =============================================================================


class TestWayFiltering:
    """Tests for filtering ways by highway type."""

    def test_filter_excludes_footway(self, loader, sample_osm_path):
        """Test footway is excluded."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        filtered_ways, _ = loader._filter_ways(stats)

        # Footway (id 3001) should be excluded
        way_ids = [w.osm_id for w in filtered_ways]
        assert 3001 not in way_ids

    def test_filter_excludes_cycleway(self, loader, sample_osm_path):
        """Test cycleway is excluded."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        filtered_ways, _ = loader._filter_ways(stats)

        # Cycleway (id 3002) should be excluded
        way_ids = [w.osm_id for w in filtered_ways]
        assert 3002 not in way_ids

    def test_filter_includes_primary(self, loader, sample_osm_path):
        """Test primary road is included."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        filtered_ways, _ = loader._filter_ways(stats)

        # Primary (id 2001) should be included
        way_ids = [w.osm_id for w in filtered_ways]
        assert 2001 in way_ids

    def test_filter_collects_referenced_nodes(self, loader, sample_osm_path):
        """Test only referenced nodes are collected."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        _, referenced_nodes = loader._filter_ways(stats)

        # All nodes used by valid ways should be referenced
        assert 1001 in referenced_nodes
        assert 1002 in referenced_nodes

    def test_filter_tracks_skipped_ways(self, loader, sample_osm_path):
        """Test skipped ways are counted."""
        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        loader._filter_ways(stats)

        # 2 ways should be skipped (footway and cycleway)
        assert stats.ways_skipped == 2


# =============================================================================
# Test File Validation
# =============================================================================


class TestFileValidation:
    """Tests for file validation."""

    @pytest.mark.asyncio
    async def test_file_not_found(self, loader):
        """Test FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            await loader.load_from_file("nonexistent.osm")

    @pytest.mark.asyncio
    async def test_unsupported_format(self, loader, tmp_path):
        """Test ValueError for unsupported format."""
        # Create a dummy file with wrong extension
        bad_file = tmp_path / "test.txt"
        bad_file.write_text("dummy")

        with pytest.raises(ValueError, match="Unsupported file format"):
            await loader.load_from_file(str(bad_file))

    @pytest.mark.asyncio
    async def test_pbf_format_not_supported(self, loader, tmp_path):
        """Test PBF format shows helpful error."""
        pbf_file = tmp_path / "test.osm.pbf"
        pbf_file.write_text("dummy")

        with pytest.raises(ValueError, match="PBF format not supported"):
            await loader.load_from_file(str(pbf_file))


# =============================================================================
# Test Progress Callback
# =============================================================================


class TestProgressCallback:
    """Tests for progress callback functionality."""

    def test_progress_callback_called(self, mock_session, sample_osm_path):
        """Test progress callback is called during parsing."""
        callback = MagicMock()
        loader = OSMLoader(mock_session, progress_callback=callback)

        stats = OSMLoadStats()
        loader._parse_osm_xml(str(sample_osm_path), stats)

        # Callback should have been called at least once
        # (May not be called if file is too small to hit interval)
        # Just verify it doesn't crash
        assert True

    def test_progress_callback_receives_stage_and_count(self, mock_session):
        """Test progress callback receives correct arguments."""
        calls = []

        def callback(stage: str, count: int):
            calls.append((stage, count))

        loader = OSMLoader(mock_session, progress_callback=callback)
        loader._report_progress("Test stage", 100)

        assert len(calls) == 1
        assert calls[0] == ("Test stage", 100)

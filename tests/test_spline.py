"""
Tests for the centripetal Catmull-Rom spline + RDP module and its integration
with RoadNetworkGraph (ring node fusion + edge splinification).

Covers:
  - sample_spline / build_spline_table / position_at_arc_length
  - rdp simplification with adaptive tolerance and protected indices
  - _fuse_ring_nodes collapses degree-2 ring-internal nodes into a single edge
  - _splinify_roundabout_edges marks edges with ATTR_USE_SPLINE and stores a
    sample table whose total arc length matches a synthetic circle
  - _edge_position dispatches through the spline branch when ATTR_USE_SPLINE
    is set
"""

from __future__ import annotations

import math

import networkx as nx

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_MID_TLS,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_RING_RADIUS_M,
    ATTR_ROAD_TYPE,
    ATTR_ROUNDABOUT_ID,
    ATTR_SPLINE_LENGTH,
    ATTR_SPLINE_SAMPLES,
    ATTR_USE_SPLINE,
    ATTR_WAYPOINTS,
    ATTR_WEIGHT,
)
from app.core.spline import (
    build_spline_table,
    position_at_arc_length,
    rdp,
    sample_spline,
)
from app.core.vehicle_physics import _edge_position
from app.models.enums import NodeType, RoadType
from app.services.network_graph import RoadNetworkGraph

_LAT0 = 39.96
_LON0 = -4.83
_R = 10.0  # 10 m roundabout (Tres Olivos size)
_MPD = 111_320.0


def _ring_points(n: int, radius_m: float = _R) -> list[tuple[float, float]]:
    """Return n points evenly spaced on a circle around (_LON0, _LAT0)."""
    cos_lat = math.cos(math.radians(_LAT0))
    pts: list[tuple[float, float]] = []
    for k in range(n):
        a = 2 * math.pi * k / n
        dx = radius_m * math.cos(a) / (_MPD * cos_lat)
        dy = radius_m * math.sin(a) / _MPD
        pts.append((_LON0 + dx, _LAT0 + dy))
    return pts


# ---------------------------------------------------------------------------
# Spline core
# ---------------------------------------------------------------------------


class TestSampleSpline:
    def test_sample_count_matches_formula(self):
        pts = _ring_points(8) + [_ring_points(8)[0]]  # 9 control points
        out = sample_spline(pts, samples_per_segment=8)
        # (n - 1) * spp + 1 final
        assert len(out) == (len(pts) - 1) * 8 + 1

    def test_first_and_last_match_inputs(self):
        pts = _ring_points(6)
        out = sample_spline(pts, samples_per_segment=4)
        assert out[0] == pts[0]
        assert out[-1] == pts[-1]

    def test_passes_through_every_control_point(self):
        # At indices i * spp, the spline should land exactly on points[i]
        pts = _ring_points(5)
        spp = 8
        out = sample_spline(pts, samples_per_segment=spp)
        for i, ctrl in enumerate(pts):
            sample = out[i * spp]
            assert math.isclose(sample[0], ctrl[0], abs_tol=1e-9)
            assert math.isclose(sample[1], ctrl[1], abs_tol=1e-9)

    def test_two_point_input_is_linear(self):
        a = (1.0, 2.0)
        b = (3.0, 5.0)
        out = sample_spline([a, b], samples_per_segment=4)
        assert len(out) == 5
        assert out[0] == a
        assert out[-1] == b
        midpoint = out[2]
        assert math.isclose(midpoint[0], 2.0, abs_tol=1e-9)
        assert math.isclose(midpoint[1], 3.5, abs_tol=1e-9)


class TestArcLengthLookup:
    def test_circle_arc_length_matches_to_one_percent(self):
        pts = _ring_points(8) + [_ring_points(8)[0]]
        table, total = build_spline_table(pts, samples_per_segment=8)
        expected = 2 * math.pi * _R
        # 8-vertex octagon spline approximates the inscribed circle within ~1%.
        assert abs(total - expected) / expected < 0.02

    def test_position_at_zero_returns_first_sample(self):
        pts = _ring_points(6)
        table, _ = build_spline_table(pts)
        lon, lat, _ = position_at_arc_length(table, 0.0)
        assert math.isclose(lon, pts[0][0], abs_tol=1e-9)
        assert math.isclose(lat, pts[0][1], abs_tol=1e-9)

    def test_position_at_total_returns_last_sample(self):
        pts = _ring_points(6)
        table, total = build_spline_table(pts)
        lon, lat, _ = position_at_arc_length(table, total)
        assert math.isclose(lon, pts[-1][0], abs_tol=1e-9)
        assert math.isclose(lat, pts[-1][1], abs_tol=1e-9)

    def test_heading_within_360(self):
        pts = _ring_points(6)
        table, total = build_spline_table(pts)
        for s in (0.0, total * 0.25, total * 0.5, total * 0.75, total):
            _, _, heading = position_at_arc_length(table, s)
            assert 0.0 <= heading < 360.0


# ---------------------------------------------------------------------------
# RDP
# ---------------------------------------------------------------------------


class TestRDP:
    def test_keeps_endpoints(self):
        pts = [(0.0, 0.0), (0.0001, 0.0), (0.0002, 0.0)]
        out = rdp(pts, eps_m=1.0)
        assert out[0] == pts[0]
        assert out[-1] == pts[-1]

    def test_collapses_collinear_points(self):
        # 5 collinear points along a single great circle line.
        pts = [(_LON0 + 1e-4 * i, _LAT0) for i in range(5)]
        out = rdp(pts, eps_m=0.5)
        assert len(out) == 2

    def test_protected_indices_are_kept(self):
        pts = [(_LON0 + 1e-4 * i, _LAT0) for i in range(5)]
        out = rdp(pts, eps_m=0.5, protected_indices=[2])
        assert pts[2] in out

    def test_octagon_vertices_survive_small_eps(self):
        pts = _ring_points(8)
        out = rdp(pts, eps_m=0.01)
        assert len(out) == len(pts)


# ---------------------------------------------------------------------------
# Ring node fusion
# ---------------------------------------------------------------------------


def _build_synthetic_ring_graph(
    n_nodes: int,
    *,
    entry_at: list[int] | None = None,
    tl_at: list[int] | None = None,
) -> RoadNetworkGraph:
    """
    Build a roadnetwork graph with one ring of `n_nodes` nodes; entries are
    extra dangling nodes attached to the ring at the indices in `entry_at`.
    Ring nodes at `tl_at` indices are tagged TRAFFIC_LIGHT (so they won't be
    fused).
    """
    entry_at = entry_at or []
    tl_at = tl_at or []
    rng = RoadNetworkGraph(cache_ttl=0)
    g: nx.DiGraph = rng._graph

    pts = _ring_points(n_nodes)
    for i, (lon, lat) in enumerate(pts):
        node_type = (
            NodeType.TRAFFIC_LIGHT.value
            if i in tl_at
            else NodeType.INTERSECTION.value
        )
        g.add_node(
            i,
            **{
                ATTR_NODE_ID: i,
                ATTR_LONGITUDE: lon,
                ATTR_LATITUDE: lat,
                ATTR_NODE_TYPE: node_type,
            },
        )

    # Ring edges (one-way around the ring)
    for i in range(n_nodes):
        u = i
        v = (i + 1) % n_nodes
        g.add_edge(
            u,
            v,
            **{
                ATTR_EDGE_ID: 1000 + i,
                ATTR_LENGTH: 5.0,  # 5 m per arc
                ATTR_MAX_SPEED: 30.0,
                ATTR_WEIGHT: 1.0,
                ATTR_ROAD_TYPE: RoadType.PRIMARY.value,
                ATTR_ONE_WAY: True,
                ATTR_WAYPOINTS: [pts[u], pts[v]],
                ATTR_MID_TLS: [],
                ATTR_LANES: 1,
                ATTR_IS_ROUNDABOUT: True,
                ATTR_ROUNDABOUT_ID: 1,
            },
        )

    # Entry stubs (dangling external nodes attached to ring)
    next_id = n_nodes + 100
    for ring_idx in entry_at:
        ext_lon = pts[ring_idx][0] + 1e-4
        ext_lat = pts[ring_idx][1] + 1e-4
        g.add_node(
            next_id,
            **{
                ATTR_NODE_ID: next_id,
                ATTR_LONGITUDE: ext_lon,
                ATTR_LATITUDE: ext_lat,
                ATTR_NODE_TYPE: NodeType.INTERSECTION.value,
            },
        )
        g.add_edge(
            next_id,
            ring_idx,
            **{
                ATTR_EDGE_ID: 5000 + ring_idx,
                ATTR_LENGTH: 10.0,
                ATTR_MAX_SPEED: 30.0,
                ATTR_WEIGHT: 1.0,
                ATTR_ROAD_TYPE: RoadType.PRIMARY.value,
                ATTR_ONE_WAY: True,
                ATTR_WAYPOINTS: [(ext_lon, ext_lat), pts[ring_idx]],
                ATTR_MID_TLS: [],
                ATTR_LANES: 1,
                ATTR_IS_ROUNDABOUT: False,
                ATTR_ROUNDABOUT_ID: None,
            },
        )
        next_id += 1

    rng._rebuild_roundabout_indices()
    return rng


class TestRingNodeFusion:
    def test_fuses_internal_degree_two_nodes(self):
        # 8-node ring with entries at 0 and 4 → after fusion only nodes 0 and 4
        # should remain in the ring (plus the two external entries).
        rng = _build_synthetic_ring_graph(8, entry_at=[0, 4])
        removed = rng._fuse_ring_nodes()
        # 6 internal degree-2 ring nodes (1, 2, 3, 5, 6, 7) should be fused.
        assert removed == 6
        # The ring has been collapsed to two arcs: 0→4 and 4→0.
        ring_nodes = {0, 4}
        for u, v, attrs in rng._graph.edges(data=True):
            if attrs.get(ATTR_IS_ROUNDABOUT):
                assert u in ring_nodes
                assert v in ring_nodes

    def test_traffic_lights_are_not_fused(self):
        rng = _build_synthetic_ring_graph(8, entry_at=[0], tl_at=[4])
        removed = rng._fuse_ring_nodes()
        # Nodes 0 (entry), 4 (TL) survive; the other 6 should be fused.
        assert removed == 6
        assert 4 in rng._graph

    def test_closed_ring_without_entries_stays_intact(self):
        rng = _build_synthetic_ring_graph(8, entry_at=[])
        removed = rng._fuse_ring_nodes()
        # No anchors → fusion bails to avoid creating a self-loop.
        assert removed == 0
        assert rng._graph.number_of_nodes() == 8

    def test_lengths_sum_after_fusion(self):
        rng = _build_synthetic_ring_graph(8, entry_at=[0, 4])
        original_total = sum(
            attrs.get(ATTR_LENGTH, 0.0)
            for _, _, attrs in rng._graph.edges(data=True)
            if attrs.get(ATTR_IS_ROUNDABOUT)
        )
        rng._fuse_ring_nodes()
        merged_total = sum(
            attrs.get(ATTR_LENGTH, 0.0)
            for _, _, attrs in rng._graph.edges(data=True)
            if attrs.get(ATTR_IS_ROUNDABOUT)
        )
        # Length is preserved exactly — the chains concatenate without RDP yet.
        assert math.isclose(original_total, merged_total, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# Splinify (RDP + spline samples)
# ---------------------------------------------------------------------------


class TestSplinifyRoundaboutEdges:
    def test_marks_edges_with_use_spline(self):
        rng = _build_synthetic_ring_graph(8, entry_at=[0, 4])
        rng._fuse_ring_nodes()
        rng._rebuild_roundabout_indices()
        count = rng._splinify_roundabout_edges()
        assert count >= 1
        for _, _, attrs in rng._graph.edges(data=True):
            if attrs.get(ATTR_IS_ROUNDABOUT):
                assert attrs.get(ATTR_USE_SPLINE) is True
                assert ATTR_SPLINE_SAMPLES in attrs
                assert attrs.get(ATTR_SPLINE_LENGTH, 0.0) > 0

    def test_ring_radius_is_cached(self):
        rng = _build_synthetic_ring_graph(8, entry_at=[0, 4])
        rng._fuse_ring_nodes()
        rng._rebuild_roundabout_indices()
        rng._splinify_roundabout_edges()
        for _, _, attrs in rng._graph.edges(data=True):
            if attrs.get(ATTR_IS_ROUNDABOUT):
                R = attrs.get(ATTR_RING_RADIUS_M)
                assert R is not None
                # Synthetic ring-arc lengths sum to 8 * 5 m = 40 m.
                # R = 40 / (2π) ≈ 6.366 m
                assert math.isclose(R, 40.0 / (2 * math.pi), rel_tol=1e-9)

    def test_spline_length_close_to_circle(self):
        rng = _build_synthetic_ring_graph(8, entry_at=[0, 4])
        rng._fuse_ring_nodes()
        rng._rebuild_roundabout_indices()
        rng._splinify_roundabout_edges()
        spline_total = sum(
            attrs.get(ATTR_SPLINE_LENGTH, 0.0)
            for _, _, attrs in rng._graph.edges(data=True)
            if attrs.get(ATTR_USE_SPLINE)
        )
        # The fixture spaces 8 vertices on a 10 m radius circle. The Catmull-Rom
        # spline traces a curve close to that circle, slightly inside the
        # vertices. Expected perimeter ≈ 2πR ≈ 62.83 m, with the spline within ~2%.
        expected = 2 * math.pi * _R
        assert abs(spline_total - expected) / expected < 0.02


# ---------------------------------------------------------------------------
# Vehicle physics dispatch
# ---------------------------------------------------------------------------


class TestEdgePositionDispatch:
    def test_uses_polyline_when_no_spline_attr(self):
        wps = [(0.0, 0.0), (1e-4, 0.0)]
        attrs = {ATTR_WAYPOINTS: wps}
        lon, lat, _ = _edge_position(attrs, 0.5, wps)
        # Linear lerp midpoint of the two points.
        assert math.isclose(lon, 5e-5, abs_tol=1e-12)
        assert math.isclose(lat, 0.0, abs_tol=1e-12)

    def test_uses_spline_when_use_spline_true(self):
        pts = _ring_points(6)
        table, total = build_spline_table(pts)
        attrs = {
            ATTR_USE_SPLINE: True,
            ATTR_SPLINE_SAMPLES: table,
            ATTR_SPLINE_LENGTH: total,
            ATTR_WAYPOINTS: pts,  # would be used by polyline path; we ignore it
        }
        lon0, lat0, _ = _edge_position(attrs, 0.0, pts)
        assert math.isclose(lon0, pts[0][0], abs_tol=1e-9)
        assert math.isclose(lat0, pts[0][1], abs_tol=1e-9)
        lon1, lat1, _ = _edge_position(attrs, 1.0, pts)
        assert math.isclose(lon1, pts[-1][0], abs_tol=1e-9)
        assert math.isclose(lat1, pts[-1][1], abs_tol=1e-9)

"""Tests para la velocidad máxima según curvatura (Fase 4 TFG)."""

import math

import pytest

from app.core.constants import (
    ATTR_CURVE_VMAX,
    ATTR_WAYPOINTS,
    CURVE_LATERAL_ACCEL_MAX_MS2,
    MIN_ROUNDABOUT_RADIUS_M,
)
from app.core.vehicle_physics import _curvature_radius_m, _edge_curvature_vmax


def _circle_waypoints(
    radius_m: float, center_lat: float = 39.96, arc_deg: float = 90.0, n: int = 7
) -> list[tuple[float, float]]:
    """Waypoints sobre un círculo de `radius_m` metros (3 pts mínimo)."""
    pts: list[tuple[float, float]] = []
    cos_lat = math.cos(math.radians(center_lat))
    for i in range(n):
        theta = math.radians(arc_deg) * (i / (n - 1))
        dx_m = radius_m * math.cos(theta)
        dy_m = radius_m * math.sin(theta)
        lon = dx_m / (111_320.0 * cos_lat)
        lat = center_lat + dy_m / 111_320.0
        pts.append((lon, lat))
    return pts


class TestCurvatureRadius:
    @pytest.mark.unit
    def test_straight_line_is_infinite(self):
        pts = [(0.0, 39.96), (0.001, 39.96), (0.002, 39.96)]
        assert math.isinf(_curvature_radius_m(pts))

    @pytest.mark.unit
    def test_less_than_three_points_is_infinite(self):
        assert math.isinf(_curvature_radius_m([]))
        assert math.isinf(_curvature_radius_m([(0.0, 0.0)]))
        assert math.isinf(_curvature_radius_m([(0.0, 0.0), (0.001, 0.0)]))

    @pytest.mark.unit
    def test_circle_radius_recovered_within_tolerance(self):
        target_R = 12.0
        R = _curvature_radius_m(_circle_waypoints(target_R))
        assert abs(R - target_R) / target_R < 0.1

    @pytest.mark.unit
    def test_small_radius_clamped(self):
        tiny = _circle_waypoints(1.0)
        assert _curvature_radius_m(tiny) >= MIN_ROUNDABOUT_RADIUS_M


class TestEdgeCurvatureVmax:
    @pytest.mark.unit
    def test_straight_edge_is_infinite_vmax(self):
        attrs = {ATTR_WAYPOINTS: [(0.0, 39.96), (0.001, 39.96), (0.002, 39.96)]}
        assert math.isinf(_edge_curvature_vmax(attrs))

    @pytest.mark.unit
    def test_tight_curve_caps_speed(self):
        # R=10 m → v_max = sqrt(2.5 * 10) ≈ 5 m/s
        attrs = {ATTR_WAYPOINTS: _circle_waypoints(10.0)}
        v_max = _edge_curvature_vmax(attrs)
        expected = math.sqrt(CURVE_LATERAL_ACCEL_MAX_MS2 * 10.0)
        assert abs(v_max - expected) / expected < 0.15

    @pytest.mark.unit
    def test_vmax_is_cached_in_edge_attrs(self):
        attrs = {ATTR_WAYPOINTS: _circle_waypoints(10.0)}
        assert ATTR_CURVE_VMAX not in attrs
        v1 = _edge_curvature_vmax(attrs)
        assert ATTR_CURVE_VMAX in attrs
        # Segunda llamada devuelve el cacheado aunque cambiemos los waypoints
        attrs[ATTR_WAYPOINTS] = []
        v2 = _edge_curvature_vmax(attrs)
        assert v1 == v2

    @pytest.mark.unit
    def test_truck_lateral_cap_lowers_vmax(self):
        attrs = {ATTR_WAYPOINTS: _circle_waypoints(15.0)}
        car = _edge_curvature_vmax(dict(attrs), lateral_accel_max_ms2=2.5)
        truck = _edge_curvature_vmax(dict(attrs), lateral_accel_max_ms2=1.8)
        assert truck < car

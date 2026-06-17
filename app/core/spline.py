"""
Centripetal Catmull-Rom spline + Douglas-Peucker simplification.

Used to:
  - Resample roundabout polylines as smooth curves so vehicles follow a
    geometrically continuous path (instead of pivoting at every OSM waypoint).
  - Reduce waypoint counts via RDP before the spline pass, so the precomputed
    sample table stays small.

The spline is built in (lon, lat) space and the arc length is measured with
the same equirectangular haversine approximation used elsewhere in the backend
(_haversine_approx in vehicle_physics). The GDScript twin (scripts/utils/spline.gd)
implements the exact same arithmetic so a parity test can compare per-sample
positions to within IEEE-754 rounding.

Centripetal Catmull-Rom (α=0.5) was chosen because:
  - It passes through every control point (mid-way TLs land on waypoints).
  - It avoids cusps/loops on tight turns typical of small roundabouts.
  - Its closed form is short enough to keep both implementations in sync.

Phantom endpoints default to reflection (`p[-1] = 2·p[0] - p[1]`); callers may
override them to match adjacent edges' interior waypoints, which removes the
tangent discontinuity at ring edge boundaries.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

# Centripetal exponent. Hardcoded so the GDScript twin can match bit-for-bit.
_CR_ALPHA: float = 0.5

_EARTH_RADIUS_M: float = 6_371_000.0
_METERS_PER_DEGREE: float = 111_320.0


def _eval_segment(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    u: float,
) -> tuple[float, float]:
    """
    Evaluate the centripetal Catmull-Rom segment between p1 and p2.

    `u` ∈ [0, 1] with u=0 → p1 and u=1 → p2.
    """
    d01 = max(math.sqrt((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** _CR_ALPHA, 1e-12)
    d12 = max(math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** _CR_ALPHA, 1e-12)
    d23 = max(math.sqrt((p3[0] - p2[0]) ** 2 + (p3[1] - p2[1]) ** 2) ** _CR_ALPHA, 1e-12)

    t0 = 0.0
    t1 = t0 + d01
    t2 = t1 + d12
    t3 = t2 + d23
    t = t1 + u * (t2 - t1)

    w_a1 = (t - t0) / (t1 - t0)
    a1x = p0[0] + (p1[0] - p0[0]) * w_a1
    a1y = p0[1] + (p1[1] - p0[1]) * w_a1

    w_a2 = (t - t1) / (t2 - t1)
    a2x = p1[0] + (p2[0] - p1[0]) * w_a2
    a2y = p1[1] + (p2[1] - p1[1]) * w_a2

    w_a3 = (t - t2) / (t3 - t2)
    a3x = p2[0] + (p3[0] - p2[0]) * w_a3
    a3y = p2[1] + (p3[1] - p2[1]) * w_a3

    w_b1 = (t - t0) / (t2 - t0)
    b1x = a1x + (a2x - a1x) * w_b1
    b1y = a1y + (a2y - a1y) * w_b1

    w_b2 = (t - t1) / (t3 - t1)
    b2x = a2x + (a3x - a2x) * w_b2
    b2y = a2y + (a3y - a2y) * w_b2

    w_c = (t - t1) / (t2 - t1)
    cx = b1x + (b2x - b1x) * w_c
    cy = b1y + (b2y - b1y) * w_c
    return cx, cy


def sample_spline(
    points: list[tuple[float, float]],
    samples_per_segment: int = 8,
    *,
    phantom_pre: Optional[tuple[float, float]] = None,
    phantom_post: Optional[tuple[float, float]] = None,
) -> list[tuple[float, float]]:
    """
    Sample a centripetal Catmull-Rom spline through `points`.

    Returns `(n-1) * samples_per_segment + 1` points; first and last samples
    coincide with the first and last input points.

    `phantom_pre` and `phantom_post` override the implicit reflected phantoms
    at the endpoints — useful to match the tangent of an adjacent spline.
    """
    n = len(points)
    if n < 2:
        return list(points)
    if n == 2:
        return [
            (
                points[0][0] + (points[1][0] - points[0][0]) * j / samples_per_segment,
                points[0][1] + (points[1][1] - points[0][1]) * j / samples_per_segment,
            )
            for j in range(samples_per_segment + 1)
        ]

    p_pre = phantom_pre if phantom_pre is not None else (
        2.0 * points[0][0] - points[1][0],
        2.0 * points[0][1] - points[1][1],
    )
    p_post = phantom_post if phantom_post is not None else (
        2.0 * points[n - 1][0] - points[n - 2][0],
        2.0 * points[n - 1][1] - points[n - 2][1],
    )

    out: list[tuple[float, float]] = []
    for i in range(n - 1):
        p0 = p_pre if i == 0 else points[i - 1]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = p_post if i == n - 2 else points[i + 2]
        for j in range(samples_per_segment):
            u = j / samples_per_segment
            out.append(_eval_segment(p0, p1, p2, p3, u))
    out.append(points[-1])
    return out


def _haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Equirectangular haversine in metres (matches vehicle_physics._haversine_approx)."""
    lat1_r = math.radians(p1[1])
    lat2_r = math.radians(p2[1])
    dlat = lat2_r - lat1_r
    dlon = math.radians(p2[0] - p1[0]) * math.cos((lat1_r + lat2_r) * 0.5)
    return _EARTH_RADIUS_M * math.hypot(dlon, dlat)


def build_spline_table(
    waypoints: list[tuple[float, float]],
    samples_per_segment: int = 8,
    *,
    phantom_pre: Optional[tuple[float, float]] = None,
    phantom_post: Optional[tuple[float, float]] = None,
) -> tuple[list[tuple[float, float, float]], float]:
    """
    Build a per-edge sample table for fast arc-length lookup.

    Returns:
        table: list of (s_m, lon, lat). `s_m` is cumulative haversine distance
               from the first sample. `len(table) >= 2` for any valid input.
        total_length_m: arc length, equal to `table[-1][0]`.

    The table is dense enough for `position_at_arc_length` to bisect+lerp with
    sub-millimetre error at the default sampling rate. Choose
    `samples_per_segment` so the maximum spacing along the spline stays under
    ~0.5 m for the tightest turns; a single large value works well for
    roundabouts, where every segment is short and curved.
    """
    pts = sample_spline(
        waypoints,
        samples_per_segment,
        phantom_pre=phantom_pre,
        phantom_post=phantom_post,
    )
    if not pts:
        return [], 0.0

    table: list[tuple[float, float, float]] = [(0.0, pts[0][0], pts[0][1])]
    total = 0.0
    for i in range(1, len(pts)):
        total += _haversine_m(pts[i - 1], pts[i])
        table.append((total, pts[i][0], pts[i][1]))
    return table, total


def _heading_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return math.degrees(math.atan2(lon2 - lon1, lat2 - lat1)) % 360.0


def position_at_arc_length(
    table: list[tuple[float, float, float]],
    s_target: float,
) -> tuple[float, float, float]:
    """
    Interpolate (lon, lat, heading_deg) at cumulative arc length `s_target`.

    Heading is bearing degrees (0=N, 90=E) measured on the local sample segment
    — matches the convention used by `_position_along_waypoints`.
    """
    n = len(table)
    if n == 0:
        return 0.0, 0.0, 0.0
    if n == 1 or s_target <= 0.0:
        return table[0][1], table[0][2], 0.0
    if s_target >= table[-1][0]:
        s1, lon1, lat1 = table[-2]
        _, lon2, lat2 = table[-1]
        return lon2, lat2, _heading_deg(lon1, lat1, lon2, lat2)

    lo, hi = 0, n - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if table[mid][0] <= s_target:
            lo = mid
        else:
            hi = mid

    s_a, lon_a, lat_a = table[lo]
    s_b, lon_b, lat_b = table[hi]
    span = s_b - s_a
    if span < 1e-9:
        return lon_b, lat_b, _heading_deg(lon_a, lat_a, lon_b, lat_b)
    w = (s_target - s_a) / span
    lon = lon_a + w * (lon_b - lon_a)
    lat = lat_a + w * (lat_b - lat_a)
    heading = _heading_deg(lon_a, lat_a, lon_b, lat_b)
    return lon, lat, heading


def rdp(
    points: list[tuple[float, float]],
    eps_m: float,
    *,
    protected_indices: Iterable[int] = (),
) -> list[tuple[float, float]]:
    """
    Iterative Douglas-Peucker on (lon, lat) polyline with metric tolerance.

    Distances are measured in an equirectangular projection centred at the
    polyline's mean latitude — accurate to a few cm at urban scales.

    Endpoints and `protected_indices` are always kept (use the latter to
    pin down waypoints that carry mid-TL semantics so they survive RDP and
    can be re-attached afterwards).
    """
    n = len(points)
    if n <= 2 or eps_m <= 0:
        return list(points)

    mean_lat = sum(p[1] for p in points) / n
    cos_lat = math.cos(math.radians(mean_lat))
    xy = [(p[0] * cos_lat * _METERS_PER_DEGREE, p[1] * _METERS_PER_DEGREE) for p in points]

    keep = [False] * n
    keep[0] = True
    keep[-1] = True
    for i in protected_indices:
        if 0 <= i < n:
            keep[i] = True

    stack: list[tuple[int, int]] = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        protected_in_range = [k for k in range(a + 1, b) if keep[k]]
        if protected_in_range:
            mid = protected_in_range[0]
            stack.append((a, mid))
            stack.append((mid, b))
            continue
        ax, ay = xy[a]
        bx, by = xy[b]
        ABx = bx - ax
        ABy = by - ay
        ab_sq = ABx * ABx + ABy * ABy
        max_d = 0.0
        max_k = -1
        if ab_sq < 1e-12:
            for k in range(a + 1, b):
                px, py = xy[k]
                d = math.hypot(px - ax, py - ay)
                if d > max_d:
                    max_d = d
                    max_k = k
        else:
            inv = 1.0 / ab_sq
            for k in range(a + 1, b):
                px, py = xy[k]
                t = max(0.0, min(1.0, ((px - ax) * ABx + (py - ay) * ABy) * inv))
                projx = ax + t * ABx
                projy = ay + t * ABy
                d = math.hypot(px - projx, py - projy)
                if d > max_d:
                    max_d = d
                    max_k = k
        if max_d > eps_m and max_k >= 0:
            keep[max_k] = True
            stack.append((a, max_k))
            stack.append((max_k, b))

    return [points[i] for i in range(n) if keep[i]]

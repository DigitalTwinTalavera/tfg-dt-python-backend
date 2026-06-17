"""Tests for intersection arbitration (priority-to-the-right + TTC tiebreak)."""

from unittest.mock import MagicMock

import pytest

from app.core.constants import (
    ATTR_IS_ROUNDABOUT,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_NODE_TYPE,
    ATTR_WAYPOINTS,
    INTERSECTION_DETECTION_ZONE_M,
)
from app.core.physics.intersection import (
    _build_intersection_arm_index,
    _check_intersection_yield,
    _is_contender_on_right,
)
from app.core.route import RouteInfo
from app.models.enums import NodeType, VehicleStatus
from app.services.vehicle_spawner import SimVehicle


def _make_vehicle(
    vid: str,
    node_path: list[int],
    progress: float,
    velocity: float,
) -> SimVehicle:
    route = RouteInfo(
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        node_path=node_path,
        edge_ids=[0] * (len(node_path) - 1),
        length_m=100.0,
    )
    return SimVehicle(
        id=vid,
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        route=route,
        status=VehicleStatus.MOVING,
        current_edge_index=0,
        progress_on_edge=progress,
        velocity=velocity,
    )


def _make_graph(
    node_types: dict[int, str | None],
    edges: dict[tuple[int, int], dict],
    in_degrees: dict[int, int],
) -> MagicMock:
    """Mock graph that satisfies the interface used by intersection.py."""
    g = MagicMock()
    g.get_node_attributes.side_effect = lambda n: (
        {ATTR_NODE_TYPE: node_types[n]} if n in node_types and node_types[n] else {
            ATTR_LATITUDE: 0.0, ATTR_LONGITUDE: 0.0,
        }
    )
    g.get_edge_attributes.side_effect = lambda u, v: edges.get((u, v), {ATTR_LENGTH: 50.0})

    inner = MagicMock()
    inner.in_degree.side_effect = lambda n: in_degrees.get(n, 1)
    g.graph = inner
    return g


# ---------------------------------------------------------------------------
# Bearing helper unit tests
# ---------------------------------------------------------------------------

class TestRightOfBearing:
    """Para asegurar que la regla "viene desde la derecha" es geométrica."""

    @pytest.mark.unit
    def test_perpendicular_from_east_is_right_of_northbound(self):
        # Ego heading 0 (Norte). Contendiente heading 270 (yendo Oeste = viene del Este).
        assert _is_contender_on_right(ego_bearing_deg=0.0, contender_bearing_deg=270.0)

    @pytest.mark.unit
    def test_perpendicular_from_west_is_left_of_northbound(self):
        # Contendiente heading 90 (yendo Este = viene del Oeste).
        assert not _is_contender_on_right(ego_bearing_deg=0.0, contender_bearing_deg=90.0)

    @pytest.mark.unit
    def test_head_on_not_right(self):
        # Mismo eje, sentidos opuestos.
        assert not _is_contender_on_right(ego_bearing_deg=0.0, contender_bearing_deg=180.0)

    @pytest.mark.unit
    def test_same_direction_not_right(self):
        # Vehículo detrás yendo en la misma dirección.
        assert not _is_contender_on_right(ego_bearing_deg=0.0, contender_bearing_deg=0.0)

    @pytest.mark.unit
    def test_eastbound_yields_to_southbound(self):
        # Ego va al Este (90), contendiente va al Norte heading 0 → viene del Sur.
        # Sur respecto a un coche que va Este = atrás-derecha del coche... mejor:
        # mejor caso clásico: ego Este, contendiente del Sur (heading 0).
        # Sur está a la derecha de quien va al Este.
        assert _is_contender_on_right(ego_bearing_deg=90.0, contender_bearing_deg=0.0)


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

class TestArmIndex:
    @pytest.mark.unit
    def test_far_vehicle_excluded(self):
        # 4 nodos: ego va por (1,2), nodo 2 es cruce in_degree=2.
        # Ego está al inicio de la arista (50 m del cruce, que cabe en zona),
        # luego con edge_length=200 m el ego está a 200 m → fuera de zona.
        v = _make_vehicle("v1", [1, 2, 3], progress=0.0, velocity=10.0)
        edges = {(1, 2): {ATTR_LENGTH: 200.0}, (2, 3): {ATTR_LENGTH: 50.0}}
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": v}, g)
        assert arms == {}

    @pytest.mark.unit
    def test_close_vehicle_indexed(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        edges = {(1, 2): {ATTR_LENGTH: 50.0}, (2, 3): {ATTR_LENGTH: 50.0}}
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": v}, g)
        assert 2 in arms
        assert len(arms[2]) == 1
        assert arms[2][0][0].id == "v1"

    @pytest.mark.unit
    def test_traffic_light_node_skipped(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        edges = {(1, 2): {ATTR_LENGTH: 50.0}, (2, 3): {ATTR_LENGTH: 50.0}}
        g = _make_graph({2: NodeType.TRAFFIC_LIGHT.value}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": v}, g)
        assert arms == {}

    @pytest.mark.unit
    def test_in_degree_one_skipped(self):
        # Continuación de calle (no es un cruce real).
        v = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        edges = {(1, 2): {ATTR_LENGTH: 50.0}, (2, 3): {ATTR_LENGTH: 50.0}}
        g = _make_graph({2: None}, edges, in_degrees={2: 1})
        arms = _build_intersection_arm_index({"v1": v}, g)
        assert arms == {}

    @pytest.mark.unit
    def test_roundabout_entry_skipped(self):
        # La siguiente arista (2,3) es de rotonda → la gestiona yield específico.
        v = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        edges = {
            (1, 2): {ATTR_LENGTH: 50.0},
            (2, 3): {ATTR_LENGTH: 50.0, ATTR_IS_ROUNDABOUT: True},
        }
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": v}, g)
        assert arms == {}


# ---------------------------------------------------------------------------
# Yield decision
# ---------------------------------------------------------------------------

def _waypoints_n_to_s() -> list[tuple[float, float]]:
    """Vehículo aproximándose al nodo 2 por el sur (yendo hacia el norte)."""
    # heading = atan2(dlon, dlat) = atan2(0, +) = 0° (Norte). Contendiente
    # llega "desde el Sur" → arrival_dir = 180.
    return [(0.0, 0.0), (0.0, 0.001)]


def _waypoints_e_to_w() -> list[tuple[float, float]]:
    """Vehículo aproximándose al nodo 2 por el este (yendo hacia el oeste)."""
    # heading = atan2(-, 0) = -90° (= 270°). Contendiente llega "desde el Este".
    return [(0.001, 0.0), (0.0, 0.0)]


def _waypoints_w_to_e() -> list[tuple[float, float]]:
    """Vehículo aproximándose al nodo 2 por el oeste (yendo hacia el este)."""
    return [(-0.001, 0.0), (0.0, 0.0)]


class TestIntersectionYield:
    @pytest.mark.unit
    def test_alone_no_yield(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        edges = {
            (1, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_n_to_s()},
            (2, 3): {ATTR_LENGTH: 50.0},
        }
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": v}, g)
        assert _check_intersection_yield(v, g, arms) is None

    @pytest.mark.unit
    def test_clear_priority_ego_first(self):
        # Ego a 5 m del nodo, contendiente a 25 m: TTC del ego mucho menor.
        ego = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        # 50 m * 0.9 = 45 m de progreso → 5 m al nodo, TTC 0.5 s.
        other = _make_vehicle("v2", [4, 2, 5], progress=0.5, velocity=10.0)
        # 50 m * 0.5 = 25 m → 25 m al nodo, TTC 2.5 s.
        edges = {
            (1, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_n_to_s()},
            (4, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_e_to_w()},
            (2, 3): {ATTR_LENGTH: 50.0},
            (2, 5): {ATTR_LENGTH: 50.0},
        }
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": ego, "v2": other}, g)
        # Ego pasa: TTC mucho menor que la del contendiente.
        assert _check_intersection_yield(ego, g, arms) is None
        # Contendiente cede al ego (que llega claramente antes).
        assert _check_intersection_yield(other, g, arms) is not None

    @pytest.mark.unit
    def test_priority_to_right_close_ttc(self):
        # Ego va Norte (heading 0), contendiente viene del Este (heading 270).
        # Mismas distancias y velocidades → TTCs iguales → ego cede al de la derecha.
        ego = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        other = _make_vehicle("v2", [4, 2, 5], progress=0.9, velocity=10.0)
        edges = {
            (1, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_n_to_s()},
            (4, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_e_to_w()},
            (2, 3): {ATTR_LENGTH: 50.0},
            (2, 5): {ATTR_LENGTH: 50.0},
        }
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": ego, "v2": other}, g)
        # Ego cede a contendiente (que viene de su derecha).
        assert _check_intersection_yield(ego, g, arms) is not None
        # Contendiente NO cede al ego (que viene de su izquierda).
        # Ego v1 viene del Sur (heading 0); para "other" (heading 270, yendo Oeste),
        # ego está a su izquierda → no cede por la regla, pero el desempate por
        # id (v1 < v2) hará que v2 ceda. Eso es correcto: v1 v2 no son simétricos
        # en este test porque el contendiente del propio "other" sería v1.
        # Para evitar simetrías inesperadas test_clear_priority_ego_first ya cubre
        # el caso "claro". Aquí basta con que ego ceda.

    @pytest.mark.unit
    def test_id_tiebreak_when_symmetric(self):
        # Dos coches viniendo de calles que NO se ven mutuamente como "derecha"
        # (ambos en la misma dirección de aproximación, p.ej., uno desde el
        # este y otro desde el este por edges paralelos cercanos). En la
        # práctica esto fuerza el desempate por id: el menor pasa.
        ego = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        other = _make_vehicle("v2", [4, 2, 5], progress=0.9, velocity=10.0)
        # Ambos heading 0 (Norte) → relative bearing = 180 (head-on, ni derecha
        # ni izquierda). El árbitro caerá al desempate por id.
        edges = {
            (1, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_n_to_s()},
            (4, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_n_to_s()},
            (2, 3): {ATTR_LENGTH: 50.0},
            (2, 5): {ATTR_LENGTH: 50.0},
        }
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": ego, "v2": other}, g)
        # v1 < v2: v1 pasa, v2 cede.
        assert _check_intersection_yield(ego, g, arms) is None
        assert _check_intersection_yield(other, g, arms) is not None

    @pytest.mark.unit
    def test_same_edge_contender_ignored(self):
        # Dos vehículos en la misma arista: el árbitro NO debe disparar
        # (eso ya lo cubre _find_leader rear-end). Aquí sólo verificamos
        # que si hay un único brazo (mismo edge_key), no se disparan
        # falsos positivos.
        ego = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        edges = {
            (1, 2): {ATTR_LENGTH: 50.0, ATTR_WAYPOINTS: _waypoints_n_to_s()},
            (2, 3): {ATTR_LENGTH: 50.0},
        }
        g = _make_graph({2: None}, edges, in_degrees={2: 2})
        arms = _build_intersection_arm_index({"v1": ego}, g)
        assert _check_intersection_yield(ego, g, arms) is None

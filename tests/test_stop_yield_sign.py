"""Tests for STOP/YIELD sign runtime (Phase 7.1)."""

from unittest.mock import MagicMock

import pytest

from app.core.constants import (
    ATTR_LENGTH,
    ATTR_NODE_TYPE,
    STOP_SIGN_DWELL_TIME_S,
)
from app.core.route import RouteInfo
from app.core.vehicle_physics import _check_stop_yield_sign
from app.models.enums import NodeType, VehicleStatus
from app.services.vehicle_spawner import SimVehicle


def _make_vehicle(vid: str, node_path: list[int], progress: float, velocity: float) -> SimVehicle:
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


def _mock_graph(node_types: dict[int, str], edge_length: float = 50.0) -> MagicMock:
    g = MagicMock()
    g.get_node_attributes.side_effect = lambda n: (
        {ATTR_NODE_TYPE: node_types[n]} if n in node_types else {}
    )
    g.get_edge_attributes.side_effect = lambda u, v: {ATTR_LENGTH: edge_length}
    return g


class TestStopSign:
    @pytest.mark.unit
    def test_far_from_sign_no_leader(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.0, velocity=10.0)
        g = _mock_graph({2: NodeType.STOP_SIGN.value}, edge_length=100.0)
        leader = _check_stop_yield_sign(v, g, {}, dt=0.1)
        assert leader is None  # 100 m > SIGN_DETECTION_ZONE_M

    @pytest.mark.unit
    def test_approaching_stop_generates_leader(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.8, velocity=10.0)
        g = _mock_graph({2: NodeType.STOP_SIGN.value}, edge_length=50.0)
        leader = _check_stop_yield_sign(v, g, {}, dt=0.1)
        assert leader is not None
        assert leader.velocity_ms == 0.0
        assert leader.gap_m > 0

    @pytest.mark.unit
    def test_dwell_clears_stop(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.99, velocity=0.0)
        g = _mock_graph({2: NodeType.STOP_SIGN.value}, edge_length=50.0)
        # Simular dwell acumulado: llamar varias veces hasta superar el umbral.
        for _ in range(int(STOP_SIGN_DWELL_TIME_S / 0.1) + 2):
            _check_stop_yield_sign(v, g, {}, dt=0.1)
        assert v.stop_sign_cleared_node == 2
        # Tras cumplir el dwell no debe generar más líder.
        leader = _check_stop_yield_sign(v, g, {}, dt=0.1)
        assert leader is None

    @pytest.mark.unit
    def test_clear_resets_on_edge_change(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.5, velocity=10.0)
        v.stop_sign_cleared_node = 99  # Node antiguo (otra arista)
        g = _mock_graph({2: NodeType.STOP_SIGN.value}, edge_length=50.0)
        _check_stop_yield_sign(v, g, {}, dt=0.1)
        # Se resetea porque el end_node actual (2) no coincide con 99.
        assert v.stop_sign_cleared_node == -1

    @pytest.mark.unit
    def test_non_sign_node_returns_none(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.8, velocity=10.0)
        g = _mock_graph({2: NodeType.TRAFFIC_LIGHT.value}, edge_length=50.0)
        assert _check_stop_yield_sign(v, g, {}, dt=0.1) is None


class TestYieldSign:
    @pytest.mark.unit
    def test_empty_intersection_no_leader(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.8, velocity=10.0)
        g = _mock_graph({2: NodeType.YIELD_SIGN.value}, edge_length=50.0)
        leader = _check_stop_yield_sign(v, g, {}, dt=0.1)
        assert leader is None  # Sin tráfico convergente → libre

    @pytest.mark.unit
    def test_conflicting_traffic_generates_leader(self):
        v = _make_vehicle("v1", [1, 2, 3], progress=0.9, velocity=10.0)
        # Otro vehículo aproximándose al mismo nodo 2 por una rama distinta (4→2).
        other = _make_vehicle("v2", [4, 2, 5], progress=0.95, velocity=10.0)
        edge_index = {(4, 2): {0: [other]}}
        g = _mock_graph({2: NodeType.YIELD_SIGN.value}, edge_length=50.0)
        leader = _check_stop_yield_sign(v, g, edge_index, dt=0.1)
        assert leader is not None
        assert leader.velocity_ms == 0.0

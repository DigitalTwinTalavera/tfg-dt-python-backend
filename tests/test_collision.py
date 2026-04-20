"""Tests for permanent collision blocking (Phase 3 TFG)."""

import pytest

from app.core.route import RouteInfo
from app.core.vehicle_physics import _trigger_collision
from app.models.enums import VehicleStatus
from app.services.vehicle_spawner import SimVehicle


def _make_vehicle(vid: str, node_path: list[int], edge_index: int = 0) -> SimVehicle:
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
        current_edge_index=edge_index,
        velocity=10.0,
    )


class TestTriggerCollision:
    @pytest.mark.unit
    def test_both_vehicles_marked_collision_and_stopped(self):
        v1 = _make_vehicle("v1", [1, 2, 3])
        v2 = _make_vehicle("v2", [1, 2, 3])
        blocked: dict[tuple[int, int], object | None] = {}

        _trigger_collision(v1, v2, blocked)

        assert v1.status == VehicleStatus.COLLISION
        assert v2.status == VehicleStatus.COLLISION
        assert v1.velocity == 0.0
        assert v2.velocity == 0.0
        assert v1.acceleration == 0.0
        assert v2.acceleration == 0.0
        # Sin timer — el choque es permanente hasta retirada manual.
        assert v1.collision_timer == 0.0
        assert v2.collision_timer == 0.0

    @pytest.mark.unit
    def test_edge_becomes_permanently_blocked(self):
        v1 = _make_vehicle("v1", [10, 20, 30], edge_index=0)
        v2 = _make_vehicle("v2", [10, 20, 30], edge_index=0)
        blocked: dict[tuple[int, int], object | None] = {}

        _trigger_collision(v1, v2, blocked)

        # La arista en curso (10 → 20) queda bloqueada con valor None = permanente.
        assert (10, 20) in blocked
        assert blocked[(10, 20)] is None

    @pytest.mark.unit
    def test_no_block_when_vehicle_past_last_edge(self):
        # Vehículo sin arista siguiente (caso degenerado).
        route = RouteInfo(
            start_node_id=1,
            end_node_id=2,
            node_path=[1, 2],
            edge_ids=[0],
            length_m=50.0,
        )
        v1 = SimVehicle(
            id="v1",
            start_node_id=1,
            end_node_id=2,
            route=route,
            current_edge_index=1,  # ya fuera de rango
            status=VehicleStatus.MOVING,
        )
        v2 = SimVehicle(
            id="v2",
            start_node_id=1,
            end_node_id=2,
            route=route,
            current_edge_index=1,
            status=VehicleStatus.MOVING,
        )
        blocked: dict[tuple[int, int], object | None] = {}

        _trigger_collision(v1, v2, blocked)

        assert blocked == {}
        assert v1.status == VehicleStatus.COLLISION
        assert v2.status == VehicleStatus.COLLISION

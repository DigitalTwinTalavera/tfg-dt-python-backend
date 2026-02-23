"""
Tests para el broadcaster de estado de simulación en tiempo real,
los builders de mensajes WebSocket y la integración con el engine.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.websocket.manager import ConnectionManager
from app.api.websocket.messages import (
    MSG_TYPE_SIM_STATE,
    MSG_TYPE_TICK,
    MSG_TYPE_VEHICLE_FINISHED,
    MSG_TYPE_VEHICLE_SPAWNED,
    build_sim_state_message,
    build_tick_message,
    build_vehicle_finished_message,
    build_vehicle_spawned_message,
    build_vehicle_state,
)
from app.core.broadcaster import SimulationBroadcaster
from app.core.route import RouteInfo
from app.core.simulation_engine import SimulationEngine, SimulationState
from app.models.enums import VehicleStatus
from app.services.vehicle_spawner import SimVehicle, VehicleSpawner


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_manager():
    """ConnectionManager mockeado con broadcast async."""
    manager = MagicMock(spec=ConnectionManager)
    manager.connection_count = 1
    manager.broadcast = AsyncMock()
    return manager


@pytest.fixture
def mock_spawner():
    """VehicleSpawner mockeado."""
    spawner = MagicMock(spec=VehicleSpawner)
    spawner.get_all_vehicles.return_value = []
    return spawner


@pytest.fixture
def broadcaster(mock_manager, mock_spawner):
    """Broadcaster con dependencias mockeadas."""
    return SimulationBroadcaster(
        connection_manager=mock_manager,
        vehicle_spawner=mock_spawner,
    )


@pytest.fixture
def sample_route():
    """Ruta de ejemplo para SimVehicle."""
    return RouteInfo(
        start_node_id=1,
        end_node_id=5,
        node_path=[1, 2, 3, 5],
        edge_ids=[10, 20, 30],
        length_m=500.0,
    )


@pytest.fixture
def sample_vehicle(sample_route):
    """Vehículo de ejemplo con valores conocidos."""
    return SimVehicle(
        id="v_001",
        start_node_id=1,
        end_node_id=5,
        route=sample_route,
        status=VehicleStatus.MOVING,
        current_edge_index=1,
        longitude=-3.7037902,
        latitude=40.4167754,
        velocity=12.5,
        acceleration=0.8,
        heading=45.3,
        progress_on_edge=0.65,
    )


@pytest.fixture
def engine_with_broadcaster(mock_manager, mock_spawner):
    """SimulationEngine con broadcaster inyectado."""
    engine = SimulationEngine(tick_interval_ms=50.0)
    bc = SimulationBroadcaster(
        connection_manager=mock_manager,
        vehicle_spawner=mock_spawner,
    )
    engine.set_broadcaster(bc)
    return engine, bc, mock_manager


# =============================================================================
# Message Builders
# =============================================================================


class TestBuildTickMessage:
    @pytest.mark.unit
    def test_tick_message_structure(self):
        msg = build_tick_message(tick=10, sim_time=1.0, vehicles=[])
        assert msg["type"] == MSG_TYPE_TICK
        assert msg["tick"] == 10
        assert msg["sim_time"] == 1.0
        assert msg["vehicles"] == []
        assert msg["count"] == 0

    @pytest.mark.unit
    def test_tick_message_with_vehicles(self):
        vehicles = [{"id": "v_001"}, {"id": "v_002"}]
        msg = build_tick_message(tick=5, sim_time=0.5, vehicles=vehicles)
        assert msg["count"] == 2
        assert len(msg["vehicles"]) == 2

    @pytest.mark.unit
    def test_sim_time_rounded(self):
        msg = build_tick_message(tick=0, sim_time=1.23456789, vehicles=[])
        assert msg["sim_time"] == 1.235


class TestBuildVehicleState:
    @pytest.mark.unit
    def test_vehicle_state_fields(self):
        state = build_vehicle_state(
            vehicle_id="v_001",
            longitude=-3.7037902,
            latitude=40.4167754,
            velocity=12.5,
            acceleration=0.8,
            heading=45.3,
            status="moving",
            current_edge_index=2,
            progress_on_edge=0.654321,
        )
        assert state["id"] == "v_001"
        assert state["lon"] == -3.7037902
        assert state["lat"] == 40.4167754
        assert state["v"] == 12.5
        assert state["a"] == 0.8
        assert state["h"] == 45.3
        assert state["status"] == "moving"
        assert state["edge_idx"] == 2
        assert state["progress"] == 0.6543

    @pytest.mark.unit
    def test_vehicle_state_rounding(self):
        state = build_vehicle_state(
            vehicle_id="v_001",
            longitude=-3.70379029999,
            latitude=40.41677549999,
            velocity=12.556,
            acceleration=0.856,
            heading=45.36,
            status="moving",
            current_edge_index=0,
            progress_on_edge=0.99999,
        )
        assert state["lon"] == -3.7037903
        assert state["lat"] == 40.4167755
        assert state["v"] == 12.56
        assert state["a"] == 0.86
        assert state["h"] == 45.4  # 45.36 rounds to 45.4
        assert state["progress"] == 1.0


class TestBuildSimStateMessage:
    @pytest.mark.unit
    def test_sim_state_message(self):
        msg = build_sim_state_message("running")
        assert msg["type"] == MSG_TYPE_SIM_STATE
        assert msg["state"] == "running"

    @pytest.mark.unit
    def test_sim_state_stopped(self):
        msg = build_sim_state_message("stopped")
        assert msg["state"] == "stopped"


class TestBuildVehicleSpawnedMessage:
    @pytest.mark.unit
    def test_spawned_message(self):
        msg = build_vehicle_spawned_message(
            vehicle_id="v_003",
            start_node_id=1,
            end_node_id=5,
            route_edges=[10, 20, 30],
        )
        assert msg["type"] == MSG_TYPE_VEHICLE_SPAWNED
        assert msg["vehicle_id"] == "v_003"
        assert msg["start_node_id"] == 1
        assert msg["end_node_id"] == 5
        assert msg["route_edges"] == [10, 20, 30]


class TestBuildVehicleFinishedMessage:
    @pytest.mark.unit
    def test_finished_message(self):
        msg = build_vehicle_finished_message("v_007")
        assert msg["type"] == MSG_TYPE_VEHICLE_FINISHED
        assert msg["vehicle_id"] == "v_007"


# =============================================================================
# SimulationBroadcaster - broadcast_tick
# =============================================================================


class TestBroadcastTick:
    @pytest.mark.unit
    async def test_no_broadcast_when_no_clients(self, broadcaster, mock_manager):
        mock_manager.connection_count = 0
        await broadcaster.broadcast_tick(tick=0, sim_time=0.0)
        mock_manager.broadcast.assert_not_called()

    @pytest.mark.unit
    async def test_broadcast_tick_zero_always_sent(self, broadcaster, mock_manager, mock_spawner):
        mock_spawner.get_all_vehicles.return_value = []
        await broadcaster.broadcast_tick(tick=0, sim_time=0.0)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["type"] == MSG_TYPE_TICK
        assert msg["tick"] == 0
        assert msg["vehicles"] == []

    @pytest.mark.unit
    async def test_broadcast_with_vehicles(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["count"] == 1
        v = msg["vehicles"][0]
        assert v["id"] == "v_001"
        assert v["v"] == 12.5
        assert v["status"] == "moving"

    @pytest.mark.unit
    async def test_broadcast_count_increments(self, broadcaster, mock_spawner):
        mock_spawner.get_all_vehicles.return_value = []
        assert broadcaster.broadcast_count == 0
        await broadcaster.broadcast_tick(tick=0, sim_time=0.0)
        assert broadcaster.broadcast_count == 1
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)
        assert broadcaster.broadcast_count == 2

    @pytest.mark.unit
    async def test_avg_broadcast_time(self, broadcaster, mock_spawner):
        mock_spawner.get_all_vehicles.return_value = []
        await broadcaster.broadcast_tick(tick=0, sim_time=0.0)
        assert broadcaster.avg_broadcast_time_ms >= 0.0

    @pytest.mark.unit
    async def test_avg_broadcast_time_zero_when_no_broadcasts(self, broadcaster):
        assert broadcaster.avg_broadcast_time_ms == 0.0


# =============================================================================
# SimulationBroadcaster - Delta updates
# =============================================================================


class TestDeltaUpdates:
    @pytest.mark.unit
    async def test_new_vehicle_always_included(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)
        msg = mock_manager.broadcast.call_args[0][0]
        assert len(msg["vehicles"]) == 1

    @pytest.mark.unit
    async def test_unchanged_vehicle_excluded_on_second_tick(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        # First tick: vehicle is new, included
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)
        assert len(mock_manager.broadcast.call_args[0][0]["vehicles"]) == 1

        mock_manager.broadcast.reset_mock()
        # Second tick: same state, should NOT broadcast (no changes, not tick 0)
        await broadcaster.broadcast_tick(tick=2, sim_time=0.2)
        mock_manager.broadcast.assert_not_called()

    @pytest.mark.unit
    async def test_changed_vehicle_included_on_second_tick(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)

        mock_manager.broadcast.reset_mock()
        # Change position
        sample_vehicle.longitude = -3.704
        await broadcaster.broadcast_tick(tick=2, sim_time=0.2)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert len(msg["vehicles"]) == 1
        assert msg["vehicles"][0]["id"] == "v_001"

    @pytest.mark.unit
    async def test_vehicle_removed_clears_snapshot(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)

        mock_manager.broadcast.reset_mock()
        # Vehicle disappears
        mock_spawner.get_all_vehicles.return_value = []
        await broadcaster.broadcast_tick(tick=2, sim_time=0.2)
        # No vehicles changed, not tick 0 => no broadcast
        mock_manager.broadcast.assert_not_called()

    @pytest.mark.unit
    async def test_multiple_vehicles_partial_change(
        self, broadcaster, mock_manager, mock_spawner, sample_route
    ):
        v1 = SimVehicle(
            id="v_001", start_node_id=1, end_node_id=5,
            route=sample_route, status=VehicleStatus.MOVING,
            longitude=1.0, latitude=2.0,
        )
        v2 = SimVehicle(
            id="v_002", start_node_id=1, end_node_id=5,
            route=sample_route, status=VehicleStatus.MOVING,
            longitude=3.0, latitude=4.0,
        )
        mock_spawner.get_all_vehicles.return_value = [v1, v2]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)

        mock_manager.broadcast.reset_mock()
        # Only v1 changes
        v1.longitude = 1.5
        await broadcaster.broadcast_tick(tick=2, sim_time=0.2)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert len(msg["vehicles"]) == 1
        assert msg["vehicles"][0]["id"] == "v_001"


# =============================================================================
# SimulationBroadcaster - reset
# =============================================================================


class TestBroadcasterReset:
    @pytest.mark.unit
    async def test_reset_clears_state(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)
        assert broadcaster.broadcast_count == 1

        broadcaster.reset()
        assert broadcaster.broadcast_count == 0
        assert broadcaster.avg_broadcast_time_ms == 0.0

    @pytest.mark.unit
    async def test_reset_clears_snapshot(
        self, broadcaster, mock_manager, mock_spawner, sample_vehicle
    ):
        mock_spawner.get_all_vehicles.return_value = [sample_vehicle]
        await broadcaster.broadcast_tick(tick=1, sim_time=0.1)

        broadcaster.reset()
        mock_manager.broadcast.reset_mock()

        # After reset, same vehicle is "new" again
        await broadcaster.broadcast_tick(tick=0, sim_time=0.0)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert len(msg["vehicles"]) == 1


# =============================================================================
# SimulationBroadcaster - sim state events
# =============================================================================


class TestBroadcastSimState:
    @pytest.mark.unit
    async def test_broadcast_sim_state(self, broadcaster, mock_manager):
        await broadcaster.broadcast_sim_state("running")
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["type"] == MSG_TYPE_SIM_STATE
        assert msg["state"] == "running"

    @pytest.mark.unit
    async def test_no_broadcast_sim_state_without_clients(self, broadcaster, mock_manager):
        mock_manager.connection_count = 0
        await broadcaster.broadcast_sim_state("running")
        mock_manager.broadcast.assert_not_called()


# =============================================================================
# SimulationBroadcaster - vehicle events
# =============================================================================


class TestBroadcastVehicleEvents:
    @pytest.mark.unit
    async def test_broadcast_vehicle_spawned(
        self, broadcaster, mock_manager, sample_vehicle
    ):
        await broadcaster.broadcast_vehicle_spawned(sample_vehicle)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["type"] == MSG_TYPE_VEHICLE_SPAWNED
        assert msg["vehicle_id"] == "v_001"
        assert msg["route_edges"] == [10, 20, 30]

    @pytest.mark.unit
    async def test_broadcast_vehicle_finished(self, broadcaster, mock_manager):
        await broadcaster.broadcast_vehicle_finished("v_005")
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["type"] == MSG_TYPE_VEHICLE_FINISHED
        assert msg["vehicle_id"] == "v_005"

    @pytest.mark.unit
    async def test_no_spawn_broadcast_without_clients(
        self, broadcaster, mock_manager, sample_vehicle
    ):
        mock_manager.connection_count = 0
        await broadcaster.broadcast_vehicle_spawned(sample_vehicle)
        mock_manager.broadcast.assert_not_called()

    @pytest.mark.unit
    async def test_no_finished_broadcast_without_clients(
        self, broadcaster, mock_manager
    ):
        mock_manager.connection_count = 0
        await broadcaster.broadcast_vehicle_finished("v_005")
        mock_manager.broadcast.assert_not_called()


# =============================================================================
# Integration: Engine + Broadcaster
# =============================================================================


class TestEngineWithBroadcaster:
    @pytest.mark.unit
    async def test_start_broadcasts_running(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        await engine.start()
        # Should have broadcast "running" state
        manager.broadcast.assert_called()
        calls = manager.broadcast.call_args_list
        state_msg = calls[0][0][0]
        assert state_msg["type"] == MSG_TYPE_SIM_STATE
        assert state_msg["state"] == "running"
        await engine.shutdown()

    @pytest.mark.unit
    async def test_stop_broadcasts_stopped(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        await engine.start()
        manager.broadcast.reset_mock()
        await engine.stop()
        manager.broadcast.assert_called()
        # Last call should be sim_state stopped
        last_call = manager.broadcast.call_args_list[-1][0][0]
        assert last_call["type"] == MSG_TYPE_SIM_STATE
        assert last_call["state"] == "stopped"

    @pytest.mark.unit
    async def test_pause_broadcasts_paused(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        await engine.start()
        manager.broadcast.reset_mock()
        await engine.pause()
        manager.broadcast.assert_called()
        msg = manager.broadcast.call_args_list[-1][0][0]
        assert msg["type"] == MSG_TYPE_SIM_STATE
        assert msg["state"] == "paused"

    @pytest.mark.unit
    async def test_resume_broadcasts_running(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        await engine.start()
        await engine.pause()
        manager.broadcast.reset_mock()
        await engine.resume()
        manager.broadcast.assert_called()
        msg = manager.broadcast.call_args[0][0]
        assert msg["type"] == MSG_TYPE_SIM_STATE
        assert msg["state"] == "running"
        await engine.shutdown()

    @pytest.mark.unit
    async def test_start_resets_broadcaster(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        # Simulate a previous broadcast
        bc._broadcast_count = 50
        bc._total_broadcast_time_ms = 100.0
        await engine.start()
        assert bc.broadcast_count == 0
        await engine.shutdown()

    @pytest.mark.unit
    async def test_status_includes_broadcast_stats(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        status = engine.get_status()
        assert "broadcast_count" in status
        assert "avg_broadcast_ms" in status

    @pytest.mark.unit
    async def test_tick_calls_broadcast(self, engine_with_broadcaster):
        engine, bc, manager = engine_with_broadcaster
        mock_spawner = bc._spawner
        mock_spawner.get_all_vehicles.return_value = []
        await engine._tick(0.1)
        # broadcast_tick was called via _tick
        assert bc.broadcast_count == 1

    @pytest.mark.unit
    def test_engine_without_broadcaster_has_no_stats(self):
        engine = SimulationEngine()
        status = engine.get_status()
        assert "broadcast_count" not in status

    @pytest.mark.unit
    async def test_engine_without_broadcaster_tick_noop(self):
        engine = SimulationEngine()
        # Should not raise
        await engine._tick(0.1)


# =============================================================================
# Performance
# =============================================================================


class TestBroadcastPerformance:
    @pytest.mark.unit
    async def test_100_vehicles_under_5ms(self, mock_manager, sample_route):
        """100 vehículos deben procesarse en menos de 5ms (sin I/O real)."""
        vehicles = []
        for i in range(100):
            v = SimVehicle(
                id=f"v_{i:03d}",
                start_node_id=1,
                end_node_id=5,
                route=sample_route,
                status=VehicleStatus.MOVING,
                longitude=-3.7 + i * 0.001,
                latitude=40.4 + i * 0.001,
                velocity=10.0 + i * 0.1,
                acceleration=0.5,
                heading=float(i % 360),
                progress_on_edge=i / 100.0,
            )
            vehicles.append(v)

        spawner = MagicMock(spec=VehicleSpawner)
        spawner.get_all_vehicles.return_value = vehicles
        bc = SimulationBroadcaster(
            connection_manager=mock_manager,
            vehicle_spawner=spawner,
        )

        t0 = time.monotonic()
        await bc.broadcast_tick(tick=0, sim_time=0.0)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        assert elapsed_ms < 5.0, f"Broadcast tardó {elapsed_ms:.2f}ms (límite: 5ms)"
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["count"] == 100

    @pytest.mark.unit
    async def test_delta_reduces_payload(self, mock_manager, sample_route):
        """En el segundo tick, si solo 10 de 100 vehículos cambian,
        solo esos 10 deben incluirse."""
        vehicles = []
        for i in range(100):
            v = SimVehicle(
                id=f"v_{i:03d}",
                start_node_id=1,
                end_node_id=5,
                route=sample_route,
                status=VehicleStatus.MOVING,
                longitude=-3.7 + i * 0.001,
                latitude=40.4 + i * 0.001,
            )
            vehicles.append(v)

        spawner = MagicMock(spec=VehicleSpawner)
        spawner.get_all_vehicles.return_value = vehicles
        bc = SimulationBroadcaster(
            connection_manager=mock_manager,
            vehicle_spawner=spawner,
        )

        # First tick: all 100 are new
        await bc.broadcast_tick(tick=0, sim_time=0.0)
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["count"] == 100

        mock_manager.broadcast.reset_mock()
        # Change only first 10 vehicles
        for i in range(10):
            vehicles[i].longitude += 0.001

        await bc.broadcast_tick(tick=1, sim_time=0.1)
        mock_manager.broadcast.assert_called_once()
        msg = mock_manager.broadcast.call_args[0][0]
        assert msg["count"] == 10

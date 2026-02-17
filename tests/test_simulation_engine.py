"""
Tests para el motor de simulación: máquina de estados, bucle de ticks
y endpoints de control.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_simulation_engine
from app.core.exceptions import (
    SimulationAlreadyRunningError,
    SimulationNotPausedError,
    SimulationNotRunningError,
)
from app.core.simulation_engine import SimulationEngine, SimulationState
from app.db.database import get_db_session
from app.main import app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
    """Crea una instancia limpia del motor de simulación para cada test."""
    return SimulationEngine(tick_interval_ms=50.0)


def _override_db():
    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.scalar.return_value = "3.3.0"
    mock_session.execute.return_value = mock_result
    yield mock_session


@pytest.fixture
def client(engine):
    """TestClient con engine inyectado y DB mockeada."""
    app.dependency_overrides[get_simulation_engine] = lambda: engine
    app.dependency_overrides[get_db_session] = _override_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# =============================================================================
# Unit Tests - Estado inicial
# =============================================================================


class TestSimulationEngineInit:
    @pytest.mark.unit
    def test_initial_state_is_idle(self, engine):
        assert engine.state == SimulationState.IDLE

    @pytest.mark.unit
    def test_initial_tick_count_is_zero(self, engine):
        assert engine.tick_count == 0

    @pytest.mark.unit
    def test_initial_simulation_time_is_zero(self, engine):
        assert engine.simulation_time == 0.0

    @pytest.mark.unit
    def test_tick_rate_matches_interval(self, engine):
        assert engine.tick_rate == 1000.0 / 50.0  # 20 Hz

    @pytest.mark.unit
    def test_tick_interval_ms(self, engine):
        assert engine.tick_interval_ms == 50.0

    @pytest.mark.unit
    def test_initial_vehicles_active(self, engine):
        assert engine.vehicles_active == 0

    @pytest.mark.unit
    def test_initial_uptime_is_zero(self, engine):
        assert engine.uptime_seconds == 0.0

    @pytest.mark.unit
    def test_default_tick_interval(self):
        default_engine = SimulationEngine()
        assert default_engine.tick_interval_ms == 100.0
        assert default_engine.tick_rate == 10.0


# =============================================================================
# Unit Tests - Máquina de estados
# =============================================================================


class TestSimulationStateTransitions:
    @pytest.mark.unit
    async def test_start_changes_state_to_running(self, engine):
        await engine.start()
        assert engine.state == SimulationState.RUNNING
        await engine.shutdown()

    @pytest.mark.unit
    async def test_stop_changes_state_to_stopped(self, engine):
        await engine.start()
        await engine.stop()
        assert engine.state == SimulationState.STOPPED

    @pytest.mark.unit
    async def test_pause_changes_state_to_paused(self, engine):
        await engine.start()
        await engine.pause()
        assert engine.state == SimulationState.PAUSED

    @pytest.mark.unit
    async def test_resume_changes_state_to_running(self, engine):
        await engine.start()
        await engine.pause()
        await engine.resume()
        assert engine.state == SimulationState.RUNNING
        await engine.shutdown()

    @pytest.mark.unit
    async def test_start_from_stopped(self, engine):
        await engine.start()
        await engine.stop()
        await engine.start()
        assert engine.state == SimulationState.RUNNING
        await engine.shutdown()

    @pytest.mark.unit
    async def test_stop_from_paused(self, engine):
        await engine.start()
        await engine.pause()
        await engine.stop()
        assert engine.state == SimulationState.STOPPED


# =============================================================================
# Unit Tests - Transiciones inválidas
# =============================================================================


class TestSimulationInvalidTransitions:
    @pytest.mark.unit
    async def test_start_when_running_raises(self, engine):
        await engine.start()
        with pytest.raises(SimulationAlreadyRunningError):
            await engine.start()
        await engine.shutdown()

    @pytest.mark.unit
    async def test_start_when_paused_raises(self, engine):
        await engine.start()
        await engine.pause()
        with pytest.raises(SimulationAlreadyRunningError):
            await engine.start()
        await engine.stop()

    @pytest.mark.unit
    async def test_stop_when_idle_raises(self, engine):
        with pytest.raises(SimulationNotRunningError):
            await engine.stop()

    @pytest.mark.unit
    async def test_stop_when_stopped_raises(self, engine):
        await engine.start()
        await engine.stop()
        with pytest.raises(SimulationNotRunningError):
            await engine.stop()

    @pytest.mark.unit
    async def test_pause_when_idle_raises(self, engine):
        with pytest.raises(SimulationNotRunningError):
            await engine.pause()

    @pytest.mark.unit
    async def test_pause_when_paused_raises(self, engine):
        await engine.start()
        await engine.pause()
        with pytest.raises(SimulationNotRunningError):
            await engine.pause()
        await engine.stop()

    @pytest.mark.unit
    async def test_resume_when_idle_raises(self, engine):
        with pytest.raises(SimulationNotPausedError):
            await engine.resume()

    @pytest.mark.unit
    async def test_resume_when_running_raises(self, engine):
        await engine.start()
        with pytest.raises(SimulationNotPausedError):
            await engine.resume()
        await engine.shutdown()

    @pytest.mark.unit
    async def test_resume_when_stopped_raises(self, engine):
        await engine.start()
        await engine.stop()
        with pytest.raises(SimulationNotPausedError):
            await engine.resume()


# =============================================================================
# Unit Tests - Bucle de ticks
# =============================================================================


class TestSimulationLoop:
    @pytest.mark.unit
    async def test_ticks_increment(self, engine):
        await engine.start()
        await asyncio.sleep(0.15)  # ~3 ticks a 50ms
        await engine.stop()
        assert engine.tick_count >= 2

    @pytest.mark.unit
    async def test_simulation_time_advances(self, engine):
        await engine.start()
        await asyncio.sleep(0.15)
        await engine.stop()
        assert engine.simulation_time > 0.0

    @pytest.mark.unit
    async def test_pause_stops_ticking(self, engine):
        await engine.start()
        await asyncio.sleep(0.12)
        await engine.pause()
        ticks_at_pause = engine.tick_count
        await asyncio.sleep(0.12)
        assert engine.tick_count == ticks_at_pause
        await engine.stop()

    @pytest.mark.unit
    async def test_resume_continues_ticking(self, engine):
        await engine.start()
        await asyncio.sleep(0.12)
        await engine.pause()
        ticks_at_pause = engine.tick_count
        await engine.resume()
        await asyncio.sleep(0.12)
        await engine.stop()
        assert engine.tick_count > ticks_at_pause

    @pytest.mark.unit
    async def test_start_resets_counters(self, engine):
        await engine.start()
        await asyncio.sleep(0.12)
        await engine.stop()
        assert engine.tick_count > 0

        await engine.start()
        # Counters reset on start
        assert engine.tick_count == 0
        assert engine.simulation_time == 0.0
        await engine.shutdown()


# =============================================================================
# Unit Tests - get_status
# =============================================================================


class TestSimulationStatus:
    @pytest.mark.unit
    def test_status_idle(self, engine):
        status = engine.get_status()
        assert status["state"] == "idle"
        assert status["tick_count"] == 0
        assert status["simulation_time_seconds"] == 0.0
        assert status["vehicles_active"] == 0
        assert status["uptime_seconds"] == 0.0

    @pytest.mark.unit
    async def test_status_running(self, engine):
        await engine.start()
        await asyncio.sleep(0.06)
        status = engine.get_status()
        assert status["state"] == "running"
        assert status["tick_count"] >= 1
        assert status["uptime_seconds"] > 0
        await engine.shutdown()

    @pytest.mark.unit
    async def test_status_paused(self, engine):
        await engine.start()
        await engine.pause()
        status = engine.get_status()
        assert status["state"] == "paused"
        await engine.stop()

    @pytest.mark.unit
    async def test_status_stopped(self, engine):
        await engine.start()
        await engine.stop()
        status = engine.get_status()
        assert status["state"] == "stopped"


# =============================================================================
# Unit Tests - Shutdown graceful
# =============================================================================


class TestSimulationShutdown:
    @pytest.mark.unit
    async def test_shutdown_when_running(self, engine):
        await engine.start()
        await engine.shutdown()
        assert engine.state == SimulationState.STOPPED

    @pytest.mark.unit
    async def test_shutdown_when_paused(self, engine):
        await engine.start()
        await engine.pause()
        await engine.shutdown()
        assert engine.state == SimulationState.STOPPED

    @pytest.mark.unit
    async def test_shutdown_when_idle_is_noop(self, engine):
        await engine.shutdown()
        assert engine.state == SimulationState.IDLE

    @pytest.mark.unit
    async def test_shutdown_when_stopped_is_noop(self, engine):
        await engine.start()
        await engine.stop()
        await engine.shutdown()
        assert engine.state == SimulationState.STOPPED


# =============================================================================
# Unit Tests - Exceptions
# =============================================================================


class TestSimulationExceptions:
    @pytest.mark.unit
    def test_already_running_message(self):
        e = SimulationAlreadyRunningError()
        assert "ya está en ejecución" in str(e)

    @pytest.mark.unit
    def test_not_running_message(self):
        e = SimulationNotRunningError()
        assert "no está en ejecución" in str(e)

    @pytest.mark.unit
    def test_not_paused_message(self):
        e = SimulationNotPausedError()
        assert "no está pausada" in str(e)


# =============================================================================
# Integration Tests - API Endpoints
# =============================================================================


class TestSimulationEndpoints:
    @pytest.mark.integration
    def test_get_status_idle(self, client):
        response = client.get("/api/simulation/status")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "idle"
        assert data["tick_count"] == 0
        assert "simulation_time_seconds" in data
        assert "vehicles_active" in data
        assert "uptime_seconds" in data

    @pytest.mark.integration
    def test_start_simulation(self, client):
        response = client.post("/api/simulation/start")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["tick_rate"] == 1000.0 / 50.0
        assert data["tick_interval_ms"] == 50.0

    @pytest.mark.integration
    def test_start_already_running_returns_409(self, client):
        client.post("/api/simulation/start")
        response = client.post("/api/simulation/start")
        assert response.status_code == 409

    @pytest.mark.integration
    def test_stop_simulation(self, client):
        client.post("/api/simulation/start")
        response = client.post("/api/simulation/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"

    @pytest.mark.integration
    def test_stop_when_idle_returns_409(self, client):
        response = client.post("/api/simulation/stop")
        assert response.status_code == 409

    @pytest.mark.integration
    def test_pause_simulation(self, client):
        client.post("/api/simulation/start")
        response = client.post("/api/simulation/pause")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "paused"

    @pytest.mark.integration
    def test_pause_when_idle_returns_409(self, client):
        response = client.post("/api/simulation/pause")
        assert response.status_code == 409

    @pytest.mark.integration
    def test_resume_simulation(self, client):
        client.post("/api/simulation/start")
        client.post("/api/simulation/pause")
        response = client.post("/api/simulation/resume")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "resumed"

    @pytest.mark.integration
    def test_resume_when_idle_returns_409(self, client):
        response = client.post("/api/simulation/resume")
        assert response.status_code == 409

    @pytest.mark.integration
    def test_full_lifecycle(self, client):
        """Test del ciclo completo: start -> pause -> resume -> stop."""
        # Start
        r = client.post("/api/simulation/start")
        assert r.status_code == 200

        # Status while running
        r = client.get("/api/simulation/status")
        assert r.json()["state"] == "running"

        # Pause
        r = client.post("/api/simulation/pause")
        assert r.status_code == 200

        # Status while paused
        r = client.get("/api/simulation/status")
        assert r.json()["state"] == "paused"

        # Resume
        r = client.post("/api/simulation/resume")
        assert r.status_code == 200

        # Stop
        r = client.post("/api/simulation/stop")
        assert r.status_code == 200

        # Status after stop
        r = client.get("/api/simulation/status")
        assert r.json()["state"] == "stopped"

    @pytest.mark.integration
    def test_restart_after_stop(self, client):
        """Se puede reiniciar la simulación después de detenerla."""
        client.post("/api/simulation/start")
        client.post("/api/simulation/stop")
        r = client.post("/api/simulation/start")
        assert r.status_code == 200
        assert r.json()["status"] == "started"

"""
Tests para SimulationConfig: validación, cálculos derivados, conversión a
parámetros de física, hot-update en el engine y endpoints de API.
"""

import math
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_simulation_config, get_simulation_engine
from app.core.physics.parameters import IDMParameters, MOBILParameters
from app.core.simulation_config import IDMConfig, MOBILConfig, SimulationConfig
from app.core.simulation_engine import SimulationEngine, SimulationState
from app.db.database import get_db_session
from app.main import app
from app.services.vehicle_spawner import VehicleSpawner


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def default_config() -> SimulationConfig:
    return SimulationConfig()


@pytest.fixture
def custom_config() -> SimulationConfig:
    return SimulationConfig(
        tick_rate=20,
        auto_spawn=True,
        spawn_rate=30,
        max_vehicles=50,
        idm=IDMConfig(v0=20.0, s0=3.0, T=2.0, a=1.5, b=2.0, delta=4.0),
        mobil=MOBILConfig(politeness=0.3, b_safe=3.5, a_threshold=0.1),
    )


def _override_db():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = "3.3.0"
    mock_session.execute.return_value = mock_result
    yield mock_session


@pytest.fixture
def engine():
    return SimulationEngine(tick_interval_ms=50.0)


@pytest.fixture
def client(engine):
    """TestClient con engine y config mockeados."""
    config = SimulationConfig()
    engine.set_config(config)
    app.dependency_overrides[get_simulation_engine] = lambda: engine
    app.dependency_overrides[get_simulation_config] = lambda: config
    app.dependency_overrides[get_db_session] = _override_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


@pytest.fixture
def client_with_config(engine):
    """TestClient que usa el engine real con set_config para hot-update."""
    config = SimulationConfig()
    engine.set_config(config)
    app.dependency_overrides[get_simulation_engine] = lambda: engine
    app.dependency_overrides[get_simulation_config] = lambda: engine.get_config() or config
    app.dependency_overrides[get_db_session] = _override_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# =============================================================================
# IDMConfig
# =============================================================================


class TestIDMConfig:
    @pytest.mark.unit
    def test_default_values(self):
        cfg = IDMConfig()
        assert cfg.v0 == 13.89
        assert cfg.s0 == 2.0
        assert cfg.T == 1.5
        assert cfg.a == 1.0
        assert cfg.b == 1.5
        assert cfg.delta == 4.0

    @pytest.mark.unit
    def test_custom_values(self):
        cfg = IDMConfig(v0=20.0, s0=3.0, T=2.0, a=2.0, b=3.0, delta=5.0)
        assert cfg.v0 == 20.0
        assert cfg.s0 == 3.0

    @pytest.mark.unit
    def test_to_parameters_returns_idm_parameters(self):
        cfg = IDMConfig(v0=20.0, s0=3.0, T=2.0, a=2.0, b=3.0, delta=5.0)
        params = cfg.to_parameters()
        assert isinstance(params, IDMParameters)
        assert params.v0 == 20.0
        assert params.s0 == 3.0
        assert params.T == 2.0
        assert params.a == 2.0
        assert params.b == 3.0
        assert params.delta == 5.0

    @pytest.mark.unit
    def test_invalid_v0_zero(self):
        with pytest.raises(ValidationError):
            IDMConfig(v0=0.0)

    @pytest.mark.unit
    def test_invalid_v0_negative(self):
        with pytest.raises(ValidationError):
            IDMConfig(v0=-5.0)

    @pytest.mark.unit
    def test_invalid_s0_negative(self):
        with pytest.raises(ValidationError):
            IDMConfig(s0=-1.0)

    @pytest.mark.unit
    def test_invalid_a_zero(self):
        with pytest.raises(ValidationError):
            IDMConfig(a=0.0)


# =============================================================================
# MOBILConfig
# =============================================================================


class TestMOBILConfig:
    @pytest.mark.unit
    def test_default_values(self):
        cfg = MOBILConfig()
        assert cfg.politeness == 0.5
        assert cfg.b_safe == 4.0
        assert cfg.a_threshold == 0.2

    @pytest.mark.unit
    def test_to_parameters_returns_mobil_parameters(self):
        cfg = MOBILConfig(politeness=0.3, b_safe=3.0, a_threshold=0.1)
        params = cfg.to_parameters()
        assert isinstance(params, MOBILParameters)
        assert params.politeness == 0.3
        assert params.b_safe == 3.0
        assert params.a_threshold == 0.1

    @pytest.mark.unit
    def test_politeness_bounds(self):
        MOBILConfig(politeness=0.0)
        MOBILConfig(politeness=1.0)
        with pytest.raises(ValidationError):
            MOBILConfig(politeness=-0.1)
        with pytest.raises(ValidationError):
            MOBILConfig(politeness=1.1)

    @pytest.mark.unit
    def test_invalid_b_safe_zero(self):
        with pytest.raises(ValidationError):
            MOBILConfig(b_safe=0.0)

    @pytest.mark.unit
    def test_a_threshold_zero_allowed(self):
        cfg = MOBILConfig(a_threshold=0.0)
        assert cfg.a_threshold == 0.0


# =============================================================================
# SimulationConfig - valores por defecto y validación
# =============================================================================


class TestSimulationConfigDefaults:
    @pytest.mark.unit
    def test_default_values(self, default_config):
        assert default_config.tick_rate == 3
        assert default_config.auto_spawn is True
        assert default_config.spawn_rate == 10
        # Default es ilimitado (1e9): el cap real sale de settings.MAX_VEHICLES.
        assert default_config.max_vehicles >= 1_000_000_000
        assert isinstance(default_config.idm, IDMConfig)
        assert isinstance(default_config.mobil, MOBILConfig)

    @pytest.mark.unit
    def test_tick_interval_ms_property(self, default_config):
        # Default tick_rate=3 → 1000/3 ≈ 333.33 ms por tick.
        assert default_config.tick_interval_ms == pytest.approx(1000.0 / 3.0)

    @pytest.mark.unit
    def test_tick_interval_ms_custom(self):
        cfg = SimulationConfig(tick_rate=20)
        assert cfg.tick_interval_ms == 50.0

    @pytest.mark.unit
    def test_tick_rate_min(self):
        SimulationConfig(tick_rate=1)
        with pytest.raises(ValidationError):
            SimulationConfig(tick_rate=0)

    @pytest.mark.unit
    def test_tick_rate_max(self):
        SimulationConfig(tick_rate=100)
        with pytest.raises(ValidationError):
            SimulationConfig(tick_rate=101)

    @pytest.mark.unit
    def test_spawn_rate_zero_allowed(self):
        cfg = SimulationConfig(spawn_rate=0)
        assert cfg.spawn_rate == 0

    @pytest.mark.unit
    def test_spawn_rate_max(self):
        SimulationConfig(spawn_rate=600)
        with pytest.raises(ValidationError):
            SimulationConfig(spawn_rate=601)

    @pytest.mark.unit
    def test_max_vehicles_min(self):
        SimulationConfig(max_vehicles=1)
        with pytest.raises(ValidationError):
            SimulationConfig(max_vehicles=0)


# =============================================================================
# SimulationConfig - ticks_between_spawns
# =============================================================================


class TestTicksBetweenSpawns:
    @pytest.mark.unit
    def test_default_config(self, default_config):
        # tick_rate=3, spawn_rate=10 => ceil(3*60/10) = 18
        assert default_config.ticks_between_spawns == 18

    @pytest.mark.unit
    def test_high_spawn_rate(self):
        # tick_rate=10, spawn_rate=60 => ceil(600/60) = 10
        cfg = SimulationConfig(tick_rate=10, spawn_rate=60)
        assert cfg.ticks_between_spawns == 10

    @pytest.mark.unit
    def test_very_high_spawn_rate(self):
        # tick_rate=10, spawn_rate=600 => ceil(600/600) = 1 (minimum)
        cfg = SimulationConfig(tick_rate=10, spawn_rate=600)
        assert cfg.ticks_between_spawns == 1

    @pytest.mark.unit
    def test_low_spawn_rate(self):
        # tick_rate=10, spawn_rate=1 => ceil(600/1) = 600
        cfg = SimulationConfig(tick_rate=10, spawn_rate=1)
        assert cfg.ticks_between_spawns == 600

    @pytest.mark.unit
    def test_spawn_rate_zero_returns_large_value(self):
        cfg = SimulationConfig(spawn_rate=0)
        assert cfg.ticks_between_spawns == int(1e9)

    @pytest.mark.unit
    def test_higher_tick_rate_means_more_ticks_between_spawns(self):
        # Same spawn_rate but higher tick_rate => more ticks between spawns
        cfg_slow = SimulationConfig(tick_rate=10, spawn_rate=10)
        cfg_fast = SimulationConfig(tick_rate=20, spawn_rate=10)
        assert cfg_fast.ticks_between_spawns == cfg_slow.ticks_between_spawns * 2


# =============================================================================
# SimulationConfig - conversión a parámetros de física
# =============================================================================


class TestConfigConversion:
    @pytest.mark.unit
    def test_to_idm_parameters(self, custom_config):
        params = custom_config.to_idm_parameters()
        assert isinstance(params, IDMParameters)
        assert params.v0 == 20.0
        assert params.s0 == 3.0

    @pytest.mark.unit
    def test_to_mobil_parameters(self, custom_config):
        params = custom_config.to_mobil_parameters()
        assert isinstance(params, MOBILParameters)
        assert params.politeness == 0.3
        assert params.b_safe == 3.5

    @pytest.mark.unit
    def test_to_dict_includes_all_keys(self, default_config):
        d = default_config.to_dict()
        assert "tick_rate" in d
        assert "tick_interval_ms" in d
        assert "auto_spawn" in d
        assert "spawn_rate" in d
        assert "ticks_between_spawns" in d
        assert "max_vehicles" in d
        assert "idm" in d
        assert "mobil" in d

    @pytest.mark.unit
    def test_to_dict_computed_values(self, default_config):
        d = default_config.to_dict()
        assert d["tick_interval_ms"] == 333.333  # round(1000/3, 3)
        assert d["ticks_between_spawns"] == 18

    @pytest.mark.unit
    def test_idm_params_are_frozen(self, default_config):
        params = default_config.to_idm_parameters()
        with pytest.raises((AttributeError, TypeError)):
            params.v0 = 99.0  # frozen dataclass


# =============================================================================
# SimulationEngine - set_config / hot-update
# =============================================================================


class TestEngineSetConfig:
    @pytest.mark.unit
    def test_set_config_updates_tick_interval(self, engine):
        cfg = SimulationConfig(tick_rate=20)
        engine.set_config(cfg)
        assert engine.tick_interval_ms == 50.0

    @pytest.mark.unit
    def test_set_config_stores_config(self, engine):
        cfg = SimulationConfig(tick_rate=5)
        engine.set_config(cfg)
        assert engine.get_config() is cfg

    @pytest.mark.unit
    def test_get_config_none_before_injection(self):
        fresh = SimulationEngine()
        assert fresh.get_config() is None

    @pytest.mark.unit
    def test_set_config_updates_spawner_max_vehicles(self, engine):
        spawner = MagicMock(spec=VehicleSpawner)
        spawner.max_vehicles = 100
        engine.set_spawner(spawner)
        cfg = SimulationConfig(max_vehicles=42)
        engine.set_config(cfg)
        assert spawner.max_vehicles == 42

    @pytest.mark.unit
    def test_hot_update_changes_tick_interval_without_restart(self, engine):
        engine.set_config(SimulationConfig(tick_rate=10))
        assert engine.tick_interval_ms == 100.0
        engine.set_config(SimulationConfig(tick_rate=20))
        assert engine.tick_interval_ms == 50.0

    @pytest.mark.unit
    async def test_start_uses_config_tick_rate(self, engine):
        cfg = SimulationConfig(tick_rate=20)
        engine.set_config(cfg)
        await engine.start()
        assert engine.tick_interval_ms == 50.0
        await engine.shutdown()


# =============================================================================
# SimulationEngine - auto-spawn in _tick()
# =============================================================================


class TestAutoSpawn:
    @pytest.fixture
    def spawner(self):
        s = MagicMock(spec=VehicleSpawner)
        s.spawn.return_value = []
        # SimulationEngine._tick consulta `_spawner.graph.node_count` para
        # decidir si lazy-init del controller de semáforos. Con grafo vacío
        # se salta esa rama y nos centramos en la lógica de auto-spawn.
        s.graph.node_count = 0
        return s

    @pytest.mark.unit
    async def test_auto_spawn_calls_spawn_on_correct_ticks(self, engine, spawner):
        cfg = SimulationConfig(tick_rate=10, auto_spawn=True, spawn_rate=10)
        engine.set_config(cfg)
        engine.set_spawner(spawner)
        # ticks_between_spawns = 60
        # tick_count=0: should spawn
        await engine._tick(0.1)
        spawner.spawn.assert_called_once_with(count=1)

    @pytest.mark.unit
    async def test_auto_spawn_not_called_on_non_spawn_tick(self, engine, spawner):
        cfg = SimulationConfig(tick_rate=10, auto_spawn=True, spawn_rate=10)
        engine.set_config(cfg)
        engine.set_spawner(spawner)
        engine._tick_count = 1  # not a spawn tick (ticks_between=60)
        await engine._tick(0.1)
        spawner.spawn.assert_not_called()

    @pytest.mark.unit
    async def test_auto_spawn_disabled(self, engine, spawner):
        cfg = SimulationConfig(auto_spawn=False, spawn_rate=10)
        engine.set_config(cfg)
        engine.set_spawner(spawner)
        engine._tick_count = 0
        await engine._tick(0.1)
        spawner.spawn.assert_not_called()

    @pytest.mark.unit
    async def test_auto_spawn_spawn_rate_zero(self, engine, spawner):
        cfg = SimulationConfig(auto_spawn=True, spawn_rate=0)
        engine.set_config(cfg)
        engine.set_spawner(spawner)
        engine._tick_count = 0
        await engine._tick(0.1)
        spawner.spawn.assert_not_called()

    @pytest.mark.unit
    async def test_auto_spawn_without_spawner(self, engine):
        cfg = SimulationConfig(auto_spawn=True, spawn_rate=10)
        engine.set_config(cfg)
        engine._spawner = None
        engine._tick_count = 0
        # Should not raise
        await engine._tick(0.1)

    @pytest.mark.unit
    async def test_auto_spawn_without_config(self, engine, spawner):
        engine.set_spawner(spawner)
        engine._config = None
        engine._tick_count = 0
        await engine._tick(0.1)
        spawner.spawn.assert_not_called()

    @pytest.mark.unit
    async def test_auto_spawn_value_error_silenced(self, engine, spawner):
        spawner.spawn.side_effect = ValueError("No hay nodos")
        cfg = SimulationConfig(auto_spawn=True, spawn_rate=600)
        engine.set_config(cfg)
        engine.set_spawner(spawner)
        engine._tick_count = 0
        # Should not raise
        await engine._tick(0.1)

    @pytest.mark.unit
    async def test_auto_spawn_periodic_interval(self, engine, spawner):
        """Comprueba que spawn se llama en ticks 0, N, 2N, ... pero no intermedios."""
        cfg = SimulationConfig(tick_rate=10, auto_spawn=True, spawn_rate=60)
        engine.set_config(cfg)
        engine.set_spawner(spawner)
        # ticks_between_spawns = ceil(10*60/60) = 10

        spawn_ticks = []
        for tick in range(30):
            engine._tick_count = tick
            spawner.spawn.reset_mock()
            await engine._tick(0.1)
            if spawner.spawn.called:
                spawn_ticks.append(tick)

        assert spawn_ticks == [0, 10, 20]


# =============================================================================
# API Endpoints - GET /config
# =============================================================================


class TestGetConfigEndpoint:
    @pytest.mark.unit
    def test_get_config_returns_200(self, client):
        resp = client.get("/api/simulation/config")
        assert resp.status_code == 200

    @pytest.mark.unit
    def test_get_config_has_required_fields(self, client):
        data = client.get("/api/simulation/config").json()
        assert "tick_rate" in data
        assert "tick_interval_ms" in data
        assert "auto_spawn" in data
        assert "spawn_rate" in data
        assert "max_vehicles" in data
        assert "idm" in data
        assert "mobil" in data

    @pytest.mark.unit
    def test_get_config_default_tick_rate(self, client):
        data = client.get("/api/simulation/config").json()
        assert data["tick_rate"] == 3

    @pytest.mark.unit
    def test_get_config_idm_sub_fields(self, client):
        data = client.get("/api/simulation/config").json()
        idm = data["idm"]
        assert "v0" in idm
        assert "s0" in idm
        assert "T" in idm
        assert "a" in idm
        assert "b" in idm
        assert "delta" in idm

    @pytest.mark.unit
    def test_get_config_mobil_sub_fields(self, client):
        data = client.get("/api/simulation/config").json()
        mobil = data["mobil"]
        assert "politeness" in mobil
        assert "b_safe" in mobil
        assert "a_threshold" in mobil


# =============================================================================
# API Endpoints - PUT /config
# =============================================================================


class TestPutConfigEndpoint:
    @pytest.mark.unit
    def test_put_config_returns_200(self, client_with_config):
        payload = SimulationConfig(tick_rate=20).model_dump()
        resp = client_with_config.put("/api/simulation/config", json=payload)
        assert resp.status_code == 200

    @pytest.mark.unit
    def test_put_config_response_has_status_updated(self, client_with_config):
        payload = SimulationConfig(tick_rate=20).model_dump()
        data = client_with_config.put("/api/simulation/config", json=payload).json()
        assert data["status"] == "updated"
        assert "config" in data

    @pytest.mark.unit
    def test_put_config_updates_tick_rate(self, client_with_config, engine):
        payload = SimulationConfig(tick_rate=20).model_dump()
        client_with_config.put("/api/simulation/config", json=payload)
        assert engine.tick_interval_ms == 50.0

    @pytest.mark.unit
    def test_put_config_updates_auto_spawn(self, client_with_config, engine):
        payload = SimulationConfig(auto_spawn=False).model_dump()
        client_with_config.put("/api/simulation/config", json=payload)
        assert engine.get_config().auto_spawn is False

    @pytest.mark.unit
    def test_put_config_invalid_tick_rate_returns_422(self, client_with_config):
        payload = {"tick_rate": 0, "auto_spawn": True, "spawn_rate": 10, "max_vehicles": 100,
                   "idm": IDMConfig().model_dump(), "mobil": MOBILConfig().model_dump()}
        resp = client_with_config.put("/api/simulation/config", json=payload)
        assert resp.status_code == 422

    @pytest.mark.unit
    def test_put_config_invalid_max_vehicles_returns_422(self, client_with_config):
        payload = {"tick_rate": 10, "auto_spawn": True, "spawn_rate": 10, "max_vehicles": 0,
                   "idm": IDMConfig().model_dump(), "mobil": MOBILConfig().model_dump()}
        resp = client_with_config.put("/api/simulation/config", json=payload)
        assert resp.status_code == 422

    @pytest.mark.unit
    def test_put_config_invalid_idm_v0_returns_422(self, client_with_config):
        payload = SimulationConfig().model_dump()
        payload["idm"]["v0"] = -1.0
        resp = client_with_config.put("/api/simulation/config", json=payload)
        assert resp.status_code == 422

    @pytest.mark.unit
    def test_put_config_hot_update_while_running(self, client_with_config, engine):
        """El PUT es aceptado aunque la simulación esté corriendo."""
        engine._state = engine.state.__class__.RUNNING
        payload = SimulationConfig(tick_rate=5).model_dump()
        resp = client_with_config.put("/api/simulation/config", json=payload)
        assert resp.status_code == 200
        assert engine.tick_interval_ms == 200.0
        engine._state = engine.state.__class__.IDLE

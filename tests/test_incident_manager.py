"""
Tests unitarios del IncidentManager (sin BD).

Se mockea ``async_session_factory`` para que la persistencia falle de forma
limpia y el manager caiga al camino de IDs sintéticos negativos, permitiendo
testar la lógica de proyección (``blocked_edges``, ``closed_lanes``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import IncidentStatus, IncidentType
from app.services.incident_manager import IncidentManager


def _make_stub_graph() -> MagicMock:
    """Grafo mock con una única arista (1,2) de 3 carriles, edge_id=42."""
    g = MagicMock()
    g.get_edge_attributes.return_value = {"edge_id": 42, "lanes": 3}
    # edges(data=True) yielding una entrada: (1, 2, {edge_id: 42})
    g.graph.edges = MagicMock(return_value=[(1, 2, {"edge_id": 42})])
    return g


def _make_stub_spawner() -> MagicMock:
    spawner = MagicMock()
    spawner.closed_lanes = {}
    spawner.blocked_edges = {}
    return spawner


def _make_stub_broadcaster() -> MagicMock:
    b = MagicMock()
    b.broadcast_incident = AsyncMock()
    return b


@pytest.fixture
def manager():
    mgr = IncidentManager(
        spawner=_make_stub_spawner(),
        broadcaster=_make_stub_broadcaster(),
        graph=_make_stub_graph(),
    )
    return mgr


class TestProjection:
    @pytest.mark.unit
    async def test_partial_lanes_only_populates_closed_lanes(self, manager):
        # La persistencia fallará → synthetic ID.
        with patch(
            "app.services.incident_manager.async_session_factory",
            side_effect=Exception("no-db"),
        ):
            inc = await manager.create(
                type_=IncidentType.ROADWORK,
                edge=(1, 2),
                lanes_affected=[0, 1],  # 2 de 3 carriles
                duration_s=60.0,
                sim_time=0.0,
            )
        assert inc.id < 0  # synthetic
        # closed_lanes debe tener {0, 1}, blocked_edges vacío.
        assert manager._spawner.closed_lanes[(1, 2)] == {0, 1}
        assert (1, 2) not in manager._spawner.blocked_edges

    @pytest.mark.unit
    async def test_all_lanes_blocks_edge(self, manager):
        with patch(
            "app.services.incident_manager.async_session_factory",
            side_effect=Exception("no-db"),
        ):
            inc = await manager.create(
                type_=IncidentType.ACCIDENT,
                edge=(1, 2),
                lanes_affected=[0, 1, 2],  # 3 de 3
                duration_s=None,  # permanente
                sim_time=0.0,
            )
        assert manager._spawner.closed_lanes[(1, 2)] == {0, 1, 2}
        assert (1, 2) in manager._spawner.blocked_edges

    @pytest.mark.unit
    async def test_clear_reverts_projection(self, manager):
        with patch(
            "app.services.incident_manager.async_session_factory",
            side_effect=Exception("no-db"),
        ):
            inc = await manager.create(
                type_=IncidentType.ROADWORK,
                edge=(1, 2),
                lanes_affected=[0],
                duration_s=60.0,
                sim_time=0.0,
            )
            cleared = await manager.clear(inc.id)
        assert cleared is True
        assert (1, 2) not in manager._spawner.closed_lanes  # set vacío → popeado
        assert (1, 2) not in manager._spawner.blocked_edges

    @pytest.mark.unit
    async def test_clear_keeps_other_incident_closed_lanes(self, manager):
        """Si dos incidentes comparten arista, cerrar uno no libera los carriles del otro."""
        with patch(
            "app.services.incident_manager.async_session_factory",
            side_effect=Exception("no-db"),
        ):
            inc1 = await manager.create(
                type_=IncidentType.ROADWORK,
                edge=(1, 2),
                lanes_affected=[0],
                duration_s=60.0,
                sim_time=0.0,
            )
            inc2 = await manager.create(
                type_=IncidentType.ROADWORK,
                edge=(1, 2),
                lanes_affected=[1],
                duration_s=60.0,
                sim_time=0.0,
            )
            await manager.clear(inc1.id)
        # El carril 1 debe seguir cerrado por inc2
        assert manager._spawner.closed_lanes[(1, 2)] == {1}


class TestExpiration:
    @pytest.mark.unit
    async def test_tick_returns_expired_ids(self, manager):
        with patch(
            "app.services.incident_manager.async_session_factory",
            side_effect=Exception("no-db"),
        ):
            inc = await manager.create(
                type_=IncidentType.BREAKDOWN,
                edge=(1, 2),
                lanes_affected=[0],
                duration_s=10.0,
                sim_time=0.0,
            )
        # sim_time < duration → no expira
        assert manager.tick(sim_time=5.0) == []
        # sim_time >= duration → expira
        assert inc.id in manager.tick(sim_time=10.0)

    @pytest.mark.unit
    async def test_permanent_incident_never_expires(self, manager):
        with patch(
            "app.services.incident_manager.async_session_factory",
            side_effect=Exception("no-db"),
        ):
            await manager.create(
                type_=IncidentType.ACCIDENT,
                edge=(1, 2),
                lanes_affected=[0, 1, 2],
                duration_s=None,
                sim_time=0.0,
            )
        # Después de 1h no expira
        assert manager.tick(sim_time=3600.0) == []


class TestRecordAccident:
    @pytest.mark.unit
    def test_record_accident_is_sync(self, manager):
        inc = manager.record_accident(
            edge=(1, 2), sim_time=12.34, vehicle_ids=("v_1", "v_2")
        )
        assert inc.type == IncidentType.ACCIDENT.value
        assert inc.status == IncidentStatus.ACTIVE.value
        assert inc.severity == 3
        assert inc.lanes_affected == [0, 1, 2]
        assert inc.id < 0  # synthetic

    @pytest.mark.unit
    def test_record_accident_is_idempotent_per_edge(self, manager):
        inc1 = manager.record_accident(edge=(1, 2), sim_time=0.0)
        inc2 = manager.record_accident(edge=(1, 2), sim_time=1.0)
        # La segunda llamada devuelve el MISMO incidente (no se duplica).
        assert inc1 is inc2

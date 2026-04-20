"""
Tests para VehicleSpawner y endpoints de vehículos.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_simulation_engine, get_vehicle_spawner
from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_NODE_TYPE,
    ATTR_WEIGHT,
)
from app.db.database import get_db_session
from app.main import app
from app.models.enums import NodeType, VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import (
    SimVehicle,
    VehicleSpawner,
)


# =============================================================================
# Fixtures
# =============================================================================


def _build_test_graph() -> RoadNetworkGraph:
    """Grafo de prueba con entry/exit nodes y varias rutas."""
    g = RoadNetworkGraph()

    # Entry points
    g.graph.add_node(1, **{
        ATTR_NODE_TYPE: NodeType.ENTRY_POINT.value,
        ATTR_LONGITUDE: -3.7,
        ATTR_LATITUDE: 40.4,
    })
    g.graph.add_node(2, **{
        ATTR_NODE_TYPE: NodeType.ENTRY_POINT.value,
        ATTR_LONGITUDE: -3.6,
        ATTR_LATITUDE: 40.5,
    })

    # Intersections
    g.graph.add_node(10, **{
        ATTR_NODE_TYPE: NodeType.INTERSECTION.value,
        ATTR_LONGITUDE: -3.65,
        ATTR_LATITUDE: 40.45,
    })
    g.graph.add_node(11, **{
        ATTR_NODE_TYPE: NodeType.INTERSECTION.value,
        ATTR_LONGITUDE: -3.63,
        ATTR_LATITUDE: 40.43,
    })

    # Exit points
    g.graph.add_node(100, **{
        ATTR_NODE_TYPE: NodeType.EXIT_POINT.value,
        ATTR_LONGITUDE: -3.5,
        ATTR_LATITUDE: 40.3,
    })
    g.graph.add_node(101, **{
        ATTR_NODE_TYPE: NodeType.EXIT_POINT.value,
        ATTR_LONGITUDE: -3.55,
        ATTR_LATITUDE: 40.35,
    })

    # Edges: 1->10->100, 1->11->101, 2->10->100, 2->11->101
    g.graph.add_edge(1, 10, **{ATTR_EDGE_ID: 1, ATTR_LENGTH: 500.0, ATTR_WEIGHT: 25.0})
    g.graph.add_edge(10, 100, **{ATTR_EDGE_ID: 2, ATTR_LENGTH: 800.0, ATTR_WEIGHT: 40.0})
    g.graph.add_edge(1, 11, **{ATTR_EDGE_ID: 3, ATTR_LENGTH: 600.0, ATTR_WEIGHT: 30.0})
    g.graph.add_edge(11, 101, **{ATTR_EDGE_ID: 4, ATTR_LENGTH: 700.0, ATTR_WEIGHT: 35.0})
    g.graph.add_edge(2, 10, **{ATTR_EDGE_ID: 5, ATTR_LENGTH: 400.0, ATTR_WEIGHT: 20.0})
    g.graph.add_edge(2, 11, **{ATTR_EDGE_ID: 6, ATTR_LENGTH: 350.0, ATTR_WEIGHT: 17.5})
    g.graph.add_edge(10, 101, **{ATTR_EDGE_ID: 7, ATTR_LENGTH: 450.0, ATTR_WEIGHT: 22.5})
    g.graph.add_edge(11, 100, **{ATTR_EDGE_ID: 8, ATTR_LENGTH: 550.0, ATTR_WEIGHT: 27.5})

    return g


@pytest.fixture
def graph():
    return _build_test_graph()


@pytest.fixture
def spawner(graph):
    return VehicleSpawner(graph=graph, max_vehicles=10)


def _override_db():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = "3.3.0"
    mock_session.execute.return_value = mock_result
    yield mock_session


@pytest.fixture
def client(spawner):
    """TestClient con spawner inyectado."""
    from app.core.simulation_engine import SimulationEngine

    engine = SimulationEngine(tick_interval_ms=50.0)
    app.dependency_overrides[get_vehicle_spawner] = lambda: spawner
    app.dependency_overrides[get_simulation_engine] = lambda: engine
    app.dependency_overrides[get_db_session] = _override_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# =============================================================================
# VehicleSpawner - Inicialización
# =============================================================================


class TestVehicleSpawnerInit:
    @pytest.mark.unit
    def test_initial_state(self, spawner):
        assert spawner.active_count == 0
        assert spawner.max_vehicles == 10
        assert len(spawner.get_all_vehicles()) == 0

    @pytest.mark.unit
    def test_entry_nodes(self, spawner):
        entries = spawner.get_entry_nodes()
        assert set(entries) == {1, 2}

    @pytest.mark.unit
    def test_exit_nodes(self, spawner):
        exits = spawner.get_exit_nodes()
        assert set(exits) == {100, 101}


# =============================================================================
# VehicleSpawner - Spawn
# =============================================================================


class TestVehicleSpawnerSpawn:
    @pytest.mark.unit
    def test_spawn_one(self, spawner):
        vehicles = spawner.spawn(count=1)
        assert len(vehicles) == 1
        v = vehicles[0]
        assert v.id == "v_001"
        assert v.status == VehicleStatus.IDLE
        assert v.start_node_id in {1, 2}
        assert v.end_node_id in {100, 101}
        assert len(v.route.edge_ids) >= 1
        assert v.route.length_m > 0

    @pytest.mark.unit
    def test_spawn_multiple(self, spawner):
        vehicles = spawner.spawn(count=3)
        assert len(vehicles) == 3
        ids = {v.id for v in vehicles}
        assert len(ids) == 3  # IDs únicos

    @pytest.mark.unit
    def test_spawn_increments_counter(self, spawner):
        spawner.spawn(count=2)
        v3 = spawner.spawn(count=1)
        assert v3[0].id == "v_003"

    @pytest.mark.unit
    def test_spawn_respects_max_vehicles(self, spawner):
        spawner.spawn(count=10)
        assert spawner.active_count == 10

        # Intentar spawn más allá del límite
        overflow = spawner.spawn(count=5)
        assert len(overflow) == 0
        assert spawner.active_count == 10

    @pytest.mark.unit
    def test_spawn_partial_when_near_limit(self, spawner):
        spawner.spawn(count=8)
        extra = spawner.spawn(count=5)
        assert len(extra) == 2
        assert spawner.active_count == 10

    @pytest.mark.unit
    def test_spawn_vehicle_has_position(self, spawner):
        vehicles = spawner.spawn(count=1)
        v = vehicles[0]
        # Debe tener coordenadas del nodo de entrada
        assert v.longitude != 0.0 or v.latitude != 0.0

    @pytest.mark.unit
    def test_spawn_no_entry_nodes_raises(self):
        g = RoadNetworkGraph()
        g.graph.add_node(1, **{ATTR_NODE_TYPE: "intersection"})
        g.graph.add_node(2, **{ATTR_NODE_TYPE: NodeType.EXIT_POINT.value})
        s = VehicleSpawner(graph=g, max_vehicles=10)
        with pytest.raises(ValueError, match="entrada"):
            s.spawn(count=1)

    @pytest.mark.unit
    def test_spawn_no_exit_nodes_raises(self):
        g = RoadNetworkGraph()
        g.graph.add_node(1, **{ATTR_NODE_TYPE: NodeType.ENTRY_POINT.value})
        g.graph.add_node(2, **{ATTR_NODE_TYPE: "intersection"})
        s = VehicleSpawner(graph=g, max_vehicles=10)
        with pytest.raises(ValueError, match="salida"):
            s.spawn(count=1)


# =============================================================================
# VehicleSpawner - CRUD
# =============================================================================


class TestVehicleSpawnerCRUD:
    @pytest.mark.unit
    def test_get_vehicle(self, spawner):
        vehicles = spawner.spawn(count=1)
        v = spawner.get_vehicle(vehicles[0].id)
        assert v is not None
        assert v.id == vehicles[0].id

    @pytest.mark.unit
    def test_get_vehicle_not_found(self, spawner):
        assert spawner.get_vehicle("v_999") is None

    @pytest.mark.unit
    def test_get_all_vehicles(self, spawner):
        spawner.spawn(count=3)
        all_v = spawner.get_all_vehicles()
        assert len(all_v) == 3

    @pytest.mark.unit
    def test_remove_vehicle(self, spawner):
        vehicles = spawner.spawn(count=2)
        removed = spawner.remove_vehicle(vehicles[0].id)
        assert removed is True
        assert spawner.get_vehicle(vehicles[0].id) is None
        assert len(spawner.get_all_vehicles()) == 1

    @pytest.mark.unit
    def test_remove_vehicle_not_found(self, spawner):
        assert spawner.remove_vehicle("v_999") is False


# =============================================================================
# SimVehicle
# =============================================================================


class TestSimVehicle:
    @pytest.mark.unit
    def test_to_dict(self, spawner):
        vehicles = spawner.spawn(count=1)
        d = vehicles[0].to_dict()
        assert "id" in d
        assert "start_node_id" in d
        assert "end_node_id" in d
        assert "route_edges" in d
        assert "route_length_m" in d
        assert d["status"] == "idle"


# =============================================================================
# Integration Tests - API Endpoints
# =============================================================================


class TestVehicleEndpoints:
    @pytest.mark.integration
    def test_spawn_vehicles(self, client):
        response = client.post(
            "/api/simulation/vehicles/spawn",
            json={"count": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["spawned"] == 2
        assert len(data["vehicles"]) == 2
        v = data["vehicles"][0]
        assert "id" in v
        assert "start_node_id" in v
        assert "end_node_id" in v
        assert "route_edges" in v
        assert "route_length_m" in v
        assert v["status"] == "idle"

    @pytest.mark.integration
    def test_spawn_default_count(self, client):
        response = client.post(
            "/api/simulation/vehicles/spawn",
            json={},
        )
        assert response.status_code == 200
        assert response.json()["spawned"] == 1

    @pytest.mark.integration
    def test_list_vehicles(self, client):
        client.post("/api/simulation/vehicles/spawn", json={"count": 3})
        response = client.get("/api/simulation/vehicles")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert len(data["vehicles"]) == 3

    @pytest.mark.integration
    def test_get_vehicle_by_id(self, client):
        spawn_resp = client.post(
            "/api/simulation/vehicles/spawn",
            json={"count": 1},
        )
        vehicle_id = spawn_resp.json()["vehicles"][0]["id"]

        response = client.get(f"/api/simulation/vehicles/{vehicle_id}")
        assert response.status_code == 200
        assert response.json()["id"] == vehicle_id

    @pytest.mark.integration
    def test_get_vehicle_not_found(self, client):
        response = client.get("/api/simulation/vehicles/v_999")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_delete_vehicle(self, client):
        spawn_resp = client.post(
            "/api/simulation/vehicles/spawn",
            json={"count": 1},
        )
        vehicle_id = spawn_resp.json()["vehicles"][0]["id"]

        response = client.delete(f"/api/simulation/vehicles/{vehicle_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # Verify deleted
        response = client.get(f"/api/simulation/vehicles/{vehicle_id}")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_delete_vehicle_not_found(self, client):
        response = client.delete("/api/simulation/vehicles/v_999")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_spawn_invalid_count(self, client):
        response = client.post(
            "/api/simulation/vehicles/spawn",
            json={"count": 0},
        )
        assert response.status_code == 422

    @pytest.mark.integration
    def test_spawn_respects_max_vehicles(self, client):
        # Spawner has max_vehicles=10
        client.post("/api/simulation/vehicles/spawn", json={"count": 10})
        response = client.post(
            "/api/simulation/vehicles/spawn",
            json={"count": 5},
        )
        assert response.status_code == 200
        assert response.json()["spawned"] == 0

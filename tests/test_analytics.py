"""
Tests del motor de analíticas de tráfico (app/core/analytics.py).

Validan las tres métricas clave de dominio:
  - tiempo medio de viaje y velocidad media de trayecto,
  - nivel de congestión por arista (ocupación/capacidad),
  - impacto de incidentes (vehículos cuya ruta atraviesa una arista bloqueada).

Todo se ejerce sin base de datos, construyendo un grafo sintético y vehículos
en memoria, de modo que son pruebas unitarias puras.
"""

import pytest

from app.core.analytics import TrafficAnalytics
from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_ROAD_TYPE,
    ATTR_WEIGHT,
)
from app.core.route import RouteInfo
from app.models.enums import VehicleStatus
from app.services.network_graph import RoadNetworkGraph
from app.services.vehicle_spawner import SimVehicle


@pytest.fixture
def line_graph() -> RoadNetworkGraph:
    """Grafo lineal 1 → 2 → 3, dos aristas de 100 m y un solo carril."""
    g = RoadNetworkGraph()
    base = {ATTR_NODE_TYPE: "intersection", ATTR_LONGITUDE: -4.83}
    g.graph.add_node(1, **{ATTR_NODE_ID: 1, ATTR_LATITUDE: 39.960, **base})
    g.graph.add_node(2, **{ATTR_NODE_ID: 2, ATTR_LATITUDE: 39.961, **base})
    g.graph.add_node(3, **{ATTR_NODE_ID: 3, ATTR_LATITUDE: 39.962, **base})
    common = {
        ATTR_MAX_SPEED: 50,
        ATTR_ROAD_TYPE: "residential",
        ATTR_ONE_WAY: True,
        ATTR_LENGTH: 100.0,
        ATTR_LANES: 1,
        ATTR_WEIGHT: 1.0,
    }
    g.graph.add_edge(1, 2, **{ATTR_EDGE_ID: 1, **common})
    g.graph.add_edge(2, 3, **{ATTR_EDGE_ID: 2, **common})
    return g


def _vehicle(vid: str, node_path: list[int], edge_idx: int = 0) -> SimVehicle:
    route = RouteInfo(
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        node_path=node_path,
        edge_ids=list(range(1, len(node_path))),
        length_m=100.0 * (len(node_path) - 1),
    )
    return SimVehicle(
        id=vid,
        start_node_id=node_path[0],
        end_node_id=node_path[-1],
        route=route,
        status=VehicleStatus.MOVING,
        current_edge_index=edge_idx,
    )


class TestTravelTime:
    @pytest.mark.unit
    def test_record_trip_aggregates_time_and_speed(self):
        a = TrafficAnalytics()
        # 200 m en 40 s → 5 m/s = 18 km/h.
        a.record_trip(travel_time_s=40.0, route_length_m=200.0)
        snap = a.snapshot()
        assert snap["travel_time_s"]["trips_completed"] == 1
        assert snap["travel_time_s"]["mean"] == pytest.approx(40.0)
        assert snap["trip_speed_kmh"]["mean"] == pytest.approx(18.0)

    @pytest.mark.unit
    def test_zero_or_negative_time_ignored(self):
        a = TrafficAnalytics()
        a.record_trip(travel_time_s=0.0, route_length_m=100.0)
        a.record_trip(travel_time_s=-5.0, route_length_m=100.0)
        assert a.snapshot()["travel_time_s"]["trips_completed"] == 0

    @pytest.mark.unit
    def test_percentiles_over_several_trips(self):
        a = TrafficAnalytics()
        for t in (10.0, 20.0, 30.0, 40.0, 50.0):
            a.record_trip(travel_time_s=t, route_length_m=100.0)
        tt = a.snapshot()["travel_time_s"]
        assert tt["mean"] == pytest.approx(30.0)
        assert tt["max"] == pytest.approx(50.0)
        assert tt["p50"] == pytest.approx(30.0)


class TestCongestion:
    @pytest.mark.unit
    def test_empty_network_has_zero_congestion(self, line_graph):
        a = TrafficAnalytics()
        a.sample({}, line_graph, {})
        cong = a.snapshot()["congestion"]
        assert cong["occupied_edges"] == 0
        assert cong["congestion_mean"] == 0.0
        assert cong["congested_edges"] == 0

    @pytest.mark.unit
    def test_saturated_edge_is_flagged_congested(self, line_graph):
        # Capacidad de la arista 1→2: 100 m · 1 carril / 7 m ≈ 14 huecos.
        # 12 vehículos → ratio ≈ 0.857 > umbral 0.6 → congestionada.
        vehicles = {
            f"v{i}": _vehicle(f"v{i}", [1, 2, 3], edge_idx=0) for i in range(12)
        }
        a = TrafficAnalytics()
        a.sample(vehicles, line_graph, {})
        cong = a.snapshot()["congestion"]
        assert cong["occupied_edges"] == 1
        assert cong["congested_edges"] == 1
        assert cong["congestion_max"] > 0.6

    @pytest.mark.unit
    def test_finished_vehicles_excluded(self, line_graph):
        v = _vehicle("v1", [1, 2, 3], edge_idx=0)
        v.status = VehicleStatus.FINISHED
        a = TrafficAnalytics()
        a.sample({"v1": v}, line_graph, {})
        assert a.snapshot()["congestion"]["occupied_edges"] == 0


class TestIncidentImpact:
    @pytest.mark.unit
    def test_vehicle_on_blocked_route_counts_as_affected(self, line_graph):
        # Vehículo en 1→2 con 2→3 bloqueada por delante: afectado.
        v = _vehicle("v1", [1, 2, 3], edge_idx=0)
        a = TrafficAnalytics()
        a.sample({"v1": v}, line_graph, {(2, 3): None})
        inc = a.snapshot()["incidents"]
        assert inc["blocked_edges"] == 1
        assert inc["vehicles_affected"] == 1

    @pytest.mark.unit
    def test_vehicle_past_the_block_not_affected(self, line_graph):
        # Ya pasó 1→2; el bloqueo en 1→2 queda detrás → no afectado.
        v = _vehicle("v1", [1, 2, 3], edge_idx=1)
        a = TrafficAnalytics()
        a.sample({"v1": v}, line_graph, {(1, 2): None})
        assert a.snapshot()["incidents"]["vehicles_affected"] == 0

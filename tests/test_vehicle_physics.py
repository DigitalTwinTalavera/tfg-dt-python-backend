"""
Tests para VehiclePhysics: integración IDM + geometría de aristas.
"""

import math

import pytest
from shapely.geometry import LineString

from app.core.physics.parameters import IDMParameters
from app.core.physics.vehicle_physics import (
    NeighborInfo,
    VehiclePhysics,
    VehicleStateUpdate,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def physics():
    return VehiclePhysics()


@pytest.fixture
def simple_edge():
    """Arista recta horizontal larga (en grados ≈ 0.1 ≈ 11km)."""
    return LineString([(0.0, 0.0), (0.1, 0.0)])


@pytest.fixture
def two_edge_route():
    """Ruta con 2 aristas: horizontal luego diagonal."""
    edge1 = LineString([(0.0, 0.0), (0.1, 0.0)])
    edge2 = LineString([(0.1, 0.0), (0.2, 0.1)])
    return [edge1, edge2]


@pytest.fixture
def curved_edge():
    """Arista con curva (3 puntos)."""
    return LineString([(0.0, 0.0), (0.05, 0.05), (0.1, 0.0)])


# =============================================================================
# Tests de inicialización
# =============================================================================


class TestVehiclePhysicsInit:
    @pytest.mark.unit
    def test_default_constructor(self):
        vp = VehiclePhysics()
        assert vp.idm.params.v0 == 13.89

    @pytest.mark.unit
    def test_custom_idm_params(self):
        params = IDMParameters(v0=30.0, a=2.0)
        vp = VehiclePhysics(idm_params=params)
        assert vp.idm.params.v0 == 30.0
        assert vp.idm.params.a == 2.0


# =============================================================================
# Tests de carretera libre (free road)
# =============================================================================


class TestVehiclePhysicsFreeRoad:
    @pytest.mark.unit
    def test_vehicle_accelerates_from_stop(self, physics, simple_edge):
        result = physics.update(
            velocity=0.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.velocity > 0.0
        assert result.acceleration > 0.0
        assert not result.route_completed

    @pytest.mark.unit
    def test_vehicle_at_speed_limit_no_accel(self, physics, simple_edge):
        result = physics.update(
            velocity=13.89,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.acceleration == pytest.approx(0.0, abs=0.01)

    @pytest.mark.unit
    def test_vehicle_above_speed_limit_decelerates(self, physics, simple_edge):
        result = physics.update(
            velocity=20.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.acceleration < 0.0


# =============================================================================
# Tests de car-following
# =============================================================================


class TestVehiclePhysicsCarFollowing:
    @pytest.mark.unit
    def test_following_slower_leader(self, physics, simple_edge):
        leader = NeighborInfo(distance=20.0, velocity=5.0)
        result = physics.update(
            velocity=10.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
            leader=leader,
        )
        # Debe desacelerar por el líder lento
        assert result.acceleration < physics.idm.calculate_acceleration(
            v=10.0, v0=13.89
        )

    @pytest.mark.unit
    def test_emergency_braking(self, physics, simple_edge):
        leader = NeighborInfo(distance=1.0, velocity=0.0)
        result = physics.update(
            velocity=15.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
            leader=leader,
        )
        assert result.acceleration < -2.0

    @pytest.mark.unit
    def test_velocity_does_not_go_negative(self, physics, simple_edge):
        leader = NeighborInfo(distance=0.5, velocity=0.0)
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=1.0,
            leader=leader,
        )
        assert result.velocity >= 0.0


# =============================================================================
# Tests de posición e interpolación
# =============================================================================


class TestVehiclePhysicsPosition:
    @pytest.mark.unit
    def test_position_at_start(self, physics, simple_edge):
        """Posición al inicio con v=0 y dt pequeño: cercana al origen."""
        result = physics.update(
            velocity=0.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        # Con v=0 y dt=0.1, se mueve muy poco (0.5*a*dt²)
        assert result.longitude == pytest.approx(0.0, abs=0.01)
        assert result.latitude == pytest.approx(0.0, abs=0.01)

    @pytest.mark.unit
    def test_position_advances(self, physics, simple_edge):
        result = physics.update(
            velocity=5.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.progress_on_edge > 0.0

    @pytest.mark.unit
    def test_position_at_end(self, physics, simple_edge):
        result = physics.update(
            velocity=0.0,
            current_edge_index=0,
            progress_on_edge=1.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.longitude == pytest.approx(0.1, abs=0.001)


# =============================================================================
# Tests de transición entre aristas
# =============================================================================


class TestVehiclePhysicsEdgeTransition:
    @pytest.mark.unit
    def test_transition_to_next_edge(self, physics, two_edge_route):
        """Vehículo rápido que cruza la primera arista."""
        edge_length = two_edge_route[0].length
        # Alta velocidad para cruzar toda la arista en un tick
        result = physics.update(
            velocity=edge_length * 20,
            current_edge_index=0,
            progress_on_edge=0.9,
            edge_geometries=two_edge_route,
            edge_speed_limits=[13.89, 13.89],
            dt=0.1,
        )
        assert result.current_edge_index >= 1

    @pytest.mark.unit
    def test_route_completed(self, physics, simple_edge):
        """Vehículo que completa la ruta."""
        edge_length = simple_edge.length
        result = physics.update(
            velocity=edge_length * 20,
            current_edge_index=0,
            progress_on_edge=0.95,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.route_completed is True
        assert result.progress_on_edge == 1.0

    @pytest.mark.unit
    def test_route_completed_multiple_edges(self, physics, two_edge_route):
        """Vehículo que completa la ruta con múltiples aristas."""
        total_length = sum(e.length for e in two_edge_route)
        result = physics.update(
            velocity=total_length * 100,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=two_edge_route,
            edge_speed_limits=[13.89, 13.89],
            dt=0.1,
        )
        assert result.route_completed is True


# =============================================================================
# Tests de heading
# =============================================================================


class TestVehiclePhysicsHeading:
    @pytest.mark.unit
    def test_heading_east(self, physics):
        """Arista hacia el este → heading ≈ 90°."""
        edge = LineString([(0.0, 0.0), (0.01, 0.0)])
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.heading == pytest.approx(90.0, abs=1.0)

    @pytest.mark.unit
    def test_heading_north(self, physics):
        """Arista hacia el norte → heading ≈ 0°."""
        edge = LineString([(0.0, 0.0), (0.0, 0.01)])
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.heading == pytest.approx(0.0, abs=1.0)

    @pytest.mark.unit
    def test_heading_south(self, physics):
        """Arista hacia el sur → heading ≈ 180°."""
        edge = LineString([(0.0, 0.01), (0.0, 0.0)])
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.heading == pytest.approx(180.0, abs=1.0)

    @pytest.mark.unit
    def test_heading_west(self, physics):
        """Arista hacia el oeste → heading ≈ 270°."""
        edge = LineString([(0.01, 0.0), (0.0, 0.0)])
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.heading == pytest.approx(270.0, abs=1.0)

    @pytest.mark.unit
    def test_heading_in_range(self, physics, curved_edge):
        """Heading siempre en [0, 360)."""
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[curved_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert 0.0 <= result.heading < 360.0


# =============================================================================
# Tests de edge cases
# =============================================================================


class TestVehiclePhysicsEdgeCases:
    @pytest.mark.unit
    def test_empty_geometries(self, physics):
        """Sin geometrías: ruta completada."""
        result = physics.update(
            velocity=10.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[],
            edge_speed_limits=[],
            dt=0.1,
        )
        assert result.route_completed is True
        assert result.velocity == 0.0

    @pytest.mark.unit
    def test_zero_dt(self, physics, simple_edge):
        """dt = 0: posición no cambia."""
        result = physics.update(
            velocity=10.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.0,
        )
        assert result.progress_on_edge == pytest.approx(0.5, abs=0.001)

    @pytest.mark.unit
    def test_edge_index_clamped(self, physics, simple_edge):
        """Edge index fuera de rango se clampea."""
        result = physics.update(
            velocity=0.0,
            current_edge_index=99,
            progress_on_edge=0.5,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert result.current_edge_index == 0

    @pytest.mark.unit
    def test_zero_length_edge(self, physics):
        """Arista de longitud 0: la salta."""
        zero_edge = LineString([(0.0, 0.0), (0.0, 0.0)])
        real_edge = LineString([(0.0, 0.0), (0.001, 0.0)])
        result = physics.update(
            velocity=1.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[zero_edge, real_edge],
            edge_speed_limits=[13.89, 13.89],
            dt=0.1,
        )
        # Debería haber avanzado a la segunda arista
        assert result.current_edge_index >= 1 or result.progress_on_edge > 0

    @pytest.mark.unit
    def test_speed_limit_used_as_v0(self, physics, simple_edge):
        """El límite de velocidad de la arista se usa como v0."""
        # Con límite bajo, debe desacelerar
        result_low = physics.update(
            velocity=10.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[5.0],  # 5 m/s = 18 km/h
            dt=0.1,
        )
        # Con límite alto, debe acelerar
        result_high = physics.update(
            velocity=10.0,
            current_edge_index=0,
            progress_on_edge=0.0,
            edge_geometries=[simple_edge],
            edge_speed_limits=[30.0],  # 30 m/s = 108 km/h
            dt=0.1,
        )
        assert result_low.acceleration < result_high.acceleration


# =============================================================================
# Tests de integración numérica
# =============================================================================


class TestVehiclePhysicsIntegration:
    @pytest.mark.unit
    def test_multiple_ticks_converge_to_v0(self, physics):
        """Múltiples ticks: velocidad converge al límite."""
        edge = LineString([(0.0, 0.0), (100.0, 0.0)])  # Arista muy larga
        v = 0.0
        progress = 0.0
        edge_idx = 0

        for _ in range(200):
            result = physics.update(
                velocity=v,
                current_edge_index=edge_idx,
                progress_on_edge=progress,
                edge_geometries=[edge],
                edge_speed_limits=[13.89],
                dt=0.1,
            )
            v = result.velocity
            progress = result.progress_on_edge
            edge_idx = result.current_edge_index
            if result.route_completed:
                break

        # Velocidad debe haber convergido cerca de v0
        # IDM con delta=4 converge asintóticamente, tolerancia 2 m/s
        assert v == pytest.approx(13.89, abs=2.0)

    @pytest.mark.unit
    def test_state_update_all_fields(self, physics, simple_edge):
        """Verificar que todos los campos del estado están presentes."""
        result = physics.update(
            velocity=5.0,
            current_edge_index=0,
            progress_on_edge=0.5,
            edge_geometries=[simple_edge],
            edge_speed_limits=[13.89],
            dt=0.1,
        )
        assert isinstance(result, VehicleStateUpdate)
        assert math.isfinite(result.longitude)
        assert math.isfinite(result.latitude)
        assert math.isfinite(result.velocity)
        assert math.isfinite(result.acceleration)
        assert math.isfinite(result.heading)
        assert isinstance(result.current_edge_index, int)
        assert isinstance(result.progress_on_edge, float)
        assert isinstance(result.route_completed, bool)

"""
Tests para el Intelligent Driver Model (IDM).
Verifica la fórmula IDM con resultados analíticos conocidos.
"""

import math

import pytest

from app.core.physics.idm import IDMModel
from app.core.physics.parameters import IDMParameters


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def default_params():
    return IDMParameters()


@pytest.fixture
def idm(default_params):
    return IDMModel(params=default_params)


@pytest.fixture
def custom_idm():
    """IDM con parámetros simples para verificación analítica."""
    params = IDMParameters(v0=20.0, s0=2.0, T=1.5, a=1.0, b=2.0, delta=4.0)
    return IDMModel(params=params)


# =============================================================================
# Tests de parámetros
# =============================================================================


class TestIDMParameters:
    @pytest.mark.unit
    def test_default_values(self, default_params):
        assert default_params.v0 == 13.89
        assert default_params.s0 == 2.0
        assert default_params.T == 1.5
        assert default_params.a == 1.0
        assert default_params.b == 1.5
        assert default_params.delta == 4.0

    @pytest.mark.unit
    def test_frozen(self, default_params):
        with pytest.raises(AttributeError):
            default_params.v0 = 30.0

    @pytest.mark.unit
    def test_custom_params(self):
        p = IDMParameters(v0=30.0, s0=3.0, T=2.0, a=1.5, b=2.0, delta=2.0)
        assert p.v0 == 30.0
        assert p.delta == 2.0


# =============================================================================
# Tests de carretera libre (free road)
# =============================================================================


class TestIDMFreeRoad:
    @pytest.mark.unit
    def test_stopped_on_free_road(self, idm):
        """Vehículo parado en carretera libre: aceleración máxima."""
        accel = idm.calculate_acceleration(v=0.0)
        # a * (1 - 0) = a = 1.0
        assert accel == pytest.approx(1.0)

    @pytest.mark.unit
    def test_at_desired_speed(self, idm):
        """Vehículo a velocidad deseada: aceleración ≈ 0."""
        accel = idm.calculate_acceleration(v=idm.params.v0)
        # a * (1 - 1^delta) = 0
        assert accel == pytest.approx(0.0)

    @pytest.mark.unit
    def test_above_desired_speed(self, idm):
        """Vehículo por encima de velocidad deseada: desacelera."""
        accel = idm.calculate_acceleration(v=idm.params.v0 * 1.5)
        assert accel < 0.0

    @pytest.mark.unit
    def test_half_desired_speed(self, custom_idm):
        """Vehículo a v0/2 en carretera libre: verificación analítica."""
        v = 10.0  # v0 = 20.0
        accel = custom_idm.calculate_acceleration(v=v)
        # a * (1 - (10/20)^4) = 1.0 * (1 - 0.0625) = 0.9375
        assert accel == pytest.approx(0.9375)

    @pytest.mark.unit
    def test_custom_v0_overrides_default(self, idm):
        """v0 explícito sobreescribe el del parámetro."""
        accel_default = idm.calculate_acceleration(v=10.0)
        accel_custom = idm.calculate_acceleration(v=10.0, v0=10.0)
        # Con v0=10.0, a v=10.0 → accel ≈ 0
        assert accel_custom == pytest.approx(0.0)
        assert accel_default != accel_custom

    @pytest.mark.unit
    def test_free_road_no_lead_velocity(self, idm):
        """s dado pero v_lead None → free road."""
        accel_free = idm.calculate_acceleration(v=5.0)
        accel_with_s = idm.calculate_acceleration(v=5.0, s=100.0, v_lead=None)
        assert accel_free == accel_with_s

    @pytest.mark.unit
    def test_free_road_no_gap(self, idm):
        """v_lead dado pero s None → free road."""
        accel_free = idm.calculate_acceleration(v=5.0)
        accel_with_v = idm.calculate_acceleration(v=5.0, s=None, v_lead=10.0)
        assert accel_free == accel_with_v


# =============================================================================
# Tests de car-following
# =============================================================================


class TestIDMCarFollowing:
    @pytest.mark.unit
    def test_large_gap_approaches_free_road(self, idm):
        """Con gap muy grande, aceleración ≈ free road."""
        accel_free = idm.calculate_acceleration(v=5.0)
        accel_follow = idm.calculate_acceleration(v=5.0, s=10000.0, v_lead=5.0)
        assert accel_follow == pytest.approx(accel_free, abs=0.01)

    @pytest.mark.unit
    def test_same_speed_desired_gap(self, custom_idm):
        """Misma velocidad → Δv=0, s* = s0 + v*T."""
        v = 10.0
        # s* = 2.0 + 10.0*1.5 = 17.0
        s_star = custom_idm._desired_gap(v=10.0, v_lead=10.0)
        assert s_star == pytest.approx(17.0)

    @pytest.mark.unit
    def test_approaching_leader(self, custom_idm):
        """Δv > 0 (acercándose): s* aumenta."""
        s_star_same = custom_idm._desired_gap(v=15.0, v_lead=15.0)
        s_star_approaching = custom_idm._desired_gap(v=15.0, v_lead=10.0)
        assert s_star_approaching > s_star_same

    @pytest.mark.unit
    def test_leader_faster(self, custom_idm):
        """Δv < 0 (líder más rápido): s* disminuye (mínimo s0)."""
        s_star = custom_idm._desired_gap(v=5.0, v_lead=15.0)
        # dynamic_term = 5*1.5 + 5*(-10)/(2*sqrt(2)) = 7.5 - 17.68 < 0
        # s* = s0 + max(0, negative) = s0 = 2.0
        assert s_star == pytest.approx(2.0)

    @pytest.mark.unit
    def test_desired_gap_stopped(self, custom_idm):
        """Ambos parados: s* = s0."""
        s_star = custom_idm._desired_gap(v=0.0, v_lead=0.0)
        assert s_star == pytest.approx(2.0)

    @pytest.mark.unit
    def test_small_gap_strong_braking(self, idm):
        """Gap menor que s0: frenado fuerte."""
        accel = idm.calculate_acceleration(v=10.0, s=1.0, v_lead=5.0)
        assert accel < -1.0  # Fuerte deceleración

    @pytest.mark.unit
    def test_equilibrium_gap(self, custom_idm):
        """En equilibrio (gap = s*), aceleración ≈ 0."""
        v = 10.0
        v_lead = 10.0
        s_star = custom_idm._desired_gap(v, v_lead)
        # Con s = s*, interaction_term = 1.0
        # accel = a * (1 - free_term - 1.0)
        # No es exactamente 0 por el free_term, pero debe ser < free_road
        accel_eq = custom_idm.calculate_acceleration(v=v, s=s_star, v_lead=v_lead)
        accel_free = custom_idm.calculate_acceleration(v=v)
        assert accel_eq < accel_free


# =============================================================================
# Tests de frenado de emergencia
# =============================================================================


class TestIDMEmergencyBraking:
    @pytest.mark.unit
    def test_zero_gap_extreme_braking(self, idm):
        """Gap ≈ 0: deceleración extrema."""
        accel = idm.calculate_acceleration(v=10.0, s=0.01, v_lead=0.0)
        assert accel < -5.0

    @pytest.mark.unit
    def test_gap_less_than_s0(self, idm):
        """Gap < s0: deceleración."""
        accel = idm.calculate_acceleration(v=5.0, s=1.0, v_lead=5.0)
        assert accel < 0.0

    @pytest.mark.unit
    def test_negative_gap_protected(self, idm):
        """Gap negativo (colisión): se protege con 0.01 mínimo."""
        accel = idm.calculate_acceleration(v=10.0, s=-1.0, v_lead=5.0)
        assert math.isfinite(accel)
        assert accel < -10.0


# =============================================================================
# Tests de edge cases
# =============================================================================


class TestIDMEdgeCases:
    @pytest.mark.unit
    def test_zero_desired_speed(self):
        """v0 = 0: free_term = 1.0, vehículo no quiere moverse."""
        params = IDMParameters(v0=0.0)
        idm = IDMModel(params=params)
        # Con v=0: free_term = 1.0 si v0=0 (caso especial)
        accel = idm.calculate_acceleration(v=0.0)
        assert accel == pytest.approx(0.0)

    @pytest.mark.unit
    def test_zero_velocity_with_leader(self, idm):
        """v = 0 con líder: intenta arrancar si hay hueco suficiente."""
        accel = idm.calculate_acceleration(v=0.0, s=50.0, v_lead=10.0)
        assert accel > 0.0

    @pytest.mark.unit
    def test_both_stopped_large_gap(self, idm):
        """Ambos parados con gran gap: intenta arrancar."""
        accel = idm.calculate_acceleration(v=0.0, s=100.0, v_lead=0.0)
        # s* = s0 = 2.0, s = 100 → interaction term ≈ 0
        assert accel > 0.5

    @pytest.mark.unit
    def test_idm_default_constructor(self):
        """Constructor sin parámetros usa defaults."""
        idm = IDMModel()
        assert idm.params.v0 == 13.89
        accel = idm.calculate_acceleration(v=0.0)
        assert accel == pytest.approx(1.0)

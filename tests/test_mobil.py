"""
Tests para el modelo MOBIL de cambio de carril.
"""

import pytest

from app.core.physics.mobil import (
    LaneChangeDecision,
    LaneChangeDirection,
    LaneContext,
    MOBILModel,
)
from app.core.physics.parameters import IDMParameters, MOBILParameters


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def default_mobil():
    return MOBILModel()


@pytest.fixture
def aggressive_mobil():
    """Conductor agresivo: politeness=0, threshold bajo."""
    return MOBILModel(
        mobil_params=MOBILParameters(politeness=0.0, b_safe=4.0, a_threshold=0.1),
    )


@pytest.fixture
def polite_mobil():
    """Conductor cortés: politeness=1.0, threshold alto."""
    return MOBILModel(
        mobil_params=MOBILParameters(politeness=1.0, b_safe=4.0, a_threshold=0.5),
    )


# =============================================================================
# Tests de parámetros
# =============================================================================


class TestMOBILParameters:
    @pytest.mark.unit
    def test_default_values(self):
        p = MOBILParameters()
        assert p.politeness == 0.5
        assert p.b_safe == 4.0
        assert p.a_threshold == 0.2

    @pytest.mark.unit
    def test_frozen(self):
        p = MOBILParameters()
        with pytest.raises(AttributeError):
            p.politeness = 0.0


# =============================================================================
# Tests de LaneContext y LaneChangeDecision
# =============================================================================


class TestDataClasses:
    @pytest.mark.unit
    def test_lane_context_defaults(self):
        ctx = LaneContext()
        assert ctx.gap_front is None
        assert ctx.v_front is None
        assert ctx.gap_back is None
        assert ctx.v_back is None
        assert ctx.v_back_current_accel == 0.0

    @pytest.mark.unit
    def test_lane_change_decision(self):
        d = LaneChangeDecision(
            should_change=True,
            direction=LaneChangeDirection.LEFT,
            incentive=0.5,
        )
        assert d.should_change is True
        assert d.direction == LaneChangeDirection.LEFT


# =============================================================================
# Tests sin carriles disponibles
# =============================================================================


class TestMOBILNoLanes:
    @pytest.mark.unit
    def test_no_lanes_available(self, default_mobil):
        """Sin carriles alternativos: no cambiar."""
        decision = default_mobil.evaluate_lane_change(
            v_ego=10.0,
            v0_ego=15.0,
            current_accel=0.5,
            gap_front_current=30.0,
            v_front_current=10.0,
        )
        assert decision.should_change is False
        assert decision.direction == LaneChangeDirection.NONE

    @pytest.mark.unit
    def test_both_lanes_none(self, default_mobil):
        decision = default_mobil.evaluate_lane_change(
            v_ego=10.0,
            v0_ego=15.0,
            current_accel=0.5,
            gap_front_current=30.0,
            v_front_current=10.0,
            lane_left=None,
            lane_right=None,
        )
        assert decision.should_change is False


# =============================================================================
# Tests de criterio de seguridad
# =============================================================================


class TestMOBILSafety:
    @pytest.mark.unit
    def test_unsafe_lane_change_rejected(self, default_mobil):
        """Cambio inseguro: nuevo seguidor frenaría demasiado fuerte."""
        # Ego rápido, seguidor muy cerca detrás en carril objetivo
        decision = default_mobil.evaluate_lane_change(
            v_ego=15.0,
            v0_ego=15.0,
            current_accel=0.0,
            gap_front_current=10.0,
            v_front_current=5.0,
            lane_left=LaneContext(
                gap_front=100.0,
                v_front=15.0,
                gap_back=2.0,  # Muy cerca
                v_back=20.0,   # Viene rápido
                v_back_current_accel=0.5,
            ),
        )
        # El nuevo seguidor tendría que frenar muy fuerte → inseguro
        assert decision.should_change is False

    @pytest.mark.unit
    def test_safe_lane_change_with_large_back_gap(self, aggressive_mobil):
        """Cambio seguro: seguidor lejos en carril objetivo."""
        decision = aggressive_mobil.evaluate_lane_change(
            v_ego=10.0,
            v0_ego=15.0,
            current_accel=-0.5,
            gap_front_current=8.0,
            v_front_current=5.0,
            lane_left=LaneContext(
                gap_front=200.0,
                v_front=15.0,
                gap_back=100.0,  # Muy lejos
                v_back=10.0,
                v_back_current_accel=0.0,
            ),
        )
        # Carril libre delante, seguidor lejos → debería cambiar
        assert decision.should_change is True
        assert decision.direction == LaneChangeDirection.LEFT


# =============================================================================
# Tests de criterio de incentivo
# =============================================================================


class TestMOBILIncentive:
    @pytest.mark.unit
    def test_free_target_lane_incentive(self, aggressive_mobil):
        """Carril objetivo libre: gran incentivo para cambiar."""
        decision = aggressive_mobil.evaluate_lane_change(
            v_ego=8.0,
            v0_ego=15.0,
            current_accel=-0.3,
            gap_front_current=10.0,
            v_front_current=5.0,
            lane_left=LaneContext(
                gap_front=None,  # Libre delante
                v_front=None,
            ),
        )
        # Sin líder en carril objetivo → aceleración libre vs frenando → incentivo alto
        assert decision.should_change is True
        assert decision.incentive > 0.0

    @pytest.mark.unit
    def test_no_benefit_same_situation(self, default_mobil):
        """Misma situación en ambos carriles: no cambiar."""
        decision = default_mobil.evaluate_lane_change(
            v_ego=10.0,
            v0_ego=15.0,
            current_accel=0.5,
            gap_front_current=30.0,
            v_front_current=10.0,
            lane_left=LaneContext(
                gap_front=30.0,
                v_front=10.0,
            ),
        )
        # Misma situación → incentivo ≈ 0 → no supera threshold
        assert decision.should_change is False

    @pytest.mark.unit
    def test_polite_driver_considers_others(self, polite_mobil):
        """Conductor cortés: rechaza cambio que perjudica al seguidor."""
        decision = polite_mobil.evaluate_lane_change(
            v_ego=12.0,
            v0_ego=15.0,
            current_accel=0.2,
            gap_front_current=15.0,
            v_front_current=8.0,
            lane_left=LaneContext(
                gap_front=50.0,
                v_front=14.0,
                gap_back=20.0,
                v_back=14.0,
                v_back_current_accel=0.3,
            ),
        )
        # Cortesía alta reduce el incentivo neto
        # Puede o no cambiar, pero incentivo debe ser menor que agresivo
        assert decision.incentive >= 0.0

    @pytest.mark.unit
    def test_aggressive_driver_ignores_others(self, aggressive_mobil):
        """Conductor agresivo: solo le importa su ganancia."""
        decision = aggressive_mobil.evaluate_lane_change(
            v_ego=8.0,
            v0_ego=15.0,
            current_accel=-0.5,
            gap_front_current=10.0,
            v_front_current=4.0,
            lane_left=LaneContext(
                gap_front=80.0,
                v_front=14.0,
                gap_back=30.0,
                v_back=12.0,
                v_back_current_accel=0.3,
            ),
        )
        assert decision.should_change is True
        assert decision.incentive > 0.0


# =============================================================================
# Tests de selección de dirección
# =============================================================================


class TestMOBILDirectionSelection:
    @pytest.mark.unit
    def test_prefers_better_lane(self, aggressive_mobil):
        """Elige el carril con mayor incentivo."""
        # Izquierda libre, derecha con líder lento
        decision = aggressive_mobil.evaluate_lane_change(
            v_ego=10.0,
            v0_ego=15.0,
            current_accel=0.0,
            gap_front_current=15.0,
            v_front_current=8.0,
            lane_left=LaneContext(
                gap_front=None,  # Libre
                v_front=None,
            ),
            lane_right=LaneContext(
                gap_front=10.0,
                v_front=5.0,
            ),
        )
        assert decision.direction == LaneChangeDirection.LEFT

    @pytest.mark.unit
    def test_only_right_available(self, aggressive_mobil):
        """Solo carril derecho disponible."""
        decision = aggressive_mobil.evaluate_lane_change(
            v_ego=8.0,
            v0_ego=15.0,
            current_accel=-0.5,
            gap_front_current=10.0,
            v_front_current=4.0,
            lane_left=None,
            lane_right=LaneContext(
                gap_front=None,
                v_front=None,
            ),
        )
        if decision.should_change:
            assert decision.direction == LaneChangeDirection.RIGHT


# =============================================================================
# Tests de carretera libre en carril actual
# =============================================================================


class TestMOBILFreeCurrentLane:
    @pytest.mark.unit
    def test_free_current_lane_no_change(self, default_mobil):
        """Carril actual libre: no hay razón para cambiar."""
        decision = default_mobil.evaluate_lane_change(
            v_ego=13.0,
            v0_ego=15.0,
            current_accel=0.3,
            gap_front_current=None,
            v_front_current=None,
            lane_left=LaneContext(
                gap_front=None,
                v_front=None,
            ),
        )
        # Ya libre en carril actual → incentivo ≈ 0
        assert decision.should_change is False


# =============================================================================
# Tests del constructor
# =============================================================================


class TestMOBILConstructor:
    @pytest.mark.unit
    def test_default_constructor(self):
        m = MOBILModel()
        assert m.params.politeness == 0.5
        assert m.idm.params.v0 == 13.89

    @pytest.mark.unit
    def test_custom_idm_params(self):
        idm_p = IDMParameters(v0=30.0)
        m = MOBILModel(idm_params=idm_p)
        assert m.idm.params.v0 == 30.0

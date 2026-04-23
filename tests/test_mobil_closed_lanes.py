"""
Tests para la extensión de MOBIL con carriles cerrados (Módulo 4 TFG).
"""

import pytest

from app.core.physics.mobil import (
    LaneChangeDirection,
    LaneContext,
    MOBILModel,
)
from app.core.physics.parameters import IDMParameters, MOBILParameters


@pytest.fixture
def mobil():
    # Threshold bajo para que cualquier ganancia tenue dispare un cambio.
    return MOBILModel(
        mobil_params=MOBILParameters(politeness=0.0, b_safe=4.0, a_threshold=0.1),
        idm_params=IDMParameters(v0=13.89, s0=2.0, T=1.5, a=1.0, b=2.5),
    )


class TestClosedLanesAsTargets:
    @pytest.mark.unit
    def test_target_lane_in_closed_set_is_rejected(self, mobil):
        """Un candidato con lane_index en closed_lanes_on_edge debe descartarse."""
        ctx_left = LaneContext(
            gap_front=200.0, v_front=15.0, gap_back=200.0, v_back=5.0,
            lane_index=1,
        )
        decision = mobil.evaluate_lane_change(
            v_ego=8.0,
            v0_ego=13.89,
            current_accel=-0.5,
            gap_front_current=10.0,
            v_front_current=2.0,
            lane_left=ctx_left,
            lane_right=None,
            closed_lanes_on_edge={1},  # el carril izquierdo está cerrado
        )
        # Sin alternativa válida
        assert decision.direction == LaneChangeDirection.NONE
        assert decision.should_change is False

    @pytest.mark.unit
    def test_other_lane_accepted_when_one_closed(self, mobil):
        """Si LEFT está cerrado pero RIGHT libre, debe aceptar RIGHT."""
        ctx_left = LaneContext(
            gap_front=200.0, v_front=15.0, gap_back=200.0, v_back=5.0,
            lane_index=1,
        )
        ctx_right = LaneContext(
            gap_front=200.0, v_front=15.0, gap_back=200.0, v_back=5.0,
            lane_index=-1,
        )
        decision = mobil.evaluate_lane_change(
            v_ego=8.0,
            v0_ego=13.89,
            current_accel=-0.5,
            gap_front_current=10.0,
            v_front_current=2.0,
            lane_left=ctx_left,
            lane_right=ctx_right,
            closed_lanes_on_edge={1},
        )
        assert decision.direction == LaneChangeDirection.RIGHT


class TestForceChange:
    @pytest.mark.unit
    def test_force_change_accepts_suboptimal(self, mobil):
        """
        Con force_change=True, MOBIL acepta el mejor candidato seguro aunque
        la ganancia sea negativa (vehículo atrapado en carril cerrado debe salir).
        """
        # Carril izquierdo: líder muy lento → cambiar allí PIERDE aceleración.
        ctx_left = LaneContext(
            gap_front=3.5,  # > MOBIL_MIN_SAFE_GAP_M (3.0) pero ajustado
            v_front=1.0,
            gap_back=50.0,
            v_back=5.0,
            lane_index=1,
        )
        decision = mobil.evaluate_lane_change(
            v_ego=12.0,
            v0_ego=13.89,
            current_accel=1.0,  # actualmente acelerando — lane current es mejor
            gap_front_current=200.0,
            v_front_current=None,
            lane_left=ctx_left,
            lane_right=None,
            force_change=True,
        )
        # Aunque la ganancia sea < a_threshold, force_change lo acepta.
        assert decision.should_change is True
        assert decision.direction == LaneChangeDirection.LEFT

    @pytest.mark.unit
    def test_force_change_still_respects_safety_floor(self, mobil):
        """
        force_change NO sortea el safety floor: si el gap objetivo es < 3 m,
        el candidato se descarta incluso con force_change=True.
        """
        ctx_left = LaneContext(
            gap_front=0.5,  # muy por debajo de MOBIL_MIN_SAFE_GAP_M
            v_front=1.0,
            gap_back=50.0,
            v_back=5.0,
            lane_index=1,
        )
        decision = mobil.evaluate_lane_change(
            v_ego=12.0,
            v0_ego=13.89,
            current_accel=1.0,
            gap_front_current=200.0,
            v_front_current=None,
            lane_left=ctx_left,
            lane_right=None,
            force_change=True,
        )
        # Safety floor > force_change
        assert decision.should_change is False


class TestBackwardCompat:
    @pytest.mark.unit
    def test_no_closed_lanes_behaves_like_before(self, mobil):
        """Sin closed_lanes_on_edge ni force_change, la decisión es la clásica."""
        ctx_left = LaneContext(
            gap_front=200.0, v_front=15.0, gap_back=200.0, v_back=5.0,
            lane_index=1,
        )
        decision = mobil.evaluate_lane_change(
            v_ego=8.0,
            v0_ego=13.89,
            current_accel=-0.5,
            gap_front_current=10.0,
            v_front_current=2.0,
            lane_left=ctx_left,
            lane_right=None,
        )
        # Con gap pequeño delante y lane_left libre → cambio a la izquierda.
        assert decision.direction == LaneChangeDirection.LEFT
        assert decision.should_change is True

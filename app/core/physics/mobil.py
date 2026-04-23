"""
Modelo MOBIL (Minimizing Overall Braking Induced by Lane changes).

Referencia:
  Kesting, A., Treiber, M. & Helbing, D. (2007).
  "General Lane-Changing Model MOBIL for Car-Following Models."
  Transportation Research Record, 1999(1), 86-94.

Criterio de seguridad:
  ã_new_follower >= -b_safe

Criterio de incentivo:
  ã_ego - a_ego - p * (ã_new_follower - a_new_follower + ã_old_follower - a_old_follower) > a_threshold

Donde:
  ã = aceleración después del cambio de carril
  a = aceleración antes del cambio de carril
  p = factor de cortesía
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.constants import MOBIL_MIN_SAFE_GAP_M
from app.core.physics.idm import IDMModel
from app.core.physics.parameters import IDMParameters, MOBILParameters


class LaneChangeDirection(str, Enum):
    """Dirección del cambio de carril."""

    LEFT = "left"
    RIGHT = "right"
    NONE = "none"


@dataclass(frozen=True)
class LaneChangeDecision:
    """Resultado de la evaluación MOBIL."""

    should_change: bool
    direction: LaneChangeDirection
    incentive: float  # ganancia neta en m/s²


@dataclass
class LaneContext:
    """
    Contexto de un carril para evaluación MOBIL.

    Attrs:
        gap_front: Distancia al vehículo delante en el carril objetivo (m).
        v_front: Velocidad del vehículo delante (m/s), None si no hay.
        gap_back: Distancia al vehículo detrás en el carril objetivo (m).
        v_back: Velocidad del vehículo detrás (m/s), None si no hay.
        v_back_current_accel: Aceleración actual del seguidor en el carril objetivo (m/s²).
        lane_index: Índice de carril objetivo (0 = derecha). Si está en
            `closed_lanes_on_edge`, la evaluación se descarta.
    """

    gap_front: float | None = None
    v_front: float | None = None
    gap_back: float | None = None
    v_back: float | None = None
    v_back_current_accel: float = 0.0
    lane_index: int | None = None


class MOBILModel:
    """Motor de evaluación de cambios de carril MOBIL."""

    def __init__(
        self,
        mobil_params: MOBILParameters | None = None,
        idm_params: IDMParameters | None = None,
    ) -> None:
        self.params = mobil_params or MOBILParameters()
        self.idm = IDMModel(params=idm_params or IDMParameters())

    def evaluate_lane_change(
        self,
        v_ego: float,
        v0_ego: float,
        current_accel: float,
        gap_front_current: float | None,
        v_front_current: float | None,
        lane_left: LaneContext | None = None,
        lane_right: LaneContext | None = None,
        closed_lanes_on_edge: set[int] | None = None,
        force_change: bool = False,
    ) -> LaneChangeDecision:
        """
        Evalúa si el vehículo debería cambiar de carril.

        Args:
            v_ego: Velocidad actual del vehículo ego (m/s).
            v0_ego: Velocidad deseada del ego (m/s).
            current_accel: Aceleración actual del ego en su carril (m/s²).
            gap_front_current: Distancia al líder actual (m), None si libre.
            v_front_current: Velocidad del líder actual (m/s), None si libre.
            lane_left: Contexto del carril izquierdo (None si no existe).
            lane_right: Contexto del carril derecho (None si no existe).
            closed_lanes_on_edge: Índices de carriles cerrados en la arista
                actual. Los candidatos cuyo lane_index esté aquí se descartan
                directamente (no se evalúa el incentivo).
            force_change: Si True, el ego está en un carril cerrado y DEBE
                salir. Se acepta cualquier candidato que pase el safety check,
                ignorando el umbral de incentivo.

        Returns:
            LaneChangeDecision con la dirección y la ganancia.
        """
        best_direction = LaneChangeDirection.NONE
        best_incentive = 0.0
        best_direction_safe = LaneChangeDirection.NONE
        best_incentive_safe = float("-inf")
        closed = closed_lanes_on_edge or set()

        for direction, ctx in [
            (LaneChangeDirection.LEFT, lane_left),
            (LaneChangeDirection.RIGHT, lane_right),
        ]:
            if ctx is None:
                continue
            # Descartar candidatos que apunten a un carril cerrado.
            if ctx.lane_index is not None and ctx.lane_index in closed:
                continue

            incentive = self._evaluate_single_lane(
                v_ego=v_ego,
                v0_ego=v0_ego,
                current_accel=current_accel,
                gap_front_current=gap_front_current,
                v_front_current=v_front_current,
                target=ctx,
            )

            if incentive is None:
                continue

            if incentive > best_incentive:
                best_incentive = incentive
                best_direction = direction
            if incentive > best_incentive_safe:
                best_incentive_safe = incentive
                best_direction_safe = direction

        if force_change and best_direction_safe != LaneChangeDirection.NONE:
            # Vehículo atrapado en carril cerrado: acepta el mejor candidato
            # seguro aunque el incentivo sea negativo.
            return LaneChangeDecision(
                should_change=True,
                direction=best_direction_safe,
                incentive=round(best_incentive_safe, 4),
            )

        should_change = (
            best_direction != LaneChangeDirection.NONE
            and best_incentive > self.params.a_threshold
        )

        return LaneChangeDecision(
            should_change=should_change,
            direction=best_direction,
            incentive=round(best_incentive, 4),
        )

    def _evaluate_single_lane(
        self,
        v_ego: float,
        v0_ego: float,
        current_accel: float,
        gap_front_current: float | None,
        v_front_current: float | None,
        target: LaneContext,
    ) -> float | None:
        """
        Evalúa incentivo para un carril objetivo.

        Returns:
            Incentivo neto (m/s²) si es seguro, None si no pasa safety check.
        """
        p = self.params

        # --- Safety floor absoluto: gaps sub-MIN_SAFE_GAP rechazados ---
        # `_build_lane_context` satura gaps negativos a 0.01 m, lo que engaña al
        # criterio IDM cuando el candidato adyacente está físicamente solapado.
        # Un suelo explícito corta de raíz cambios sobre vehículos pegados sin
        # depender de la aritmética de `accel_new_follower`.
        if target.gap_front is not None and target.gap_front < MOBIL_MIN_SAFE_GAP_M:
            return None
        if target.gap_back is not None and target.gap_back < MOBIL_MIN_SAFE_GAP_M:
            return None

        # --- Aceleración del ego en el carril objetivo ---
        accel_ego_target = self.idm.calculate_acceleration(
            v=v_ego,
            v0=v0_ego,
            s=target.gap_front,
            v_lead=target.v_front,
        )

        # --- Safety criterion: nuevo seguidor en el carril objetivo ---
        if target.gap_back is not None and target.v_back is not None:
            accel_new_follower = self.idm.calculate_acceleration(
                v=target.v_back,
                v0=v0_ego,  # simplificación: mismo v0
                s=target.gap_back,
                v_lead=v_ego,  # el ego será su nuevo líder
            )
            if accel_new_follower < -p.b_safe:
                return None  # No es seguro
        else:
            accel_new_follower = 0.0

        # --- Ganancia del ego ---
        gain_ego = accel_ego_target - current_accel

        # --- Pérdida del nuevo seguidor ---
        loss_new_follower = target.v_back_current_accel - accel_new_follower

        # --- Pérdida del antiguo seguidor (simplificado: se libera) ---
        # Al irse el ego, el antiguo seguidor gana, así que loss <= 0
        # Asumimos que su pérdida es 0 (ya que mejora su situación)
        loss_old_follower = 0.0

        # --- Incentivo MOBIL ---
        incentive = gain_ego - p.politeness * (loss_new_follower + loss_old_follower)

        return incentive

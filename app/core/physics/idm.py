"""
Intelligent Driver Model (IDM) — modelo de seguimiento longitudinal.

Referencia:
  Treiber, M., Hennecke, A. & Helbing, D. (2000).
  "Congested traffic states in empirical observations and microscopic simulations."
  Physical Review E, 62(2), 1805.

Fórmula de aceleración:
  a_IDM = a * [1 - (v/v0)^delta - (s*(v, Δv) / s)²]

Distancia deseada:
  s*(v, Δv) = s0 + max(0, v*T + v*Δv / (2*sqrt(a*b)))
"""

from __future__ import annotations

import math

from app.core.physics.parameters import IDMParameters


class IDMModel:
    """Motor de cálculo del Intelligent Driver Model."""

    def __init__(self, params: IDMParameters | None = None) -> None:
        self.params = params or IDMParameters()

    def calculate_acceleration(
        self,
        v: float,
        v0: float | None = None,
        s: float | None = None,
        v_lead: float | None = None,
    ) -> float:
        """
        Calcula la aceleración IDM para un vehículo.

        Args:
            v: Velocidad actual del vehículo (m/s).
            v0: Velocidad deseada (m/s). Si None, usa params.v0.
            s: Distancia al vehículo líder (m). Si None → carretera libre.
            v_lead: Velocidad del vehículo líder (m/s). Si None → carretera libre.

        Returns:
            Aceleración en m/s².
        """
        p = self.params
        desired_v = v0 if v0 is not None else p.v0

        # Término de velocidad libre
        if desired_v > 0:
            free_term = (v / desired_v) ** p.delta
        else:
            free_term = 1.0

        # Carretera libre (sin líder)
        if s is None or v_lead is None:
            return p.a * (1.0 - free_term)

        # Car-following: distancia deseada s*
        s_star = self._desired_gap(v, v_lead)

        # Proteger contra gap <= 0
        effective_gap = max(s, 0.01)

        interaction_term = (s_star / effective_gap) ** 2

        return p.a * (1.0 - free_term - interaction_term)

    def _desired_gap(self, v: float, v_lead: float) -> float:
        """
        Calcula la distancia deseada s*(v, Δv).

        Args:
            v: Velocidad del vehículo ego.
            v_lead: Velocidad del vehículo líder.

        Returns:
            Distancia deseada s* en metros.
        """
        p = self.params
        delta_v = v - v_lead
        sqrt_ab = math.sqrt(p.a * p.b)

        dynamic_term = v * p.T + (v * delta_v) / (2.0 * sqrt_ab)

        return p.s0 + max(0.0, dynamic_term)

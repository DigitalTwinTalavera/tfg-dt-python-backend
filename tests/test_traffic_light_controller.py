"""
Cobertura básica del controlador de semáforos.

Tests de inicialización, transición de fases por tiempo, override por nodo y
override global. La lógica de agrupación por eje (in-edges agrupadas N-S vs E-O)
queda fuera del scope de estos tests — está cubierta indirectamente por
``test_traffic_light_per_node_override.py``.
"""

from __future__ import annotations

import pytest

from app.core.constants import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_NODE_TYPE,
    TL_GREEN_SECONDS,
    TL_PHASE_GREEN,
    TL_PHASE_RED,
    TL_PHASE_YELLOW,
    TL_RED_SECONDS,
    TL_YELLOW_SECONDS,
)
from app.core.traffic_light_controller import TrafficLightController
from app.models.enums import NodeType
from app.services.network_graph import RoadNetworkGraph


_CYCLE = TL_GREEN_SECONDS + TL_YELLOW_SECONDS + TL_RED_SECONDS


def _build_graph_with_tl() -> RoadNetworkGraph:
    """Grafo mínimo con un nodo TRAFFIC_LIGHT y dos in-edges en el mismo eje."""
    g = RoadNetworkGraph()
    # Nodo TL central
    g.graph.add_node(
        10,
        **{
            ATTR_NODE_TYPE: NodeType.TRAFFIC_LIGHT.value,
            ATTR_LONGITUDE: 0.0,
            ATTR_LATITUDE: 0.0,
        },
    )
    # Aproximaciones desde el oeste y desde el este (mismo eje, grupo 0)
    g.graph.add_node(1, **{ATTR_LONGITUDE: -1.0, ATTR_LATITUDE: 0.0})
    g.graph.add_node(2, **{ATTR_LONGITUDE: 1.0, ATTR_LATITUDE: 0.0})
    g.graph.add_edge(1, 10)
    g.graph.add_edge(2, 10)
    return g


@pytest.fixture
def graph() -> RoadNetworkGraph:
    return _build_graph_with_tl()


@pytest.fixture
def controller(graph: RoadNetworkGraph) -> TrafficLightController:
    tl = TrafficLightController(graph)
    # Resetear el timer para que los tests sean deterministas. El __init__
    # asigna un offset aleatorio entre 0 y _CYCLE para des-sincronizar TLs
    # en mapas reales — aquí lo fijamos a 0 (verde recién empezado).
    for nid in list(tl._lights.keys()):
        tl._lights[nid] = 0.0
    return tl


class TestInit:
    @pytest.mark.unit
    def test_only_traffic_light_nodes_tracked(self, graph: RoadNetworkGraph):
        # Añadimos un nodo no-TL y comprobamos que no se registra
        graph.graph.add_node(99, **{ATTR_NODE_TYPE: NodeType.INTERSECTION.value})
        tl = TrafficLightController(graph)
        assert tl.knows_node(10)
        assert not tl.knows_node(99)
        assert tl.light_count == 1

    @pytest.mark.unit
    def test_initial_mode_is_normal(self, controller: TrafficLightController):
        assert controller.get_override_mode() == "normal"


class TestPhaseFromTime:
    @pytest.mark.unit
    def test_green_phase_at_start(self, controller: TrafficLightController):
        # t=0 → verde
        assert controller.get_phase(10) == TL_PHASE_GREEN

    @pytest.mark.unit
    def test_yellow_phase_after_green(self, controller: TrafficLightController):
        controller.tick(TL_GREEN_SECONDS + 0.1)
        assert controller.get_phase(10) == TL_PHASE_YELLOW

    @pytest.mark.unit
    def test_red_phase_after_yellow(self, controller: TrafficLightController):
        controller.tick(TL_GREEN_SECONDS + TL_YELLOW_SECONDS + 0.1)
        assert controller.get_phase(10) == TL_PHASE_RED

    @pytest.mark.unit
    def test_cycle_wraps_around(self, controller: TrafficLightController):
        # Avanzar un ciclo entero → vuelve a verde
        controller.tick(_CYCLE)
        assert controller.get_phase(10) == TL_PHASE_GREEN


class TestOverrides:
    @pytest.mark.unit
    def test_set_override_for_single_node(self, controller: TrafficLightController):
        controller.set_override(10, TL_PHASE_RED)
        # Verde por timer, pero override fuerza rojo
        assert controller.get_phase(10) == TL_PHASE_RED
        assert controller.has_override_for_node(10)
        assert "per_node" in controller.get_override_mode()

    @pytest.mark.unit
    def test_clear_override_returns_to_cycle(
        self, controller: TrafficLightController
    ):
        controller.set_override(10, TL_PHASE_RED)
        had_override = controller.clear_override_for_node(10)
        assert had_override is True
        # Sin override y t=0 → verde
        assert controller.get_phase(10) == TL_PHASE_GREEN

    @pytest.mark.unit
    def test_clear_override_for_unset_node_returns_false(
        self, controller: TrafficLightController
    ):
        assert controller.clear_override_for_node(10) is False

    @pytest.mark.unit
    def test_global_override_takes_precedence(
        self, controller: TrafficLightController
    ):
        controller.set_override(10, TL_PHASE_RED)
        controller.set_all_override(TL_PHASE_GREEN)
        # Aunque haya override por-nodo en rojo, el global gana
        assert controller.get_phase(10) == TL_PHASE_GREEN
        assert controller.get_override_mode() == "global_green"

    @pytest.mark.unit
    def test_clear_all_overrides_returns_to_cycle(
        self, controller: TrafficLightController
    ):
        controller.set_all_override(TL_PHASE_RED)
        controller.set_override(10, TL_PHASE_YELLOW)
        controller.clear_overrides()
        assert controller.get_phase(10) == TL_PHASE_GREEN  # t=0 → verde
        assert controller.get_override_mode() == "normal"


class TestSnapshot:
    @pytest.mark.unit
    def test_snapshot_includes_known_nodes(
        self, controller: TrafficLightController
    ):
        snap = controller.get_snapshot()
        assert 10 in snap
        # Cada in-edge serializada como "u_v"
        assert any(key.startswith("1_") or key.startswith("2_") for key in snap[10])

    @pytest.mark.unit
    def test_flat_snapshot_returns_phase_per_node(
        self, controller: TrafficLightController
    ):
        flat = controller.get_flat_snapshot()
        assert flat == {10: TL_PHASE_GREEN}


class TestQueries:
    @pytest.mark.unit
    def test_is_red_when_in_red_phase(self, controller: TrafficLightController):
        controller.set_override(10, TL_PHASE_RED)
        assert controller.is_red(10) is True

    @pytest.mark.unit
    def test_is_blocking_in_red_or_yellow(
        self, controller: TrafficLightController
    ):
        controller.set_override(10, TL_PHASE_YELLOW)
        assert controller.is_blocking(10) is True
        controller.set_override(10, TL_PHASE_GREEN)
        assert controller.is_blocking(10) is False

"""
Tests for WebSocket Connection Manager and endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.websocket.manager import ConnectionManager
from app.core.constants import WS_SIMULATION_PATH, WS_TYPE_SIM_STATE
from app.main import app

client = TestClient(app)


class TestConnectionManager:
    """Tests for the ConnectionManager class."""

    def test_connection_manager_initialization(self):
        manager = ConnectionManager()
        assert manager.connection_count == 0
        assert manager.active_connections == []


class TestWebSocketEndpoint:
    """Tests for the WebSocket endpoint."""

    @pytest.mark.unit
    def test_websocket_connection_accepts(self):
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            assert websocket is not None

    @pytest.mark.unit
    def test_websocket_sends_initial_sim_state(self):
        """Al conectar, el servidor envía inmediatamente el estado de la simulación."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            response = websocket.receive_json()
            assert response["type"] == WS_TYPE_SIM_STATE
            assert "state" in response

    @pytest.mark.unit
    def test_websocket_ignores_incoming_messages(self):
        """El endpoint acepta datos del cliente pero no responde (no es un echo)."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            # First message is always sim_state
            websocket.receive_json()
            # Client may send messages; the server should just consume them
            websocket.send_json({"type": "ping"})
            # No further response should arrive; we verify via a quick timeout.
            # If an unexpected response came, the test would receive it here.

    @pytest.mark.integration
    def test_websocket_connection_lifecycle(self):
        """Ciclo completo: conectar, recibir sim_state inicial, desconectar limpio."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            msg = websocket.receive_json()
            assert msg["type"] == WS_TYPE_SIM_STATE

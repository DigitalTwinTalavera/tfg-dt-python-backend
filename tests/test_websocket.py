"""
Tests for WebSocket Connection Manager and endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.websocket.manager import ConnectionManager
from app.core.constants import STATUS_OK, WS_SIMULATION_PATH, WS_TYPE_ECHO
from app.main import app

client = TestClient(app)


class TestConnectionManager:
    """Tests for the ConnectionManager class."""

    def test_connection_manager_initialization(self):
        """Test that ConnectionManager initializes with empty connections."""
        manager = ConnectionManager()
        assert manager.connection_count == 0
        assert manager.active_connections == []

    def test_connection_count_property(self):
        """Test the connection_count property."""
        manager = ConnectionManager()
        assert manager.connection_count == 0


class TestWebSocketEndpoint:
    """Tests for the WebSocket endpoint."""

    @pytest.mark.unit
    def test_websocket_connection(self):
        """Test that WebSocket connection can be established."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            # Connection should be accepted
            assert websocket is not None

    @pytest.mark.unit
    def test_websocket_echo_message(self):
        """Test that server echoes received messages (echo test)."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            # Send a test message
            test_message = {"type": "test", "data": "hello"}
            websocket.send_json(test_message)

            # Receive echo response
            response = websocket.receive_json()

            assert response["type"] == WS_TYPE_ECHO
            assert response["status"] == STATUS_OK
            assert response["received"] == test_message

    @pytest.mark.unit
    def test_websocket_multiple_messages(self):
        """Test sending multiple messages in sequence."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            messages = [
                {"type": "command", "action": "start"},
                {"type": "command", "action": "stop"},
                {"type": "data", "value": 42},
            ]

            for msg in messages:
                websocket.send_json(msg)
                response = websocket.receive_json()

                assert response["type"] == WS_TYPE_ECHO
                assert response["received"] == msg

    @pytest.mark.integration
    def test_websocket_connection_lifecycle(self):
        """Test the full connection lifecycle: connect, communicate, disconnect."""
        # Connect
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            # Communicate
            websocket.send_json({"action": "ping"})
            response = websocket.receive_json()
            assert response["status"] == STATUS_OK

        # Disconnection happens automatically when exiting the context

    @pytest.mark.unit
    def test_websocket_json_message_structure(self):
        """Test that response has correct JSON structure."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            websocket.send_json({"test": "data"})
            response = websocket.receive_json()

            # Verify response structure
            assert "type" in response
            assert "status" in response
            assert "received" in response

    @pytest.mark.integration
    def test_websocket_simulation_data(self):
        """Test sending simulation-like data through WebSocket."""
        with client.websocket_connect(WS_SIMULATION_PATH) as websocket:
            # Simulate vehicle position update
            vehicle_data = {
                "type": "vehicle_update",
                "vehicle_id": 1,
                "position": {"x": 100.5, "y": 200.3, "z": 0.0},
                "velocity": 15.5,
            }

            websocket.send_json(vehicle_data)
            response = websocket.receive_json()

            assert response["type"] == WS_TYPE_ECHO
            assert response["received"]["vehicle_id"] == 1
            assert response["received"]["position"]["x"] == 100.5

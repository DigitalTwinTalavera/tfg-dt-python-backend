"""
WebSocket Connection Manager.
Handles incoming connections, disconnections, and message broadcasting
for real-time communication with Godot clients.
"""

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections for the simulation.

    This class handles:
    - Client connections and disconnections
    - Broadcasting messages to all connected clients
    - Sending messages to specific clients

    Attributes:
        active_connections: List of currently connected WebSocket clients
    """

    def __init__(self) -> None:
        """Initialize the connection manager with an empty connections list."""
        self._active_connections: list[WebSocket] = []

    @property
    def active_connections(self) -> list[WebSocket]:
        """Get the list of active connections."""
        return self._active_connections

    @property
    def connection_count(self) -> int:
        """Get the number of active connections."""
        return len(self._active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection and add it to active connections.

        Args:
            websocket: The WebSocket connection to accept
        """
        await websocket.accept()
        self._active_connections.append(websocket)
        logger.info(
            f"Client connected. Total connections: {self.connection_count}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket connection from active connections.

        Args:
            websocket: The WebSocket connection to remove
        """
        if websocket in self._active_connections:
            self._active_connections.remove(websocket)
            logger.info(
                f"Client disconnected. Total connections: {self.connection_count}"
            )

    async def send_personal_message(
        self, message: dict[str, Any], websocket: WebSocket
    ) -> None:
        """
        Send a JSON message to a specific client.

        Args:
            message: The message dictionary to send
            websocket: The target WebSocket connection
        """
        await websocket.send_json(message)

    async def _broadcast_to_all(
        self,
        send_func_name: str,
        message: dict[str, Any] | str,
    ) -> None:
        """
        Internal method to broadcast a message to all connected clients.

        Args:
            send_func_name: Name of the WebSocket send method ('send_json' or 'send_text')
            message: The message to broadcast (dict for JSON, str for text)
        """
        disconnected: list[WebSocket] = []

        for connection in self._active_connections:
            try:
                send_func = getattr(connection, send_func_name)
                await send_func(message)
            except Exception as e:
                logger.warning("Failed to send message to client: %s", e)
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """
        Broadcast a JSON message to all connected clients.

        Args:
            message: The message dictionary to broadcast
        """
        await self._broadcast_to_all("send_json", message)

    async def broadcast_text(self, message: str) -> None:
        """
        Broadcast a text message to all connected clients.

        Args:
            message: The text message to broadcast
        """
        await self._broadcast_to_all("send_text", message)


# Singleton instance for the application
connection_manager = ConnectionManager()

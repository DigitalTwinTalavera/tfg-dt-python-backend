"""
WebSocket routes for real-time simulation communication.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.websocket import ConnectionManager
from app.api.websocket.manager import connection_manager
from app.core.constants import (
    STATUS_OK,
    WS_SIMULATION_PATH,
    WS_TYPE_ECHO,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket(WS_SIMULATION_PATH)
async def websocket_simulation(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for simulation real-time updates.

    This endpoint handles:
    - Client connections from Godot
    - Receiving messages from clients
    - Broadcasting simulation state updates

    The connection remains open until the client disconnects.
    All received messages are logged to the console (echo test).
    """
    await connection_manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_json()
            logger.info(f"Received message: {data}")

            response = {
                "type": WS_TYPE_ECHO,
                "received": data,
                "status": STATUS_OK,
            }
            await connection_manager.send_personal_message(response, websocket)

    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
        logger.info(f"Client disconnected from {WS_SIMULATION_PATH}")


def get_connection_manager() -> ConnectionManager:
    """
    Get the singleton ConnectionManager instance.

    Returns:
        The global ConnectionManager instance
    """
    return connection_manager

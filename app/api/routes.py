"""
WebSocket routes for real-time simulation communication.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.websocket import ConnectionManager
from app.api.websocket.manager import connection_manager
from app.api.websocket.messages import build_sim_state_message
from app.core.constants import WS_SIMULATION_PATH
from app.core.simulation_engine import simulation_engine

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket(WS_SIMULATION_PATH)
async def websocket_simulation(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for simulation real-time updates.

    Registers the client in the broadcast list, pushes the current simulation
    state immediately, and keeps the connection open until the client
    disconnects. Incoming messages from the client are currently ignored —
    all client→server control happens via the REST API.
    """
    await connection_manager.connect(websocket)

    await connection_manager.send_personal_message(
        build_sim_state_message(simulation_engine.state.value), websocket
    )

    try:
        while True:
            await websocket.receive_text()
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

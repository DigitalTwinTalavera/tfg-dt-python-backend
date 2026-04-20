"""
Broadcaster de estado de simulación en tiempo real.

Recopila el estado de todos los vehículos activos y lo envía
a los clientes WebSocket conectados en cada tick.
Soporta delta updates (solo campos que cambiaron).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.api.websocket.manager import ConnectionManager
from app.api.websocket.messages import (
    build_sim_state_message,
    build_tick_message,
    build_traffic_lights_message,
    build_vehicle_collision_message,
    build_vehicle_finished_message,
    build_vehicle_spawned_message,
    build_vehicle_state,
    build_vehicles_batch_spawned_message,
)
from app.services.vehicle_spawner import SimVehicle, VehicleSpawner

logger = logging.getLogger(__name__)


class SimulationBroadcaster:
    """
    Recopila y emite el estado de la simulación a clientes WebSocket.

    Mantiene un snapshot del tick anterior para calcular delta updates
    (solo se envían los vehículos cuyos campos han cambiado).
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
        vehicle_spawner: VehicleSpawner,
    ) -> None:
        self._manager = connection_manager
        self._spawner = vehicle_spawner
        self._last_snapshot: dict[str, dict[str, Any]] = {}
        self._broadcast_count: int = 0
        self._total_broadcast_time_ms: float = 0.0

    @property
    def broadcast_count(self) -> int:
        return self._broadcast_count

    @property
    def avg_broadcast_time_ms(self) -> float:
        if self._broadcast_count == 0:
            return 0.0
        return self._total_broadcast_time_ms / self._broadcast_count

    def reset(self) -> None:
        """Reinicia el estado del broadcaster (al iniciar simulación)."""
        self._last_snapshot.clear()
        self._broadcast_count = 0
        self._total_broadcast_time_ms = 0.0

    # Número máximo de vehículos por mensaje tick.
    # Cada vehículo ocupa ~130 bytes de JSON; con 500 vehículos el mensaje
    # ronda los 65 KB, por debajo del límite de 4 MB configurado en Godot.
    # Dividir en chunks también reduce la latencia de renderizado: el cliente
    # puede procesar el primer chunk antes de que llegue el siguiente.
    _TICK_CHUNK_SIZE: int = 500

    async def broadcast_tick(self, tick: int, sim_time: float) -> None:
        """
        Emite el estado de todos los vehículos activos a los clientes.

        Solo envía vehículos cuyos campos hayan cambiado desde el último tick
        (delta update). Si no hay clientes conectados, no hace nada.

        Los vehículos se envían en chunks de _TICK_CHUNK_SIZE para evitar
        mensajes WebSocket demasiado grandes (código 1009).

        Args:
            tick: Número de tick actual.
            sim_time: Tiempo de simulación acumulado (s).
        """
        if self._manager.connection_count == 0:
            return

        t0 = time.monotonic()

        vehicles = self._spawner.get_all_vehicles()
        vehicle_states = self._build_delta_states(vehicles)

        if vehicle_states or tick == 0:
            chunk_size = self._TICK_CHUNK_SIZE
            if len(vehicle_states) <= chunk_size:
                await self._manager.broadcast(
                    build_tick_message(tick=tick, sim_time=sim_time, vehicles=vehicle_states)
                )
            else:
                # Enviar en múltiples mensajes para no superar el límite de tamaño
                for i in range(0, len(vehicle_states), chunk_size):
                    chunk = vehicle_states[i : i + chunk_size]
                    await self._manager.broadcast(
                        build_tick_message(tick=tick, sim_time=sim_time, vehicles=chunk)
                    )

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._broadcast_count += 1
        self._total_broadcast_time_ms += elapsed_ms

        if self._broadcast_count % 100 == 0:
            logger.debug(
                "Broadcast stats: count=%d, avg=%.2fms, last=%.2fms",
                self._broadcast_count,
                self.avg_broadcast_time_ms,
                elapsed_ms,
            )

    def _build_delta_states(
        self, vehicles: list[SimVehicle]
    ) -> list[dict[str, Any]]:
        """
        Construye la lista de estados de vehículos con delta updates.

        Un vehículo se incluye si:
        - Es nuevo (no estaba en el snapshot anterior).
        - Alguno de sus campos ha cambiado.

        Returns:
            Lista de dicts con el estado de cada vehículo que cambió.
        """
        new_snapshot: dict[str, dict[str, Any]] = {}
        changed: list[dict[str, Any]] = []

        for v in vehicles:
            vtype = getattr(v, "vtype", None)
            state = build_vehicle_state(
                vehicle_id=v.id,
                longitude=v.longitude,
                latitude=v.latitude,
                velocity=getattr(v, "velocity", 0.0),
                acceleration=getattr(v, "acceleration", 0.0),
                heading=getattr(v, "heading", 0.0),
                status=v.status.value,
                current_edge_index=v.current_edge_index,
                progress_on_edge=getattr(v, "progress_on_edge", 0.0),
                lane=getattr(v, "lane", 0),
                vtype=vtype.value if vtype is not None else "car",
            )
            new_snapshot[v.id] = state

            prev = self._last_snapshot.get(v.id)
            if prev is None or prev != state:
                changed.append(state)

        self._last_snapshot = new_snapshot
        return changed

    async def broadcast_sim_state(self, state: str) -> None:
        """Emite un cambio de estado de la simulación."""
        if self._manager.connection_count == 0:
            return
        message = build_sim_state_message(state)
        await self._manager.broadcast(message)

    async def broadcast_vehicle_spawned(self, vehicle: SimVehicle) -> None:
        """Emite notificación de vehículo generado."""
        if self._manager.connection_count == 0:
            return
        vtype = getattr(vehicle, "vtype", None)
        message = build_vehicle_spawned_message(
            vehicle_id=vehicle.id,
            start_node_id=vehicle.start_node_id,
            end_node_id=vehicle.end_node_id,
            route_edges=vehicle.route.edge_ids,
            lane=getattr(vehicle, "lane", 0),
            vtype=vtype.value if vtype is not None else "car",
        )
        await self._manager.broadcast(message)

    async def broadcast_vehicle_finished(self, vehicle_id: str) -> None:
        """Emite notificación de vehículo que completó su ruta."""
        if self._manager.connection_count == 0:
            return
        message = build_vehicle_finished_message(vehicle_id)
        await self._manager.broadcast(message)

    async def broadcast_traffic_lights(
        self, snapshot: dict[int, dict[str, str]]
    ) -> None:
        """
        Emite el estado actual de todos los semáforos por arista de aproximación.

        Args:
            snapshot: {node_id: {"u_v": phase}} de TrafficLightController.get_snapshot().
        """
        if self._manager.connection_count == 0:
            return
        await self._manager.broadcast(build_traffic_lights_message(snapshot))

    async def broadcast_vehicles_batch_spawned(self, vehicles: list[SimVehicle]) -> None:
        """
        Emite un único mensaje con todos los vehículos creados en un spawn masivo.

        El cliente (Godot) lo usa para registrar y renderizar el batch completo
        de golpe, sin esperar a los mensajes de tick individuales. Esto elimina
        la latencia de hasta 100 ms que existía cuando los vehículos se
        descubrían uno a uno a través del broadcaster de ticks.
        """
        if self._manager.connection_count == 0 or not vehicles:
            return
        message = build_vehicles_batch_spawned_message(vehicles)
        await self._manager.broadcast(message)
        logger.debug(
            "Batch WS enviado: %d vehículos (vehicles_batch_spawned)",
            len(vehicles),
        )

    async def broadcast_vehicle_collision(
        self,
        vehicle_id_1: str,
        vehicle_id_2: str,
        node_from: int,
        node_to: int,
    ) -> None:
        """Emite notificación de colisión entre dos vehículos."""
        if self._manager.connection_count == 0:
            return
        message = build_vehicle_collision_message(
            vehicle_id_1, vehicle_id_2, node_from, node_to
        )
        await self._manager.broadcast(message)

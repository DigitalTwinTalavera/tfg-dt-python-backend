"""
Broadcaster de estado de simulación en tiempo real.

Recopila el estado de todos los vehículos activos y lo envía
a los clientes WebSocket conectados en cada tick.
Soporta delta updates (solo campos que cambiaron).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.api.websocket.manager import ConnectionManager
from app.api.websocket.messages import (
    build_incident_message,
    build_sim_state_message,
    build_tick_binary_from_vehicles,
    build_traffic_lights_message,
    build_vehicle_collision_message,
    build_vehicle_finished_message,
    build_vehicle_spawned_message,
    build_vehicle_state,
    build_vehicles_batch_spawned_message,
    build_zone_message,
)
from app.core.constants import BROADCAST_CHUNK_SIZE
from app.core.instrumentation import registry
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
        self._broadcast_count: int = 0
        self._total_broadcast_time_ms: float = 0.0
        # Tarea de envío en vuelo del tick anterior. Permite solapar el envío
        # WS con el cómputo del siguiente tick: al inicio de cada
        # `broadcast_tick` esperamos a que termine la anterior (suele ser
        # cero porque el envío es ms y el tick son 100 ms) y luego lanzamos
        # la nueva como fire-and-forget. Si un cliente lento bloquea el send,
        # solo retrasa UN tick (el siguiente await la espera).
        self._pending_send: asyncio.Task[None] | None = None

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
        self._broadcast_count = 0
        self._total_broadcast_time_ms = 0.0
        # Cualquier envío en vuelo del estado anterior se descarta — los
        # clientes recibirán el primer tick de la nueva simulación.
        if self._pending_send is not None and not self._pending_send.done():
            self._pending_send.cancel()
        self._pending_send = None

    # Dividir en chunks reduce la latencia de renderizado: el cliente puede
    # procesar el primer chunk antes de que llegue el siguiente. Tamaño en
    # `BROADCAST_CHUNK_SIZE` (constants.py).
    _TICK_CHUNK_SIZE: int = BROADCAST_CHUNK_SIZE

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
        registry.gauge("ws.clients", self._manager.connection_count)
        if self._manager.connection_count == 0:
            return

        t0 = time.perf_counter_ns()

        # Drop-frame: no esperamos al pending_send anterior. Si todavía vuela,
        # lo cancelamos y descartamos el frame (mejor que retrasar el tick
        # actual). A 5 Hz, perder un frame puntual no daña la interpolación
        # del cliente, pero retrasar el bucle de física sí causa "tirones".
        if self._pending_send is not None and not self._pending_send.done():
            self._pending_send.cancel()
            registry.inc("ws.frames_dropped")
        self._pending_send = None

        vehicles = self._spawner.get_all_vehicles()

        # Serializamos DIRECTAMENTE desde SimVehicle al wire binario sin pasar
        # por dicts intermedios. Serial: probamos paralelizar con asyncio.to_thread
        # pero el overhead de task creation × 8 chunks (~120 ms) supera la
        # ganancia del paralelismo CPU (~80 ms secuencial). Revertido.
        chunk_size = self._TICK_CHUNK_SIZE
        payloads: list[bytes] = []
        if vehicles or tick == 0:
            n = len(vehicles)
            if n <= chunk_size:
                payloads.append(build_tick_binary_from_vehicles(
                    tick=tick,
                    sim_time=sim_time,
                    vehicles=vehicles,
                    chunk_index=0,
                    chunk_total=1,
                ))
            else:
                total = (n + chunk_size - 1) // chunk_size
                for idx, i in enumerate(range(0, n, chunk_size)):
                    chunk = vehicles[i : i + chunk_size]
                    payloads.append(build_tick_binary_from_vehicles(
                        tick=tick,
                        sim_time=sim_time,
                        vehicles=chunk,
                        chunk_index=idx,
                        chunk_total=total,
                    ))

        if payloads:
            self._pending_send = asyncio.create_task(self._send_payloads(payloads))

        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        self._broadcast_count += 1
        self._total_broadcast_time_ms += elapsed_ms

        # Métricas WS: tamaño del payload por tick + bytes acumulados (counter
        # monotónico, deriva bytes/s al dividir por uptime). Chunks/tick
        # también histograma para detectar saltos al pasar de 1 a múltiples.
        bytes_this_tick = sum(len(p) for p in payloads)
        registry.record("ws.bytes_per_tick", bytes_this_tick)
        registry.inc("ws.bytes", bytes_this_tick)
        registry.record("ws.chunks_per_tick", len(payloads))
        registry.record("ws.serialize_ms", elapsed_ms)

        if self._broadcast_count % 100 == 0:
            logger.debug(
                "Broadcast stats: count=%d, avg=%.2fms, last=%.2fms",
                self._broadcast_count,
                self.avg_broadcast_time_ms,
                elapsed_ms,
            )

    async def _send_payloads(self, payloads: list[bytes]) -> None:
        """
        Envía secuencialmente todos los chunks de un tick. Se ejecuta como
        tarea fire-and-forget para que el bucle de simulación pueda iniciar
        el siguiente tick sin esperar a que el WS complete el envío.
        """
        t0 = time.perf_counter_ns()
        for payload in payloads:
            await self._manager.broadcast_bytes(payload)
        registry.record("ws.send_ms", (time.perf_counter_ns() - t0) / 1_000_000.0)

    def _build_all_states(
        self, vehicles: list[SimVehicle]
    ) -> list[dict[str, Any]]:
        """
        Construye la lista de estados de TODOS los vehículos activos.

        Antes había una optimización de delta (`_build_delta_states`) que
        comparaba con el snapshot anterior y omitía vehículos sin cambios,
        pero a 10 Hz casi todos cambian (lon/lat/v/a se mueven cada tick),
        de modo que el delta no ahorraba bytes y sí costaba un dict-deep
        compare por vehículo en O(N) cada tick. Eliminada para reducir CPU.
        """
        return [
            build_vehicle_state(
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
                vtype=v.vtype.value if getattr(v, "vtype", None) is not None else "car",
            )
            for v in vehicles
        ]

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

    async def broadcast_incident(
        self, *, action: str, payload: dict[str, Any]
    ) -> None:
        """Emite alta, actualización o baja de un incidente.

        Args:
            action: "created" | "updated" | "cleared".
            payload: Dict público del incidente (IncidentManager.to_public_dict).
        """
        if self._manager.connection_count == 0:
            return
        await self._manager.broadcast(build_incident_message(action, payload))

    async def broadcast_zone(
        self, *, action: str, payload: dict[str, Any]
    ) -> None:
        """Emite alta, actualización o baja de una zona de control.

        Args:
            action: "created" | "updated" | "cleared".
            payload: Dict público de la zona (ZoneManager.to_public_dict).
        """
        if self._manager.connection_count == 0:
            return
        await self._manager.broadcast(build_zone_message(action, payload))

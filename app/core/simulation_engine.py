"""
Motor de simulación con bucle de timestep fijo y máquina de estados.
Ejecuta como tarea asíncrona en background dentro del event loop de FastAPI.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING

from app.core.exceptions import (
    SimulationAlreadyRunningError,
    SimulationNotPausedError,
    SimulationNotRunningError,
)

if TYPE_CHECKING:
    from app.core.broadcaster import SimulationBroadcaster
    from app.core.simulation_config import SimulationConfig
    from app.services.vehicle_spawner import VehicleSpawner

logger = logging.getLogger(__name__)


class SimulationState(str, Enum):
    """Estados posibles de la simulación."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class SimulationEngine:
    """
    Motor de simulación con bucle de timestep fijo.

    Gestiona el ciclo de vida de la simulación mediante una máquina de estados
    (IDLE -> RUNNING <-> PAUSED -> STOPPED) y ejecuta un bucle asíncrono
    en background con intervalo configurable.

    Soporta inyección de:
      - SimulationConfig  (hot-update sin reiniciar)
      - VehicleSpawner    (auto-spawn en cada tick)
      - SimulationBroadcaster (broadcast a clientes WS)
    """

    def __init__(self, tick_interval_ms: float = 100.0) -> None:
        self._state: SimulationState = SimulationState.IDLE
        self._tick_interval_ms: float = tick_interval_ms
        self._tick_count: int = 0
        self._simulation_time: float = 0.0
        self._start_wall_time: float = 0.0
        self._task: asyncio.Task | None = None
        self._vehicles_active: int = 0

        self._config: SimulationConfig | None = None
        self._spawner: VehicleSpawner | None = None
        self._broadcaster: SimulationBroadcaster | None = None

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def state(self) -> SimulationState:
        return self._state

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def simulation_time(self) -> float:
        return self._simulation_time

    @property
    def tick_rate(self) -> float:
        return 1000.0 / self._tick_interval_ms

    @property
    def tick_interval_ms(self) -> float:
        return self._tick_interval_ms

    @property
    def vehicles_active(self) -> int:
        return self._vehicles_active

    @property
    def uptime_seconds(self) -> float:
        if self._start_wall_time == 0.0:
            return 0.0
        return time.monotonic() - self._start_wall_time

    @property
    def broadcaster(self) -> "SimulationBroadcaster | None":
        return self._broadcaster

    # -------------------------------------------------------------------------
    # Dependency injection
    # -------------------------------------------------------------------------

    def set_broadcaster(self, broadcaster: "SimulationBroadcaster") -> None:
        """Inyecta el broadcaster para emitir estado en cada tick."""
        self._broadcaster = broadcaster

    def set_spawner(self, spawner: "VehicleSpawner") -> None:
        """Inyecta el spawner para el auto-spawn automático en cada tick."""
        self._spawner = spawner

    def set_config(self, config: "SimulationConfig") -> None:
        """
        Inyecta o actualiza la configuración de simulación.

        Hot-update: los cambios de tick_rate, auto_spawn, spawn_rate y
        max_vehicles toman efecto en el próximo tick sin reiniciar.
        """
        self._config = config
        self._tick_interval_ms = config.tick_interval_ms
        if self._spawner is not None:
            self._spawner.max_vehicles = config.max_vehicles

    def get_config(self) -> "SimulationConfig | None":
        """Devuelve la configuración activa, o None si no se ha inyectado."""
        return self._config

    # -------------------------------------------------------------------------
    # State machine
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Inicia la simulación. Solo válido desde IDLE o STOPPED."""
        if self._state == SimulationState.RUNNING:
            raise SimulationAlreadyRunningError()
        if self._state == SimulationState.PAUSED:
            raise SimulationAlreadyRunningError()

        self._tick_count = 0
        self._simulation_time = 0.0
        self._start_wall_time = time.monotonic()
        self._state = SimulationState.RUNNING

        if self._broadcaster is not None:
            self._broadcaster.reset()
            await self._broadcaster.broadcast_sim_state(SimulationState.RUNNING.value)

        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Simulación iniciada (tick_rate=%.1f Hz, interval=%.1f ms)",
            self.tick_rate,
            self._tick_interval_ms,
        )

    async def stop(self) -> None:
        """Detiene la simulación. Válido desde RUNNING o PAUSED."""
        if self._state not in (SimulationState.RUNNING, SimulationState.PAUSED):
            raise SimulationNotRunningError()

        self._state = SimulationState.STOPPED
        await self._cancel_task()

        if self._broadcaster is not None:
            await self._broadcaster.broadcast_sim_state(SimulationState.STOPPED.value)

        logger.info(
            "Simulación detenida (ticks=%d, tiempo=%.1fs)",
            self._tick_count,
            self._simulation_time,
        )

    async def pause(self) -> None:
        """Pausa la simulación. Solo válido desde RUNNING."""
        if self._state != SimulationState.RUNNING:
            raise SimulationNotRunningError()

        self._state = SimulationState.PAUSED
        await self._cancel_task()

        if self._broadcaster is not None:
            await self._broadcaster.broadcast_sim_state(SimulationState.PAUSED.value)

        logger.info("Simulación pausada en tick %d", self._tick_count)

    async def resume(self) -> None:
        """Reanuda la simulación. Solo válido desde PAUSED."""
        if self._state != SimulationState.PAUSED:
            raise SimulationNotPausedError()

        self._state = SimulationState.RUNNING

        if self._broadcaster is not None:
            await self._broadcaster.broadcast_sim_state(SimulationState.RUNNING.value)

        self._task = asyncio.create_task(self._run_loop())
        logger.info("Simulación reanudada desde tick %d", self._tick_count)

    def get_status(self) -> dict:
        """Devuelve el estado actual de la simulación."""
        status: dict = {
            "state": self._state.value,
            "tick_count": self._tick_count,
            "simulation_time_seconds": round(self._simulation_time, 3),
            "vehicles_active": self._vehicles_active,
            "uptime_seconds": round(self.uptime_seconds, 3),
        }
        if self._broadcaster is not None:
            status["broadcast_count"] = self._broadcaster.broadcast_count
            status["avg_broadcast_ms"] = round(self._broadcaster.avg_broadcast_time_ms, 2)
        return status

    async def shutdown(self) -> None:
        """Apagado graceful: detiene la simulación si está activa."""
        if self._state in (SimulationState.RUNNING, SimulationState.PAUSED):
            self._state = SimulationState.STOPPED
            await self._cancel_task()
            logger.info("Simulación detenida por shutdown")

    # -------------------------------------------------------------------------
    # Internal loop
    # -------------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Bucle principal de simulación con timestep fijo.

        Recomputa interval_s en cada iteración para que los hot-updates
        de tick_rate se apliquen sin reiniciar el loop.
        """
        try:
            while self._state == SimulationState.RUNNING:
                tick_start = time.monotonic()
                interval_s = self._tick_interval_ms / 1000.0

                await self._tick(interval_s)

                self._tick_count += 1
                self._simulation_time += interval_s

                elapsed = time.monotonic() - tick_start
                sleep_time = max(0.0, interval_s - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            pass

    async def _tick(self, dt: float) -> None:
        """
        Ejecuta un tick de simulación.

        Orden de operaciones:
          1. Auto-spawn (si procede según config)
          2. Broadcast del estado a clientes WS

        Args:
            dt: Delta time en segundos para este tick.
        """
        # 1. Auto-spawn
        if (
            self._config is not None
            and self._config.auto_spawn
            and self._config.spawn_rate > 0
            and self._spawner is not None
        ):
            ticks_between = self._config.ticks_between_spawns
            if self._tick_count % ticks_between == 0:
                try:
                    self._spawner.spawn(count=1)
                except ValueError:
                    pass  # sin nodos de entrada/salida -> ignorar silenciosamente

        # 2. Broadcast
        if self._broadcaster is not None:
            await self._broadcaster.broadcast_tick(
                tick=self._tick_count,
                sim_time=self._simulation_time,
            )

    async def _cancel_task(self) -> None:
        """Cancela la tarea de background si existe."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


# Instancia singleton a nivel de aplicación
simulation_engine = SimulationEngine()

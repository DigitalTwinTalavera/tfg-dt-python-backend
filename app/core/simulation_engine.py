"""
Motor de simulación con bucle de timestep fijo y máquina de estados.
Ejecuta como tarea asíncrona en background dentro del event loop de FastAPI.
"""

import asyncio
import logging
import time
from enum import Enum

from app.core.exceptions import (
    SimulationAlreadyRunningError,
    SimulationNotPausedError,
    SimulationNotRunningError,
)

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
    """

    def __init__(self, tick_interval_ms: float = 100.0) -> None:
        self._state: SimulationState = SimulationState.IDLE
        self._tick_interval_ms: float = tick_interval_ms
        self._tick_count: int = 0
        self._simulation_time: float = 0.0
        self._start_wall_time: float = 0.0
        self._task: asyncio.Task | None = None
        self._vehicles_active: int = 0

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
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Simulación iniciada (tick_rate=%.1f Hz, interval=%d ms)",
            self.tick_rate,
            self._tick_interval_ms,
        )

    async def stop(self) -> None:
        """Detiene la simulación. Válido desde RUNNING o PAUSED."""
        if self._state not in (SimulationState.RUNNING, SimulationState.PAUSED):
            raise SimulationNotRunningError()

        self._state = SimulationState.STOPPED
        await self._cancel_task()
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
        logger.info("Simulación pausada en tick %d", self._tick_count)

    async def resume(self) -> None:
        """Reanuda la simulación. Solo válido desde PAUSED."""
        if self._state != SimulationState.PAUSED:
            raise SimulationNotPausedError()

        self._state = SimulationState.RUNNING
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Simulación reanudada desde tick %d", self._tick_count)

    def get_status(self) -> dict:
        """Devuelve el estado actual de la simulación."""
        return {
            "state": self._state.value,
            "tick_count": self._tick_count,
            "simulation_time_seconds": round(self._simulation_time, 3),
            "vehicles_active": self._vehicles_active,
            "uptime_seconds": round(self.uptime_seconds, 3),
        }

    async def shutdown(self) -> None:
        """Apagado graceful: detiene la simulación si está activa."""
        if self._state in (SimulationState.RUNNING, SimulationState.PAUSED):
            self._state = SimulationState.STOPPED
            await self._cancel_task()
            logger.info("Simulación detenida por shutdown")

    async def _run_loop(self) -> None:
        """Bucle principal de simulación con timestep fijo."""
        interval_s = self._tick_interval_ms / 1000.0

        try:
            while self._state == SimulationState.RUNNING:
                tick_start = time.monotonic()

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

        Este método será extendido en sprints futuros para actualizar
        posiciones de vehículos, física, etc.

        Args:
            dt: Delta time en segundos para este tick.
        """
        pass

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

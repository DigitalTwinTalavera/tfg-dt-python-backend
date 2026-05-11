"""
Motor de simulación con bucle de timestep fijo y máquina de estados.
Ejecuta como tarea asíncrona en background dentro del event loop de FastAPI.
"""

import asyncio
import json
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING

from app.core.constants import (
    ATTR_IS_ROUNDABOUT,
    ATTR_ROUNDABOUT_ID,
    DYNAMIC_WEIGHT_CHANGE_THRESHOLD,
    DYNAMIC_WEIGHT_MAX_MULT,
    DYNAMIC_WEIGHTS_TICK_INTERVAL,
    ROUNDABOUT_SATURATION_VEH_PER_100M,
)
from app.core.exceptions import (
    SimulationAlreadyRunningError,
    SimulationNotPausedError,
    SimulationNotRunningError,
)
from app.core.instrumentation import SplitTimer, registry
from app.models.enums import VehicleStatus

if TYPE_CHECKING:
    from app.core.broadcaster import SimulationBroadcaster
    from app.core.simulation_config import SimulationConfig
    from app.core.traffic_light_controller import TrafficLightController
    from app.services.incident_manager import IncidentManager
    from app.services.vehicle_spawner import VehicleSpawner
    from app.services.zone_manager import ZoneManager

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

    def __init__(self, tick_interval_ms: float = 200.0) -> None:
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
        self._tl_controller: "TrafficLightController | None" = None
        self._incident_manager: "IncidentManager | None" = None
        self._zone_manager: "ZoneManager | None" = None

        # Decoupling del broadcast: el tick arranca el broadcast del estado
        # del tick anterior y espera a que termine el actual solo si no
        # está ya completado. Así física (tick N+1) y broadcast (tick N)
        # corren en paralelo dentro del event loop de asyncio.
        self._prev_broadcast_task: asyncio.Task | None = None

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

    def get_tl_controller(self) -> "TrafficLightController | None":
        """Devuelve el controlador de semáforos activo, o None si no se ha inicializado."""
        return self._tl_controller

    def set_incident_manager(self, manager: "IncidentManager") -> None:
        """Inyecta el gestor de incidentes."""
        self._incident_manager = manager

    def get_incident_manager(self) -> "IncidentManager | None":
        return self._incident_manager

    def set_zone_manager(self, manager: "ZoneManager") -> None:
        """Inyecta el gestor de zonas (ZBE / restringidas / peatonales)."""
        self._zone_manager = manager

    def get_zone_manager(self) -> "ZoneManager | None":
        return self._zone_manager

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
        self._tl_controller = None  # se re-inicializa en el primer tick
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
        """Bucle principal de simulación con scheduling por deadline.

        Recomputa interval_s en cada iteración para que los hot-updates
        de tick_rate se apliquen sin reiniciar el loop.

        Scheduling: calcula una deadline fija (``next_deadline``) para cada
        tick. Si un tick se pasa del presupuesto, el siguiente intenta
        recuperar el tiempo perdido. Si el retraso supera 2 intervalos,
        re-sincroniza en lugar de encadenar ticks atrasados (evita el
        "catch-up storm" que produce saltos visibles en el cliente).
        Los slow-ticks se registran en el log para diagnóstico.
        """
        next_deadline = time.monotonic()
        # Cada PERF_LOG_INTERVAL ticks volcamos snapshot del registry como
        # JSON-line para análisis offline (jq, pandas) sin scrapear Prometheus.
        PERF_LOG_INTERVAL = 100
        try:
            while self._state == SimulationState.RUNNING:
                tick_start_ns = time.perf_counter_ns()
                interval_s = self._tick_interval_ms / 1000.0

                await self._tick(interval_s)

                self._tick_count += 1
                self._simulation_time += interval_s

                elapsed_ns = time.perf_counter_ns() - tick_start_ns
                elapsed_ms = elapsed_ns / 1_000_000.0
                registry.record("tick_total_ms", elapsed_ms)
                registry.gauge("vehicles_active", self._vehicles_active)
                registry.gauge("tick_count", self._tick_count)
                registry.gauge("sim_time_seconds", self._simulation_time)

                if elapsed_ms > interval_s * 1000.0 * 1.2:
                    registry.inc("slow_tick_count")
                    logger.warning(
                        "Slow tick #%d: %.0f ms (presupuesto %.0f ms)",
                        self._tick_count,
                        elapsed_ms,
                        interval_s * 1000.0,
                    )

                if self._tick_count % PERF_LOG_INTERVAL == 0:
                    snap = registry.snapshot()
                    logger.info(
                        "perf %s",
                        json.dumps(
                            {
                                "tick": self._tick_count,
                                "active": self._vehicles_active,
                                "sim_time_s": round(self._simulation_time, 3),
                                "metrics": snap,
                            },
                            default=str,
                        ),
                    )

                next_deadline += interval_s
                now = time.monotonic()
                # Si acumulamos más de 2 ticks de retraso, re-sincronizamos la
                # deadline en vez de encadenar ticks rápidos sin dormir: en el
                # cliente esto se percibe como un salto hacia adelante brusco.
                if now - next_deadline > 2 * interval_s:
                    logger.warning(
                        "Re-sincronización del tick loop: "
                        "%.0f ms de retraso acumulado (>%.0f ms)",
                        (now - next_deadline) * 1000.0,
                        2 * interval_s * 1000.0,
                    )
                    next_deadline = now + interval_s

                sleep_time = next_deadline - now
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            pass

    async def _tick(self, dt: float) -> None:
        """
        Ejecuta un tick de simulación.

        Orden de operaciones:
          1. Lazy-init del TrafficLightController.
          2. Avance de ciclos de semáforos.
          3. Auto-spawn (si procede según config).
          4. Física de vehículos con IDM + restricción de semáforos.
          5. Broadcast de vehículos terminados y estado de tick.
          6. Broadcast de estados de semáforos (cada TL_BROADCAST_INTERVAL_TICKS).

        Args:
            dt: Delta time en segundos para este tick.
        """
        from app.core.constants import TL_BROADCAST_INTERVAL_TICKS

        st = SplitTimer()

        # 1. Lazy-init del controlador de semáforos (primera vez que el grafo está listo)
        if (
            self._tl_controller is None
            and self._spawner is not None
            and self._spawner.graph.node_count > 0
        ):
            from app.core.traffic_light_controller import TrafficLightController
            self._tl_controller = TrafficLightController(self._spawner.graph)
            logger.info(
                "TrafficLightController iniciado con %d semáforos",
                self._tl_controller.light_count,
            )

        # 2. Avanzar ciclos de semáforos
        if self._tl_controller is not None:
            self._tl_controller.tick(dt)
        st.split("tl_advance_ms")

        # 2b. Expirar incidentes con TTL cumplido — se procesa async tras el
        # tick físico para evitar I/O durante el hot path.
        expired_incidents: list[int] = []
        if self._incident_manager is not None:
            expired_incidents = self._incident_manager.tick(self._simulation_time)
        st.split("incident_tick_ms")

        # 3. Auto-spawn
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
        st.split("auto_spawn_ms")

        # 4. Física de vehículos con IDM.
        #   - Con < 500 vehículos: asyncio.to_thread (libera el event loop).
        #   - Con ≥ 500 vehículos: ProcessPoolExecutor con workers paralelos.
        #   update_vehicles_parallel tiene fallback interno a to_thread si el
        #   ProcessPool falla, así que no rompe la simulación.
        finished_ids: list[str] = []
        pending_collisions: list[tuple[str, str, tuple[int, int]]] = []
        if self._spawner is not None and self._spawner.graph.node_count > 0:
            from app.core.vehicle_physics import update_vehicles_parallel
            try:
                finished_ids = await update_vehicles_parallel(
                    self._spawner.vehicles,
                    self._spawner.graph,
                    dt,
                    tl_controller=self._tl_controller,
                    blocked_edges=self._spawner.blocked_edges,
                    tick_count=self._tick_count,
                    closed_lanes=self._spawner.closed_lanes,
                    pending_collisions=pending_collisions,
                    zone_manager=self._zone_manager,
                )
            except Exception:
                logger.exception("Error inesperado en update_vehicles_parallel; tick ignorado")
        st.split("physics_total_ms")

        # 4c. Registrar colisiones recién detectadas como incidentes ACCIDENT.
        if self._incident_manager is not None and pending_collisions:
            for v1_id, v2_id, edge_key in pending_collisions:
                try:
                    self._incident_manager.record_accident(
                        edge=edge_key,
                        sim_time=self._simulation_time,
                        vehicle_ids=(v1_id, v2_id),
                    )
                except Exception:
                    logger.exception("Error registrando accidente como incidente")

        # 4d. Cerrar incidentes expirados — se emite broadcast y se revierte
        # el estado (best-effort async).
        if expired_incidents and self._incident_manager is not None:
            asyncio.create_task(
                self._incident_manager.process_expired(expired_incidents)
            )
        st.split("incident_post_ms")

        # 4b. Recálculo periódico de pesos dinámicos por saturación en rotondas
        #     (Fase 6). Penaliza en A* las aristas de anillos con mucha ocupación
        #     → los nuevos spawns evitan el embudo y las rerutas pasan por rutas
        #     alternativas. Solo invalida la caché de rutas si algún
        #     multiplicador cambia más de DYNAMIC_WEIGHT_CHANGE_THRESHOLD.
        if (
            self._spawner is not None
            and self._tick_count % DYNAMIC_WEIGHTS_TICK_INTERVAL == 0
        ):
            self._recompute_dynamic_weights()
        st.split("dynamic_weights_ms")

        # 5. Broadcast vehicle_finished + eliminar vehículos completados
        for vid in finished_ids:
            if self._broadcaster is not None:
                await self._broadcaster.broadcast_vehicle_finished(vid)
            if self._spawner is not None:
                self._spawner.remove_vehicle(vid)

        self._vehicles_active = self._spawner.active_count if self._spawner else 0
        st.split("vehicle_finished_ms")

        # Broadcast tick — decoupleado del critical path. Esperamos a que el
        # broadcast del tick ANTERIOR termine (si aún está en marcha) y después
        # arrancamos el de este tick SIN esperarlo. El siguiente tick hará lo
        # mismo. Resultado: mientras corre la física del tick N+1, el broadcast
        # del tick N se serializa + envía en paralelo dentro del event loop.
        # Nota: el broadcast lee v.longitude/latitude directamente de los
        # SimVehicle; en CPython lecturas/escrituras de atributos float son
        # atómicas bajo GIL — el cliente puede ver un tick con algunos vehículos
        # ya una iteración por delante, pero su interpolador lo absorbe.
        if self._broadcaster is not None:
            if self._prev_broadcast_task is not None:
                try:
                    await self._prev_broadcast_task
                except Exception:
                    logger.exception("Error en broadcast previo (tick %d)", self._tick_count - 1)
                self._prev_broadcast_task = None
            self._prev_broadcast_task = asyncio.create_task(
                self._broadcaster.broadcast_tick(
                    tick=self._tick_count,
                    sim_time=self._simulation_time,
                )
            )
        st.split("broadcast_schedule_ms")

        # 6. Broadcast estados de semáforos (a menor frecuencia que los ticks)
        if (
            self._tl_controller is not None
            and self._broadcaster is not None
            and self._tick_count % TL_BROADCAST_INTERVAL_TICKS == 0
        ):
            await self._broadcaster.broadcast_traffic_lights(
                self._tl_controller.get_snapshot()
            )
        st.split("tl_broadcast_ms")

        st.emit()

    def _recompute_dynamic_weights(self) -> None:
        """
        Recalcula multiplicadores de A* por saturación de rotondas (Fase 6).

        Para cada rotonda cargada en el grafo calcula su ocupación actual
        (vehículos cuya arista pertenezca al anillo). El multiplicador es
        ``1 + (MAX_MULT-1) * min(load/sat, 1)^2`` (saturación cuadrática),
        y se aplica a todas las aristas del anillo.

        Si el cambio relativo respecto a los pesos previos supera
        ``DYNAMIC_WEIGHT_CHANGE_THRESHOLD`` en alguna arista, se invalida
        la caché de rutas del spawner → los siguientes spawns usarán los
        pesos nuevos.
        """
        spawner = self._spawner
        if spawner is None:
            return
        graph = spawner.graph

        # Ocupación por rotonda.
        occ: dict[int, int] = {}
        for v in spawner.vehicles.values():
            if v.status == VehicleStatus.FINISHED:
                continue
            np_ = v.route.node_path
            ei = v.current_edge_index
            if ei >= len(np_) - 1:
                continue
            attrs = graph.get_edge_attributes(np_[ei], np_[ei + 1])
            if not attrs.get(ATTR_IS_ROUNDABOUT):
                continue
            rid = attrs.get(ATTR_ROUNDABOUT_ID)
            if rid is not None:
                occ[int(rid)] = occ.get(int(rid), 0) + 1

        new_weights: dict[tuple[int, int], float] = {}
        max_extra = DYNAMIC_WEIGHT_MAX_MULT - 1.0
        for rid in graph.iter_roundabouts():
            ring_len = graph.get_roundabout_length(rid)
            if ring_len <= 0:
                continue
            load = occ.get(rid, 0) / max(ring_len / 100.0, 1.0)
            ratio = min(load / ROUNDABOUT_SATURATION_VEH_PER_100M, 1.0)
            mult = 1.0 + max_extra * (ratio * ratio)
            if mult <= 1.0001:
                continue  # sin penalización efectiva → no guardar
            for edge in graph.get_roundabout_members(rid):
                new_weights[edge] = mult

        # Invalidar caché solo si algún multiplicador cambió significativamente.
        prev = graph._dynamic_weights  # acceso interno intencional
        invalidate = False
        touched = set(new_weights.keys()) | set(prev.keys())
        for e in touched:
            old_m = prev.get(e, 1.0)
            new_m = new_weights.get(e, 1.0)
            if old_m == 0.0:
                continue
            if abs(new_m - old_m) / old_m > DYNAMIC_WEIGHT_CHANGE_THRESHOLD:
                invalidate = True
                break

        graph.set_dynamic_weights(new_weights)
        if invalidate:
            spawner.clear_route_cache()

    async def _cancel_task(self) -> None:
        """Cancela la tarea de background si existe."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        # Drenar cualquier broadcast pendiente para que los clientes no se
        # queden colgando con un mensaje a medias cuando paramos el motor.
        if self._prev_broadcast_task is not None:
            try:
                await self._prev_broadcast_task
            except Exception:
                pass
            self._prev_broadcast_task = None


# Instancia singleton a nivel de aplicación
simulation_engine = SimulationEngine()

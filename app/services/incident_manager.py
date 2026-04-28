"""
Gestor de incidentes de tráfico.

Un incidente es una interrupción puntual sobre una arista (accidente, obra,
avería, evento) con una duración opcional y que afecta a un subconjunto de
sus carriles. El manager se encarga de:

  * Persistir el incidente en BD (``dt_incidents``).
  * Proyectar su efecto sobre el estado vivo del spawner
    (``blocked_edges`` y ``closed_lanes``).
  * Expirar incidentes con TTL cumplido en cada tick.
  * Convertir las colisiones detectadas por la física en incidentes
    del tipo ACCIDENT de forma automática.
  * Broadcast de alta/baja/extensión por WebSocket.

Es async-friendly pero también ofrece métodos síncronos (``record_accident``,
``tick``) para ser invocados desde el tick loop sin hacer commits a BD.
Los commits quedan bufferizados y se vacían en el próximo ``flush_pending``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.core.constants import INCIDENT_DEFAULT_DURATION_S
from app.db.database import async_session_factory
from app.models.enums import IncidentStatus, IncidentType
from app.models.incident import IncidentModel

if TYPE_CHECKING:
    from app.core.broadcaster import SimulationBroadcaster
    from app.services.network_graph import RoadNetworkGraph
    from app.services.vehicle_spawner import VehicleSpawner

logger = logging.getLogger(__name__)


class IncidentManager:
    """
    Ciclo de vida de incidentes + proyección al estado vivo del grafo.

    El estado "vivo" está deliberadamente en memoria (``_active`` dict). La BD
    es solo para persistencia / UI; al reiniciar la simulación el estado se
    borra (como las colisiones). El flush a BD se hace de forma fire-and-forget
    vía ``async_session_factory``.
    """

    def __init__(
        self,
        spawner: "VehicleSpawner",
        broadcaster: "SimulationBroadcaster",
        graph: "RoadNetworkGraph",
    ) -> None:
        self._spawner = spawner
        self._broadcaster = broadcaster
        self._graph = graph
        # _active: id → IncidentModel (detached, en memoria)
        self._active: dict[int, IncidentModel] = {}
        # Contador para IDs sintéticos cuando no se persiste (p. ej. en tests)
        self._next_synthetic_id = -1

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_active(self) -> list[IncidentModel]:
        return [i for i in self._active.values() if i.status == IncidentStatus.ACTIVE.value]

    def get(self, incident_id: int) -> IncidentModel | None:
        return self._active.get(incident_id)

    # ------------------------------------------------------------------
    # Creación y retirada
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        type_: IncidentType | str,
        edge: tuple[int, int],
        lanes_affected: list[int] | None = None,
        duration_s: float | None = -1.0,
        severity: int = 1,
        description: str = "",
        sim_time: float = 0.0,
    ) -> IncidentModel:
        """
        Crea un incidente, persiste en BD, aplica al estado vivo y emite WS.

        ``duration_s=-1.0`` (sentinel) significa "usa el default del tipo".
        ``duration_s=None`` significa "permanente" (hasta retirada manual).
        """
        type_str = type_.value if isinstance(type_, IncidentType) else str(type_)
        lanes = list(lanes_affected or [])
        if duration_s == -1.0:
            duration_s = INCIDENT_DEFAULT_DURATION_S.get(type_str)

        edge_id = self._lookup_edge_id(edge)
        incident = IncidentModel(
            type=type_str,
            edge_id=edge_id,
            lanes_affected=lanes,
            start_time=sim_time,
            duration_s=duration_s,
            severity=int(severity),
            status=IncidentStatus.ACTIVE.value,
            description=description or None,
        )

        # Persistir en BD. Si falla (entorno sin DB en tests), seguimos con
        # un ID sintético para que el estado vivo funcione igualmente.
        try:
            async with async_session_factory() as session:
                session.add(incident)
                await session.commit()
                await session.refresh(incident)
        except Exception:
            logger.exception("No se pudo persistir el incidente — se usa ID sintético")
            incident.id = self._next_synthetic_id
            self._next_synthetic_id -= 1

        self._active[incident.id] = incident
        self._apply_to_graph_state(incident, edge)
        await self._broadcast("created", incident, edge)
        logger.info(
            "Incidente creado id=%s type=%s edge=%s lanes=%s dur=%s",
            incident.id, incident.type, edge, lanes, duration_s,
        )
        return incident

    def record_accident(
        self,
        *,
        edge: tuple[int, int],
        sim_time: float,
        vehicle_ids: tuple[str, str] | None = None,
    ) -> IncidentModel:
        """
        Variante síncrona para registrar un ACCIDENT detectado por la física.

        No persiste en BD (lo hará el siguiente ``flush_pending``), pero ya
        añade el incidente al estado vivo y lo marca para broadcast. La
        arista ya está en ``blocked_edges`` (puesta allí por ``_trigger_collision``),
        así que aquí solo registramos el tracking.
        """
        # Si ya hay un accidente activo en esta arista, no duplicar.
        for inc in self._active.values():
            if (
                inc.status == IncidentStatus.ACTIVE.value
                and inc.type == IncidentType.ACCIDENT.value
                and self._edge_of(inc) == edge
            ):
                return inc

        edge_id = self._lookup_edge_id(edge)
        lanes = self._all_lanes(edge)
        description = None
        if vehicle_ids is not None:
            description = f"{vehicle_ids[0]} <-> {vehicle_ids[1]}"
        incident = IncidentModel(
            id=self._next_synthetic_id,
            type=IncidentType.ACCIDENT.value,
            edge_id=edge_id,
            lanes_affected=lanes,
            start_time=sim_time,
            duration_s=None,
            severity=3,
            status=IncidentStatus.ACTIVE.value,
            description=description,
        )
        self._next_synthetic_id -= 1
        self._active[incident.id] = incident
        # No re-proyectamos (_trigger_collision ya puso la arista en
        # blocked_edges); proyección del closed_lanes tampoco hace falta
        # porque todos los carriles están en lanes_affected y blocked_edges
        # ya prevalece. Persistencia diferida.
        return incident

    async def flush_pending_persistence(self) -> None:
        """Persiste en BD los incidentes con ID sintético (<0)."""
        to_persist = [i for i in self._active.values() if i.id is not None and i.id < 0]
        if not to_persist:
            return
        try:
            async with async_session_factory() as session:
                for inc in to_persist:
                    old_id = inc.id
                    inc.id = None  # deja que la BD asigne
                    session.add(inc)
                await session.commit()
                for inc in to_persist:
                    await session.refresh(inc)
            # Reinsertar con los nuevos IDs
            refreshed = {inc.id: inc for inc in to_persist}
            for old_id in list(self._active.keys()):
                if old_id < 0 and old_id in self._active:
                    del self._active[old_id]
            self._active.update(refreshed)
        except Exception:
            logger.exception("Flush de incidentes pendientes falló")

    async def clear(self, incident_id: int) -> bool:
        """Marca un incidente como CLEARED, revierte su efecto y emite WS."""
        incident = self._active.get(incident_id)
        if incident is None or incident.status == IncidentStatus.CLEARED.value:
            return False
        edge = self._edge_of(incident)
        incident.status = IncidentStatus.CLEARED.value
        self._revert_from_graph_state(incident, edge)
        # Persistir baja (best-effort)
        if incident.id is not None and incident.id > 0:
            try:
                async with async_session_factory() as session:
                    db_inc = await session.get(IncidentModel, incident.id)
                    if db_inc is not None:
                        db_inc.status = IncidentStatus.CLEARED.value
                        await session.commit()
            except Exception:
                logger.exception("No se pudo persistir baja del incidente %s", incident.id)
        await self._broadcast("cleared", incident, edge)
        self._active.pop(incident_id, None)
        return True

    async def extend(self, incident_id: int, extra_s: float) -> IncidentModel | None:
        incident = self._active.get(incident_id)
        if incident is None or incident.status != IncidentStatus.ACTIVE.value:
            return None
        if incident.duration_s is None:
            incident.duration_s = float(extra_s)
        else:
            incident.duration_s = float(incident.duration_s) + float(extra_s)
        await self._broadcast("updated", incident, self._edge_of(incident))
        return incident

    # ------------------------------------------------------------------
    # Tick loop — expiración automática
    # ------------------------------------------------------------------

    def tick(self, sim_time: float) -> list[int]:
        """Marca como CLEARED los incidentes cuya TTL haya expirado.

        Devuelve los IDs expirados. Los efectos colaterales (broadcast, revert,
        persistencia) se harán en la corrutina async ``process_expired`` que
        el engine invoca con la lista que devolvemos aquí.
        """
        expired: list[int] = []
        for inc in self._active.values():
            if inc.status != IncidentStatus.ACTIVE.value:
                continue
            if inc.duration_s is None:
                continue
            if sim_time - inc.start_time >= inc.duration_s:
                expired.append(inc.id)
        return expired

    async def process_expired(self, expired_ids: list[int]) -> None:
        for iid in expired_ids:
            await self.clear(iid)

    # ------------------------------------------------------------------
    # Proyección sobre el estado vivo
    # ------------------------------------------------------------------

    def _apply_to_graph_state(
        self, incident: IncidentModel, edge: tuple[int, int]
    ) -> None:
        lanes = list(incident.lanes_affected or [])
        total_lanes = self._total_lanes(edge)
        if not lanes:
            return  # Evento puramente informativo
        current = self._spawner.closed_lanes.setdefault(edge, set())
        current.update(lanes)
        if len(current) >= total_lanes:
            self._spawner.blocked_edges[edge] = None

    def _revert_from_graph_state(
        self, incident: IncidentModel, edge: tuple[int, int]
    ) -> None:
        lanes = list(incident.lanes_affected or [])
        if not lanes:
            return
        # ¿Otro incidente activo sigue tapando estos carriles?
        still_blocked: set[int] = set()
        for other in self._active.values():
            if other.id == incident.id:
                continue
            if other.status != IncidentStatus.ACTIVE.value:
                continue
            if self._edge_of(other) != edge:
                continue
            still_blocked.update(other.lanes_affected or [])
        current = self._spawner.closed_lanes.get(edge)
        if current is not None:
            for lane in lanes:
                if lane not in still_blocked:
                    current.discard(lane)
            if not current:
                self._spawner.closed_lanes.pop(edge, None)
        # Solo liberar blocked_edges si ningún otro incidente activo cubre
        # todos los carriles de la arista.
        total_lanes = self._total_lanes(edge)
        remaining = self._spawner.closed_lanes.get(edge, set())
        if len(remaining) < total_lanes:
            # Asegurarse de que ninguna otra entrada siga bloqueando.
            any_full = any(
                other.status == IncidentStatus.ACTIVE.value
                and self._edge_of(other) == edge
                and len(set(other.lanes_affected or [])) >= total_lanes
                for other in self._active.values()
                if other.id != incident.id
            )
            if not any_full:
                self._spawner.blocked_edges.pop(edge, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _edge_of(self, incident: IncidentModel) -> tuple[int, int]:
        for u, v, data in self._graph.graph.edges(data=True):
            if data.get("edge_id") == incident.edge_id:
                return (u, v)
        return (0, 0)

    def _lookup_edge_id(self, edge: tuple[int, int]) -> int:
        attrs = self._graph.get_edge_attributes(*edge) or {}
        return int(attrs.get("edge_id", 0))

    def _total_lanes(self, edge: tuple[int, int]) -> int:
        attrs = self._graph.get_edge_attributes(*edge) or {}
        return int(attrs.get("lanes", 1))

    def _all_lanes(self, edge: tuple[int, int]) -> list[int]:
        return list(range(self._total_lanes(edge)))

    async def _broadcast(
        self, action: str, incident: IncidentModel, edge: tuple[int, int]
    ) -> None:
        if self._broadcaster is None:
            return
        try:
            await self._broadcaster.broadcast_incident(
                action=action,
                payload=self.to_public_dict(incident, edge),
            )
        except Exception:
            logger.exception("Fallo broadcast incidente")

    @staticmethod
    def to_public_dict(
        incident: IncidentModel, edge: tuple[int, int]
    ) -> dict[str, Any]:
        return {
            "id": incident.id,
            "type": incident.type,
            "edge": [edge[0], edge[1]],
            "edge_id": incident.edge_id,
            "lanes_affected": list(incident.lanes_affected or []),
            "start_time": incident.start_time,
            "duration_s": incident.duration_s,
            "severity": incident.severity,
            "status": incident.status,
            "description": incident.description,
        }

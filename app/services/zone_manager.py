"""
Gestor de zonas de control (ZBE, restringidas, peatonales).

Cada zona es un polígono PostGIS. Al crear o actualizar una zona se consulta
``ST_Intersects`` con las aristas de ``dt_edges`` para precomputar qué edges
caen dentro; el resultado se cachea como ``set[int]`` (edge IDs) para que
el hot path (A*, spawn) haga lookup O(1).

El enforcement determina el efecto:
  * ``warn`` — solo visualización, sin impacto en routing ni spawn.
  * ``deny_spawn`` — rechaza spawnear con origen/destino dentro de la zona
    cuando el vtype está en ``restricted_vtypes``.
  * ``force_reroute`` — penaliza las aristas internas en A* con
    ``ZBE_EDGE_PENALTY_FACTOR`` para los vtypes restringidos.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.models.enums import VehicleStatus, ZoneEnforcement, ZoneType  # noqa: F401
from app.models.zone import ZoneModel

if TYPE_CHECKING:
    from app.core.broadcaster import SimulationBroadcaster
    from app.core.physics.vehicle_types import VehicleType
    from app.services.network_graph import RoadNetworkGraph

logger = logging.getLogger(__name__)


class ZoneManager:
    """
    Mantiene el catálogo de zonas y la caché (zone_id → set de edge IDs).
    """

    def __init__(
        self,
        graph: "RoadNetworkGraph",
        broadcaster: "SimulationBroadcaster | None" = None,
    ) -> None:
        self._graph = graph
        self._broadcaster = broadcaster
        self._zones: dict[int, ZoneModel] = {}
        self._edge_ids_in_zone: dict[int, set[int]] = {}

    # ------------------------------------------------------------------
    # Carga inicial
    # ------------------------------------------------------------------

    async def load_from_db(self) -> None:
        """Recarga todas las zonas desde la BD y precomputa las intersecciones."""
        self._zones.clear()
        self._edge_ids_in_zone.clear()
        try:
            async with async_session_factory() as session:
                result = await session.execute(select(ZoneModel))
                zones = list(result.scalars().all())
                for z in zones:
                    self._zones[z.id] = z
                    self._edge_ids_in_zone[z.id] = await self._compute_edges_in_zone(
                        session, z.id
                    )
        except Exception:
            logger.exception("No se pudieron cargar zonas desde BD")
            return
        logger.info(
            "ZoneManager: %d zonas cargadas (%d activas)",
            len(self._zones),
            sum(1 for z in self._zones.values() if z.active),
        )

    async def _compute_edges_in_zone(
        self, session: AsyncSession, zone_id: int
    ) -> set[int]:
        """Consulta PostGIS las aristas cuya geometría intersecta con la zona."""
        stmt = text(
            """
            SELECT e.id
            FROM dt_edges AS e
            JOIN dt_zones AS z ON z.id = :zone_id
            WHERE ST_Intersects(e.geometry, z.geometry)
            """
        )
        try:
            result = await session.execute(stmt, {"zone_id": zone_id})
            return {row[0] for row in result.all()}
        except Exception:
            logger.exception("Intersección PostGIS falló para zona %s", zone_id)
            return set()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        zone_type: ZoneType | str,
        geometry_wkt: str,
        restricted_vtypes: list[str],
        enforcement: ZoneEnforcement | str = ZoneEnforcement.FORCE_REROUTE,
        active: bool = True,
    ) -> ZoneModel:
        ztype = zone_type.value if isinstance(zone_type, ZoneType) else str(zone_type)
        enf = (
            enforcement.value
            if isinstance(enforcement, ZoneEnforcement)
            else str(enforcement)
        )
        async with async_session_factory() as session:
            # INSERT en SQL plano para poder incluir ST_SetSRID(ST_GeomFromText(...))
            # en el mismo statement (la columna geometry es NOT NULL).
            result = await session.execute(
                text(
                    """
                    INSERT INTO dt_zones
                        (name, zone_type, geometry, restricted_vtypes,
                         enforcement, active)
                    VALUES
                        (:name, :zone_type,
                         ST_SetSRID(ST_GeomFromText(:wkt), 4326),
                         CAST(:restricted_vtypes AS JSONB),
                         :enforcement, :active)
                    RETURNING id
                    """
                ),
                {
                    "name": name,
                    "zone_type": ztype,
                    "wkt": geometry_wkt,
                    "restricted_vtypes": json.dumps(list(restricted_vtypes)),
                    "enforcement": enf,
                    "active": active,
                },
            )
            zone_id = int(result.scalar_one())
            await session.commit()
            # Releemos la zona como ORM para tener todos los campos poblados.
            zone: ZoneModel | None = await session.get(ZoneModel, zone_id)
            if zone is None:
                raise RuntimeError(f"No se pudo recuperar la zona {zone_id} tras insertar")
            self._zones[zone.id] = zone
            self._edge_ids_in_zone[zone.id] = await self._compute_edges_in_zone(
                session, zone.id
            )
        await self._broadcast("created", zone)
        logger.info(
            "Zona creada id=%s name=%s edges=%d",
            zone.id,
            zone.name,
            len(self._edge_ids_in_zone.get(zone.id, set())),
        )
        return zone

    async def update(
        self,
        zone_id: int,
        *,
        name: str | None = None,
        active: bool | None = None,
        enforcement: ZoneEnforcement | str | None = None,
        restricted_vtypes: list[str] | None = None,
    ) -> ZoneModel | None:
        async with async_session_factory() as session:
            zone = await session.get(ZoneModel, zone_id)
            if zone is None:
                return None
            if name is not None:
                zone.name = name
            if active is not None:
                zone.active = bool(active)
            if enforcement is not None:
                zone.enforcement = (
                    enforcement.value
                    if isinstance(enforcement, ZoneEnforcement)
                    else str(enforcement)
                )
            if restricted_vtypes is not None:
                zone.restricted_vtypes = list(restricted_vtypes)
            await session.commit()
            await session.refresh(zone)
            self._zones[zone.id] = zone
        await self._broadcast("updated", zone)
        return zone

    async def delete(self, zone_id: int) -> bool:
        async with async_session_factory() as session:
            zone = await session.get(ZoneModel, zone_id)
            if zone is None:
                return False
            await session.delete(zone)
            await session.commit()
        removed = self._zones.pop(zone_id, None)
        self._edge_ids_in_zone.pop(zone_id, None)
        if removed is not None:
            await self._broadcast("cleared", removed)
        return True

    # ------------------------------------------------------------------
    # Queries usadas por routing y spawn
    # ------------------------------------------------------------------

    def list_all(self) -> list[ZoneModel]:
        return list(self._zones.values())

    def get(self, zone_id: int) -> ZoneModel | None:
        return self._zones.get(zone_id)

    def edges_in_zone(self, zone_id: int) -> set[int]:
        return self._edge_ids_in_zone.get(zone_id, set())

    def restricted_edge_keys_for(
        self, vtype: "VehicleType | str"
    ) -> set[tuple[int, int]]:
        """
        Aristas (u,v) que deben penalizarse en A* para este ``vtype``.

        Combina todas las zonas activas con ``enforcement=force_reroute`` cuyo
        ``restricted_vtypes`` incluya el tipo solicitado. Devuelve pares (u,v)
        (la clave que usa el DiGraph), ya que el weight callback los compara
        por tupla, no por edge_id.
        """
        vstr = vtype.value if hasattr(vtype, "value") else str(vtype)
        wanted_edge_ids: set[int] = set()
        for z in self._zones.values():
            if not z.active:
                continue
            if z.enforcement != ZoneEnforcement.FORCE_REROUTE.value:
                continue
            if vstr not in (z.restricted_vtypes or []):
                continue
            wanted_edge_ids |= self._edge_ids_in_zone.get(z.id, set())
        if not wanted_edge_ids:
            return set()
        # Traducir edge_id → (u,v). Hacemos scan del grafo una vez.
        result: set[tuple[int, int]] = set()
        for u, v, data in self._graph.graph.edges(data=True):
            if int(data.get("edge_id", 0)) in wanted_edge_ids:
                result.add((u, v))
        return result

    def is_spawn_denied(
        self,
        *,
        start_edge_id: int | None,
        end_edge_id: int | None,
        vtype: "VehicleType | str",
    ) -> tuple[bool, ZoneModel | None]:
        """
        Comprueba si un spawn debe rechazarse por la política ``deny_spawn``.

        Devuelve ``(True, zone)`` si origen o destino caen en alguna zona
        activa con ``deny_spawn`` que restrinja al vtype; ``(False, None)``
        en otro caso.
        """
        vstr = vtype.value if hasattr(vtype, "value") else str(vtype)
        for z in self._zones.values():
            if not z.active:
                continue
            if z.enforcement != ZoneEnforcement.DENY_SPAWN.value:
                continue
            if vstr not in (z.restricted_vtypes or []):
                continue
            eset = self._edge_ids_in_zone.get(z.id, set())
            if (start_edge_id is not None and start_edge_id in eset) or (
                end_edge_id is not None and end_edge_id in eset
            ):
                return True, z
        return False, None

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    async def to_public_dict(
        self, zone: ZoneModel, session: AsyncSession | None = None
    ) -> dict[str, Any]:
        """Incluye el polígono como array de [lon, lat] para el cliente."""
        coords: list[list[float]] = []
        try:
            ctx_session = session or async_session_factory()
            owns = session is None
            if owns:
                s = await ctx_session.__aenter__()  # type: ignore[attr-defined]
            else:
                s = session
            try:
                res = await s.execute(
                    text(
                        "SELECT ST_AsGeoJSON(geometry) FROM dt_zones WHERE id = :id"
                    ),
                    {"id": zone.id},
                )
                row = res.first()
                if row and row[0]:
                    import json

                    gj = json.loads(row[0])
                    ring = gj.get("coordinates", [[]])[0]
                    coords = [[c[0], c[1]] for c in ring]
            finally:
                if owns:
                    await ctx_session.__aexit__(None, None, None)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("to_public_dict: no se pudo leer geometry de zona %s", zone.id)

        return {
            "id": zone.id,
            "name": zone.name,
            "zone_type": zone.zone_type,
            "polygon_coords": coords,
            "enforcement": zone.enforcement,
            "restricted_vtypes": list(zone.restricted_vtypes or []),
            "active": zone.active,
            "edges_count": len(self._edge_ids_in_zone.get(zone.id, set())),
        }

    async def _broadcast(self, action: str, zone: ZoneModel) -> None:
        if self._broadcaster is None:
            return
        try:
            payload = await self.to_public_dict(zone)
            await self._broadcaster.broadcast_zone(action=action, payload=payload)
        except Exception:
            logger.exception("Fallo broadcast zona")

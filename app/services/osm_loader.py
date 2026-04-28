"""
OSM (OpenStreetMap) data loader service.
ETL pipeline for extracting road network data from OSM files and loading into PostgreSQL.
"""

import json
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy.ext.asyncio import AsyncSession

from collections import defaultdict, deque

from sqlalchemy import update as sa_update

from app.core.constants import (
    DEFAULT_MAX_SPEED_KMH,
    EARTH_RADIUS_METERS,
    OSM_ALLOWED_HIGHWAY_TYPES,
    OSM_BATCH_SIZE,
    OSM_DEFAULT_ONEWAY_TYPES,
    OSM_DEFAULT_SPEED_LIMITS,
    OSM_JUNCTION_ROUNDABOUT,
    OSM_NODE_GIVE_WAY,
    OSM_NODE_STOP,
    OSM_NODE_CROSSING,
    OSM_NODE_TRAFFIC_SIGNALS,
    OSM_TAG_CROSSING,
    OSM_TAG_CROSSING_SIGNALS,
    OSM_ONEWAY_REVERSE,
    OSM_ONEWAY_YES,
    OSM_PBF_EXTENSION,
    OSM_PROGRESS_INTERVAL,
    OSM_TAG_HIGHWAY,
    OSM_TAG_JUNCTION,
    OSM_TAG_LANES,
    OSM_TAG_LANES_BACKWARD,
    OSM_TAG_LANES_FORWARD,
    OSM_TAG_MAXSPEED,
    OSM_TAG_MAXSPEED_LANES,
    OSM_TAG_NAME,
    OSM_TAG_NOEXIT,
    OSM_TAG_ONEWAY,
    OSM_TAG_TURN_LANES,
    OSM_VALUE_YES,
    OSM_XML_EXTENSIONS,
    SRID_WGS84,
)
from app.db.repositories.edge_repository import EdgeRepository
from app.db.repositories.node_repository import NodeRepository
from app.models.enums import NodeType, RoadType
from app.models.road_network import EdgeModel, NodeModel


@dataclass
class OSMNode:
    """Temporary storage for OSM node data during parsing."""

    osm_id: int
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class OSMWay:
    """Temporary storage for OSM way data during parsing."""

    osm_id: int
    node_refs: list[int]
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class OSMLoadStats:
    """Statistics from OSM loading process."""

    nodes_parsed: int = 0
    nodes_imported: int = 0
    ways_parsed: int = 0
    edges_imported: int = 0
    ways_skipped: int = 0
    errors: int = 0
    duration_seconds: float = 0.0


class OSMLoader:
    """
    ETL pipeline for loading OpenStreetMap data into PostgreSQL.

    Supports OSM XML format (.osm files). Filters roads by highway type
    and converts them to the road network data model.

    Usage:
        async with get_db_session() as session:
            loader = OSMLoader(session)
            stats = await loader.load_from_file("data/talavera.osm")
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        batch_size: int = OSM_BATCH_SIZE,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ):
        """
        Initialize the OSM loader.

        Args:
            session: AsyncSession for database operations
            batch_size: Number of entities to insert per batch
            progress_callback: Optional callback for progress reporting
                               Receives (stage_name, count) arguments
        """
        self._session = session
        self._batch_size = batch_size
        self._progress_callback = progress_callback

        # Repositories
        self._node_repo = NodeRepository(session)
        self._edge_repo = EdgeRepository(session)

        # Parsed OSM data
        self._osm_nodes: dict[int, OSMNode] = {}
        self._osm_ways: list[OSMWay] = []

        # Mapping from OSM node IDs to database IDs
        self._osm_to_db_node_id: dict[int, int] = {}

    def _report_progress(self, stage: str, count: int) -> None:
        """Report progress if callback is configured."""
        if self._progress_callback:
            self._progress_callback(stage, count)

    async def load_from_file(
        self,
        filepath: str,
        *,
        clear_existing: bool = False,
    ) -> OSMLoadStats:
        """
        Load OSM data from file into the database.

        Args:
            filepath: Path to OSM file (.osm XML format)
            clear_existing: If True, delete existing nodes/edges before import

        Returns:
            Statistics about the loading process

        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If the file format is not supported
        """
        start_time = time.time()
        stats = OSMLoadStats()

        # Validate file
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"OSM file not found: {filepath}")

        suffix = path.suffix.lower()
        if suffix == OSM_PBF_EXTENSION or filepath.endswith(".osm.pbf"):
            raise ValueError(
                f"PBF format not supported yet. Please use .osm XML format. "
                f"Convert with: osmium cat {filepath} -o output.osm"
            )

        if suffix not in OSM_XML_EXTENSIONS:
            raise ValueError(
                f"Unsupported file format: {suffix}. "
                f"Supported: {OSM_XML_EXTENSIONS}"
            )

        # Clear existing data if requested
        if clear_existing:
            await self._clear_existing_data()

        # Parse OSM file
        self._report_progress("Parsing OSM file", 0)
        self._parse_osm_xml(filepath, stats)

        # Filter ways and get referenced nodes
        self._report_progress("Filtering roads", stats.ways_parsed)
        filtered_ways, referenced_node_ids = self._filter_ways(stats)

        # Compute split points (intersections + traffic_signals) so a single
        # OSM way produces one edge per segment between any two split nodes.
        # This makes traffic_signals real graph endpoints and lets the
        # TrafficLightController gestionarlos.
        split_nodes = self._compute_split_nodes(filtered_ways)

        # Save nodes to database
        self._report_progress("Saving nodes", len(referenced_node_ids))
        await self._save_nodes(referenced_node_ids, stats)

        # Save edges to database
        self._report_progress("Saving edges", len(filtered_ways))
        await self._save_edges(filtered_ways, split_nodes, stats)

        # Asignar roundabout_id a todas las aristas del mismo anillo físico.
        # Se hace tras guardar las aristas porque trabajamos con IDs de BD.
        self._report_progress("Grouping roundabout edges", 0)
        await self._group_roundabout_edges()

        # Commit transaction
        await self._session.commit()

        stats.duration_seconds = time.time() - start_time
        return stats

    async def _clear_existing_data(self) -> None:
        """Delete all existing nodes and edges."""
        # Delete edges first (foreign key constraint)
        await self._session.execute(EdgeModel.__table__.delete())
        await self._session.execute(NodeModel.__table__.delete())
        await self._session.flush()

    def _parse_osm_xml(self, filepath: str, stats: OSMLoadStats) -> None:
        """
        Parse OSM XML file using iterparse for memory efficiency.

        Args:
            filepath: Path to OSM XML file
            stats: Statistics object to update
        """
        self._osm_nodes.clear()
        self._osm_ways.clear()

        context = ET.iterparse(filepath, events=("end",))

        for event, elem in context:
            if elem.tag == "node":
                osm_id = int(elem.get("id", 0))
                lat = float(elem.get("lat", 0))
                lon = float(elem.get("lon", 0))

                tags = {
                    tag.get("k", ""): tag.get("v", "")
                    for tag in elem.findall("tag")
                }

                self._osm_nodes[osm_id] = OSMNode(
                    osm_id=osm_id, lat=lat, lon=lon, tags=tags
                )
                stats.nodes_parsed += 1

                if stats.nodes_parsed % OSM_PROGRESS_INTERVAL == 0:
                    self._report_progress("Parsing nodes", stats.nodes_parsed)

                # Clear element to save memory
                elem.clear()

            elif elem.tag == "way":
                osm_id = int(elem.get("id", 0))
                node_refs = [
                    int(nd.get("ref", 0)) for nd in elem.findall("nd")
                ]
                tags = {
                    tag.get("k", ""): tag.get("v", "")
                    for tag in elem.findall("tag")
                }

                self._osm_ways.append(
                    OSMWay(osm_id=osm_id, node_refs=node_refs, tags=tags)
                )
                stats.ways_parsed += 1

                if stats.ways_parsed % OSM_PROGRESS_INTERVAL == 0:
                    self._report_progress("Parsing ways", stats.ways_parsed)

                elem.clear()

    def _filter_ways(
        self, stats: OSMLoadStats
    ) -> tuple[list[OSMWay], set[int]]:
        """
        Filter ways by highway type and collect referenced node IDs.

        Args:
            stats: Statistics object to update

        Returns:
            Tuple of (filtered_ways, referenced_node_ids)
        """
        filtered_ways: list[OSMWay] = []
        referenced_node_ids: set[int] = set()

        for way in self._osm_ways:
            highway = way.tags.get(OSM_TAG_HIGHWAY)

            # Skip ways without highway tag or with excluded types
            if not highway or highway not in OSM_ALLOWED_HIGHWAY_TYPES:
                stats.ways_skipped += 1
                continue

            # Skip ways with insufficient nodes
            if len(way.node_refs) < 2:
                stats.ways_skipped += 1
                continue

            # Check that all referenced nodes exist
            missing_nodes = [
                ref for ref in way.node_refs if ref not in self._osm_nodes
            ]
            if missing_nodes:
                stats.errors += 1
                continue

            filtered_ways.append(way)
            referenced_node_ids.update(way.node_refs)

        return filtered_ways, referenced_node_ids

    async def _save_nodes(
        self, node_ids: set[int], stats: OSMLoadStats
    ) -> None:
        """
        Save nodes to database in batches.

        Args:
            node_ids: Set of OSM node IDs to save
            stats: Statistics object to update
        """
        node_list = list(node_ids)
        total = len(node_list)

        for i in range(0, total, self._batch_size):
            batch_ids = node_list[i : i + self._batch_size]
            batch_models: list[NodeModel] = []

            for osm_id in batch_ids:
                osm_node = self._osm_nodes[osm_id]

                # Determine node type
                node_type = self._determine_node_type(osm_node)

                # Create model with PostGIS geometry
                model = NodeModel(
                    name=osm_node.tags.get(OSM_TAG_NAME),
                    node_type=node_type.value,
                    position=ST_SetSRID(
                        ST_MakePoint(osm_node.lon, osm_node.lat), SRID_WGS84
                    ),
                    is_active=True,
                    metadata_json=json.dumps({"osm_id": osm_id}),
                )
                batch_models.append(model)

            # Bulk insert
            created = await self._node_repo.bulk_create(batch_models)

            # Store mapping from OSM ID to DB ID
            for j, osm_id in enumerate(batch_ids):
                self._osm_to_db_node_id[osm_id] = created[j].id

            stats.nodes_imported += len(created)

            if stats.nodes_imported % OSM_PROGRESS_INTERVAL == 0:
                self._report_progress("Nodes imported", stats.nodes_imported)

    def _determine_node_type(self, osm_node: OSMNode) -> NodeType:
        """Determine the node type from OSM tags."""
        tags = osm_node.tags

        if tags.get(OSM_TAG_HIGHWAY) == OSM_NODE_TRAFFIC_SIGNALS:
            return NodeType.TRAFFIC_LIGHT
        if tags.get(OSM_TAG_CROSSING) == OSM_NODE_TRAFFIC_SIGNALS:
            return NodeType.TRAFFIC_LIGHT
        # Etiqueta moderna de OSM para pasos peatonales semaforizados
        # (`highway=crossing` + `crossing:signals=yes`). Sin esto se pierden
        # los pasos peatonales con semáforo más recientes (post-2017).
        if tags.get(OSM_TAG_CROSSING_SIGNALS) == OSM_VALUE_YES:
            return NodeType.TRAFFIC_LIGHT
        # `highway=crossing` (paso peatonal sobre vía motorizada). Lo tratamos
        # como semáforo aunque no esté explícitamente semaforizado, porque en
        # el modelo de simulación los vehículos deben ceder/parar ahí.
        if tags.get(OSM_TAG_HIGHWAY) == OSM_NODE_CROSSING:
            return NodeType.TRAFFIC_LIGHT
        if tags.get(OSM_TAG_HIGHWAY) == OSM_NODE_STOP:
            return NodeType.STOP_SIGN
        if tags.get(OSM_TAG_HIGHWAY) == OSM_NODE_GIVE_WAY:
            return NodeType.YIELD_SIGN
        if tags.get(OSM_TAG_JUNCTION) == OSM_JUNCTION_ROUNDABOUT:
            return NodeType.ROUNDABOUT
        if tags.get(OSM_TAG_NOEXIT) == OSM_VALUE_YES:
            return NodeType.DEAD_END

        return NodeType.INTERSECTION

    def _compute_split_nodes(
        self, filtered_ways: list[OSMWay]
    ) -> set[int]:
        """
        Compute the set of OSM node IDs at which ways must be split.

        A node is a split point if either:
        - It is referenced by ≥2 filtered ways (a real intersection
          between motorized roads).
        - Its OSM tags mark it as a traffic light (so it becomes a real
          graph endpoint and the TrafficLightController can manage it).
        """
        way_count: dict[int, int] = defaultdict(int)
        for way in filtered_ways:
            for ref in set(way.node_refs):
                way_count[ref] += 1

        split_nodes: set[int] = {
            nid for nid, count in way_count.items() if count >= 2
        }

        for nid in way_count:
            osm_node = self._osm_nodes.get(nid)
            if (
                osm_node is not None
                and self._determine_node_type(osm_node) == NodeType.TRAFFIC_LIGHT
            ):
                split_nodes.add(nid)

        return split_nodes

    async def _save_edges(
        self,
        ways: list[OSMWay],
        split_nodes: set[int],
        stats: OSMLoadStats,
    ) -> None:
        """
        Save edges to database in batches.

        Each OSM way is converted to one or more edges, splitting the
        way at every node in ``split_nodes`` so intersections and
        traffic_signals become real edge endpoints.

        Args:
            ways: List of filtered OSM ways
            split_nodes: OSM node IDs at which to split ways
            stats: Statistics object to update
        """
        for i in range(0, len(ways), self._batch_size):
            batch_ways = ways[i : i + self._batch_size]
            batch_models: list[EdgeModel] = []

            for way in batch_ways:
                batch_models.extend(self._way_to_edge_models(way, split_nodes))

            if batch_models:
                await self._edge_repo.bulk_create(batch_models)
                stats.edges_imported += len(batch_models)

            if stats.edges_imported % OSM_PROGRESS_INTERVAL == 0:
                self._report_progress("Edges imported", stats.edges_imported)

    def _way_to_edge_models(
        self, way: OSMWay, split_nodes: set[int]
    ) -> list[EdgeModel]:
        """
        Convert an OSM way to one or more EdgeModels.

        The way is split at any interior node present in ``split_nodes``
        (intersections and traffic_signals). Each segment becomes its
        own edge with its portion of the way's polyline as geometry.
        Attributes derived from the way (road_type, max_speed, lanes,
        one_way, is_roundabout, name, lane-granular metadata) are
        replicated on every sub-edge.

        Args:
            way: OSM way data
            split_nodes: OSM node IDs at which to split this way

        Returns:
            List of EdgeModels (possibly empty if the way is malformed)
        """
        tags = way.tags
        node_refs = way.node_refs

        if len(node_refs) < 2:
            return []

        split_indices: list[int] = [0]
        for i in range(1, len(node_refs) - 1):
            if node_refs[i] in split_nodes:
                split_indices.append(i)
        split_indices.append(len(node_refs) - 1)

        highway = tags.get(OSM_TAG_HIGHWAY, "unclassified")
        road_type = RoadType.from_osm_highway(highway)
        max_speed = self._parse_maxspeed(tags.get(OSM_TAG_MAXSPEED), highway)
        lanes = self._parse_lanes(tags.get(OSM_TAG_LANES))
        one_way = self._is_one_way(tags)
        is_roundabout = tags.get(OSM_TAG_JUNCTION) == OSM_JUNCTION_ROUNDABOUT
        name = tags.get(OSM_TAG_NAME)

        meta: dict = {"osm_id": way.osm_id}
        # Preservar tags de granularidad por carril para fases posteriores
        # (giros permitidos, velocidades por carril, tránsito por carriles).
        for key, tag_name in (
            ("turn_lanes", OSM_TAG_TURN_LANES),
            ("maxspeed_lanes", OSM_TAG_MAXSPEED_LANES),
            ("lanes_forward", OSM_TAG_LANES_FORWARD),
            ("lanes_backward", OSM_TAG_LANES_BACKWARD),
        ):
            v = tags.get(tag_name)
            if v:
                meta[key] = v
        meta_json = json.dumps(meta)

        edges: list[EdgeModel] = []
        for seg_idx in range(len(split_indices) - 1):
            i_start = split_indices[seg_idx]
            i_end = split_indices[seg_idx + 1]
            segment_refs = node_refs[i_start : i_end + 1]

            start_db_id = self._osm_to_db_node_id.get(segment_refs[0])
            end_db_id = self._osm_to_db_node_id.get(segment_refs[-1])
            if start_db_id is None or end_db_id is None:
                continue

            coords: list[tuple[float, float]] = []
            for ref in segment_refs:
                osm_node = self._osm_nodes.get(ref)
                if osm_node is not None:
                    coords.append((osm_node.lon, osm_node.lat))

            if len(coords) < 2:
                continue

            edges.append(
                EdgeModel(
                    name=name,
                    start_node_id=start_db_id,
                    end_node_id=end_db_id,
                    road_type=road_type.value,
                    geometry=self._make_linestring(coords),
                    length=self._calculate_length(coords),
                    max_speed=max_speed,
                    lanes=lanes,
                    one_way=one_way,
                    is_roundabout=is_roundabout,
                    is_active=True,
                    metadata_json=meta_json,
                )
            )

        return edges

    def _make_linestring(self, coords: list[tuple[float, float]]) -> str:
        """
        Create a PostGIS LineString geometry using WKT format.

        Args:
            coords: List of (longitude, latitude) tuples

        Returns:
            WKT string for the LineString with SRID
        """
        # Build WKT format: LINESTRING(lon1 lat1, lon2 lat2, ...)
        coord_strings = [f"{lon} {lat}" for lon, lat in coords]
        wkt = f"SRID={SRID_WGS84};LINESTRING({', '.join(coord_strings)})"
        return wkt

    def _calculate_length(self, coords: list[tuple[float, float]]) -> float:
        """
        Calculate the total length of a path in meters using Haversine formula.

        Args:
            coords: List of (longitude, latitude) tuples

        Returns:
            Total length in meters
        """
        total_length = 0.0

        for i in range(len(coords) - 1):
            lon1, lat1 = coords[i]
            lon2, lat2 = coords[i + 1]

            # Convert to radians
            lat1_rad = math.radians(lat1)
            lat2_rad = math.radians(lat2)
            delta_lat = math.radians(lat2 - lat1)
            delta_lon = math.radians(lon2 - lon1)

            # Haversine formula
            a = (
                math.sin(delta_lat / 2) ** 2
                + math.cos(lat1_rad)
                * math.cos(lat2_rad)
                * math.sin(delta_lon / 2) ** 2
            )
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            total_length += EARTH_RADIUS_METERS * c

        return total_length

    def _parse_maxspeed(
        self, maxspeed: Optional[str], highway: str
    ) -> int:
        """
        Parse OSM maxspeed tag to integer km/h.

        Handles formats: "50", "50 km/h", "30 mph"

        Args:
            maxspeed: OSM maxspeed tag value
            highway: Highway type for default lookup

        Returns:
            Speed limit in km/h
        """
        if not maxspeed:
            return OSM_DEFAULT_SPEED_LIMITS.get(highway, DEFAULT_MAX_SPEED_KMH)

        # Remove whitespace
        maxspeed = maxspeed.strip()

        # Try to extract numeric value
        match = re.match(r"(\d+(?:\.\d+)?)\s*(mph|km/h|kmh)?", maxspeed, re.I)
        if match:
            value = float(match.group(1))
            unit = (match.group(2) or "").lower()

            if unit == "mph":
                return int(value * 1.60934)  # Convert to km/h
            return int(value)

        # Fallback to default
        return OSM_DEFAULT_SPEED_LIMITS.get(highway, DEFAULT_MAX_SPEED_KMH)

    def _parse_lanes(self, lanes: Optional[str]) -> int:
        """Parse OSM lanes tag to integer."""
        if not lanes:
            return 1

        try:
            return max(1, int(lanes))
        except ValueError:
            return 1

    def _is_one_way(self, tags: dict[str, str]) -> bool:
        """
        Determine if the road is one-way from OSM tags.

        Args:
            tags: OSM way tags

        Returns:
            True if the road is one-way
        """
        oneway = tags.get(OSM_TAG_ONEWAY, "").lower()

        # Explicit one-way
        if oneway in OSM_ONEWAY_YES:
            return True

        # Reverse one-way (still one-way, direction handled separately)
        if oneway in OSM_ONEWAY_REVERSE:
            return True

        # Roundabouts are always one-way
        if tags.get(OSM_TAG_JUNCTION) == OSM_JUNCTION_ROUNDABOUT:
            return True

        # Some highway types are one-way by default
        highway = tags.get(OSM_TAG_HIGHWAY, "")
        if highway in OSM_DEFAULT_ONEWAY_TYPES:
            # Unless explicitly marked as not one-way
            return oneway != "no"

        return False

    async def _group_roundabout_edges(self) -> None:
        """
        Asigna un ``roundabout_id`` único a cada anillo conexo.

        Recorre las aristas con ``is_roundabout=True`` y las agrupa por
        componentes conexas sobre el grafo no dirigido (``start_node`` y
        ``end_node`` como nodos compartidos). Cada componente recibe un ID
        incremental a partir de 1.
        """
        from sqlalchemy import select

        stmt = select(
            EdgeModel.id, EdgeModel.start_node_id, EdgeModel.end_node_id
        ).where(EdgeModel.is_roundabout.is_(True))
        rows = (await self._session.execute(stmt)).all()

        if not rows:
            return

        # Mapa nodo -> aristas incidentes (por id).
        incident: dict[int, list[int]] = defaultdict(list)
        end_nodes: dict[int, tuple[int, int]] = {}
        for row in rows:
            incident[row.start_node_id].append(row.id)
            incident[row.end_node_id].append(row.id)
            end_nodes[row.id] = (row.start_node_id, row.end_node_id)

        # BFS sobre aristas conectadas por nodos compartidos.
        assigned: dict[int, int] = {}
        next_rid = 1
        for edge_id in end_nodes.keys():
            if edge_id in assigned:
                continue
            component: list[int] = []
            queue: deque[int] = deque([edge_id])
            assigned[edge_id] = next_rid
            while queue:
                cur = queue.popleft()
                component.append(cur)
                u, v = end_nodes[cur]
                for nb in incident[u] + incident[v]:
                    if nb in assigned:
                        continue
                    assigned[nb] = next_rid
                    queue.append(nb)
            next_rid += 1

        # Update en bloque por componente (pocas filas por anillo).
        buckets: dict[int, list[int]] = defaultdict(list)
        for eid, rid in assigned.items():
            buckets[rid].append(eid)
        for rid, ids in buckets.items():
            await self._session.execute(
                sa_update(EdgeModel)
                .where(EdgeModel.id.in_(ids))
                .values(roundabout_id=rid)
            )

    def clear(self) -> None:
        """Clear internal state."""
        self._osm_nodes.clear()
        self._osm_ways.clear()
        self._osm_to_db_node_id.clear()

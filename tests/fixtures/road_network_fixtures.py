"""
Sample data fixtures for road network testing.
Based on Talavera de la Reina geographic coordinates.
"""

from app.core.constants import (
    ATTR_EDGE_ID,
    ATTR_IS_ROUNDABOUT,
    ATTR_LANES,
    ATTR_LATITUDE,
    ATTR_LENGTH,
    ATTR_LONGITUDE,
    ATTR_MAX_SPEED,
    ATTR_NODE_ID,
    ATTR_NODE_TYPE,
    ATTR_ONE_WAY,
    ATTR_ROUNDABOUT_ID,
    ATTR_WAYPOINTS,
    ATTR_WEIGHT,
)
from app.models.enums import NodeType, RoadType
from app.services.network_graph import RoadNetworkGraph

# Talavera de la Reina approximate center coordinates
TALAVERA_CENTER_LON = -4.8306
TALAVERA_CENTER_LAT = 39.9634

# Sample node data for testing
SAMPLE_NODES = [
    {
        "name": "Plaza del Pan",
        "node_type": NodeType.INTERSECTION.value,
        "longitude": -4.8306,
        "latitude": 39.9634,
        "is_active": True,
    },
    {
        "name": "Puente Romano",
        "node_type": NodeType.INTERSECTION.value,
        "longitude": -4.8256,
        "latitude": 39.9598,
        "is_active": True,
    },
    {
        "name": "Semáforo Calle Real",
        "node_type": NodeType.TRAFFIC_LIGHT.value,
        "longitude": -4.8320,
        "latitude": 39.9640,
        "is_active": True,
    },
    {
        "name": "Rotonda Hospital",
        "node_type": NodeType.ROUNDABOUT.value,
        "longitude": -4.8150,
        "latitude": 39.9700,
        "is_active": True,
    },
    {
        "name": "Entrada A-5",
        "node_type": NodeType.ENTRY_POINT.value,
        "longitude": -4.8500,
        "latitude": 39.9500,
        "is_active": True,
    },
    {
        "name": "Salida Toledo",
        "node_type": NodeType.EXIT_POINT.value,
        "longitude": -4.8000,
        "latitude": 39.9800,
        "is_active": True,
    },
]

# Sample edge data for testing (references node indices 0-based)
SAMPLE_EDGES = [
    {
        "name": "Calle Real",
        "start_node_index": 0,  # Plaza del Pan
        "end_node_index": 2,  # Semáforo Calle Real
        "road_type": RoadType.PRIMARY.value,
        "geometry_coordinates": [
            {"longitude": -4.8306, "latitude": 39.9634},
            {"longitude": -4.8320, "latitude": 39.9640},
        ],
        "length": 150.0,
        "max_speed": 50,
        "lanes": 2,
        "one_way": False,
        "is_active": True,
    },
    {
        "name": "Avenida del Puente",
        "start_node_index": 0,  # Plaza del Pan
        "end_node_index": 1,  # Puente Romano
        "road_type": RoadType.SECONDARY.value,
        "geometry_coordinates": [
            {"longitude": -4.8306, "latitude": 39.9634},
            {"longitude": -4.8280, "latitude": 39.9616},
            {"longitude": -4.8256, "latitude": 39.9598},
        ],
        "length": 450.0,
        "max_speed": 40,
        "lanes": 1,
        "one_way": True,
        "is_active": True,
    },
    {
        "name": "Carretera Hospital",
        "start_node_index": 2,  # Semáforo Calle Real
        "end_node_index": 3,  # Rotonda Hospital
        "road_type": RoadType.PRIMARY.value,
        "geometry_coordinates": [
            {"longitude": -4.8320, "latitude": 39.9640},
            {"longitude": -4.8200, "latitude": 39.9680},
            {"longitude": -4.8150, "latitude": 39.9700},
        ],
        "length": 800.0,
        "max_speed": 60,
        "lanes": 2,
        "one_way": False,
        "is_active": True,
    },
    {
        "name": "Acceso A-5",
        "start_node_index": 4,  # Entrada A-5
        "end_node_index": 0,  # Plaza del Pan
        "road_type": RoadType.MOTORWAY.value,
        "geometry_coordinates": [
            {"longitude": -4.8500, "latitude": 39.9500},
            {"longitude": -4.8400, "latitude": 39.9560},
            {"longitude": -4.8306, "latitude": 39.9634},
        ],
        "length": 2000.0,
        "max_speed": 80,
        "lanes": 2,
        "one_way": True,
        "is_active": True,
    },
]


def create_sample_node_data(
    name: str = "Test Node",
    node_type: str = NodeType.INTERSECTION.value,
    longitude: float = TALAVERA_CENTER_LON,
    latitude: float = TALAVERA_CENTER_LAT,
    is_active: bool = True,
    metadata_json: str | None = None,
) -> dict:
    """Create sample node data for testing."""
    return {
        "name": name,
        "node_type": node_type,
        "longitude": longitude,
        "latitude": latitude,
        "is_active": is_active,
        "metadata_json": metadata_json,
    }


def create_sample_edge_data(
    name: str = "Test Edge",
    start_node_id: int = 1,
    end_node_id: int = 2,
    road_type: str = RoadType.RESIDENTIAL.value,
    geometry_coordinates: list | None = None,
    length: float = 100.0,
    max_speed: int = 50,
    lanes: int = 1,
    one_way: bool = False,
    is_active: bool = True,
    metadata_json: str | None = None,
) -> dict:
    """Create sample edge data for testing."""
    if geometry_coordinates is None:
        geometry_coordinates = [
            {"longitude": TALAVERA_CENTER_LON, "latitude": TALAVERA_CENTER_LAT},
            {"longitude": TALAVERA_CENTER_LON + 0.001, "latitude": TALAVERA_CENTER_LAT + 0.001},
        ]
    return {
        "name": name,
        "start_node_id": start_node_id,
        "end_node_id": end_node_id,
        "road_type": road_type,
        "geometry_coordinates": geometry_coordinates,
        "length": length,
        "max_speed": max_speed,
        "lanes": lanes,
        "one_way": one_way,
        "is_active": is_active,
        "metadata_json": metadata_json,
    }


def build_two_entry_roundabout_graph() -> RoadNetworkGraph:
    """
    Construye un RoadNetworkGraph mínimo con una rotonda de 3 arcos y dos brazos
    de entrada convergentes. Pensado para tests de `_find_roundabout_yield_leader`.

    Topología::

        A_in (1) ──► N_A (10) ──┐
                                 ├─► anillo N_A ► N_B ► N_C ► N_A (is_roundabout=True)
        B_in (2) ──► N_B (11) ──┘
                                   N_C (12) ──► A_out (20)

    Todas las aristas son one-way. Anillo: 3 arcos de 10 m. Brazos de entrada:
    20 m cada uno. Salida: 20 m.
    """
    rng = RoadNetworkGraph()
    g = rng.graph

    nodes = {
        1:  (-4.830, 39.960, NodeType.ENTRY_POINT.value),   # A_in
        2:  (-4.832, 39.960, NodeType.ENTRY_POINT.value),   # B_in
        3:  (-4.830, 39.961, NodeType.ENTRY_POINT.value),   # C_in (converge en N_A)
        10: (-4.831, 39.961, NodeType.ROUNDABOUT.value),    # N_A
        11: (-4.832, 39.961, NodeType.ROUNDABOUT.value),    # N_B
        12: (-4.831, 39.962, NodeType.ROUNDABOUT.value),    # N_C
        20: (-4.830, 39.962, NodeType.EXIT_POINT.value),    # A_out
    }
    for nid, (lon, lat, ntype) in nodes.items():
        g.add_node(
            nid,
            **{
                ATTR_NODE_ID: nid,
                ATTR_LONGITUDE: lon,
                ATTR_LATITUDE: lat,
                ATTR_NODE_TYPE: ntype,
            },
        )

    def _edge(eid: int, u: int, v: int, length: float, is_ring: bool, rid: int | None):
        lon_u, lat_u, _ = nodes[u]
        lon_v, lat_v, _ = nodes[v]
        g.add_edge(
            u, v,
            **{
                ATTR_EDGE_ID: eid,
                ATTR_LENGTH: length,
                ATTR_MAX_SPEED: 30,
                ATTR_WEIGHT: length / 30.0,
                ATTR_ONE_WAY: True,
                ATTR_LANES: 1,
                ATTR_WAYPOINTS: [(lon_u, lat_u), (lon_v, lat_v)],
                ATTR_IS_ROUNDABOUT: is_ring,
                ATTR_ROUNDABOUT_ID: rid,
            },
        )

    # Brazos de entrada (no-ring), 20 m cada uno.
    # A_in y C_in convergen en N_A (test cross-arm arbitration).
    _edge(100, 1, 10, 20.0, is_ring=False, rid=None)
    _edge(101, 2, 11, 20.0, is_ring=False, rid=None)
    _edge(102, 3, 10, 20.0, is_ring=False, rid=None)

    # Anillo (one-way, 10 m cada arco) — sentido N_A → N_B → N_C → N_A.
    _edge(200, 10, 11, 10.0, is_ring=True, rid=1)
    _edge(201, 11, 12, 10.0, is_ring=True, rid=1)
    _edge(202, 12, 10, 10.0, is_ring=True, rid=1)

    # Salida desde N_C.
    _edge(300, 12, 20, 20.0, is_ring=False, rid=None)

    rng._rebuild_roundabout_indices()
    return rng

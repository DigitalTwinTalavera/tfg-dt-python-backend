"""
Sample data fixtures for road network testing.
Based on Talavera de la Reina geographic coordinates.
"""

from app.models.enums import NodeType, RoadType

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

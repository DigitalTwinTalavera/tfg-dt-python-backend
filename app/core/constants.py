"""
Constantes globales de la aplicación.
Centraliza todos los valores reutilizables para evitar duplicación.
"""

# API Routes
API_PREFIX = "/api"
ROOT_PATH = "/"

# API Documentation
DOCS_URL = "/docs"
REDOC_URL = "/redoc"
OPENAPI_URL = "/openapi.json"

# API Tags
TAG_ROOT = "Root"
TAG_HEALTH = "Health Check"
TAG_WEBSOCKET = "WebSocket"

# WebSocket Routes
WS_SIMULATION_PATH = "/ws/simulation"

# Status Messages
STATUS_OK = "ok"
STATUS_RUNNING = "running"

# Environment Types
ENV_DEVELOPMENT = "development"
ENV_PRODUCTION = "production"

# HTTP Methods
CORS_ALLOW_METHODS = ["*"]
CORS_ALLOW_HEADERS = ["*"]

# Time Constants
HEALTHCHECK_INTERVAL_SECONDS = 30
HEALTHCHECK_TIMEOUT_SECONDS = 3
HEALTHCHECK_RETRIES = 3
HEALTHCHECK_START_PERIOD_SECONDS = 5

# System Messages
MSG_STARTUP_SERVER = "🚀 {app_name} v{version}"
MSG_SERVER_URL = "📡 Servidor iniciado en http://{host}:{port}"
MSG_DOCS_URL = "📚 Documentación disponible en http://{host}:{port}{docs_url}"
MSG_WS_URL = "🔌 WebSocket disponible en ws://{host}:{port}{ws_path}"
MSG_SHUTDOWN = "👋 Servidor detenido"

# WebSocket Message Types
WS_TYPE_TICK = "tick"
WS_TYPE_SIM_STATE = "sim_state"
WS_TYPE_VEHICLE_SPAWNED = "vehicle_spawned"
WS_TYPE_VEHICLE_FINISHED = "vehicle_finished"

# Response Keys
KEY_STATUS = "status"
KEY_APP = "app"
KEY_VERSION = "version"
KEY_TIMESTAMP = "timestamp"
KEY_ENVIRONMENT = "environment"
KEY_DOCS = "docs"
KEY_SYSTEM = "system"
KEY_CONFIG = "config"
KEY_DESCRIPTION = "description"
KEY_NAME = "name"
KEY_DATABASE = "database"

# Database Status Messages
MSG_DB_CONNECTED = "Database connection initialized"
MSG_DB_DISCONNECTED = "Database connection closed"

# Database Queries
SQL_HEALTH_CHECK = "SELECT 1"
SQL_POSTGIS_VERSION = "SELECT PostGIS_Version()"

# Database Connection Pool
DB_POOL_RECYCLE_SECONDS = 3600

# =============================================================================
# Road Network Constants
# =============================================================================

# Spatial Reference System (EPSG:4326 - WGS84)
SRID_WGS84 = 4326

# Coordinate Bounds (WGS84)
LONGITUDE_MIN = -180.0
LONGITUDE_MAX = 180.0
LATITUDE_MIN = -90.0
LATITUDE_MAX = 90.0

# Road Network Table Names (prefixed with dt_ to avoid PostGIS Tiger conflicts)
TABLE_NODES = "dt_nodes"
TABLE_EDGES = "dt_edges"

# Road Network Field Constraints
NODE_NAME_MAX_LENGTH = 255
EDGE_NAME_MAX_LENGTH = 255
TYPE_FIELD_MAX_LENGTH = 50

# Edge Default Values
DEFAULT_MAX_SPEED_KMH = 50
DEFAULT_LANES = 1
DEFAULT_ONE_WAY = False
DEFAULT_IS_ACTIVE = True

# Edge Validation Constraints
MIN_SPEED_KMH = 1
MAX_SPEED_KMH = 300
MIN_LANES = 1
MAX_LANES = 10
MIN_GEOMETRY_POINTS = 2

# Index Names (dt_ prefix to avoid PostGIS Tiger conflicts)
IDX_NODES_POSITION = "idx_dt_nodes_position_gist"
IDX_NODES_TYPE = "idx_dt_nodes_node_type"
IDX_NODES_ACTIVE = "idx_dt_nodes_is_active"
IDX_EDGES_GEOMETRY = "idx_dt_edges_geometry_gist"
IDX_EDGES_ROAD_TYPE = "idx_dt_edges_road_type"
IDX_EDGES_START_NODE = "idx_dt_edges_start_node_id"
IDX_EDGES_END_NODE = "idx_dt_edges_end_node_id"
IDX_EDGES_ACTIVE = "idx_dt_edges_is_active"

# API Tags for Road Network
TAG_NODES = "Nodes"
TAG_EDGES = "Edges"
TAG_ROAD_NETWORK = "Road Network"

# =============================================================================
# Vehicle Constants
# =============================================================================

# Vehicle Table Name
TABLE_VEHICLES = "dt_vehicles"

# Vehicle Field Constraints
VEHICLE_STATUS_MAX_LENGTH = 50

# Vehicle Physics Default Values
DEFAULT_VELOCITY = 0.0
DEFAULT_ACCELERATION = 0.0
DEFAULT_HEADING = 0.0

# Vehicle Physics Validation Constraints
MIN_VELOCITY = 0.0
MAX_VELOCITY = 200.0  # m/s (~720 km/h, reasonable max for simulation)
MIN_ACCELERATION = -50.0  # m/s² (hard braking)
MAX_ACCELERATION = 20.0  # m/s² (sports car acceleration)
MIN_HEADING = 0.0
MAX_HEADING = 360.0  # degrees

# Vehicle Index Names
IDX_VEHICLES_POSITION = "idx_dt_vehicles_position_gist"
IDX_VEHICLES_STATUS = "idx_dt_vehicles_status"
IDX_VEHICLES_CURRENT_EDGE = "idx_dt_vehicles_current_edge_id"
IDX_VEHICLES_UPDATED_AT = "idx_dt_vehicles_updated_at"

# API Tags for Vehicles
TAG_VEHICLES = "Vehicles"

# =============================================================================
# Repository Constants
# =============================================================================

# Pagination Defaults
DEFAULT_PAGE_LIMIT = 100
MAX_BULK_OPERATION_LIMIT = 100000

# =============================================================================
# Graph Service Constants
# =============================================================================

# Edge weight calculation (travel time in seconds)
# weight = length_meters / (max_speed_kmh * KMH_TO_MS)
KMH_TO_MS = 1000 / 3600  # Convert km/h to m/s (0.2778)

# Default values for graph edges
DEFAULT_EDGE_WEIGHT = 1.0
DEFAULT_MAX_SPEED_MS = 13.89  # 50 km/h in m/s

# Graph node/edge attribute keys
ATTR_NODE_ID = "node_id"
ATTR_LATITUDE = "lat"
ATTR_LONGITUDE = "lon"
ATTR_NODE_TYPE = "node_type"
ATTR_EDGE_ID = "edge_id"
ATTR_LENGTH = "length"
ATTR_MAX_SPEED = "max_speed"
ATTR_WEIGHT = "weight"
ATTR_ROAD_TYPE = "road_type"
ATTR_ONE_WAY = "one_way"
ATTR_WAYPOINTS = "waypoints"  # list[tuple[float,float]] — (lon, lat) pairs from LineString
ATTR_LANES = "lanes"           # número de carriles del edge (>=1)
# Semáforos intermedios del edge (no en endpoints). Detectados por coordenada
# contra los waypoints al cargar el grafo. Lista ordenada por distancia creciente:
#   list[tuple[int, float]]  →  [(tl_node_id, distance_from_start_m), ...]
ATTR_MID_TLS = "mid_tls"

# Cache settings
GRAPH_CACHE_TTL_SECONDS = 300  # 5 minutes

# Road-type routing penalty factors (multiplied on top of travel-time weight).
# Values > 1 discourage a road type; values < 1 encourage it.
# Keeps major roads preferred while still allowing minor roads when necessary.
ROAD_TYPE_WEIGHT_FACTORS: dict[str, float] = {
    "motorway":        0.6,
    "motorway_link":   0.7,
    "trunk":           0.7,
    "trunk_link":      0.8,
    "primary":         0.8,
    "primary_link":    0.9,
    "secondary":       1.0,
    "secondary_link":  1.1,
    "tertiary":        1.2,
    "tertiary_link":   1.3,
    "residential":     1.8,
    "living_street":   2.0,
    "service":         2.5,
    "unclassified":    1.5,
}

# =============================================================================
# OSM Loader Constants
# =============================================================================

# API Tags for Map Import
TAG_MAP = "Map"

# Allowed highway types for road network (relevant for vehicle traffic)
OSM_ALLOWED_HIGHWAY_TYPES: set[str] = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "residential",
    "service",
    "unclassified",
    "living_street",
}

# Highway types to exclude (pedestrian, cycling paths)
OSM_EXCLUDED_HIGHWAY_TYPES: set[str] = {
    "footway",
    "cycleway",
    "path",
    "pedestrian",
    "steps",
    "track",
    "bridleway",
    "corridor",
    "elevator",
    "escalator",
    "proposed",
    "construction",
    "raceway",
}

# Default speed limits by road type (km/h) when OSM data is missing
OSM_DEFAULT_SPEED_LIMITS: dict[str, int] = {
    "motorway": 120,
    "motorway_link": 80,
    "trunk": 100,
    "trunk_link": 60,
    "primary": 90,
    "primary_link": 50,
    "secondary": 70,
    "secondary_link": 50,
    "tertiary": 50,
    "tertiary_link": 30,
    "residential": 30,
    "service": 20,
    "unclassified": 50,
    "living_street": 20,
}

# OSM batch processing settings
OSM_BATCH_SIZE = 1000  # Nodes/edges per batch insert
OSM_PROGRESS_INTERVAL = 1000  # Report progress every N items

# OSM file extensions
OSM_XML_EXTENSIONS = {".osm", ".xml"}
OSM_PBF_EXTENSION = ".pbf"

# OSM tag keys
OSM_TAG_HIGHWAY = "highway"
OSM_TAG_MAXSPEED = "maxspeed"
OSM_TAG_ONEWAY = "oneway"
OSM_TAG_NAME = "name"
OSM_TAG_LANES = "lanes"
OSM_TAG_JUNCTION = "junction"

# One-way indicator values
OSM_ONEWAY_YES = {"yes", "true", "1"}
OSM_ONEWAY_REVERSE = {"-1", "reverse"}
OSM_ONEWAY_NO = {"no", "false", "0"}

# Highway types that are one-way by default
OSM_DEFAULT_ONEWAY_TYPES: set[str] = {"motorway", "motorway_link"}

# Roundabouts are always one-way
OSM_JUNCTION_ROUNDABOUT = "roundabout"

# Node type detection values
OSM_NODE_TRAFFIC_SIGNALS = "traffic_signals"
OSM_TAG_CROSSING = "crossing"
OSM_TAG_NOEXIT = "noexit"
OSM_VALUE_YES = "yes"

# Earth radius for Haversine distance calculation
EARTH_RADIUS_METERS = 6371000

# Data directory for OSM file imports
OSM_DATA_DIRECTORY = "data"
OSM_SUPPORTED_FORMATS = [".osm"]

# =============================================================================
# Simulation Engine Constants
# =============================================================================

# Simulation State Machine
TAG_SIMULATION = "Simulation"

# Default tick interval in milliseconds (10 ticks/s)
DEFAULT_TICK_INTERVAL_MS = 100.0

# Simulation status messages
MSG_SIMULATION_STARTED = "Simulación iniciada"
MSG_SIMULATION_STOPPED = "Simulación detenida por shutdown"

# =============================================================================
# Traffic Light Constants
# =============================================================================

# Phase durations (seconds)
TL_GREEN_SECONDS: float = 30.0
TL_YELLOW_SECONDS: float = 5.0
TL_RED_SECONDS: float = 35.0

# Phase name strings
TL_PHASE_GREEN = "green"
TL_PHASE_YELLOW = "yellow"
TL_PHASE_RED = "red"

# Broadcast TL snapshot every N simulation ticks
TL_BROADCAST_INTERVAL_TICKS = 10

# =============================================================================
# Vehicle Physics Constants
# =============================================================================

VEHICLE_LENGTH_M: float = 4.5           # bumper-to-bumper length (metres)
MAX_EMERGENCY_DECEL_MS2: float = 8.0    # physical braking cap (m/s²)
DEFAULT_VEHICLE_SPEED_KMH: float = 50.0 # default desired speed in km/h
MIN_EDGE_LENGTH_M: float = 0.1          # prevent division by zero
VEHICLE_PHYSICS_PARALLEL_THRESHOLD: int = 500
# Distancia (m) sobre la que se mezcla la tangente final de la arista saliente
# con la inicial de la entrante al cambiar de arista. Elimina el snap visible
# de heading en cruces sin curvar el movimiento más de lo necesario.
EDGE_HEADING_BLEND_DIST_M: float = 3.0
# Cada cuántos ticks se evalúa MOBIL por vehículo. Con tick=100 ms, un valor de
# 5 corresponde a 500 ms: suficiente para que un cambio de carril sea reactivo
# sin saturar CPU ni producir oscilaciones por re-evaluación inmediata.
MOBIL_EVAL_INTERVAL_TICKS: int = 5
# No re-evaluar MOBIL en los últimos metros de una arista: la transición ya
# reasigna el carril (min(lane, new_lanes-1)) y un cambio aquí sería inútil.
MOBIL_MIN_DIST_TO_EDGE_END_M: float = 15.0

# =============================================================================
# Spawn Randomisation Constants
# =============================================================================

SPAWN_SPEED_VARIANCE_MIN: float = 0.85
SPAWN_SPEED_VARIANCE_MAX: float = 1.10
SPAWN_INITIAL_VELOCITY_MIN: float = 0.0
SPAWN_INITIAL_VELOCITY_MAX: float = 0.7
SPAWN_INITIAL_PROGRESS_MIN: float = 0.0
SPAWN_INITIAL_PROGRESS_MAX: float = 0.4

# =============================================================================
# Collision Constants
# =============================================================================

COLLISION_GAP_THRESHOLD_M: float = 0.3           # gap below this triggers proximity timer
COLLISION_PROXIMITY_DURATION_S: float = 0.5      # sostenido > este tiempo → choque
COLLISION_DURATION_S: float = 300.0              # 5 minutos bloqueado tras el choque

# =============================================================================
# Traffic Light Yellow Behaviour
# =============================================================================

YELLOW_BRAKE_DISTANCE_M: float = 15.0   # if closer than this, always run yellow
YELLOW_BRAKE_PROBABILITY: float = 0.85  # 85% de vehículos frena en amarillo

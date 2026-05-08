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

# Tamaño máximo de mensaje WebSocket. Necesario porque cada tick puede llevar
# cientos de vehículos. Si se cambia, mantener en sincronía con `--ws-max-size`
# del CMD del Dockerfile.
WS_MAX_MESSAGE_SIZE: int = 4 * 1024 * 1024  # 4 MB

# Vehículos máximos por mensaje tick. Cada vehículo ocupa ~130 bytes JSON;
# 500 vehículos → ~65 KB por mensaje, holgado bajo WS_MAX_MESSAGE_SIZE.
BROADCAST_CHUNK_SIZE: int = 500

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
ATTR_IS_ROUNDABOUT = "is_roundabout"   # bool — the edge is part of a roundabout ring
ATTR_ROUNDABOUT_ID = "roundabout_id"   # int|None — identifies a connected ring component
ATTR_CURVE_VMAX = "curve_vmax"         # float m/s — cached curvature speed cap for the edge
# Set on roundabout edges that have been resampled with a centripetal
# Catmull-Rom spline. When True, vehicle_physics reads ATTR_SPLINE_SAMPLES /
# ATTR_SPLINE_LENGTH instead of running the polyline lerp.
ATTR_USE_SPLINE = "use_spline"
# list[tuple[float, float, float]] — precomputed sample table for the spline:
# each entry is (s_m, lon, lat) where s_m is cumulative arc length (haversine)
# from the start of the edge. Dense enough that bisect+lerp gives sub-mm
# error at runtime.
ATTR_SPLINE_SAMPLES = "spline_samples"
ATTR_SPLINE_LENGTH = "spline_length"   # float m — total arc length of the spline
# Radio circular del anillo (m) cacheado en cada arista de rotonda. Lo usa
# vehicle_physics._edge_curvature_vmax para imponer un cap de velocidad
# coherente con la geometría del ring entero, en vez de estimarlo con 3
# waypoints (poco fiable tras RDP).
ATTR_RING_RADIUS_M = "ring_radius_m"
# Roundabout RDP tolerance bounds. ε scales with the ring radius so small
# glorietas (Tres Olivos R≈10 m) keep more detail than large ones, but never
# exceeds 0.5 m and never collapses an edge below RDP_MIN_POINTS waypoints.
RDP_TOLERANCE_PER_RADIUS: float = 0.012
RDP_TOLERANCE_MIN_M: float = 0.20
RDP_TOLERANCE_MAX_M: float = 0.50
RDP_MIN_POINTS: int = 4
# Spline sample density. With segments shortened by RDP, 8 samples per segment
# give ≤0.5 m spacing along the curve for typical roundabout edges.
SPLINE_SAMPLES_PER_SEGMENT: int = 8

# Routing penalties
# Plan D3: exclusión efectiva de aristas bloqueadas en A*. Con el factor
# anterior (1000×) un camino alternativo de 500 s perdía frente a un atajo
# bloqueado de ~0.5 s; la cascada seguía alimentando la arista bloqueada.
# 1e9 garantiza que cualquier alternativa finita gana, manteniendo fallback
# cuando el grafo queda realmente desconectado (no se excluye del grafo).
BLOCKED_EDGE_PENALTY_FACTOR: float = 1e9

# Plan D1: cadencia del reroute proactivo periódico. Cada N ticks todos los
# vehículos MOVING re-evalúan si su ruta pendiente toca alguna arista bloqueada
# y recalculan. Compensa el hecho de que `_reroute_affected_by_new_blocks` sólo
# dispara sobre bloques recién creados — vehículos spawneados/ruteados después
# no se enteran. 50 ticks ≈ 5 s a 10 Hz.
PERIODIC_REROUTE_TICK_INTERVAL: int = 50
# Antes `_periodic_reroute_all` revisaba los N vehículos cada PERIODIC_REROUTE_TICK_INTERVAL
# ticks → con 3500+ coches esto causaba picos de 1000-1500 ms cada 5 s bloqueando el tick
# loop (visibles como tirones en el cliente). Ahora se amortiza: cada tick procesa
# PERIODIC_REROUTE_BATCH_SIZE vehículos arrancando desde un cursor rotatorio, de modo que
# todos los vehículos son visitados cada ceil(N / batch) ticks. Para 6000 vehículos
# con batch=50 → cobertura completa en 120 ticks = 24 s a 5 Hz. El on-block reroute
# sigue disparándose de forma inmediata vía `_reroute_affected_by_new_blocks`.
PERIODIC_REROUTE_BATCH_SIZE: int = 50

# Tras detectar un nuevo bloqueo (cierre, ZBE, colisión), durante una ventana
# corta el batch del reroute periódico se incrementa para cubrir la flota
# rápidamente y minimizar el tiempo en que vehículos siguen rutas inválidas.
# 200 vehículos × 50 ticks = 10000 visitas en 5 s — cubre flotas de 4000 con
# margen y mantiene los picos por tick por debajo del presupuesto (cada
# `_maybe_reroute_around_blocks` es ~0.1 ms cuando la ruta ya es válida).
URGENT_REROUTE_BATCH_SIZE: int = 200
URGENT_REROUTE_TTL_TICKS: int = 50

# Cap duro de llamadas a A* (compute_route) por tick desde el periodic batch.
# Cada A* en un grafo real puede costar 5-50 ms; sin cap, una activación de
# ZBE que afecte a 100+ vehículos genera spikes catastróficos (tick >> 200 ms,
# tirones obvios). El cap permite que el batch ESCANEE muchos vehículos baratos
# (route-intersection check) pero ABORTE más A* cuando el presupuesto se agota.
# Coverage degrada elegantemente: vehículos no servidos en este tick pasan al
# siguiente vía el cursor rotatorio.
PERIODIC_REROUTE_ASTAR_CAP_PER_TICK: int = 20

# Cache settings
GRAPH_CACHE_TTL_SECONDS = 300  # 5 minutes

# Edge index for roundabout-related column (used by migrations and models)
IDX_EDGES_ROUNDABOUT = "idx_dt_edges_roundabout_id"

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
OSM_TAG_TURN_LANES = "turn:lanes"
OSM_TAG_MAXSPEED_LANES = "maxspeed:lanes"
OSM_TAG_LANES_FORWARD = "lanes:forward"
OSM_TAG_LANES_BACKWARD = "lanes:backward"

# Sign / control nodes
OSM_NODE_STOP = "stop"
OSM_NODE_GIVE_WAY = "give_way"

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
OSM_NODE_CROSSING = "crossing"
OSM_TAG_CROSSING = "crossing"
OSM_TAG_CROSSING_SIGNALS = "crossing:signals"
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
# Events & Zones Constants (Módulo 4)
# =============================================================================

# Duraciones por defecto (s) para cada tipo de incidente. None = permanente
# (retirada manual por API). Los accidentes detectados automáticamente son
# permanentes: simulan una intervención de emergencia que el operador cierra.
INCIDENT_DEFAULT_DURATION_S: dict[str, float | None] = {
    "accident": None,
    "roadwork": 3600.0,   # 1 hora
    "breakdown": 600.0,   # 10 minutos
    "event": 1800.0,      # 30 minutos
}

# Factor de penalización aplicado en A* a las aristas dentro de una ZBE con
# enforcement=force_reroute para los tipos de vehículo restringidos. Elevado
# para que cualquier alternativa razonable gane, pero no ∞: si no hay
# alternativa (isleño) el coche entra igualmente.
ZBE_EDGE_PENALTY_FACTOR: float = 50.0

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
# Cada cuántos ticks se evalúa MOBIL por vehículo. Con tick=200 ms (5 Hz), un
# valor de 20 corresponde a 4 s: suficiente para que un cambio de carril sea
# reactivo sin saturar CPU. MOBIL corre en el main thread (workers no tienen
# edge_index completo), así que bajar su frecuencia es el mayor win CPU-side
# con miles de vehículos. 4 s es coherente con el tiempo de decisión humano
# para un cambio de carril discrecional en tráfico medio.
MOBIL_EVAL_INTERVAL_TICKS: int = 20
# Velocidad mínima por debajo de la cual saltamos la evaluación de MOBIL. Un
# vehículo casi parado no tiene incentivo IDM para cambiar de carril (la
# ganancia de aceleración es despreciable), así que el coste de construir el
# contexto y llamar al modelo es puro waste. Con 4000-6000 vehículos en ciudad
# gran parte está parada o rodando a <10 km/h en cada tick.
MOBIL_MIN_VELOCITY_MS: float = 3.0
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

# Legacy straight-road threshold (kept as alias for backward compat).
COLLISION_GAP_THRESHOLD_M: float = 0.3
# Umbrales por contexto: en curvas de rotonda los vehículos se rozan más por la
# discretización de waypoints; tolerar gap mayor antes de declarar choque.
COLLISION_GAP_THRESHOLD_STRAIGHT_M: float = 0.3
COLLISION_GAP_THRESHOLD_ROUNDABOUT_M: float = 0.8
COLLISION_PROXIMITY_DURATION_S: float = 1.0      # sostenido > este tiempo → choque
# Velocidad relativa mínima para disparar colisión. Evita que dos vehículos
# parados juntos (p. ej. en cola de semáforo) se marquen como choque.
COLLISION_RELATIVE_SPEED_MIN_MS: float = 1.0

# =============================================================================
# Emergency Brake (Plan C)
# =============================================================================

# Ventana ampliada de freno de emergencia ante cola densa: si el líder está
# casi parado a distancia corta y el ego va significativamente más rápido,
# se fuerza decel=MAX_EMERGENCY_DECEL_MS2 (el IDM puro puede quedarse corto
# cuando el gap cae más rápido que un tick de 100 ms).
EMERGENCY_BRAKE_LEADER_V_MAX_MS: float = 2.0   # líder a < 2 m/s ⇒ potencial cola
EMERGENCY_BRAKE_GAP_MAX_M: float = 12.0        # dentro de 12 m
EMERGENCY_BRAKE_EGO_V_DELTA_MS: float = 2.0    # ego ≥ 2 m/s más rápido que líder

# =============================================================================
# Auto-Reroute on Block (Plan C)
# =============================================================================

# Cuando una colisión marca una arista como bloqueada, todos los vehículos
# MOVING cuya ruta pendiente atraviese esa arista se re-rutean inmediatamente.
# Evita el efecto cascada (coches rutados antes del bloqueo que siguen
# avanzando hacia la pared de parados).
REROUTE_ON_BLOCK_ENABLED: bool = True

# =============================================================================
# MOBIL Safety Floor (Plan C)
# =============================================================================

# Suelo absoluto de gap en carril destino para que MOBIL acepte el cambio.
# Complementa al criterio IDM (b_safe): en `_build_lane_context` cualquier
# gap negativo se satura a 0.01 m, haciendo que el IDM pueda evaluar un
# `accel_new_follower` engañosamente tratable cuando el "back" tiene v=0.
# Con este suelo, cambios sobre vehículos físicamente solapados se rechazan
# de plano sin depender de la aritmética de IDM.
MOBIL_MIN_SAFE_GAP_M: float = 3.0

# =============================================================================
# Roundabout Yield Constants (Fase 2)
# =============================================================================

# Distancia desde la línea de entrada a la rotonda en la que el vehículo empieza
# a mirar hacia dentro del anillo para ceder el paso. 30 m permite frenar
# cómodamente desde 50 km/h (v²/2b ≈ 32 m con b=3) sin entrar en pánico al
# borde mismo. Alineada con LOOKAHEAD_ENTRY_TRIGGER_M.
YIELD_DETECTION_ZONE_M: float = 30.0
# Time-to-conflict: si un vehículo circulando llega antes de este tiempo a la
# entrada del ego, el ego debe ceder. 3.0 s es un margen realista — un coche
# del anillo a velocidad típica (4-5 m/s) será conflicto si está dentro de
# 12-15 m de la línea de entrada.
YIELD_TTC_THRESHOLD_S: float = 3.0
# Gap mínimo en arco (m) dentro del anillo para aceptar la entrada. Equivale
# a la holgura mínima requerida con un coche del anillo aunque esté parado
# o muy lento (TTC alto): si está físicamente más cerca de 8 m del entry
# node, hay conflicto y se cede.
YIELD_GAP_MIN_M: float = 8.0
# Gap mínimo (m) que `_find_leader` reporta cuando el ego está en la última
# arista del anillo y el líder está en la arista de salida (ring → non-ring).
# Sin clamp, una cola en el borde de la salida con v_lead=0 lleva el IDM a
# v=0 dentro del ring (viola el principio "ceder fuera, no dentro"). Con 8 m
# el IDM frena gradualmente sin parar, y al cruzar al exit el `_find_leader`
# normal toma el control y para correctamente fuera del ring.
RING_EXIT_MIN_GAP_M: float = 8.0
# Velocidad mínima (m/s ≈ 5 km/h) que `_find_leader` reporta del líder en la
# salida del anillo. Evita que el IDM calcule s* infinito por v_lead=0 con
# Δv grande, lo que produce frenado catastrófico.
RING_EXIT_MIN_LEADER_V_MS: float = 1.5
# Distancia restante (m) en la arista actual por debajo de la cual se activa
# el look-ahead cross-edge cuando la siguiente arista es anillo de rotonda.
# Cubre ~5 ticks a 50 km/h (13.9 m/s · 0.1 s ≈ 1.39 m), evitando que entradas
# cortas salten el gate fraccional del 70% en un único tick.
LOOKAHEAD_ROUNDABOUT_TRIGGER_M: float = 8.0
# Ventana extendida de look-ahead cuando la arista actual NO es anillo pero la
# siguiente SÍ: el ego encola ante la línea de ceda y puede tener parado al
# líder del siguiente arco (o una cola en el propio anillo). 30 m cubre la
# distancia de frenado IDM desde 50 km/h con b≈2.5 (v²/2b ≈ 34 m) con margen.
LOOKAHEAD_ENTRY_TRIGGER_M: float = 30.0
# Distancia (m) antes del nodo de entrada en la que se activa la arbitración
# entre brazos convergentes. Alineada con YIELD_DETECTION_ZONE_M.
ENTRY_ARBITRATION_ZONE_M: float = 15.0

# =============================================================================
# Curvature Speed Cap (Fase 4)
# =============================================================================

CURVE_LATERAL_ACCEL_MAX_MS2: float = 2.5   # confort ≈ 0.25 g para coche de turismo
MIN_ROUNDABOUT_RADIUS_M: float = 6.0       # radio mínimo asumido (rotonda muy pequeña)

# =============================================================================
# Spawn Coordination (Fase 5)
# =============================================================================

# Máx. densidad de vehículos dentro de un anillo (veh por cada 100 m de recorrido).
ROUNDABOUT_SATURATION_VEH_PER_100M: float = 8.0
# Nº máximo de vehículos que pueden estrenarse por rotonda dentro del mismo batch.
SPAWN_MAX_ENTRIES_PER_ROUNDABOUT_PER_TICK: int = 1

# =============================================================================
# Dynamic Routing (Fase 6)
# =============================================================================

DYNAMIC_WEIGHTS_TICK_INTERVAL: int = 20   # recalcular pesos dinámicos cada N ticks
DYNAMIC_WEIGHT_MAX_MULT: float = 5.0       # multiplicador máximo por saturación
DYNAMIC_WEIGHT_CHANGE_THRESHOLD: float = 0.2  # invalidar cache si un peso cambia >20 %

# =============================================================================
# STOP/YIELD Sign Runtime (Fase 7.1)
# =============================================================================

# Zona desde la línea de stop en la que el vehículo empieza a considerar la
# señal. Fuera de esta distancia no se genera líder virtual.
SIGN_DETECTION_ZONE_M: float = 20.0
# STOP: velocidad máxima considerada "parado" durante el dwell obligatorio.
STOP_SIGN_DWELL_SPEED_MS: float = 0.5
# STOP: tiempo mínimo (s) a baja velocidad para dar por cumplida la parada.
STOP_SIGN_DWELL_TIME_S: float = 1.0
# YIELD: gap mínimo (m) en la arista convergente para aceptar el cruce.
YIELD_SIGN_GAP_MIN_M: float = 8.0
# YIELD: TTC umbral (s) en la arista convergente.
YIELD_SIGN_TTC_S: float = 3.0

# =============================================================================
# Heading Blend (Fase 7)
# =============================================================================

# Distancia de blending cuando la arista previa era rotonda — necesita ser
# mayor que el blend estándar para que la salida curve suavemente.
EDGE_HEADING_BLEND_DIST_ROUNDABOUT_EXIT_M: float = 12.0

# =============================================================================
# Traffic Light Yellow Behaviour
# =============================================================================

YELLOW_BRAKE_DISTANCE_M: float = 15.0   # if closer than this, always run yellow
YELLOW_BRAKE_PROBABILITY: float = 0.85  # 85% de vehículos frena en amarillo

# Road Network Data Models (Nodes & Edges)

## Overview

This document describes the road network data models implemented for the Digital Twin Traffic Backend. The models represent the road network as a directed graph where:

- **Nodes**: Represent intersections, traffic lights, roundabouts, entry/exit points
- **Edges**: Represent road segments connecting nodes

## Entity-Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                            DT_NODES                                 │
├─────────────────────────────────────────────────────────────────────┤
│ PK  id              INTEGER        AUTO_INCREMENT                   │
│     name            VARCHAR(255)   NULLABLE                         │
│     node_type       VARCHAR(50)    NOT NULL (NodeType enum)         │
│     position        POINT(4326)    NOT NULL (PostGIS)               │
│     is_active       BOOLEAN        NOT NULL DEFAULT TRUE            │
│     metadata_json   TEXT           NULLABLE                         │
│     created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()           │
│     updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()           │
├─────────────────────────────────────────────────────────────────────┤
│ INDEXES:                                                            │
│   - idx_dt_nodes_position_gist (GIST on position)                   │
│   - idx_dt_nodes_node_type (BTREE on node_type)                     │
│   - idx_dt_nodes_is_active (BTREE on is_active)                     │
└─────────────────────────────────────────────────────────────────────┘
                          │
                          │ 1:N (start_node)
                          │ 1:N (end_node)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            DT_EDGES                                 │
├─────────────────────────────────────────────────────────────────────┤
│ PK  id              INTEGER        AUTO_INCREMENT                   │
│     name            VARCHAR(255)   NULLABLE                         │
│ FK  start_node_id   INTEGER        NOT NULL → dt_nodes.id CASCADE   │
│ FK  end_node_id     INTEGER        NOT NULL → dt_nodes.id CASCADE   │
│     road_type       VARCHAR(50)    NOT NULL (RoadType enum)         │
│     geometry        LINESTRING(4326) NOT NULL (PostGIS)             │
│     length          FLOAT          NOT NULL                         │
│     max_speed       INTEGER        NOT NULL DEFAULT 50              │
│     lanes           INTEGER        NOT NULL DEFAULT 1               │
│     one_way         BOOLEAN        NOT NULL DEFAULT FALSE           │
│     is_active       BOOLEAN        NOT NULL DEFAULT TRUE            │
│     metadata_json   TEXT           NULLABLE                         │
│     created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()           │
│     updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()           │
├─────────────────────────────────────────────────────────────────────┤
│ INDEXES:                                                            │
│   - idx_dt_edges_geometry_gist (GIST on geometry)                   │
│   - idx_dt_edges_road_type (BTREE on road_type)                     │
│   - idx_dt_edges_start_node_id (BTREE on start_node_id)             │
│   - idx_dt_edges_end_node_id (BTREE on end_node_id)                 │
│   - idx_dt_edges_is_active (BTREE on is_active)                     │
└─────────────────────────────────────────────────────────────────────┘
```

> **Note**: Table names use the `dt_` prefix to avoid conflicts with PostGIS Tiger geocoder tables (`tiger.edges`, `tiger.nodes`).

## Enums

### NodeType

Defines the types of nodes in the road network:

| Value | Description |
|-------|-------------|
| `intersection` | Standard road intersection |
| `endpoint` | Generic endpoint (start/end of road) |
| `traffic_light` | Intersection with traffic signals |
| `roundabout` | Circular intersection |
| `dead_end` | End of road with no exit |
| `entry_point` | Entry point to the simulation area |
| `exit_point` | Exit point from the simulation area |

### RoadType

Defines the types of roads/edges:

| Value | Description |
|-------|-------------|
| `motorway` | Major highway/motorway |
| `primary` | Primary road (main arterial) |
| `secondary` | Secondary road |
| `tertiary` | Tertiary road |
| `residential` | Residential street |
| `service` | Service road |
| `pedestrian` | Pedestrian-only path |
| `cycleway` | Bicycle path |

## PostGIS Geometry Types

### SRID 4326 (WGS84)

All geometries use EPSG:4326 (WGS84) coordinate reference system:

- **Node.position**: `POINT` - Single geographic point (longitude, latitude)
- **Edge.geometry**: `LINESTRING` - Sequence of connected points forming the road path

### Spatial Indexes

GIST indexes are created on geometry columns for efficient spatial queries:

```sql
-- Find nodes within a bounding box
SELECT * FROM dt_nodes
WHERE ST_Within(position, ST_MakeEnvelope(-4.85, 39.95, -4.80, 40.00, 4326));

-- Find edges intersecting an area
SELECT * FROM dt_edges
WHERE ST_Intersects(geometry, ST_MakeEnvelope(-4.85, 39.95, -4.80, 40.00, 4326));
```

## Constants

All magic numbers and repeated values are centralized in `app/core/constants.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `SRID_WGS84` | 4326 | Spatial Reference System ID |
| `TABLE_NODES` | "dt_nodes" | Nodes table name |
| `TABLE_EDGES` | "dt_edges" | Edges table name |
| `NODE_NAME_MAX_LENGTH` | 255 | Max chars for node name |
| `EDGE_NAME_MAX_LENGTH` | 255 | Max chars for edge name |
| `TYPE_FIELD_MAX_LENGTH` | 50 | Max chars for type fields |
| `DEFAULT_MAX_SPEED_KMH` | 50 | Default speed limit |
| `DEFAULT_LANES` | 1 | Default number of lanes |
| `DEFAULT_ONE_WAY` | False | Default one-way value |
| `DEFAULT_IS_ACTIVE` | True | Default active status |
| `MIN_SPEED_KMH` | 1 | Minimum speed validation |
| `MAX_SPEED_KMH` | 300 | Maximum speed validation |
| `MIN_LANES` | 1 | Minimum lanes validation |
| `MAX_LANES` | 10 | Maximum lanes validation |
| `MIN_GEOMETRY_POINTS` | 2 | Minimum points in LineString |

## File Structure

```
app/
├── core/
│   ├── constants.py            # Global constants (SRID, table names, defaults)
│   └── schemas/
│       ├── __init__.py         # Schema exports
│       └── network_schema.py   # Pydantic schemas for API validation
├── models/
│   ├── __init__.py             # Exports NodeModel, EdgeModel, NodeType, RoadType
│   ├── enums.py                # NodeType and RoadType enums
│   └── road_network.py         # SQLAlchemy models (NodeModel, EdgeModel)
└── db/
    └── database.py             # Base class for models

alembic/
└── versions/
    └── 001_create_road_network_tables.py  # Migration file

tests/
├── fixtures/
│   ├── __init__.py
│   └── road_network_fixtures.py  # Sample data for testing
└── test_road_network_models.py   # Unit tests
```

## Pydantic Schemas

### Node Schemas

| Schema | Purpose |
|--------|---------|
| `NodeCreate` | Create new node (longitude, latitude required) |
| `NodeUpdate` | Update existing node (all fields optional) |
| `NodeResponse` | API response with full node data |

### Edge Schemas

| Schema | Purpose |
|--------|---------|
| `EdgeCreate` | Create new edge (start/end nodes, geometry required) |
| `EdgeUpdate` | Update existing edge (all fields optional) |
| `EdgeResponse` | API response with full edge data |

### Coordinate Schema

```python
from app.core.constants import LONGITUDE_MIN, LONGITUDE_MAX, LATITUDE_MIN, LATITUDE_MAX

class Coordinate(BaseModel):
    longitude: float  # LONGITUDE_MIN (-180) to LONGITUDE_MAX (180)
    latitude: float   # LATITUDE_MIN (-90) to LATITUDE_MAX (90)
```

## Usage Examples

### Creating a Node

```python
from app.core.schemas.network_schema import NodeCreate
from app.models.enums import NodeType

node_data = NodeCreate(
    name="Plaza del Pan",
    node_type=NodeType.INTERSECTION,
    longitude=-4.8306,
    latitude=39.9634,
    is_active=True
)
```

### Creating an Edge

```python
from app.core.schemas.network_schema import EdgeCreate, Coordinate
from app.models.enums import RoadType

edge_data = EdgeCreate(
    name="Calle Real",
    start_node_id=1,
    end_node_id=2,
    road_type=RoadType.PRIMARY,
    geometry_coordinates=[
        Coordinate(longitude=-4.8306, latitude=39.9634),
        Coordinate(longitude=-4.8320, latitude=39.9640),
    ],
    length=150.0,
    max_speed=50,
    lanes=2
)
```

## Database Migration

Run the migration to create the tables:

```bash
# Ensure PostgreSQL with PostGIS is running
docker-compose up -d postgres

# Run migration
alembic upgrade head
```

Verify tables were created:

```sql
-- Check tables exist
\dt dt_nodes
\dt dt_edges

-- Check PostGIS indexes
\di idx_dt_nodes_position_gist
\di idx_dt_edges_geometry_gist
```

## Testing

Run the unit tests:

```bash
# Run all road network tests
pytest tests/test_road_network_models.py -v

# Run only unit tests
pytest tests/test_road_network_models.py -v -m unit

# Run full test suite
pytest -v
```

### Test Coverage

| Test Class | Tests | Description |
|------------|-------|-------------|
| `TestNodeTypeEnum` | 2 | Enum values and string type |
| `TestRoadTypeEnum` | 2 | Enum values and string type |
| `TestCoordinateSchema` | 3 | Coordinate validation bounds |
| `TestNodeSchemas` | 7 | Node create/update/response |
| `TestEdgeSchemas` | 7 | Edge create/update/response |
| `TestSampleFixtures` | 6 | Sample data helpers |
| **Total** | **27** | |

## Sample Data

The fixtures module provides sample data based on Talavera de la Reina coordinates:

- 6 sample nodes (intersections, traffic lights, roundabouts, entry/exit points)
- 4 sample edges connecting the nodes

```python
from tests.fixtures.road_network_fixtures import (
    SAMPLE_NODES,
    SAMPLE_EDGES,
    create_sample_node_data,
    create_sample_edge_data,
    TALAVERA_CENTER_LON,
    TALAVERA_CENTER_LAT,
)

# Use in tests
for node_data in SAMPLE_NODES:
    node = NodeCreate(**node_data)

# Create custom test data
custom_node = create_sample_node_data(
    name="Custom Node",
    node_type=NodeType.ROUNDABOUT.value,
    longitude=-4.8200,
    latitude=39.9700,
)
```

## Validation Rules

### Node Validation

| Field | Rule |
|-------|------|
| `longitude` | -180.0 to 180.0 |
| `latitude` | -90.0 to 90.0 |
| `name` | Max 255 characters |
| `node_type` | Must be valid NodeType enum value |

### Edge Validation

| Field | Rule |
|-------|------|
| `geometry_coordinates` | Minimum 2 points |
| `length` | Must be > 0 |
| `max_speed` | 1 to 300 km/h |
| `lanes` | 1 to 10 |
| `road_type` | Must be valid RoadType enum value |

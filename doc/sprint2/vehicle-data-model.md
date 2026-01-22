# Vehicle Data Model & Manager

## Overview

This document describes the vehicle data model and manager service for the Digital Twin Traffic simulation. Vehicles are the primary entities that move through the road network, with physics properties and navigation state.

## Entity-Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          DT_VEHICLES                                │
├─────────────────────────────────────────────────────────────────────┤
│ PK  id                UUID           NOT NULL (auto-generated)      │
│     position          POINT(4326)    NOT NULL (PostGIS)             │
│     velocity          FLOAT          NOT NULL DEFAULT 0.0           │
│     acceleration      FLOAT          NOT NULL DEFAULT 0.0           │
│     heading           FLOAT          NOT NULL DEFAULT 0.0           │
│     status            VARCHAR(50)    NOT NULL DEFAULT 'idle'        │
│ FK  current_edge_id   INTEGER        NULLABLE → dt_edges.id         │
│     route_edges       JSONB          NULLABLE                       │
│     created_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW()         │
│     updated_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW()         │
├─────────────────────────────────────────────────────────────────────┤
│ INDEXES:                                                            │
│   - idx_dt_vehicles_position_gist (GIST on position)                │
│   - idx_dt_vehicles_status (BTREE on status)                        │
│   - idx_dt_vehicles_current_edge_id (BTREE on current_edge_id)      │
│   - idx_dt_vehicles_updated_at (BTREE on updated_at)                │
└─────────────────────────────────────────────────────────────────────┘
                          │
                          │ N:1 (current_edge)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          DT_EDGES                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## VehicleStatus Enum

Defines the lifecycle states of a vehicle in the simulation:

| Value | Description |
|-------|-------------|
| `idle` | Vehicle is not moving, waiting to start |
| `moving` | Vehicle is actively moving through the network |
| `stopped` | Vehicle has stopped (e.g., at traffic light) |
| `waiting` | Vehicle is waiting (e.g., in queue) |
| `finished` | Vehicle has completed its route |

## Constants

All vehicle-related constants are centralized in `app/core/constants.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `TABLE_VEHICLES` | "dt_vehicles" | Table name |
| `VEHICLE_STATUS_MAX_LENGTH` | 50 | Max chars for status field |
| `DEFAULT_VELOCITY` | 0.0 | Default velocity (m/s) |
| `DEFAULT_ACCELERATION` | 0.0 | Default acceleration (m/s²) |
| `DEFAULT_HEADING` | 0.0 | Default heading (degrees) |
| `MIN_VELOCITY` | 0.0 | Minimum velocity |
| `MAX_VELOCITY` | 200.0 | Maximum velocity (~720 km/h) |
| `MIN_ACCELERATION` | -50.0 | Maximum deceleration (hard braking) |
| `MAX_ACCELERATION` | 20.0 | Maximum acceleration (sports car) |
| `MIN_HEADING` | 0.0 | Minimum heading |
| `MAX_HEADING` | 360.0 | Maximum heading (exclusive) |

## File Structure

```
app/
├── core/
│   ├── constants.py              # Vehicle constants
│   └── schemas/
│       ├── __init__.py
│       └── vehicle_schema.py     # Pydantic schemas
├── models/
│   ├── __init__.py
│   ├── enums.py                  # VehicleStatus enum
│   └── vehicle.py                # VehicleModel SQLAlchemy model
└── services/
    ├── __init__.py
    └── vehicle_manager.py        # VehicleManager service class

alembic/
└── versions/
    └── 002_create_vehicles_table.py  # Migration file

tests/
├── test_vehicle_models.py        # Unit tests (25 tests)
└── test_vehicle_integration.py   # Integration tests (12 tests)
```

## Pydantic Schemas

### VehicleCreate

Schema for creating a new vehicle:

```python
from app.core.schemas.vehicle_schema import VehicleCreate
from app.models.enums import VehicleStatus

vehicle = VehicleCreate(
    longitude=-4.8306,
    latitude=39.9634,
    velocity=15.0,
    acceleration=1.0,
    heading=90.0,
    status=VehicleStatus.MOVING,
    route_edges=[1, 2, 3, 4],
)
```

### VehicleStateUpdate

Schema optimized for high-frequency position updates during simulation:

```python
from app.core.schemas.vehicle_schema import VehicleStateUpdate

update = VehicleStateUpdate(
    longitude=-4.8400,
    latitude=39.9700,
    velocity=20.0,
    heading=180.0,
)
```

### VehicleUpdate

Schema for general vehicle updates (all fields optional):

```python
from app.core.schemas.vehicle_schema import VehicleUpdate

update = VehicleUpdate(
    status=VehicleStatus.STOPPED,
    velocity=0.0,
)
```

### VehicleResponse

Schema for API responses with full vehicle data.

## VehicleManager Service

The `VehicleManager` class provides all vehicle operations:

### Usage

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.vehicle_manager import VehicleManager
from app.core.schemas.vehicle_schema import VehicleCreate, VehicleStateUpdate
from app.models.enums import VehicleStatus

# Initialize manager with database session
manager = VehicleManager(session)

# Add a new vehicle
vehicle_data = VehicleCreate(longitude=-4.8306, latitude=39.9634)
vehicle = await manager.add_vehicle(vehicle_data)

# Update position (high-frequency operation)
new_position = VehicleStateUpdate(
    longitude=-4.8400,
    latitude=39.9700,
    velocity=20.0,
)
await manager.update_position(vehicle.id, new_position)

# Get all active vehicles (excludes FINISHED status)
active = await manager.get_all_active_vehicles()

# Update vehicle status
await manager.update_vehicle_status(vehicle.id, VehicleStatus.STOPPED)

# Update vehicle route
await manager.update_vehicle_route(vehicle.id, route_edges=[1, 2, 3])

# Remove vehicle
await manager.remove_vehicle(vehicle.id)
```

### Available Methods

| Method | Description |
|--------|-------------|
| `add_vehicle(vehicle_data)` | Add a new vehicle to the simulation |
| `get_vehicle_by_id(vehicle_id)` | Get a vehicle by UUID |
| `update_position(vehicle_id, new_position)` | Update vehicle position (optimized) |
| `get_all_active_vehicles()` | Get all non-FINISHED vehicles |
| `remove_vehicle(vehicle_id)` | Remove a vehicle |
| `update_vehicle_status(vehicle_id, new_status)` | Update vehicle status |
| `update_vehicle_route(vehicle_id, route_edges)` | Update vehicle route |
| `get_vehicles_on_edge(edge_id)` | Get all vehicles on a specific edge |
| `count_active_vehicles()` | Count active vehicles |

## Route Storage

The `route_edges` field is a PostgreSQL JSONB column storing an ordered list of edge IDs:

```python
# Example route
route_edges = [1, 5, 12, 8, 3]  # Edge IDs in order
```

### JSONB Queries

```sql
-- Find vehicles with a specific edge in their route
SELECT * FROM dt_vehicles
WHERE route_edges @> '[5]';

-- Find vehicles whose route starts with edge 1
SELECT * FROM dt_vehicles
WHERE route_edges->0 = '1';
```

## Performance Considerations

### High-Frequency Position Updates

The `update_position` method is optimized for frequent updates during simulation:

- Uses minimal database operations
- Only updates changed fields
- Index on `updated_at` enables efficient "recent vehicles" queries

### Batching (Future Enhancement)

For very high vehicle counts, consider batching position updates:

```python
# Future implementation concept
async def batch_update_positions(updates: list[tuple[UUID, VehicleStateUpdate]]):
    # Bulk update for performance
    pass
```

### Spatial Queries

The GIST index on `position` enables efficient spatial queries:

```sql
-- Find vehicles within a bounding box
SELECT * FROM dt_vehicles
WHERE ST_Within(position, ST_MakeEnvelope(-4.85, 39.95, -4.80, 40.00, 4326));

-- Find vehicles near a point (within 100 meters)
SELECT * FROM dt_vehicles
WHERE ST_DWithin(
    position::geography,
    ST_MakePoint(-4.8306, 39.9634)::geography,
    100
);
```

## Database Migration

Run the migration to create the vehicles table:

```bash
# Ensure PostgreSQL with PostGIS is running
docker-compose up -d postgres

# Run migrations
alembic upgrade head
```

Verify table was created:

```sql
\d dt_vehicles
```

## Testing

### Unit Tests (25 tests)

```bash
pytest tests/test_vehicle_models.py -v
```

| Test Class | Tests | Description |
|------------|-------|-------------|
| `TestVehicleStatusEnum` | 2 | Enum values and string type |
| `TestVehicleCreateSchema` | 12 | Create schema validation |
| `TestVehicleUpdateSchema` | 3 | Update schema validation |
| `TestVehicleStateUpdateSchema` | 3 | State update validation |
| `TestVehicleResponseSchema` | 1 | Response schema |
| `TestVehicleFixtures` | 4 | Constants and bounds |

### Integration Tests (12 tests)

```bash
pytest tests/test_vehicle_integration.py -v
```

Tests actual database operations with VehicleManager:
- Add vehicle
- Get vehicle by ID
- Update position
- Get active vehicles
- Remove vehicle
- Update status
- Update route
- Count vehicles

## Validation Rules

### Position Validation

| Field | Rule |
|-------|------|
| `longitude` | -180.0 to 180.0 |
| `latitude` | -90.0 to 90.0 |

### Physics Validation

| Field | Rule |
|-------|------|
| `velocity` | 0.0 to 200.0 m/s |
| `acceleration` | -50.0 to 20.0 m/s² |
| `heading` | 0.0 to 360.0 degrees (exclusive) |

### Status Transitions

Recommended status transitions:

```
IDLE → MOVING → STOPPED ⇄ WAITING
                  ↓
               FINISHED
```

The system doesn't enforce transitions; validation is application-level.

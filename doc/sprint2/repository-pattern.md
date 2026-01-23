# Repository Pattern for Database Operations

## Overview

This document describes the repository pattern implementation for the Digital Twin traffic simulation backend. The pattern provides an abstraction layer between services and the database, promoting clean architecture and testability.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   API Routers   │────▶│   Repositories  │────▶│    Database     │
│   (FastAPI)     │     │   (Abstraction) │     │  (PostgreSQL)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                      │
         │                      │
         ▼                      ▼
┌─────────────────┐     ┌─────────────────┐
│   Dependencies  │     │   Transaction   │
│   (Injection)   │     │   Management    │
└─────────────────┘     └─────────────────┘
```

## Components

### 1. Base Repository (`app/db/repositories/base.py`)

Generic repository providing CRUD operations for any SQLAlchemy model.

**Methods:**
| Method | Description |
|--------|-------------|
| `create(entity)` | Create a new entity |
| `get(entity_id)` | Get entity by ID |
| `update(entity_id, values)` | Update entity fields |
| `delete(entity_id)` | Delete entity by ID |
| `list(skip, limit, filters)` | List entities with pagination |
| `count(filters)` | Count entities |
| `exists(entity_id)` | Check if entity exists |
| `bulk_create(entities)` | Create multiple entities |
| `bulk_update(entity_ids, values)` | Update multiple entities |
| `bulk_delete(entity_ids)` | Delete multiple entities |

**Usage:**
```python
from app.db.repositories.base import BaseRepository
from app.models.road_network import NodeModel

class NodeRepository(BaseRepository[NodeModel]):
    pass
```

### 2. Node Repository (`app/db/repositories/node_repository.py`)

Specialized repository for node operations with spatial query support.

**Spatial Methods:**
| Method | Description |
|--------|-------------|
| `find_within_radius(lon, lat, radius)` | Find nodes within radius (meters) |
| `find_nearest(lon, lat, limit)` | Find nearest nodes to a point |
| `get_with_edges(node_id)` | Get node with edges eagerly loaded |
| `get_connected_nodes(node_id)` | Get directly connected nodes |

**Example:**
```python
from app.db.repositories.node_repository import NodeRepository

async def get_nearby_intersections(session, lon, lat):
    repo = NodeRepository(session)
    return await repo.find_within_radius(
        longitude=lon,
        latitude=lat,
        radius_meters=500,
        node_type=NodeType.INTERSECTION
    )
```

### 3. Edge Repository (`app/db/repositories/edge_repository.py`)

Specialized repository for edge operations with node-based queries.

**Node-Based Methods:**
| Method | Description |
|--------|-------------|
| `find_by_start_node(node_id)` | Find edges starting from a node |
| `find_by_end_node(node_id)` | Find edges ending at a node |
| `find_by_nodes(start_id, end_id)` | Find edge between two nodes |
| `find_connected_to_node(node_id)` | Find all edges connected to a node |

**Spatial Methods:**
| Method | Description |
|--------|-------------|
| `find_in_bounding_box(min_lon, min_lat, max_lon, max_lat)` | Find edges in bounding box |
| `find_near_point(lon, lat, radius)` | Find edges near a point |

**Example:**
```python
from app.db.repositories.edge_repository import EdgeRepository

async def get_roads_in_area(session, bbox):
    repo = EdgeRepository(session)
    return await repo.find_in_bounding_box(
        min_lon=bbox.west,
        min_lat=bbox.south,
        max_lon=bbox.east,
        max_lat=bbox.north
    )
```

### 4. Vehicle Repository (`app/db/repositories/vehicle_repository.py`)

Specialized repository for vehicle operations with bulk update support.

**Query Methods:**
| Method | Description |
|--------|-------------|
| `get_by_status(status)` | Get vehicles by status |
| `get_active_vehicles()` | Get non-finished vehicles |
| `get_moving_vehicles()` | Get currently moving vehicles |
| `get_vehicles_on_edge(edge_id)` | Get vehicles on a specific edge |

**Bulk Operations:**
| Method | Description |
|--------|-------------|
| `bulk_update_positions(updates)` | Update multiple vehicle positions |
| `bulk_update_status(vehicle_ids, status)` | Update status of multiple vehicles |
| `mark_finished(vehicle_ids)` | Mark vehicles as finished |
| `delete_finished_vehicles()` | Remove all finished vehicles |

**Example:**
```python
from app.db.repositories.vehicle_repository import VehicleRepository

async def update_simulation_step(session, position_updates):
    repo = VehicleRepository(session)

    # Bulk update all vehicle positions
    updated = await repo.bulk_update_positions(position_updates)

    # Mark finished vehicles
    finished_ids = [v["vehicle_id"] for v in position_updates if v["finished"]]
    await repo.mark_finished(finished_ids)

    return updated
```

## Dependency Injection

### Setup (`app/db/dependencies.py`)

FastAPI dependency functions for injecting repositories into endpoints.

**Available Dependencies:**
```python
from app.db.dependencies import (
    NodeRepositoryDep,
    EdgeRepositoryDep,
    VehicleRepositoryDep,
)
```

**Usage in Endpoints:**
```python
from fastapi import APIRouter
from app.db.dependencies import NodeRepositoryDep

router = APIRouter()

@router.get("/nodes/{node_id}")
async def get_node(node_id: int, repo: NodeRepositoryDep):
    return await repo.get(node_id)
```

## Transaction Management

### Context Managers (`app/db/transaction.py`)

#### `transaction()`
Automatic commit/rollback management:
```python
from app.db.transaction import transaction

async with transaction() as session:
    repo = NodeRepository(session)
    node = await repo.create(new_node)
    # Commits automatically on exit
    # Rolls back on exception
```

#### `read_only_transaction()`
For queries that should not modify the database:
```python
from app.db.transaction import read_only_transaction

async with read_only_transaction() as session:
    repo = NodeRepository(session)
    nodes = await repo.list()
    # Always rolls back on exit
```

### Unit of Work Pattern

Coordinate multiple repositories in a single transaction:

```python
from app.db.transaction import UnitOfWork

async with UnitOfWork() as uow:
    # Create a node
    node = await uow.nodes.create(new_node)

    # Create an edge referencing the node
    edge = EdgeModel(start_node_id=node.id, ...)
    await uow.edges.create(edge)

    # Create a vehicle on the edge
    vehicle = VehicleModel(current_edge_id=edge.id, ...)
    await uow.vehicles.create(vehicle)

    # Commit all changes atomically
    await uow.commit()
```

**UnitOfWork Properties:**
| Property | Description |
|----------|-------------|
| `uow.nodes` | NodeRepository instance |
| `uow.edges` | EdgeRepository instance |
| `uow.vehicles` | VehicleRepository instance |
| `uow.session` | Underlying AsyncSession |

**UnitOfWork Methods:**
| Method | Description |
|--------|-------------|
| `commit()` | Commit all changes |
| `rollback()` | Rollback all changes |
| `flush()` | Flush pending changes without committing |

### Decorator

For wrapping functions in transactions:

```python
from app.db.transaction import transactional

@transactional
async def create_road_segment(session, start_coords, end_coords):
    node_repo = NodeRepository(session)
    edge_repo = EdgeRepository(session)

    start_node = await node_repo.create(...)
    end_node = await node_repo.create(...)
    edge = await edge_repo.create(...)

    return edge
```

## File Structure

```
app/db/
├── __init__.py           # Exports all components
├── database.py           # Engine and session factory
├── dependencies.py       # FastAPI dependency injection
├── transaction.py        # Transaction management utilities
└── repositories/
    ├── __init__.py       # Repository exports
    ├── base.py           # BaseRepository class
    ├── node_repository.py
    ├── edge_repository.py
    └── vehicle_repository.py
```

## Testing

### Unit Tests

Located in `tests/test_repositories.py`. Uses mocks to test repository logic without database:

```bash
pytest tests/test_repositories.py -v
```

### Integration Tests

Located in `tests/test_repository_integration.py`. Tests against real PostgreSQL:

```bash
# Ensure PostgreSQL is running
docker-compose up -d postgres

# Run integration tests
pytest tests/test_repository_integration.py -v
```

## Best Practices

### 1. Use Dependency Injection in Endpoints
```python
# Good
@router.get("/nodes")
async def list_nodes(repo: NodeRepositoryDep):
    return await repo.list()

# Avoid
@router.get("/nodes")
async def list_nodes(session: AsyncSession = Depends(get_db_session)):
    repo = NodeRepository(session)
    return await repo.list()
```

### 2. Use UnitOfWork for Multi-Entity Operations
```python
# Good
async with UnitOfWork() as uow:
    node = await uow.nodes.create(...)
    edge = await uow.edges.create(...)
    await uow.commit()

# Avoid
session = await get_session()
node_repo = NodeRepository(session)
edge_repo = EdgeRepository(session)
# Manual transaction management
```

### 3. Use Bulk Operations for Performance
```python
# Good - Single database round-trip
await repo.bulk_update_status(vehicle_ids, VehicleStatus.MOVING)

# Avoid - Multiple round-trips
for vid in vehicle_ids:
    await repo.update_status(vid, VehicleStatus.MOVING)
```

### 4. Filter at Database Level
```python
# Good - Filter in database
active = await repo.get_by_status(VehicleStatus.MOVING)

# Avoid - Fetch all and filter in Python
all_vehicles = await repo.list()
active = [v for v in all_vehicles if v.status == "moving"]
```

## Constants

Repository operations use constants defined in `app/core/constants.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_PAGE_LIMIT` | 100 | Default limit for paginated queries |
| `MAX_BULK_OPERATION_LIMIT` | 100000 | Maximum entities for bulk operations |
| `SRID_WGS84` | 4326 | Spatial reference system for coordinates |

## Performance Considerations

1. **Spatial Indexes**: All spatial queries use PostGIS GIST indexes
2. **Eager Loading**: Use `get_with_edges()` to avoid N+1 queries
3. **Bulk Operations**: Use bulk methods for batch updates
4. **Pagination**: Always use `skip` and `limit` for large datasets (default: `DEFAULT_PAGE_LIMIT`)
5. **Connection Pooling**: Configured in `app/db/database.py`

## Related Documentation

- [Database Setup](./database-setup.md)
- [Road Network Data Models](./road-network-models.md)
- [Vehicle Data Model](./vehicle-data-model.md)

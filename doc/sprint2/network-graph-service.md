# Road Network Graph Service

## Overview

The Road Network Graph Service provides an in-memory NetworkX graph representation of the road network for fast pathfinding and connectivity queries. It loads data from PostgreSQL and maintains a cached graph structure optimized for simulation operations.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Simulation     │────▶│ RoadNetworkGraph│────▶│   PostgreSQL    │
│  Engine         │     │   (In-Memory)   │     │   (Persistent)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                      │
         │                      │
         ▼                      ▼
┌─────────────────┐     ┌─────────────────┐
│   Pathfinding   │     │      Cache      │
│   Algorithms    │     │   Management    │
└─────────────────┘     └─────────────────┘
```

## Key Features

- **In-Memory Graph**: Fast access to road network structure
- **Directed Graph**: Supports one-way streets
- **Weighted Edges**: Travel time based on length and speed limit
- **Cache Management**: Automatic staleness detection
- **Pathfinding**: Dijkstra's algorithm for shortest paths

## Usage

### Basic Usage

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.network_graph import RoadNetworkGraph

async def setup_graph(session: AsyncSession):
    graph = RoadNetworkGraph()
    stats = await graph.build_from_database(session)

    print(f"Loaded {stats.node_count} nodes and {stats.edge_count} edges")
    print(f"Build time: {stats.build_time_ms:.2f} ms")
    print(f"Network connected: {stats.is_connected}")

    return graph
```

### Pathfinding

```python
# Find shortest path between two nodes
path = graph.get_shortest_path(start_node_id, end_node_id)
print(f"Path: {path}")

# Calculate route metrics
length = graph.get_route_length(path)
travel_time = graph.get_route_travel_time(path)
print(f"Length: {length:.2f} meters")
print(f"Travel time: {travel_time:.2f} seconds")

# Safe pathfinding (returns None if no path)
path = graph.get_shortest_path_safe(start, end)
if path is None:
    print("No path found")
```

### Neighbor Queries

```python
# Get neighbors (nodes reachable from this node)
neighbors = graph.get_neighbors(node_id)

# Get predecessors (nodes that can reach this node)
predecessors = graph.get_predecessors(node_id)
```

### Finding Nearest Node

```python
# Find nearest node to a coordinate
nearest = graph.find_nearest_node(longitude=-3.8196, latitude=39.8628)
if nearest:
    attrs = graph.get_node_attributes(nearest)
    print(f"Nearest node: {nearest} at ({attrs['lon']}, {attrs['lat']})")
```

### Graph Information

```python
# Check graph properties
print(f"Nodes: {graph.node_count}")
print(f"Edges: {graph.edge_count}")
print(f"Cache stale: {graph.is_stale}")

# Check node/edge existence
if graph.has_node(node_id):
    attrs = graph.get_node_attributes(node_id)

if graph.has_edge(start_id, end_id):
    attrs = graph.get_edge_attributes(start_id, end_id)

# Get all nodes and edges
all_nodes = graph.get_all_node_ids()
all_edges = graph.get_all_edges()
```

### Export for Debugging

```python
# Export to JSON
json_str = graph.export_to_json()
with open("graph_debug.json", "w") as f:
    f.write(json_str)

# Export to dict (for API responses)
data = graph.export_to_dict()
```

## API Reference

### RoadNetworkGraph

| Method | Description | Returns |
|--------|-------------|---------|
| `build_from_database(session, active_only)` | Load graph from PostgreSQL | `GraphStats` |
| `get_shortest_path(start, end, weight)` | Find shortest path | `list[int]` |
| `get_shortest_path_safe(start, end, weight)` | Safe pathfinding | `Optional[list[int]]` |
| `get_route_length(path)` | Calculate path length (meters) | `float` |
| `get_route_travel_time(path)` | Calculate travel time (seconds) | `float` |
| `get_neighbors(node_id)` | Get successor nodes | `list[int]` |
| `get_predecessors(node_id)` | Get predecessor nodes | `list[int]` |
| `find_nearest_node(lon, lat)` | Find nearest node to point | `Optional[int]` |
| `has_node(node_id)` | Check if node exists | `bool` |
| `has_edge(start, end)` | Check if edge exists | `bool` |
| `get_node_attributes(node_id)` | Get node attributes | `dict` |
| `get_edge_attributes(start, end)` | Get edge attributes | `dict` |
| `get_all_node_ids()` | Get all node IDs | `list[int]` |
| `get_all_edges()` | Get all edges | `list[tuple]` |
| `export_to_json()` | Export to JSON string | `str` |
| `export_to_dict()` | Export to dictionary | `dict` |
| `clear()` | Clear graph and cache | `None` |

### GraphStats

| Field | Type | Description |
|-------|------|-------------|
| `node_count` | `int` | Number of nodes loaded |
| `edge_count` | `int` | Number of edges loaded |
| `is_connected` | `bool` | Whether the network is connected |
| `build_time_ms` | `float` | Time to build graph in milliseconds |
| `last_updated` | `float` | Unix timestamp of last build |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `graph` | `nx.DiGraph` | Underlying NetworkX graph |
| `stats` | `GraphStats` | Build statistics |
| `is_stale` | `bool` | Whether cache needs refresh |
| `node_count` | `int` | Number of nodes |
| `edge_count` | `int` | Number of edges |

## Edge Weight Calculation

Edge weights represent **travel time in seconds**:

```
weight = length_meters / (max_speed_kmh * KMH_TO_MS)
```

Where:
- `length_meters`: Edge length from database
- `max_speed_kmh`: Speed limit from database
- `KMH_TO_MS`: Conversion factor (1000/3600 = 0.2778)

**Example:**
- Edge length: 1000 meters
- Speed limit: 36 km/h = 10 m/s
- Weight: 1000 / 10 = **100 seconds**

## One-Way Streets

The graph handles one-way streets correctly:

- **Bidirectional roads** (`one_way=False`): Creates edges in both directions
- **One-way roads** (`one_way=True`): Creates only the forward edge

```python
# For bidirectional road A-B:
graph.has_edge(A, B)  # True
graph.has_edge(B, A)  # True

# For one-way road A->B:
graph.has_edge(A, B)  # True
graph.has_edge(B, A)  # False
```

## Cache Management

The graph includes automatic cache staleness detection:

```python
# Default TTL: 5 minutes
graph = RoadNetworkGraph(cache_ttl=300)

# Check if rebuild needed
if graph.is_stale:
    await graph.build_from_database(session)
```

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `GRAPH_CACHE_TTL_SECONDS` | 300 | Default cache TTL (5 minutes) |
| `KMH_TO_MS` | 0.2778 | km/h to m/s conversion |
| `DEFAULT_EDGE_WEIGHT` | 1.0 | Fallback weight if speed is 0 |

### Node Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `node_id` | `int` | Node ID |
| `lon` | `float` | Longitude |
| `lat` | `float` | Latitude |
| `node_type` | `str` | Type (intersection, traffic_light, etc.) |

### Edge Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `edge_id` | `int` | Edge ID |
| `length` | `float` | Length in meters |
| `max_speed` | `int` | Speed limit in km/h |
| `weight` | `float` | Travel time in seconds |
| `road_type` | `str` | Road type (primary, secondary, etc.) |
| `one_way` | `bool` | Whether edge is one-way |

## Performance

### Benchmarks

| Operation | Typical Time |
|-----------|--------------|
| Build from DB (1000 nodes) | < 100 ms |
| Pathfinding (100 node grid) | < 1 ms |
| Find nearest node | < 1 ms |

### Performance Tips

1. **Keep graph in memory**: Don't rebuild unnecessarily
2. **Use cache TTL**: Only rebuild when data changes
3. **Batch operations**: Build once, query many times
4. **Use safe methods**: `get_shortest_path_safe()` avoids exceptions

## Testing

### Unit Tests

```bash
pytest tests/test_network_graph.py -v
```

40 tests covering:
- Initialization
- Pathfinding algorithms
- Route calculations
- Neighbor queries
- Graph queries
- Export methods
- Cache behavior
- Performance

### Integration Tests

```bash
pytest tests/test_network_graph_integration.py -v
```

9 tests covering:
- Building from empty database
- Loading test data
- One-way edge handling
- Active-only filtering
- Attribute loading
- Weight calculation
- Pathfinding with real data

## File Structure

```
app/services/
├── __init__.py          # Service exports
└── network_graph.py     # RoadNetworkGraph class

tests/
├── test_network_graph.py            # Unit tests (synthetic)
└── test_network_graph_integration.py # Integration tests (database)

doc/sprint2/
└── network-graph-service.md         # This documentation
```

## Related Documentation

- [Road Network Data Models](./road-network-models.md)
- [Repository Pattern](./repository-pattern.md)
- [Database Setup](./database-setup.md)

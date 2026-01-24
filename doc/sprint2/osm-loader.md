# OpenStreetMap Data Loader (OSM ETL Pipeline)

## Overview

The OSM Loader service provides an ETL (Extract, Transform, Load) pipeline for importing road network data from OpenStreetMap files into PostgreSQL. It parses OSM XML files, filters relevant highway types, and stores the data as nodes and edges in the database.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   OSM File      │────▶│   OSMLoader     │────▶│   PostgreSQL    │
│   (.osm XML)    │     │   (ETL)         │     │   (PostGIS)     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                      │
         │                      ├── Parse XML
         │                      ├── Filter highways
         │                      ├── Transform to models
         │                      └── Bulk insert
         │
         ▼
    ┌─────────────────┐
    │  Overpass/      │
    │  Geofabrik      │
    └─────────────────┘
```

## Usage

### Python API

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.osm_loader import OSMLoader

async def import_osm_data(session: AsyncSession):
    loader = OSMLoader(session)
    stats = await loader.load_from_file("data/talavera.osm")

    print(f"Nodes imported: {stats.nodes_imported}")
    print(f"Edges imported: {stats.edges_imported}")
    print(f"Duration: {stats.duration_seconds:.2f}s")
```

### CLI Script

```bash
# Basic usage
python scripts/load_osm.py data/talavera.osm

# Clear existing data before import
python scripts/load_osm.py data/talavera.osm --clear

# Custom batch size
python scripts/load_osm.py data/talavera.osm --batch-size 500

# Quiet mode (no progress output)
python scripts/load_osm.py data/talavera.osm -q
```

### REST API

```bash
# Check import status
curl http://localhost:8000/api/map/import/status

# Import OSM file
curl -X POST http://localhost:8000/api/map/import \
  -H "Content-Type: application/json" \
  -d '{"filepath": "talavera.osm", "clear_existing": true}'
```

## Getting OSM Data

### Option 1: Overpass Turbo (Recommended for small areas)

1. Go to https://overpass-turbo.eu/
2. Navigate to your area of interest (e.g., Talavera de la Reina)
3. Use this query:

```
[out:xml][timeout:300];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|service)"]
    ({{bbox}});
  node(w);
);
out body;
>;
out skel qt;
```

4. Click "Export" → "Download as raw OSM data"
5. Save as `data/talavera.osm`

### Option 2: Geofabrik (For larger regions)

```bash
# Download Castilla-La Mancha extract
wget https://download.geofabrik.de/europe/spain/castilla-la-mancha-latest.osm.pbf

# Extract Talavera area using osmium
osmium extract -b -4.90,39.88,-4.78,39.98 \
  castilla-la-mancha-latest.osm.pbf \
  -o data/talavera.osm
```

### Option 3: OSM.org Export

1. Go to https://www.openstreetmap.org/export
2. Select your area
3. Click "Export"
4. Save as `data/talavera.osm`

## ETL Pipeline Details

### 1. Extract (Parse)

The loader parses OSM XML files using Python's `xml.etree.ElementTree` with iterparse for memory efficiency.

**Parsed elements:**
- `<node>`: Geographic points with coordinates
- `<way>`: Linear features composed of node references

### 2. Transform

**Highway Filtering:**

Only these highway types are imported:
- `motorway`, `motorway_link`
- `trunk`, `trunk_link`
- `primary`, `primary_link`
- `secondary`, `secondary_link`
- `tertiary`, `tertiary_link`
- `residential`
- `service`
- `unclassified`
- `living_street`

**Excluded types:**
- `footway`, `cycleway`, `path`, `pedestrian`, `steps`

**Node Type Detection:**

| OSM Tag | Node Type |
|---------|-----------|
| `highway=traffic_signals` | `traffic_light` |
| `junction=roundabout` | `roundabout` |
| `noexit=yes` | `dead_end` |
| (default) | `intersection` |

**Speed Limit Parsing:**

| Input | Output |
|-------|--------|
| `"50"` | 50 km/h |
| `"50 km/h"` | 50 km/h |
| `"30 mph"` | 48 km/h |
| (missing) | Default by road type |

**One-Way Detection:**

| Condition | One-Way |
|-----------|---------|
| `oneway=yes/true/1` | Yes |
| `oneway=-1/reverse` | Yes (reverse) |
| `junction=roundabout` | Yes |
| `highway=motorway*` | Yes (default) |
| (other) | No |

### 3. Load

Nodes and edges are bulk-inserted in batches for efficiency:
- Default batch size: 1000 entities
- Uses SQLAlchemy's `add_all()` with flush/refresh cycle

**OSM ID Mapping:**
- OSM IDs are stored in `metadata_json` field
- Internal database IDs are used for relationships

## API Reference

### OSMLoader

```python
class OSMLoader:
    def __init__(
        self,
        session: AsyncSession,
        *,
        batch_size: int = 1000,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    )

    async def load_from_file(
        self,
        filepath: str,
        *,
        clear_existing: bool = False,
    ) -> OSMLoadStats
```

### OSMLoadStats

| Field | Type | Description |
|-------|------|-------------|
| `nodes_parsed` | int | Total nodes in OSM file |
| `nodes_imported` | int | Nodes saved to database |
| `ways_parsed` | int | Total ways in OSM file |
| `edges_imported` | int | Edges saved to database |
| `ways_skipped` | int | Ways excluded by filter |
| `errors` | int | Processing errors |
| `duration_seconds` | float | Total import time |

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/map/import/status` | Check import availability |
| POST | `/api/map/import` | Import OSM file |

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `OSM_BATCH_SIZE` | 1000 | Default batch size |
| `OSM_PROGRESS_INTERVAL` | 1000 | Progress report interval |
| `OSM_ALLOWED_HIGHWAY_TYPES` | set | Included road types |
| `OSM_DEFAULT_SPEED_LIMITS` | dict | Speed by road type |

## Testing

### Unit Tests

```bash
pytest tests/test_osm_loader.py -v
```

Tests cover:
- XML parsing
- Maxspeed parsing
- Lanes parsing
- One-way detection
- Highway filtering
- Node type detection
- Length calculation
- File validation

### Integration Tests

```bash
pytest tests/test_osm_loader_integration.py -v
```

Tests cover:
- Full ETL pipeline
- Database integrity
- Geometry storage
- Road type assignment
- Clear existing data
- Batch processing

## File Structure

```
app/
├── services/
│   └── osm_loader.py          # OSMLoader class
├── api/
│   └── map.py                 # REST endpoints
└── core/
    └── constants.py           # OSM constants

scripts/
└── load_osm.py                # CLI tool

tests/
├── test_osm_loader.py         # Unit tests
├── test_osm_loader_integration.py  # Integration tests
└── fixtures/
    └── sample.osm             # Test data

data/                          # OSM files (gitignored)
└── talavera.osm
```

## Performance

| Dataset | Nodes | Edges | Duration |
|---------|-------|-------|----------|
| Sample (test) | 8 | 6 | < 1s |
| Talavera (small) | ~5000 | ~2000 | ~12s |
| Madrid (large) | ~100k | ~50k | ~2-5min |

### Tips for Large Files

1. Use `--batch-size 2000` for faster inserts
2. Run with `--clear` to avoid duplicates
3. Use PBF format with osmium for extraction
4. Consider running during off-peak hours

## Troubleshooting

### File Not Found

```
Error: File not found: data/talavera.osm
```

Solution: Ensure the file exists in the `data/` directory.

### Unsupported Format

```
Error: Unsupported file format: .pbf
```

Solution: Convert PBF to OSM XML using osmium:
```bash
osmium cat input.osm.pbf -o output.osm
```

### Memory Issues

For very large files, consider:
1. Extracting a smaller region first
2. Increasing system memory
3. Using smaller batch sizes

## Related Documentation

- [Road Network Data Models](./road-network-models.md)
- [Repository Pattern](./repository-pattern.md)
- [Network Graph Service](./network-graph-service.md)

# Digital Twin Traffic Backend

Backend de simulación para gemelo digital de tráfico urbano de Talavera de la Reina.

## Descripción

Este proyecto implementa el backend para un sistema de gemelo digital que simula el tráfico urbano. Desarrollado con FastAPI, PostgreSQL/PostGIS, y Python 3.14+.

### Características principales

- **API REST** con FastAPI y documentación OpenAPI automática
- **WebSocket** para comunicación en tiempo real con el cliente Godot
- **Base de datos espacial** con PostgreSQL 15+ y PostGIS 3.3
- **ETL de datos OSM** para importar redes viales desde OpenStreetMap
- **Modelo de red vial** con nodos, aristas y grafos NetworkX
- **Modelo de vehículos** con simulación de movimiento y estados
- **Patrón Repository** para acceso a datos desacoplado

## Requisitos

- Python 3.14+
- PostgreSQL 15+ con PostGIS 3.3
- Docker & Docker Compose (recomendado)

## Instalación

### Con Docker (Recomendado)

```bash
# Clonar repositorio
git clone <repository-url>
cd tfg-dt-python-backend

# Configurar variables de entorno
cp .env.example .env

# Levantar servicios
docker-compose up -d

# Verificar que todo funciona
curl http://localhost:8000/api/health/detailed
```

### Instalación Manual

```bash
# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# o: venv\Scripts\activate  # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar PostgreSQL con PostGIS
# (Ver sección de Base de Datos)

# Configurar variables de entorno
cp .env.example .env
# Editar .env con los datos de conexión a PostgreSQL

# Ejecutar migraciones
alembic upgrade head

# Iniciar servidor
uvicorn app.main:app --reload
```

## Configuración

### Variables de Entorno (.env)

```env
# Aplicación
APP_NAME=Digital Twin Traffic Backend
DEBUG=true
PORT=8000

# Base de Datos
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=dt_user
POSTGRES_PASSWORD=dt_password
POSTGRES_DB=digital_twin

# Pool de Conexiones
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
```

## Endpoints API

### Health Check

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/` | GET | Información básica de la API |
| `/api/health` | GET | Health check básico |
| `/api/health/detailed` | GET | Health check con info del sistema y BD |

### Importación de Datos OSM

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/map/import/status` | GET | Estado del sistema de importación |
| `/api/map/import` | POST | Importar archivo OSM |

**Ejemplo de importación:**

```bash
# Verificar estado
curl http://localhost:8000/api/map/import/status

# Importar archivo OSM
curl -X POST http://localhost:8000/api/map/import \
  -H "Content-Type: application/json" \
  -d '{"filepath": "talavera.osm", "clear_existing": true}'
```

### WebSocket

| Endpoint | Descripción |
|----------|-------------|
| `ws://localhost:8000/ws/simulation` | Comunicación en tiempo real |

## CLI Tools

### Importación de Datos OSM

```bash
# Uso básico
python scripts/load_osm.py data/talavera.osm

# Limpiar datos existentes antes de importar
python scripts/load_osm.py data/talavera.osm --clear

# Personalizar tamaño de batch
python scripts/load_osm.py data/talavera.osm --batch-size 500

# Modo silencioso
python scripts/load_osm.py data/talavera.osm -q
```

### Obtener Datos OSM

**Opción 1: Overpass Turbo (Recomendado para áreas pequeñas)**

1. Ir a https://overpass-turbo.eu/
2. Navegar a Talavera de la Reina
3. Usar esta query:

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

4. Exportar como "raw OSM data" → `data/talavera.osm`

**Opción 2: Geofabrik (Regiones más grandes)**

```bash
# Descargar extracto de Castilla-La Mancha
wget https://download.geofabrik.de/europe/spain/castilla-la-mancha-latest.osm.pbf

# Extraer área de Talavera con osmium
osmium extract -b -4.90,39.88,-4.78,39.98 \
  castilla-la-mancha-latest.osm.pbf \
  -o data/talavera.osm
```

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│                          FastAPI Application                         │
├─────────────────────────────────────────────────────────────────────┤
│  API Layer                                                           │
│  ├── /api/health          Health checks                              │
│  ├── /api/map/import      OSM data import                            │
│  └── /ws/simulation       WebSocket (Godot client)                   │
├─────────────────────────────────────────────────────────────────────┤
│  Service Layer                                                       │
│  ├── OSMLoader            ETL pipeline for OSM data                  │
│  └── RoadNetworkGraph     NetworkX graph operations                  │
├─────────────────────────────────────────────────────────────────────┤
│  Repository Layer                                                    │
│  ├── NodeRepository       CRUD for road network nodes                │
│  ├── EdgeRepository       CRUD for road network edges                │
│  └── VehicleRepository    CRUD for vehicles                          │
├─────────────────────────────────────────────────────────────────────┤
│  Data Layer                                                          │
│  ├── NodeModel            PostGIS Point geometry                     │
│  ├── EdgeModel            PostGIS LineString geometry                │
│  └── VehicleModel         Vehicle state and position                 │
├─────────────────────────────────────────────────────────────────────┤
│  Database                                                            │
│  └── PostgreSQL 15 + PostGIS 3.3                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Modelos de Datos

### NodeModel (Nodos de la Red Vial)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | int | ID único (autoincremental) |
| name | str | Nombre opcional |
| node_type | NodeType | Tipo de nodo |
| position | Point | Geometría PostGIS (EPSG:4326) |
| is_active | bool | Si está activo en simulación |
| metadata_json | str | Metadatos JSON (osm_id, etc.) |

**NodeType enum:**
- `intersection` - Intersección estándar
- `traffic_light` - Semáforo
- `roundabout` - Rotonda
- `dead_end` - Calle sin salida
- `entry_point` - Punto de entrada
- `exit_point` - Punto de salida

### EdgeModel (Aristas/Calles de la Red Vial)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | int | ID único |
| name | str | Nombre de la calle |
| start_node_id | int | FK a nodo de inicio |
| end_node_id | int | FK a nodo de fin |
| road_type | RoadType | Tipo de vía |
| geometry | LineString | Geometría PostGIS |
| length | float | Longitud en metros |
| max_speed | int | Velocidad máxima (km/h) |
| lanes | int | Número de carriles |
| one_way | bool | Si es de un solo sentido |
| is_active | bool | Si está activa |
| metadata_json | str | Metadatos JSON |

**RoadType enum:**
- `motorway`, `motorway_link` - Autopista
- `trunk`, `trunk_link` - Autovía
- `primary`, `primary_link` - Carretera principal
- `secondary`, `secondary_link` - Carretera secundaria
- `tertiary`, `tertiary_link` - Carretera terciaria
- `residential` - Calle residencial
- `service` - Vía de servicio
- `unclassified` - Sin clasificar
- `living_street` - Zona residencial

### VehicleModel (Vehículos)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | int | ID único |
| position | Point | Posición actual |
| velocity | float | Velocidad (m/s) |
| acceleration | float | Aceleración (m/s²) |
| heading | float | Dirección (grados) |
| status | VehicleStatus | Estado del vehículo |
| current_edge_id | int | FK a arista actual |

**VehicleStatus enum:**
- `idle` - Esperando para iniciar
- `moving` - En movimiento
- `stopped` - Detenido (semáforo, etc.)
- `waiting` - En cola
- `finished` - Ha completado su ruta

## Servicios

### OSMLoader

ETL pipeline para importar datos de OpenStreetMap.

```python
from app.services.osm_loader import OSMLoader

async with get_db_session() as session:
    loader = OSMLoader(session, batch_size=1000)
    stats = await loader.load_from_file("data/talavera.osm", clear_existing=True)

    print(f"Nodos importados: {stats.nodes_imported}")
    print(f"Aristas importadas: {stats.edges_imported}")
```

**Tipos de vías incluidos:**
- motorway, trunk, primary, secondary, tertiary, residential, service

**Tipos de vías excluidos:**
- footway, cycleway, path, pedestrian, steps

### RoadNetworkGraph

Servicio para operaciones con grafos NetworkX.

```python
from app.services.road_network_graph import RoadNetworkGraph

async with get_db_session() as session:
    graph_service = RoadNetworkGraph(session)

    # Construir grafo desde BD
    await graph_service.build_graph()

    # Obtener estadísticas
    stats = graph_service.get_graph_stats()
    print(f"Nodos: {stats['node_count']}, Aristas: {stats['edge_count']}")

    # Encontrar ruta más corta
    path = graph_service.shortest_path(node_a_id, node_b_id)
```

## Tests

```bash
# Ejecutar todos los tests
pytest tests/ -v

# Solo tests unitarios (sin BD)
pytest tests/test_osm_loader.py tests/test_road_network_graph.py -v

# Solo tests de integración (requiere BD)
pytest tests/test_osm_loader_integration.py tests/test_road_network_graph_integration.py -v

# Con cobertura
pytest tests/ --cov=app --cov-report=html
```

### Estructura de Tests

```
tests/
├── test_api_health.py              # Tests de health check
├── test_websocket.py               # Tests de WebSocket
├── test_osm_loader.py              # Tests unitarios OSM loader
├── test_osm_loader_integration.py  # Tests integración OSM loader
├── test_road_network_graph.py      # Tests unitarios grafo
├── test_road_network_graph_integration.py  # Tests integración grafo
├── test_repository.py              # Tests unitarios repositories
├── test_repository_integration.py  # Tests integración repositories
└── fixtures/
    └── sample.osm                  # Datos OSM de prueba
```

## Estructura del Proyecto

```
tfg-dt-python-backend/
├── app/
│   ├── api/
│   │   ├── websocket/          # WebSocket manager
│   │   ├── health.py           # Health check endpoints
│   │   ├── map.py              # OSM import endpoints
│   │   └── routes.py           # WebSocket routes
│   ├── core/
│   │   ├── constants.py        # Constantes globales
│   │   ├── responses.py        # Schemas de respuesta
│   │   └── utils.py            # Utilidades
│   ├── db/
│   │   ├── repositories/       # Patrón Repository
│   │   │   ├── base.py
│   │   │   ├── node_repository.py
│   │   │   ├── edge_repository.py
│   │   │   └── vehicle_repository.py
│   │   ├── database.py         # Engine SQLAlchemy async
│   │   └── utils.py            # Utilidades BD
│   ├── models/
│   │   ├── enums.py            # Enums (NodeType, RoadType, etc.)
│   │   ├── road_network.py     # NodeModel, EdgeModel
│   │   └── vehicle.py          # VehicleModel
│   ├── services/
│   │   ├── osm_loader.py       # ETL pipeline OSM
│   │   └── road_network_graph.py  # Servicio de grafos
│   ├── config.py               # Configuración Pydantic
│   └── main.py                 # Punto de entrada FastAPI
├── scripts/
│   ├── init.sql                # Inicialización PostGIS
│   └── load_osm.py             # CLI para importar OSM
├── data/                       # Archivos OSM (gitignored)
├── tests/                      # Tests pytest
├── doc/                        # Documentación detallada
│   └── sprint2/
│       ├── database-setup.md
│       ├── road-network-models.md
│       ├── vehicle-model.md
│       ├── repository-pattern.md
│       ├── network-graph-service.md
│       └── osm-loader.md
├── alembic/                    # Migraciones de BD
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## Tecnologías

| Tecnología | Versión | Propósito |
|------------|---------|-----------|
| Python | 3.14+ | Lenguaje principal |
| FastAPI | 0.128.0 | Framework web |
| Uvicorn | 0.40.0 | Servidor ASGI |
| SQLAlchemy | 2.0.36 | ORM async |
| asyncpg | 0.30.0 | Driver PostgreSQL async |
| GeoAlchemy2 | 0.15.2 | Extensión PostGIS para SQLAlchemy |
| NetworkX | 3.4.2 | Algoritmos de grafos |
| Pydantic | 2.12.5 | Validación de datos |
| PostgreSQL | 15+ | Base de datos |
| PostGIS | 3.3 | Extensión espacial |
| Alembic | 1.14.0 | Migraciones de BD |
| pytest | 8.3.4 | Testing |

## Documentación Adicional

Consultar la carpeta `doc/` para documentación detallada:

### Sprint 1 - Infraestructura Base
- [Issue 1.1 - Project Initialization](doc/sprint1/issue-1.1-project-initialization.md)
- [Issue 1.2 - WebSocket Connection Manager](doc/sprint1/issue-1.2-websocket-connection-manager.md)
- [Arquitectura](doc/sprint1/arquitectura.md)
- [API Reference](doc/sprint1/api-reference.md)
- [Guía de Desarrollo](doc/sprint1/guia-desarrollo.md)

### Sprint 2 - Modelos y Datos
- [Issue 2.1 - Database Setup](doc/sprint2/database-setup.md)
- [Issue 2.2 - Road Network Models](doc/sprint2/road-network-models.md)
- [Issue 2.3 - Vehicle Model](doc/sprint2/vehicle-model.md)
- [Issue 2.4 - Repository Pattern](doc/sprint2/repository-pattern.md)
- [Issue 2.5 - Network Graph Service](doc/sprint2/network-graph-service.md)
- [Issue 2.6 - OSM Loader](doc/sprint2/osm-loader.md)

## Licencia

TFG - Universidad de Castilla-La Mancha

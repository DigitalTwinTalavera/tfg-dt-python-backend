# Issue 2.1: PostgreSQL + PostGIS Infrastructure Setup

## Descripcion

Configuracion de la infraestructura de base de datos PostgreSQL 15+ con extension PostGIS 3.3 para almacenamiento de datos espaciales del Digital Twin de trafico urbano.

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Network                            │
│                         (dt-network)                             │
│  ┌─────────────────┐              ┌─────────────────┐           │
│  │   dt-backend    │   asyncpg    │   dt-postgres   │           │
│  │                 │─────────────▶│                 │           │
│  │   FastAPI       │              │  PostgreSQL 15  │           │
│  │   + SQLAlchemy  │              │  + PostGIS 3.3  │           │
│  │                 │              │                 │           │
│  │   Port: 8000    │              │   Port: 5432    │           │
│  └─────────────────┘              └────────┬────────┘           │
│           │                                │                     │
│           │                                ▼                     │
│           │                       ┌─────────────────┐           │
│           │                       │  postgres_data  │           │
│           │                       │    (Volume)     │           │
│           │                       └─────────────────┘           │
│           ▼                                                      │
│  ┌─────────────────┐                                            │
│  │     Alembic     │                                            │
│  │   Migrations    │                                            │
│  └─────────────────┘                                            │
└─────────────────────────────────────────────────────────────────┘
```

## Componentes Implementados

### 1. Servicio PostgreSQL con PostGIS

**Imagen:** `postgis/postgis:15-3.3`

**Caracteristicas:**
- PostgreSQL 15 con soporte completo para transacciones ACID
- PostGIS 3.3 para datos geoespaciales (coordenadas, geometrias, topologias)
- Health check nativo con `pg_isready`
- Persistencia de datos mediante volumen Docker

### 2. SQLAlchemy Async con asyncpg

**Patron de conexion:**
- Engine asincrono para no bloquear el event loop de FastAPI
- Connection pooling configurado para optimizar rendimiento
- Session factory para inyeccion de dependencias

**Archivos:**
- `app/db/database.py` - Engine, session factory, funciones init/close
- `app/db/utils.py` - Utilidades de health check
- `app/db/__init__.py` - Exportaciones del modulo

### 3. Alembic para Migraciones

**Configuracion:**
- Entorno asincrono compatible con asyncpg
- Template personalizado para migraciones
- Directorio `alembic/versions/` para historico

### 4. Health Check de Base de Datos

**Endpoint:** `GET /api/health/detailed`

**Validaciones:**
- Conexion activa a la base de datos
- Version de PostGIS instalada

## Configuracion

### Variables de Entorno

| Variable | Tipo | Default | Descripcion |
|----------|------|---------|-------------|
| `POSTGRES_HOST` | string | localhost | Host del servidor PostgreSQL |
| `POSTGRES_PORT` | int | 5432 | Puerto del servidor (1-65535) |
| `POSTGRES_USER` | string | dt_user | Usuario de la base de datos |
| `POSTGRES_PASSWORD` | string | dt_password | Contrasena del usuario |
| `POSTGRES_DB` | string | digital_twin | Nombre de la base de datos |
| `DB_POOL_SIZE` | int | 5 | Conexiones persistentes en el pool (1-20) |
| `DB_MAX_OVERFLOW` | int | 10 | Conexiones adicionales permitidas (0-30) |

### Archivo .env

```env
# Database Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=dt_user
POSTGRES_PASSWORD=dt_password
POSTGRES_DB=digital_twin

# Connection Pool
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
```

### Docker Compose

```yaml
services:
  postgres:
    image: postgis/postgis:15-3.3
    container_name: dt-postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-dt_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-dt_password}
      POSTGRES_DB: ${POSTGRES_DB:-digital_twin}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init.sql:/docker-entrypoint-initdb.d/init.sql:ro,z
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dt_user} -d ${POSTGRES_DB:-digital_twin}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
```

## Uso

### Iniciar Servicios

```bash
# Todos los servicios
docker-compose up -d

# Solo base de datos
docker-compose up -d postgres

# Ver logs
docker-compose logs -f postgres
docker-compose logs -f backend

# Verificar estado
docker-compose ps
```

### Conectar a la Base de Datos

```bash
# Desde el host (requiere psql instalado)
psql -h localhost -U dt_user -d digital_twin

# Desde dentro del contenedor
docker-compose exec postgres psql -U dt_user -d digital_twin
```

### Verificar PostGIS

```sql
-- Version de PostGIS
SELECT PostGIS_Version();

-- Version completa
SELECT PostGIS_Full_Version();

-- Listar extensiones
\dx
```

## Migraciones con Alembic

### Crear Nueva Migracion

```bash
# Autogenerar desde cambios en modelos
alembic revision --autogenerate -m "descripcion del cambio"

# Crear migracion vacia
alembic revision -m "descripcion del cambio"
```

### Aplicar Migraciones

```bash
# Aplicar todas las pendientes
alembic upgrade head

# Aplicar una especifica
alembic upgrade <revision_id>

# Rollback una migracion
alembic downgrade -1

# Rollback a revision especifica
alembic downgrade <revision_id>
```

### Ver Historial

```bash
# Historial completo
alembic history

# Estado actual
alembic current

# Migraciones pendientes
alembic history --indicate-current
```

## Health Check

### Endpoint Basico

```bash
curl http://localhost:8000/api/health
```

```json
{
  "status": "ok",
  "app": "Digital Twin Traffic Backend",
  "version": "0.1.0",
  "timestamp": "2024-01-18T12:00:00.000000+00:00",
  "environment": "development"
}
```

### Endpoint Detallado (con DB)

```bash
curl http://localhost:8000/api/health/detailed
```

```json
{
  "status": "ok",
  "app": {
    "name": "Digital Twin Traffic Backend",
    "version": "0.1.0",
    "description": "Backend de simulacion para gemelo digital de trafico urbano"
  },
  "timestamp": "2024-01-18T12:00:00.000000+00:00",
  "system": {
    "python_version": "3.14.0",
    "platform": "Linux-6.x.x",
    "processor": "x86_64"
  },
  "config": {
    "debug": true,
    "log_level": "INFO",
    "max_vehicles": 100,
    "tick_rate": 0.1
  },
  "database": {
    "connected": true,
    "postgis_version": "3.3.0 USE_GEOS=1 USE_PROJ=1 USE_STATS=1"
  }
}
```

## Estructura de Archivos

```
tfg-dt-python-backend/
├── app/
│   ├── db/                          # Modulo de base de datos
│   │   ├── __init__.py              # Exportaciones
│   │   ├── database.py              # Engine, session factory
│   │   └── utils.py                 # Health check utilities
│   ├── config.py                    # Configuracion con DB settings
│   ├── core/
│   │   ├── constants.py             # Constantes SQL y DB
│   │   └── responses.py             # DatabaseHealthResponse
│   ├── api/
│   │   └── health.py                # Health check con DB
│   └── main.py                      # Lifespan con init/close DB
├── alembic/
│   ├── env.py                       # Entorno async
│   ├── script.py.mako               # Template migraciones
│   └── versions/                    # Migraciones
├── scripts/
│   └── init.sql                     # Inicializacion PostGIS
├── docker-compose.yml               # Servicio postgres
├── Dockerfile                       # Con libpq-dev
├── requirements.txt                 # SQLAlchemy, asyncpg, alembic
└── .env                             # Variables de DB
```

## Connection Pooling

### Configuracion del Engine

```python
engine = create_async_engine(
    settings.database_url,
    echo=settings.DEBUG,              # Log SQL en modo debug
    pool_size=settings.DB_POOL_SIZE,  # Conexiones persistentes
    max_overflow=settings.DB_MAX_OVERFLOW,  # Conexiones extra
    pool_pre_ping=True,               # Verificar antes de usar
    pool_recycle=DB_POOL_RECYCLE_SECONDS,  # Reciclar cada hora
)
```

### Parametros Recomendados

| Escenario | pool_size | max_overflow |
|-----------|-----------|--------------|
| Desarrollo | 5 | 10 |
| Produccion (bajo trafico) | 10 | 20 |
| Produccion (alto trafico) | 20 | 30 |

## Troubleshooting

### Error: Cannot connect to database

```bash
# 1. Verificar contenedor
docker-compose ps

# 2. Ver logs de postgres
docker-compose logs postgres

# 3. Verificar conectividad
docker-compose exec backend ping postgres

# 4. Verificar variables de entorno
docker-compose exec backend env | grep POSTGRES
```

### Error: PostGIS extension not found

```bash
# Verificar que init.sql se ejecuto
docker-compose logs postgres | grep -i postgis

# Habilitar manualmente
docker-compose exec postgres psql -U dt_user -d digital_twin -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

### Error: Connection pool exhausted

1. Aumentar `DB_POOL_SIZE` y `DB_MAX_OVERFLOW` en `.env`
2. Verificar que las sesiones se cierran correctamente
3. Revisar si hay queries de larga duracion bloqueando conexiones

### Error: Permission denied (SELinux/Fedora)

```bash
# Los volumenes deben tener la etiqueta :z
volumes:
  - ./app:/app/app:z
  - ./.env:/app/.env:z
```

## Dependencias

### requirements.txt

```
# Database
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
psycopg2-binary==2.9.10
geoalchemy2==0.15.2

# Migrations
alembic==1.14.0
```

### Sistema (Dockerfile)

```dockerfile
RUN apt-get install -y libpq-dev
```

## Criterios de Aceptacion

- [x] PostgreSQL 15+ container anadido a docker-compose.yml
- [x] Extension PostGIS habilitada en la base de datos
- [x] Script de inicializacion creado (init.sql)
- [x] SQLAlchemy ORM configurado en app/db/database.py
- [x] Connection pooling configurado (asyncpg driver)
- [x] Variables de entorno para credenciales de DB en .env
- [x] Migraciones configuradas con Alembic
- [x] Health check endpoint actualizado para verificar conexion DB
- [x] Volumen configurado para persistencia de datos
- [x] Documentacion en doc/sprint2/database-setup.md

## Referencias

- [PostGIS Documentation](https://postgis.net/documentation/)
- [SQLAlchemy Async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [asyncpg Documentation](https://magicstack.github.io/asyncpg/current/)
- [GeoAlchemy2](https://geoalchemy-2.readthedocs.io/)

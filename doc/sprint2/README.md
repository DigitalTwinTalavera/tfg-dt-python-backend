# Sprint 2 - Documentacion

## Descripcion

Sprint enfocado en la configuracion de la infraestructura de base de datos para el Digital Twin de trafico urbano.

## Issues Completados

| Issue | Titulo | Estado |
|-------|--------|--------|
| 2.1 | PostgreSQL + PostGIS Infrastructure Setup | Completado |

## Documentacion

| Documento | Descripcion |
|-----------|-------------|
| [database-setup.md](database-setup.md) | Configuracion completa de PostgreSQL + PostGIS |
| [../testing.md](../testing.md) | Documentacion completa de testing (global) |

## Resumen Tecnico

### Tecnologias Implementadas

- **PostgreSQL 15**: Base de datos relacional
- **PostGIS 3.3**: Extension para datos geoespaciales
- **SQLAlchemy 2.0**: ORM asincrono con asyncpg
- **Alembic**: Migraciones de base de datos
- **Docker Compose**: Orquestacion de servicios

### Archivos Principales

```
app/
├── db/
│   ├── __init__.py      # Exportaciones del modulo
│   ├── database.py      # Engine async y session factory
│   └── utils.py         # Health check de DB
├── config.py            # Variables de configuracion DB
└── core/
    ├── constants.py     # Constantes SQL y mensajes
    └── responses.py     # DatabaseHealthResponse

alembic/
├── env.py               # Entorno async
├── script.py.mako       # Template migraciones
└── versions/            # Historial de migraciones

scripts/
└── init.sql             # Inicializacion PostGIS

docker-compose.yml       # Servicio postgres
```

### Endpoints Afectados

| Metodo | Endpoint | Cambio |
|--------|----------|--------|
| GET | `/api/health/detailed` | Ahora incluye estado de DB y version PostGIS |

### Variables de Entorno Nuevas

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `POSTGRES_HOST` | localhost | Host del servidor |
| `POSTGRES_PORT` | 5432 | Puerto del servidor |
| `POSTGRES_USER` | dt_user | Usuario |
| `POSTGRES_PASSWORD` | dt_password | Contrasena |
| `POSTGRES_DB` | digital_twin | Nombre de la DB |
| `DB_POOL_SIZE` | 5 | Tamano del pool |
| `DB_MAX_OVERFLOW` | 10 | Conexiones extra |

## Comandos Utiles

```bash
# Iniciar servicios
docker-compose up -d

# Ver logs
docker-compose logs -f backend
docker-compose logs -f postgres

# Conectar a la DB
docker-compose exec postgres psql -U dt_user -d digital_twin

# Ejecutar tests
pytest

# Crear migracion
alembic revision --autogenerate -m "descripcion"

# Aplicar migraciones
alembic upgrade head
```

## Tests

- **Total**: 14 tests
- **Nuevos en Sprint 2**: 3 tests relacionados con DB
- **Resultado**: 14 passed

Ver [documentacion de testing](../testing.md) para detalles completos.

# Sprint 1 - M1: Setup & Connectivity

## Resumen del Sprint

**Milestone:** M1: Setup & Connectivity
**Objetivo:** Establecer la infraestructura base del backend y la conectividad con el cliente Godot.

## Estado del Sprint

| Issue | Título | Estado | Tests |
|-------|--------|--------|-------|
| #1 | Project Initialization & FastAPI Setup | ✅ Completado | 5/5 |
| #2 | WebSocket Connection Manager | ✅ Completado | 8/8 |

**Total Tests:** 13/13 pasando

## Issues Completados

### Issue 1.1: Project Initialization & FastAPI Setup

**Descripción:** Establecer la estructura profesional del proyecto y configurar FastAPI.

**Entregables:**
- ✅ Estructura de directorios (`app/api`, `app/core`, `app/models`, `app/services`)
- ✅ `requirements.txt` con dependencias versionadas
- ✅ `app/main.py` con instancia FastAPI configurada
- ✅ Endpoint `/api/health` funcional
- ✅ `Dockerfile` para Python 3.14+
- ✅ Aplicación ejecutable localmente

**Documentación:** [issue-1.1-project-initialization.md](issue-1.1-project-initialization.md)

### Issue 1.2: WebSocket Connection Manager

**Descripción:** Implementar el gestor de conexiones WebSocket para comunicación con Godot.

**Entregables:**
- ✅ Clase `ConnectionManager` en `app/api/websocket/manager.py`
- ✅ Métodos `connect`, `disconnect`, `broadcast`
- ✅ Endpoint WebSocket `/ws/simulation`
- ✅ Integración endpoint-manager funcional
- ✅ Test de conexión con herramientas externas
- ✅ Echo test (servidor loguea mensajes)

**Documentación:** [issue-1.2-websocket-connection-manager.md](issue-1.2-websocket-connection-manager.md)

## Estructura del Proyecto

```
TFG-DT-PYTHON-BACKEND/
├── app/
│   ├── api/
│   │   ├── websocket/
│   │   │   ├── __init__.py
│   │   │   └── manager.py       # ConnectionManager
│   │   ├── __init__.py
│   │   ├── health.py            # Health check endpoints
│   │   └── routes.py            # WebSocket routes
│   ├── core/
│   │   ├── __init__.py
│   │   ├── constants.py         # Constantes globales
│   │   ├── responses.py         # Schemas Pydantic
│   │   └── utils.py             # Funciones helper
│   ├── models/
│   │   └── __init__.py
│   ├── services/
│   │   └── __init__.py
│   ├── __init__.py
│   ├── config.py                # Configuración Pydantic
│   └── main.py                  # Punto de entrada
├── tests/
│   ├── __init__.py
│   ├── test_health.py           # Tests HTTP
│   └── test_websocket.py        # Tests WebSocket
├── doc/
│   └── sprint1/
│       ├── README.md            # Este archivo
│       ├── issue-1.1-*.md
│       ├── issue-1.2-*.md
│       ├── arquitectura.md
│       ├── api-reference.md
│       └── guia-desarrollo.md
├── .env
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pytest.ini
└── README.md
```

## Endpoints Disponibles

### HTTP

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/` | GET | Información de la API |
| `/api/health` | GET | Health check básico |
| `/api/health/detailed` | GET | Health check detallado |
| `/docs` | GET | Swagger UI |
| `/redoc` | GET | ReDoc |

### WebSocket

| Endpoint | Descripción |
|----------|-------------|
| `ws://localhost:8000/ws/simulation` | Comunicación en tiempo real |

## Principios de Código Limpio

### 1. Constantes Centralizadas

Todas las constantes están en `app/core/constants.py`:

```python
# Rutas
WS_SIMULATION_PATH = "/ws/simulation"
API_PREFIX = "/api"

# Estados
STATUS_OK = "ok"
STATUS_RUNNING = "running"

# Tipos de mensaje WebSocket
WS_TYPE_ECHO = "echo"
WS_TYPE_BROADCAST = "broadcast"
```

### 2. Schemas Tipados

Todas las respuestas usan Pydantic en `app/core/responses.py`:

```python
class HealthCheckResponse(BaseModel):
    status: str
    app: str
    version: str
    timestamp: str
    environment: str
```

### 3. Funciones Helper Reutilizables

Lógica común en `app/core/utils.py`:

```python
def get_current_timestamp() -> str
def get_environment() -> str
def get_app_info() -> AppInfoResponse
def get_system_info() -> SystemInfoResponse
def get_config_info() -> ConfigInfoResponse
```

### 4. Tests con Constantes

Los tests usan las mismas constantes que el código:

```python
from app.core.constants import STATUS_OK, WS_TYPE_ECHO

assert response["status"] == STATUS_OK
assert response["type"] == WS_TYPE_ECHO
```

## Comandos de Verificación

```bash
# Ejecutar tests
pytest tests/ -v

# Verificar calidad
flake8 app/ tests/ --max-line-length=100

# Ejecutar servidor
uvicorn app.main:app --reload

# Docker
docker-compose up --build
```

## Métricas de Calidad

| Métrica | Valor |
|---------|-------|
| Tests Pasando | 13/13 (100%) |
| Errores Flake8 | 0 |
| Cobertura Endpoints | 100% |
| Código Duplicado | 0% |

## Próximos Pasos (Sprint 2)

Los siguientes issues del Milestone M1 podrían incluir:
- Simulación básica de tráfico
- Broadcast de estados a clientes Godot
- Modelos de datos para vehículos y red vial

---

**Fecha de cierre del Sprint:** 2026-01-18
**Python:** 3.14.2
**FastAPI:** 0.128.0

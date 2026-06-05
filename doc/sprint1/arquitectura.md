# Arquitectura del Backend - Digital Twin Traffic

## Visión General

El backend está diseñado siguiendo una arquitectura modular y limpia, preparada para escalar con los futuros requerimientos del gemelo digital de tráfico urbano.

## Estructura del Proyecto

```
TFG-DT-PYTHON-BACKEND/
├── app/                          # Código fuente principal
│   ├── api/                      # Endpoints HTTP y WebSocket
│   │   ├── websocket/           # Módulo WebSocket
│   │   │   ├── __init__.py
│   │   │   └── manager.py       # ConnectionManager
│   │   ├── __init__.py
│   │   ├── health.py            # Health check endpoints
│   │   └── routes.py            # WebSocket routes
│   ├── core/                     # Módulos compartidos
│   │   ├── __init__.py
│   │   ├── constants.py         # Constantes globales
│   │   ├── responses.py         # Schemas Pydantic
│   │   └── utils.py             # Funciones helper
│   ├── models/                   # Modelos de datos (futuro)
│   │   └── __init__.py
│   ├── services/                 # Lógica de negocio (futuro)
│   │   └── __init__.py
│   ├── __init__.py
│   ├── config.py                # Configuración centralizada
│   └── main.py                  # Punto de entrada
├── tests/                        # Tests automatizados
│   ├── __init__.py
│   ├── test_health.py           # Tests HTTP
│   └── test_websocket.py        # Tests WebSocket
├── doc/                          # Documentación
│   └── sprint1/
├── .env                          # Variables de entorno
├── .env.example                  # Plantilla de configuración
├── Dockerfile                    # Containerización
├── docker-compose.yml            # Orquestación
├── requirements.txt              # Dependencias Python
├── pytest.ini                    # Configuración de tests
└── README.md                     # Documentación principal
```

## Componentes Principales

### 1. Capa de Configuración (`app/config.py`)

Gestiona toda la configuración de la aplicación usando `pydantic-settings`:

```python
class Settings(BaseSettings):
    # Información de la aplicación
    APP_NAME: str
    APP_VERSION: str
    APP_DESCRIPTION: str

    # Servidor
    HOST: str
    PORT: int
    DEBUG: bool

    # CORS
    CORS_ORIGINS: list[str]

    # Simulación
    SIMULATION_TICK_RATE: float
    MAX_VEHICLES: int
```

**Características:**
- Validación automática de tipos
- Valores por defecto sensatos
- Carga desde archivo `.env`
- Validadores personalizados (ej: LOG_LEVEL)

### 2. Capa Core (`app/core/`)

#### constants.py
Centraliza todos los valores constantes:
- Rutas de API
- Tags de documentación
- Estados de respuesta
- Mensajes del sistema

#### responses.py
Define schemas Pydantic para respuestas tipadas:
- `RootResponse`
- `HealthCheckResponse`
- `DetailedHealthCheckResponse`
- `AppInfoResponse`
- `SystemInfoResponse`
- `ConfigInfoResponse`

#### utils.py
Funciones helper reutilizables:
- `get_current_timestamp()`: Timestamp UTC
- `get_environment()`: Entorno actual
- `get_app_info()`: Información de la app
- `get_system_info()`: Info del sistema
- `get_config_info()`: Configuración actual

### 3. Capa API (`app/api/`)

#### health.py
Endpoints HTTP de monitorización:

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/health` | GET | Health check básico |
| `/api/health/detailed` | GET | Health check con info del sistema |

#### websocket/manager.py
Gestor de conexiones WebSocket (Singleton):

```python
class ConnectionManager:
    async def connect(websocket: WebSocket) -> None
    def disconnect(websocket: WebSocket) -> None
    async def broadcast(message: dict) -> None
    async def send_personal_message(message: dict, websocket: WebSocket) -> None
```

#### routes.py
Endpoints WebSocket:

| Endpoint | Protocolo | Descripción |
|----------|-----------|-------------|
| `/ws/simulation` | WebSocket | Comunicación tiempo real con Godot |

### 4. Punto de Entrada (`app/main.py`)

Configura la aplicación FastAPI:
- Instancia de FastAPI con metadata
- CORS middleware
- Gestión del ciclo de vida (lifespan)
- Registro de routers
- Endpoint raíz

## Patrones de Diseño

### 1. Singleton (Configuración)
```python
# Una única instancia de Settings
settings = Settings()
```

### 2. Factory (Responses)
```python
# Funciones que crean objetos de respuesta
def get_app_info() -> AppInfoResponse:
    return AppInfoResponse(...)
```

### 3. Repository (Futuro)
Preparado para implementar acceso a datos.

### 4. Service Layer (Futuro)
Preparado para lógica de negocio de simulación.

## Flujo de Peticiones

```
Cliente HTTP
     │
     ▼
┌─────────────┐
│   FastAPI   │
│  (main.py)  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    CORS     │
│ Middleware  │
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│   Router    │────▶│   Handler   │
│  (api/)     │     │  (endpoint) │
└─────────────┘     └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Utils     │
                    │  (core/)    │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  Response   │
                    │  (Pydantic) │
                    └─────────────┘
```

## Configuración de CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # Configurable
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Gestión del Ciclo de Vida

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: inicialización
    print("🚀 Servidor iniciado")
    yield
    # Shutdown: limpieza
    print("👋 Servidor detenido")
```

## Extensibilidad

### Agregar Nuevo Endpoint

1. Crear archivo en `app/api/nuevo_router.py`
2. Definir schemas en `app/core/responses.py`
3. Registrar en `app/main.py`:
   ```python
   app.include_router(nuevo_router.router, prefix="/api")
   ```

### Agregar Nuevo Servicio

1. Crear archivo en `app/services/nuevo_servicio.py`
2. Inyectar en endpoints mediante dependencias FastAPI

### Agregar Nuevo Modelo

1. Crear archivo en `app/models/nuevo_modelo.py`
2. Usar en servicios y endpoints

## Seguridad

- CORS configurado (restrictivo en producción)
- Variables sensibles en `.env` (no en código)
- Validación de entrada con Pydantic
- Sin exposición de errores internos en producción

## Rendimiento

- Uvicorn con workers configurables
- Async/await nativo
- Sin bloqueos en endpoints
- Healthcheck para monitorización

## Flujo WebSocket

```
Cliente Godot
     │
     ▼ ws://host:port/ws/simulation
┌─────────────────────┐
│   WebSocket Router  │
│    (routes.py)      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ ConnectionManager   │
│   (manager.py)      │
│  - connect()        │
│  - disconnect()     │
│  - broadcast()      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Active Connections │
│    [ws1, ws2, ...]  │
└─────────────────────┘
```

## Próximos Pasos (Sprints Futuros)

1. ~~**WebSocket** para comunicación en tiempo real~~ ✅ Completado
2. **Simulación** de tráfico urbano
3. **Base de datos** para persistencia
4. **Autenticación** (si se requiere)
5. **Métricas** y observabilidad

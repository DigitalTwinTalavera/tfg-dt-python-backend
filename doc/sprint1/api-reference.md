# API Reference - Digital Twin Traffic Backend

## Base URL

```
http://localhost:8000
```

## Documentación Interactiva

| URL | Descripción |
|-----|-------------|
| `/docs` | Swagger UI (OpenAPI) |
| `/redoc` | ReDoc |
| `/openapi.json` | Especificación OpenAPI JSON |

---

## Endpoints

### Root

#### GET /

Información básica de la API.

**Request:**
```http
GET / HTTP/1.1
Host: localhost:8000
```

**Response:** `200 OK`
```json
{
    "app": "Digital Twin Traffic Backend",
    "version": "0.1.0",
    "status": "running",
    "docs": "/docs"
}
```

**Schema:** `RootResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `app` | string | Nombre de la aplicación |
| `version` | string | Versión de la aplicación |
| `status` | string | Estado del servidor (`running`) |
| `docs` | string | URL de la documentación |

---

### Health Check

#### GET /api/health

Health check básico del servicio.

**Request:**
```http
GET /api/health HTTP/1.1
Host: localhost:8000
```

**Response:** `200 OK`
```json
{
    "status": "ok",
    "app": "Digital Twin Traffic Backend",
    "version": "0.1.0",
    "timestamp": "2026-01-18T12:00:00.000000+00:00",
    "environment": "development"
}
```

**Schema:** `HealthCheckResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `status` | string | Estado del servicio (`ok`) |
| `app` | string | Nombre de la aplicación |
| `version` | string | Versión de la aplicación |
| `timestamp` | string | Timestamp ISO 8601 UTC |
| `environment` | string | Entorno (`development` o `production`) |

---

#### GET /api/health/detailed

Health check detallado con información del sistema.

**Request:**
```http
GET /api/health/detailed HTTP/1.1
Host: localhost:8000
```

**Response:** `200 OK`
```json
{
    "status": "ok",
    "app": {
        "name": "Digital Twin Traffic Backend",
        "version": "0.1.0",
        "description": "Backend de simulación para gemelo digital de tráfico urbano"
    },
    "timestamp": "2026-01-18T12:00:00.000000+00:00",
    "system": {
        "python_version": "3.14.2",
        "platform": "Linux-6.18.5-arch1-1-x86_64-with-glibc2.41",
        "processor": "x86_64"
    },
    "config": {
        "debug": true,
        "log_level": "INFO",
        "max_vehicles": 100,
        "tick_rate": 0.1
    }
}
```

**Schema:** `DetailedHealthCheckResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `status` | string | Estado del servicio |
| `app` | AppInfoResponse | Información de la aplicación |
| `timestamp` | string | Timestamp ISO 8601 UTC |
| `system` | SystemInfoResponse | Información del sistema |
| `config` | ConfigInfoResponse | Configuración actual |

**Schema:** `AppInfoResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `name` | string | Nombre de la aplicación |
| `version` | string | Versión |
| `description` | string | Descripción |

**Schema:** `SystemInfoResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `python_version` | string | Versión de Python |
| `platform` | string | Plataforma del SO |
| `processor` | string | Tipo de procesador |

**Schema:** `ConfigInfoResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `debug` | boolean | Modo debug activo |
| `log_level` | string | Nivel de logging |
| `max_vehicles` | integer | Máximo de vehículos |
| `tick_rate` | number | Tasa de actualización (segundos) |

---

## WebSocket

### WS /ws/simulation

Endpoint WebSocket para comunicación en tiempo real con el cliente Godot.

**URL:**
```
ws://localhost:8000/ws/simulation
```

**Conexión:**
```javascript
const ws = new WebSocket("ws://localhost:8000/ws/simulation");
```

**Mensaje Cliente → Servidor:**
```json
{
    "type": "vehicle_update",
    "vehicle_id": 1,
    "position": {"x": 100.5, "y": 200.3, "z": 0.0}
}
```

**Respuesta Servidor → Cliente (Echo):**
```json
{
    "type": "echo",
    "received": {
        "type": "vehicle_update",
        "vehicle_id": 1,
        "position": {"x": 100.5, "y": 200.3, "z": 0.0}
    },
    "status": "ok"
}
```

### Ejemplo con websocat

```bash
# Conectar
websocat ws://localhost:8000/ws/simulation

# Enviar mensaje (escribir y Enter)
{"type": "test", "data": "hello"}
```

### Ejemplo con Python

```python
import asyncio
import websockets
import json

async def websocket_client():
    uri = "ws://localhost:8000/ws/simulation"
    async with websockets.connect(uri) as ws:
        # Enviar mensaje
        await ws.send(json.dumps({"type": "ping"}))

        # Recibir respuesta
        response = await ws.recv()
        print(json.loads(response))

asyncio.run(websocket_client())
```

### Ejemplo con GDScript (Godot)

```gdscript
extends Node

var socket = WebSocketPeer.new()

func _ready():
    socket.connect_to_url("ws://localhost:8000/ws/simulation")

func _process(_delta):
    socket.poll()
    if socket.get_ready_state() == WebSocketPeer.STATE_OPEN:
        while socket.get_available_packet_count():
            var data = JSON.parse_string(
                socket.get_packet().get_string_from_utf8()
            )
            print("Received: ", data)

func send_data(data: Dictionary):
    socket.send_text(JSON.stringify(data))
```

---

## Códigos de Estado HTTP

| Código | Descripción |
|--------|-------------|
| `200 OK` | Petición exitosa |
| `404 Not Found` | Recurso no encontrado |
| `422 Unprocessable Entity` | Error de validación |
| `500 Internal Server Error` | Error del servidor |

---

## CORS

La API tiene CORS habilitado. En desarrollo permite todos los orígenes (`*`).

**Headers permitidos:**
- Todos (`*`)

**Métodos permitidos:**
- Todos (`*`)

**Credentials:**
- Habilitadas

---

## Ejemplos con cURL

### Health Check Básico
```bash
curl -X GET http://localhost:8000/api/health
```

### Health Check Detallado
```bash
curl -X GET http://localhost:8000/api/health/detailed
```

### Root Endpoint
```bash
curl -X GET http://localhost:8000/
```

---

## Ejemplos con Python

```python
import httpx

# Cliente síncrono
with httpx.Client(base_url="http://localhost:8000") as client:
    # Health check
    response = client.get("/api/health")
    print(response.json())

    # Health check detallado
    response = client.get("/api/health/detailed")
    print(response.json())
```

```python
import httpx
import asyncio

# Cliente asíncrono
async def check_health():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        response = await client.get("/api/health")
        return response.json()

result = asyncio.run(check_health())
print(result)
```

---

## Versionado

La API actualmente está en versión `0.1.0`. No hay versionado de URL implementado en esta fase inicial.

Futuras versiones podrían usar:
- Header: `Accept-Version: v1`
- URL: `/api/v1/health`

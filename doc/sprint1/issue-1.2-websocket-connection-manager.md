# Issue 1.2: WebSocket Connection Manager

## Información del Issue

| Campo | Valor |
|-------|-------|
| **Issue** | #2 |
| **Título** | WebSocket Connection Manager |
| **Milestone** | M1: Setup & Connectivity |
| **Labels** | `priority: critical`, `type: feature` |
| **Estado** | ✅ Completado |

## Descripción

El cliente Godot necesita recibir actualizaciones en tiempo real. Se requiere una clase WebSocket manager para gestionar conexiones entrantes, desconexiones y broadcasting de mensajes.

## Definition of Done - Checklist

### ✅ ConnectionManager Class

**Archivo:** `app/api/websocket/manager.py`

```python
class ConnectionManager:
    """Manages WebSocket connections for the simulation."""

    def __init__(self) -> None
    async def connect(self, websocket: WebSocket) -> None
    def disconnect(self, websocket: WebSocket) -> None
    async def send_personal_message(self, message: dict, websocket: WebSocket) -> None
    async def broadcast(self, message: dict) -> None
    async def broadcast_text(self, message: str) -> None
```

**Métodos implementados:**

| Método | Descripción | Estado |
|--------|-------------|--------|
| `connect` | Acepta conexión WebSocket y la añade a la lista activa | ✅ |
| `disconnect` | Elimina conexión de la lista activa | ✅ |
| `broadcast` | Envía mensaje JSON a todos los clientes conectados | ✅ |
| `send_personal_message` | Envía mensaje a un cliente específico | ✅ |
| `broadcast_text` | Envía mensaje de texto a todos los clientes | ✅ |

### ✅ WebSocket Endpoint

**Archivo:** `app/api/routes.py`

**Endpoint:** `ws://localhost:8000/ws/simulation`

```python
@router.websocket("/ws/simulation")
async def websocket_simulation(websocket: WebSocket) -> None:
    """WebSocket endpoint for simulation real-time updates."""
```

**Funcionalidad:**
- Acepta conexiones de clientes Godot
- Recibe mensajes JSON de los clientes
- Responde con echo de los mensajes recibidos
- Gestiona desconexiones automáticamente

### ✅ Integración con el Manager

El endpoint utiliza el `ConnectionManager` singleton:

```python
from app.api.websocket.manager import connection_manager

await connection_manager.connect(websocket)
# ... comunicación ...
connection_manager.disconnect(websocket)
```

### ✅ Test con herramienta externa

**Probado con:** websocat, Postman, y tests automatizados de pytest

**Comando de prueba:**
```bash
# Con websocat
websocat ws://localhost:8000/ws/simulation

# Enviar mensaje
{"type": "test", "data": "hello"}
```

**Respuesta esperada:**
```json
{
    "type": "echo",
    "received": {"type": "test", "data": "hello"},
    "status": "ok"
}
```

### ✅ Echo Test

El servidor registra todos los mensajes recibidos en la consola:

```
INFO:     Client connected. Total connections: 1
INFO:     Received message: {'type': 'test', 'data': 'hello'}
INFO:     Client disconnected from /ws/simulation
```

## Estructura de Archivos Creados

```
app/api/
├── websocket/
│   ├── __init__.py          # Exports ConnectionManager
│   └── manager.py           # ConnectionManager class
└── routes.py                # WebSocket endpoint /ws/simulation
```

## Tests Implementados

| Test | Tipo | Descripción |
|------|------|-------------|
| `test_connection_manager_initialization` | Unit | Manager inicializa con lista vacía |
| `test_connection_count_property` | Unit | Propiedad connection_count funciona |
| `test_websocket_connection` | Unit | Conexión WebSocket se establece |
| `test_websocket_echo_message` | Unit | Servidor hace echo de mensajes |
| `test_websocket_multiple_messages` | Unit | Múltiples mensajes en secuencia |
| `test_websocket_connection_lifecycle` | Integration | Ciclo completo de conexión |
| `test_websocket_json_message_structure` | Unit | Estructura JSON correcta |
| `test_websocket_simulation_data` | Integration | Datos de simulación funcionan |

**Resultado:** 8/8 tests pasando

## API WebSocket

### Conexión

```
ws://localhost:8000/ws/simulation
```

### Formato de Mensajes

**Mensaje del cliente → Servidor:**
```json
{
    "type": "string",
    "data": "any"
}
```

**Respuesta del servidor → Cliente:**
```json
{
    "type": "echo",
    "received": { /* mensaje original */ },
    "status": "ok"
}
```

### Tipos de Mensajes Soportados (Futuro)

| Tipo | Dirección | Descripción |
|------|-----------|-------------|
| `echo` | Server → Client | Confirmación de mensaje recibido |
| `broadcast` | Server → All | Mensaje a todos los clientes |
| `state_update` | Server → All | Actualización de estado de simulación |
| `client_connected` | Server → All | Notificación de nueva conexión |
| `client_disconnected` | Server → All | Notificación de desconexión |

## Ejemplo de Uso

### Python Client

```python
import asyncio
import websockets
import json

async def test_client():
    uri = "ws://localhost:8000/ws/simulation"
    async with websockets.connect(uri) as websocket:
        # Enviar mensaje
        message = {"type": "vehicle_update", "vehicle_id": 1}
        await websocket.send(json.dumps(message))

        # Recibir respuesta
        response = await websocket.recv()
        print(f"Received: {response}")

asyncio.run(test_client())
```

### GDScript Client (Godot)

```gdscript
extends Node

var socket = WebSocketPeer.new()

func _ready():
    socket.connect_to_url("ws://localhost:8000/ws/simulation")

func _process(_delta):
    socket.poll()
    var state = socket.get_ready_state()

    if state == WebSocketPeer.STATE_OPEN:
        while socket.get_available_packet_count():
            var packet = socket.get_packet()
            var data = JSON.parse_string(packet.get_string_from_utf8())
            print("Received: ", data)

func send_message(data: Dictionary):
    socket.send_text(JSON.stringify(data))
```

## Logging

Mensajes de log generados:

```
INFO: Client connected. Total connections: 1
INFO: Received message: {...}
WARNING: Failed to send message to client: [error]
INFO: Client disconnected. Total connections: 0
INFO: Client disconnected from /ws/simulation
```

## Constantes Añadidas

En `app/core/constants.py`:

```python
# WebSocket Routes
WS_SIMULATION_PATH = "/ws/simulation"

# WebSocket Message Types
WS_TYPE_ECHO = "echo"
WS_TYPE_BROADCAST = "broadcast"
WS_TYPE_STATE_UPDATE = "state_update"
WS_TYPE_CLIENT_CONNECTED = "client_connected"
WS_TYPE_CLIENT_DISCONNECTED = "client_disconnected"

# System Messages
MSG_WS_URL = "🔌 WebSocket disponible en ws://{host}:{port}{ws_path}"
```

## Startup Message

Al iniciar el servidor:

```
🚀 Digital Twin Traffic Backend v0.1.0
📡 Servidor iniciado en http://0.0.0.0:8000
📚 Documentación disponible en http://0.0.0.0:8000/docs
🔌 WebSocket disponible en ws://0.0.0.0:8000/ws/simulation
```

## Comandos de Verificación

```bash
# Ejecutar tests
pytest tests/test_websocket.py -v

# Verificar calidad de código
flake8 app/api/websocket/ app/api/routes.py

# Probar conexión manual
websocat ws://localhost:8000/ws/simulation
```

---

**Fecha de Completación:** 2026-01-18
**Tests:** 8/8 pasando
**Calidad de Código:** ✅ Sin errores flake8

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

# Testing - Digital Twin Traffic Backend

## Descripcion General

Documentacion completa de la estrategia de testing, cobertura, configuracion y resultados del proyecto Digital Twin Traffic Backend.

## Configuracion

### pytest.ini

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
python_classes = Test*
addopts = -v --tb=short
markers =
    unit: Tests unitarios (rapidos, sin dependencias externas)
    integration: Tests de integracion (pueden requerir servicios)
    slow: Tests lentos
```

### Dependencias

```
# requirements.txt
pytest==8.3.4
pytest-asyncio==0.25.2
httpx==0.28.1
```

## Estructura de Tests

```
tests/
├── __init__.py
├── test_health.py       # Tests de endpoints de health check
└── test_websocket.py    # Tests de WebSocket y ConnectionManager
```

## Catalogo de Tests

### test_health.py

Tests para los endpoints de health check y modelos relacionados.

| Test | Marker | Descripcion |
|------|--------|-------------|
| `test_root_endpoint` | unit | Verifica que el endpoint raiz `/` retorna status `running` |
| `test_health_check` | unit | Verifica health check basico `/api/health` con timestamp y version |
| `test_detailed_health_check` | unit | Verifica health check detallado `/api/health/detailed` con info de DB |
| `test_health_check_returns_correct_structure` | integration | Valida estructura completa de respuesta del health check basico |
| `test_detailed_health_check_returns_correct_structure` | integration | Valida estructura completa incluyendo campo `database` |
| `test_database_health_response_model` | unit | Valida modelo Pydantic `DatabaseHealthResponse` |

### test_websocket.py

Tests para el WebSocket Connection Manager y endpoints de comunicacion en tiempo real.

| Test | Marker | Descripcion |
|------|--------|-------------|
| `TestConnectionManager::test_connection_manager_initialization` | unit | Verifica inicializacion con lista vacia |
| `TestConnectionManager::test_connection_count_property` | unit | Verifica propiedad `connection_count` |
| `TestWebSocketEndpoint::test_websocket_connection` | unit | Verifica establecimiento de conexion WebSocket |
| `TestWebSocketEndpoint::test_websocket_echo_message` | unit | Verifica echo de mensajes JSON |
| `TestWebSocketEndpoint::test_websocket_multiple_messages` | unit | Verifica envio de multiples mensajes secuenciales |
| `TestWebSocketEndpoint::test_websocket_connection_lifecycle` | integration | Ciclo completo: conectar, comunicar, desconectar |
| `TestWebSocketEndpoint::test_websocket_json_message_structure` | unit | Valida estructura JSON de respuestas |
| `TestWebSocketEndpoint::test_websocket_simulation_data` | integration | Simula datos de vehiculos con posicion y velocidad |

## Cobertura por Modulo

| Modulo | Tests | Cobertura |
|--------|-------|-----------|
| `app/api/health.py` | 5 | Endpoints `/api/health` y `/api/health/detailed` |
| `app/main.py` | 1 | Endpoint raiz `/` |
| `app/api/websocket/manager.py` | 2 | Clase `ConnectionManager` |
| `app/api/routes.py` | 6 | Endpoint WebSocket `/ws/simulation` |
| `app/core/responses.py` | 1 | Modelo `DatabaseHealthResponse` |
| `app/db/` | - | Mockeado en tests unitarios |

## Estrategia de Mocking

### Mock de Base de Datos

Para evitar dependencias de PostgreSQL en tests unitarios, se utiliza inyeccion de dependencias de FastAPI:

```python
from unittest.mock import AsyncMock
from app.db.database import get_db_session
from app.main import app

def override_get_db_session():
    """Override dependency for testing without real DB."""
    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.scalar.return_value = "3.3.0 USE_GEOS=1"
    mock_session.execute.return_value = mock_result
    yield mock_session

# Aplicar override globalmente
app.dependency_overrides[get_db_session] = override_get_db_session
```

**Comportamiento simulado:**
- `session.execute(text("SELECT 1"))` - Retorna mock exitoso
- `session.execute(text("SELECT PostGIS_Version()"))` - Retorna "3.3.0 USE_GEOS=1"
- No requiere PostgreSQL real

### Mock de WebSocket

FastAPI TestClient maneja WebSockets nativamente:

```python
from fastapi.testclient import TestClient

client = TestClient(app)

with client.websocket_connect("/ws/simulation") as websocket:
    websocket.send_json({"type": "test"})
    response = websocket.receive_json()
```

## Ejecucion de Tests

### Comandos Basicos

```bash
# Ejecutar todos los tests
pytest

# Ejecutar con salida verbose
pytest -v

# Ejecutar con salida detallada de errores
pytest -vv --tb=long
```

### Filtrar por Markers

```bash
# Solo tests unitarios
pytest -m unit

# Solo tests de integracion
pytest -m integration

# Excluir tests lentos
pytest -m "not slow"
```

### Filtrar por Archivo o Test

```bash
# Ejecutar un archivo especifico
pytest tests/test_health.py
pytest tests/test_websocket.py

# Ejecutar un test especifico
pytest tests/test_health.py::test_detailed_health_check

# Ejecutar una clase de tests
pytest tests/test_websocket.py::TestConnectionManager

# Ejecutar un metodo de una clase
pytest tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_echo_message
```

### Cobertura de Codigo

```bash
# Instalar pytest-cov
pip install pytest-cov

# Ejecutar con reporte de cobertura en terminal
pytest --cov=app

# Generar reporte HTML
pytest --cov=app --cov-report=html

# Generar reporte XML (para CI/CD)
pytest --cov=app --cov-report=xml

# Ver lineas no cubiertas
pytest --cov=app --cov-report=term-missing
```

### Ejecucion Paralela

```bash
# Instalar pytest-xdist
pip install pytest-xdist

# Ejecutar en paralelo (auto-detectar CPUs)
pytest -n auto

# Ejecutar con N procesos
pytest -n 4
```

## Resultados de Ejecucion

### Ultima Ejecucion

```
$ pytest
========================= test session starts =========================
platform linux -- Python 3.14.2, pytest-8.3.4, pluggy-1.6.0
rootdir: /home/homei/Universidad/TFG/tfg-dt-python-backend
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.12.1, asyncio-0.25.2
asyncio: mode=Mode.STRICT
collected 14 items

tests/test_health.py::test_root_endpoint PASSED                  [  7%]
tests/test_health.py::test_health_check PASSED                   [ 14%]
tests/test_health.py::test_detailed_health_check PASSED          [ 21%]
tests/test_health.py::test_health_check_returns_correct_structure PASSED [ 28%]
tests/test_health.py::test_detailed_health_check_returns_correct_structure PASSED [ 35%]
tests/test_health.py::test_database_health_response_model PASSED [ 42%]
tests/test_websocket.py::TestConnectionManager::test_connection_manager_initialization PASSED [ 50%]
tests/test_websocket.py::TestConnectionManager::test_connection_count_property PASSED [ 57%]
tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_connection PASSED [ 64%]
tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_echo_message PASSED [ 71%]
tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_multiple_messages PASSED [ 78%]
tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_connection_lifecycle PASSED [ 85%]
tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_json_message_structure PASSED [ 92%]
tests/test_websocket.py::TestWebSocketEndpoint::test_websocket_simulation_data PASSED [100%]

========================= 14 passed in 0.29s =========================
```

### Resumen

| Metrica | Valor |
|---------|-------|
| Total tests | 14 |
| Passed | 14 |
| Failed | 0 |
| Skipped | 0 |
| Tiempo | 0.29s |
| Cobertura | ~85% (estimado) |

## Tests por Endpoint

### GET /

```python
def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert "app" in data
    assert "version" in data
    assert "docs" in data
```

**Respuesta esperada:**
```json
{
  "app": "Digital Twin Traffic Backend",
  "version": "0.1.0",
  "status": "running",
  "docs": "/docs"
}
```

### GET /api/health

```python
def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "version" in data
    assert "app" in data
    assert "environment" in data
```

**Respuesta esperada:**
```json
{
  "status": "ok",
  "app": "Digital Twin Traffic Backend",
  "version": "0.1.0",
  "timestamp": "2024-01-18T12:00:00.000000+00:00",
  "environment": "development"
}
```

### GET /api/health/detailed

```python
def test_detailed_health_check():
    response = client.get("/api/health/detailed")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "database" in data
    assert data["database"]["connected"] == True
    assert "postgis_version" in data["database"]
```

**Respuesta esperada:**
```json
{
  "status": "ok",
  "app": {
    "name": "Digital Twin Traffic Backend",
    "version": "0.1.0",
    "description": "..."
  },
  "timestamp": "...",
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
    "postgis_version": "3.3.0 USE_GEOS=1"
  }
}
```

### WebSocket /ws/simulation

```python
def test_websocket_echo_message():
    with client.websocket_connect("/ws/simulation") as websocket:
        test_message = {"type": "test", "data": "hello"}
        websocket.send_json(test_message)
        response = websocket.receive_json()

        assert response["type"] == "echo"
        assert response["status"] == "ok"
        assert response["received"] == test_message
```

**Mensaje enviado:**
```json
{"type": "test", "data": "hello"}
```

**Respuesta esperada:**
```json
{
  "type": "echo",
  "status": "ok",
  "received": {"type": "test", "data": "hello"}
}
```

## Tests de Integracion con Base de Datos Real

Para tests que requieren PostgreSQL real:

### Opcion 1: Docker Compose

```yaml
# docker-compose.test.yml
services:
  postgres-test:
    image: postgis/postgis:15-3.3
    environment:
      POSTGRES_USER: test_user
      POSTGRES_PASSWORD: test_password
      POSTGRES_DB: test_db
    ports:
      - "5433:5432"
    tmpfs:
      - /var/lib/postgresql/data
```

```bash
# Levantar DB de test
docker-compose -f docker-compose.test.yml up -d

# Ejecutar tests con DB real
POSTGRES_HOST=localhost \
POSTGRES_PORT=5433 \
POSTGRES_USER=test_user \
POSTGRES_PASSWORD=test_password \
POSTGRES_DB=test_db \
pytest -m integration

# Limpiar
docker-compose -f docker-compose.test.yml down -v
```

### Opcion 2: Testcontainers (Futuro)

```python
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgis/postgis:15-3.3") as postgres:
        yield postgres
```

## CI/CD

### GitHub Actions (Ejemplo)

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgis/postgis:15-3.3
        env:
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: test_db
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.14'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run tests
        env:
          POSTGRES_HOST: localhost
          POSTGRES_PORT: 5432
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: test_db
        run: pytest --cov=app --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v4
```

## Buenas Practicas

### Naming

- Tests nombrados descriptivamente: `test_<que_se_prueba>_<escenario>`
- Clases de tests: `Test<Componente>`
- Archivos: `test_<modulo>.py`

### Estructura

```python
def test_ejemplo():
    """Docstring explicando el proposito del test."""
    # Arrange (preparar)
    datos_entrada = {...}

    # Act (ejecutar)
    resultado = funcion_a_probar(datos_entrada)

    # Assert (verificar)
    assert resultado.status == "ok"
```

### Aislamiento

- Cada test es independiente
- No compartir estado entre tests
- Usar fixtures para setup/teardown

### Assertions

- Multiples assertions para validar respuestas completas
- Mensajes descriptivos en caso de fallo
- Validar tipos y estructuras, no solo valores

## Troubleshooting

### Error: ModuleNotFoundError

```bash
# Asegurar que el proyecto esta en el path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

### Error: Database connection failed

```bash
# Verificar que el mock esta aplicado
# En test_health.py debe existir:
app.dependency_overrides[get_db_session] = override_get_db_session
```

### Warning: asyncio_default_fixture_loop_scope

Agregar a `pytest.ini`:
```ini
[pytest]
asyncio_default_fixture_loop_scope = function
```

### Tests lentos

```bash
# Identificar tests lentos
pytest --durations=10

# Ejecutar en paralelo
pytest -n auto
```

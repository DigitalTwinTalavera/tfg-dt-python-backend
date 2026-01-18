# Guía de Desarrollo - Digital Twin Traffic Backend

## Requisitos Previos

- Python 3.14+
- pip
- Docker & Docker Compose (opcional)
- Git

## Instalación

### 1. Clonar el Repositorio

```bash
git clone <repository-url>
cd TFG-DT-PYTHON-BACKEND
```

### 2. Crear Entorno Virtual

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# o
venv\Scripts\activate     # Windows
```

### 3. Instalar Dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar Variables de Entorno

```bash
cp .env.example .env
# Editar .env según necesidades
```

## Ejecución

### Desarrollo Local

```bash
# Con hot reload
uvicorn app.main:app --reload

# Especificando host y puerto
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Usando el script de Python
python -m app.main
```

### Con Docker

```bash
# Construir y ejecutar
docker-compose up --build

# En segundo plano
docker-compose up -d --build

# Ver logs
docker-compose logs -f

# Detener
docker-compose down
```

## Tests

### Ejecutar Todos los Tests

```bash
pytest tests/ -v
```

### Ejecutar Tests por Tipo

```bash
# Solo tests unitarios
pytest tests/ -v -m unit

# Solo tests de integración
pytest tests/ -v -m integration
```

### Ejecutar con Cobertura

```bash
pytest tests/ -v --cov=app --cov-report=html
# Abrir htmlcov/index.html en el navegador
```

## Calidad de Código

### Linting con Flake8

```bash
flake8 app/ tests/ --max-line-length=100
```

### Formateo con Black

```bash
# Verificar cambios
black app/ tests/ --check

# Aplicar formateo
black app/ tests/
```

### Verificación Completa

```bash
# Ejecutar todo antes de commit
flake8 app/ tests/ --max-line-length=100 && \
black app/ tests/ --check && \
pytest tests/ -v
```

## Estructura de Archivos

### Crear Nuevo Endpoint

1. **Crear el router** en `app/api/nuevo_router.py`:

```python
"""Router de ejemplo."""

from fastapi import APIRouter
from app.core.responses import MiResponse

router = APIRouter()


@router.get("/mi-endpoint", response_model=MiResponse)
async def mi_endpoint() -> MiResponse:
    """Descripción del endpoint."""
    return MiResponse(...)
```

2. **Definir el schema** en `app/core/responses.py`:

```python
class MiResponse(BaseModel):
    """Respuesta de mi endpoint"""
    campo: str = Field(..., description="Descripción")
```

3. **Registrar el router** en `app/main.py`:

```python
from app.api import nuevo_router

app.include_router(
    nuevo_router.router,
    prefix="/api",
    tags=["Mi Tag"]
)
```

4. **Crear tests** en `tests/test_nuevo_router.py`:

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.mark.unit
def test_mi_endpoint():
    response = client.get("/api/mi-endpoint")
    assert response.status_code == 200
```

### Agregar Nueva Configuración

1. **Agregar al modelo** en `app/config.py`:

```python
class Settings(BaseSettings):
    # ... existentes ...
    MI_NUEVA_CONFIG: str = Field(
        default="valor",
        description="Descripción de la config"
    )
```

2. **Agregar al .env**:

```env
MI_NUEVA_CONFIG="mi_valor"
```

3. **Usar en el código**:

```python
from app.config import settings

valor = settings.MI_NUEVA_CONFIG
```

## Convenciones de Código

### Nombres

| Elemento | Convención | Ejemplo |
|----------|------------|---------|
| Archivos | snake_case | `health_check.py` |
| Clases | PascalCase | `HealthCheckResponse` |
| Funciones | snake_case | `get_health_status()` |
| Constantes | UPPER_SNAKE_CASE | `STATUS_OK` |
| Variables | snake_case | `current_timestamp` |

### Imports

```python
# 1. Standard library
import sys
from datetime import datetime

# 2. Third-party
from fastapi import APIRouter
from pydantic import BaseModel

# 3. Local
from app.config import settings
from app.core.constants import STATUS_OK
```

### Docstrings

```python
def funcion_ejemplo(param1: str, param2: int) -> dict:
    """
    Descripción breve de la función.

    Args:
        param1: Descripción del parámetro 1
        param2: Descripción del parámetro 2

    Returns:
        Descripción del valor de retorno

    Raises:
        ValueError: Cuando param1 está vacío
    """
    pass
```

### Type Hints

```python
# Siempre usar type hints
def procesar_datos(datos: list[dict]) -> dict[str, int]:
    pass

# Para opcionales
from typing import Optional
def buscar(id: int) -> Optional[dict]:
    pass

# O con sintaxis moderna (Python 3.10+)
def buscar(id: int) -> dict | None:
    pass
```

## Debugging

### Logs

```python
import logging

logger = logging.getLogger(__name__)

logger.debug("Mensaje de debug")
logger.info("Mensaje informativo")
logger.warning("Advertencia")
logger.error("Error")
```

### Configurar Nivel de Log

En `.env`:
```env
LOG_LEVEL="DEBUG"
```

### Endpoints de Debug

- `/docs` - Swagger UI interactivo
- `/redoc` - Documentación alternativa
- `/api/health/detailed` - Info del sistema

## Solución de Problemas

### Error: ModuleNotFoundError

```bash
# Asegurar que el entorno virtual está activo
source venv/bin/activate

# Reinstalar dependencias
pip install -r requirements.txt
```

### Error: Port already in use

```bash
# Encontrar proceso usando el puerto
lsof -i :8000

# Matar el proceso
kill -9 <PID>

# O usar otro puerto
uvicorn app.main:app --port 8001
```

### Error: CORS

Verificar `CORS_ORIGINS` en `.env`:
```env
# Desarrollo
CORS_ORIGINS=["*"]

# Producción (especificar dominios)
CORS_ORIGINS=["https://midominio.com"]
```

### Tests Fallan

```bash
# Ejecutar con más detalle
pytest tests/ -v --tb=long

# Ejecutar un test específico
pytest tests/test_health.py::test_health_check -v
```

## Recursos Adicionales

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Uvicorn Documentation](https://www.uvicorn.org/)
- [pytest Documentation](https://docs.pytest.org/)

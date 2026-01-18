# Digital Twin Traffic Backend

Backend de simulación para gemelo digital de tráfico urbano.

## Descripción

Este proyecto implementa el backend para un sistema de gemelo digital que simula el tráfico urbano. Desarrollado con FastAPI y Python 3.14+.

## Requisitos

- Python 3.14+
- pip
- Docker & Docker Compose (opcional)

## Instalación Rápida

```bash
# Crear entorno virtual
python -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env

# Ejecutar
uvicorn app.main:app --reload
```

## Ejecución con Docker

```bash
docker-compose up --build
```

## Endpoints Disponibles

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/` | GET | Información básica de la API |
| `/api/health` | GET | Health check básico |
| `/api/health/detailed` | GET | Health check con info del sistema |
| `/docs` | GET | Documentación Swagger UI |
| `/redoc` | GET | Documentación ReDoc |

## Tests

```bash
pytest tests/ -v
```

## Estructura del Proyecto

```
app/
├── api/           # Endpoints HTTP
├── core/          # Módulos compartidos (constantes, schemas, utils)
├── models/        # Modelos de datos
├── services/      # Lógica de negocio
├── config.py      # Configuración centralizada
└── main.py        # Punto de entrada
```

## Documentación

Consultar la carpeta `doc/` para documentación detallada:

- [Issue 1.1 - Project Initialization](doc/sprint1/issue-1.1-project-initialization.md)
- [Arquitectura](doc/sprint1/arquitectura.md)
- [API Reference](doc/sprint1/api-reference.md)
- [Guía de Desarrollo](doc/sprint1/guia-desarrollo.md)

## Tecnologías

- **Framework:** FastAPI 0.128.0
- **Server:** Uvicorn 0.40.0
- **Validación:** Pydantic 2.12.5
- **Testing:** pytest 8.3.4
- **Python:** 3.14.2

## Licencia

TFG - Universidad de Castilla-La Mancha

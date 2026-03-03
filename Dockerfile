# Imagen base oficial de Python 3.14
FROM python:3.14-slim

# Metadata
LABEL maintainer="ismael.lopez6@alu.uclm.es" \
      description="Backend de simulación para Digital Twin de tráfico urbano" \
      version="0.1.0"

# Variables de entorno para optimización de Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema (incluyendo libpq para PostgreSQL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini .

# Exponer el puerto (se puede sobrescribir con variable de entorno)
EXPOSE ${PORT:-8000}

# Comando para ejecutar la aplicación (aplica migraciones primero)
CMD alembic upgrade head && uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}
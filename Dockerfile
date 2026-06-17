# Python 3.14 free-threaded (PEP 703 / no-GIL) via uv + python-build-standalone.
# No existe imagen oficial library/python con tag '3.14t', así que partimos de
# la imagen de uv y dejamos que uv gestione el intérprete free-threaded.
# El sufijo 't' en 'uv python install 3.14t' selecciona el build sin GIL.
FROM ghcr.io/astral-sh/uv:bookworm-slim

LABEL maintainer="ismael.lopez6@alu.uclm.es" \
      description="Backend de simulación para Digital Twin de tráfico urbano" \
      version="0.1.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Toolchain mínima: algunas wheels (shapely sin binarios para 3.14t, etc.)
# pueden compilarse desde fuente; libpq-dev por si asyncpg necesita fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instalar CPython 3.14 free-threaded gestionado por uv
RUN uv python install 3.14t

# Crear venv FT e instalar dependencias
COPY requirements.txt .
RUN uv venv --python 3.14t "$VIRTUAL_ENV" \
    && uv pip install --python "$VIRTUAL_ENV/bin/python" -r requirements.txt

# Código de la aplicación
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini .

EXPOSE ${PORT:-8000}

CMD alembic upgrade head && uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --ws-max-size 4194304

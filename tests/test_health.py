"""
Tests para los endpoints de health check.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.constants import STATUS_OK, STATUS_RUNNING
from app.core.responses import DatabaseHealthResponse
from app.db.database import get_db_session
from app.main import app


def override_get_db_session():
    """Override dependency for testing without real DB."""
    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.scalar.return_value = "3.3.0 USE_GEOS=1"
    mock_session.execute.return_value = mock_result
    yield mock_session


app.dependency_overrides[get_db_session] = override_get_db_session

client = TestClient(app)


@pytest.mark.unit
def test_root_endpoint():
    """Test del endpoint raiz"""
    response = client.get("/")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == STATUS_RUNNING
    assert "app" in data
    assert "version" in data
    assert "docs" in data


@pytest.mark.unit
def test_health_check():
    """Test del health check basico"""
    response = client.get("/api/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == STATUS_OK
    assert "timestamp" in data
    assert "version" in data
    assert "app" in data
    assert "environment" in data


@pytest.mark.unit
def test_detailed_health_check():
    """Test del health check detallado"""
    response = client.get("/api/health/detailed")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == STATUS_OK
    assert "app" in data
    assert "timestamp" in data
    assert "system" in data
    assert "config" in data
    assert "database" in data

    # Validar estructura de app
    assert "name" in data["app"]
    assert "version" in data["app"]
    assert "description" in data["app"]

    # Validar estructura de system
    assert "python_version" in data["system"]
    assert "platform" in data["system"]
    assert "processor" in data["system"]

    # Validar estructura de config
    assert "debug" in data["config"]
    assert "log_level" in data["config"]
    assert "max_vehicles" in data["config"]
    assert "tick_rate" in data["config"]

    # Validar estructura de database
    assert "connected" in data["database"]
    assert "postgis_version" in data["database"]


@pytest.mark.integration
def test_health_check_returns_correct_structure():
    """Verifica la estructura completa de la respuesta del health check"""
    response = client.get("/api/health")
    assert response.status_code == 200

    data = response.json()
    required_keys = ["status", "app", "version", "timestamp", "environment"]

    for key in required_keys:
        assert key in data, f"Falta la clave '{key}' en la respuesta"


@pytest.mark.integration
def test_detailed_health_check_returns_correct_structure():
    """Verifica la estructura completa de la respuesta del health check detallado"""
    response = client.get("/api/health/detailed")
    assert response.status_code == 200

    data = response.json()
    required_keys = ["status", "app", "timestamp", "system", "config", "database"]

    for key in required_keys:
        assert key in data, f"Falta la clave '{key}' en la respuesta"


@pytest.mark.unit
def test_database_health_response_model():
    """Test del modelo DatabaseHealthResponse"""
    response = DatabaseHealthResponse(connected=True, postgis_version="3.3.0")
    assert response.connected is True
    assert response.postgis_version == "3.3.0"

    response_disconnected = DatabaseHealthResponse(connected=False, postgis_version=None)
    assert response_disconnected.connected is False
    assert response_disconnected.postgis_version is None

from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient
from app.db.session import check_database_connectivity


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Test the root welcome endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "FloodWatch" in data["message"]
    assert "prototype_notice" in data
    assert data["health"] == "/api/v1/health"


@pytest.mark.asyncio
async def test_health_endpoint_db_connected(client: AsyncClient):
    """Test /api/v1/health when database is connected successfully."""
    mock_db_res = {
        "status": "connected",
        "provider": "supabase_postgrest",
        "table_verified": "districts",
    }
    with patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = mock_db_res
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data
        assert data["database"]["status"] == "connected"
        assert data["database"]["provider"] == "supabase_postgrest"
        assert data["database"]["table_verified"] == "districts"
        assert "NOT an official emergency-warning service" in data["prototype_notice"]


@pytest.mark.asyncio
async def test_health_endpoint_db_disconnected(client: AsyncClient):
    """Test /api/v1/health when database is disconnected or returns an error."""
    mock_db_res = {
        "status": "disconnected",
        "provider": "supabase_postgrest",
        "message": "Database endpoint returned HTTP 500",
    }
    with patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = mock_db_res
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()

        # Overall status should degrade gracefully without 500 crash
        assert data["status"] == "degraded"
        assert data["database"]["status"] == "disconnected"
        assert "HTTP 500" in data["database"]["message"]


@pytest.mark.asyncio
async def test_health_endpoint_db_timeout(client: AsyncClient):
    """Test /api/v1/health when database connection times out."""
    mock_db_res = {
        "status": "timeout",
        "provider": "supabase_postgrest",
        "message": "Connection timed out after 5.0s",
    }
    with patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = mock_db_res
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "degraded"
        assert data["database"]["status"] == "timeout"


@pytest.mark.asyncio
async def test_live_supabase_connectivity():
    """Live connectivity check against the configured Supabase instance.
    
    Verifies that the backend can connect to Supabase and query the public districts table
    without credential exposure.
    """
    res = await check_database_connectivity(timeout_seconds=6.0)
    assert "status" in res
    if res["status"] == "connected":
        assert res["provider"] == "supabase_postgrest"
        assert res["table_verified"] == "districts"
    else:
        pytest.skip(f"Live Supabase network unreachable or not configured: {res.get('message')}")

import pytest
from httpx import ASGITransport, AsyncClient
from app.core.http_clients import close_http_clients, init_http_clients
from app.main import app


@pytest.fixture
async def client():
    """Async test client for testing FastAPI endpoints."""
    init_http_clients()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    finally:
        await close_http_clients()

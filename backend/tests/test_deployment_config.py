"""Automated Tests for Deployment Configuration & Operational Readiness (Phase 2.9).

Verification Scenarios:
1. Usable development defaults.
2. CORS origins string and JSON parsing.
3. CORS wildcard safety.
4. Production safety: DEBUG=True rejection.
5. Production safety: Wildcard CORS rejection.
6. Production safety: Default insecure token rejection.
7. Production safety: Missing SUPABASE_URL rejection.
8. Production safety: Valid production configuration acceptance.
9. Liveness probe endpoint (/api/v1/health/liveness).
10. Readiness probe endpoint (/api/v1/health/readiness) connected and disconnected.
11. Production documentation toggling (docs_enabled).
12. Automated ingestion scheduler default disabled safety check.
"""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.main import app


# =============================================================================
# 1. Configuration Validation Tests
# =============================================================================

def test_development_defaults_usable():
    """Verify default Settings instance works out of the box for development."""
    s = Settings()
    assert s.ENVIRONMENT == "development"
    assert s.DEBUG is True
    assert s.is_production is False
    assert s.docs_enabled is True
    assert s.ENABLE_INGESTION_SCHEDULER is False
    assert "http://localhost:5173" in s.CORS_ORIGINS


def test_cors_origins_parsing():
    """Verify CORS_ORIGINS parses comma-separated strings and JSON arrays."""
    # Comma-separated string
    s1 = Settings(CORS_ORIGINS="https://floodwatch.org, https://app.floodwatch.org")
    assert s1.CORS_ORIGINS == ["https://floodwatch.org", "https://app.floodwatch.org"]

    # JSON-encoded string
    s2 = Settings(CORS_ORIGINS='["https://floodwatch.org", "https://preview.floodwatch.org"]')
    assert s2.CORS_ORIGINS == ["https://floodwatch.org", "https://preview.floodwatch.org"]


def test_cors_origins_wildcard_validation():
    """Verify wildcard '*' cannot be combined with specific domains."""
    with pytest.raises(ValueError, match="cannot contain wildcard"):
        Settings(CORS_ORIGINS=["*", "https://floodwatch.org"])


def test_production_debug_true_rejected():
    """Verify production startup fails if DEBUG is True."""
    with pytest.raises(ValueError, match="DEBUG must be set to False"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=True,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["https://floodwatch.org"],
        )


def test_production_wildcard_cors_rejected():
    """Verify production startup fails if wildcard '*' is in CORS_ORIGINS."""
    with pytest.raises(ValueError, match="Wildcard '\\*' in CORS_ORIGINS is strictly prohibited"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["*"],
        )


def test_production_dev_token_rejected():
    """Verify production startup fails if default dev secret token is left unchanged."""
    with pytest.raises(ValueError, match="Default development secret token"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="floodwatch-dev-secret-token",
            CORS_ORIGINS=["https://floodwatch.org"],
        )


def test_production_missing_supabase_url_rejected():
    """Verify production startup fails if SUPABASE_URL is missing or placeholder."""
    with pytest.raises(ValueError, match="A valid SUPABASE_URL must be configured"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["https://floodwatch.org"],
        )


def test_production_valid_configuration_passes():
    """Verify valid production settings instantiate successfully."""
    s = Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        SUPABASE_URL="https://floodwatch-prod.supabase.co",
        SUPABASE_ANON_KEY="prod-anon-public-key-1234567890",
        SUPABASE_SERVICE_ROLE_KEY="prod-service-role-secret-key-1234567890",
        INGESTION_TRIGGER_TOKEN="super-secret-production-token-998877",
        CORS_ORIGINS=["https://floodwatch.org", "https://api.floodwatch.org"],
    )
    assert s.is_production is True
    assert s.DEBUG is False
    assert s.docs_enabled is False


def test_docs_toggling_behavior():
    """Verify docs_enabled honors explicit ENABLE_DOCS override."""
    # Production without override -> False
    s_prod_default = Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        SUPABASE_URL="https://prod.supabase.co",
        SUPABASE_ANON_KEY="prod-anon-public-key-1234567890",
        SUPABASE_SERVICE_ROLE_KEY="prod-service-role-secret-key-1234567890",
        INGESTION_TRIGGER_TOKEN="strong-token-123456",
        CORS_ORIGINS=["https://floodwatch.org"],
    )
    assert s_prod_default.docs_enabled is False

    # Production with explicit ENABLE_DOCS=True -> True
    s_prod_override = Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        ENABLE_DOCS=True,
        SUPABASE_URL="https://prod.supabase.co",
        SUPABASE_ANON_KEY="prod-anon-public-key-1234567890",
        SUPABASE_SERVICE_ROLE_KEY="prod-service-role-secret-key-1234567890",
        INGESTION_TRIGGER_TOKEN="strong-token-123456",
        CORS_ORIGINS=["https://floodwatch.org"],
    )
    assert s_prod_override.docs_enabled is True


def test_scheduler_default_disabled():
    """Verify automated scheduler is disabled by default for single-process safety."""
    s = Settings()
    assert s.ENABLE_INGESTION_SCHEDULER is False


# =============================================================================
# 2. Health & Readiness Probe Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_liveness_probe_endpoint(client: AsyncClient):
    """Verify GET /api/v1/health/liveness returns 200 and alive status."""
    resp = await client.get("/api/v1/health/liveness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"
    assert "service" in data


@pytest.mark.asyncio
async def test_readiness_probe_endpoint_connected(client: AsyncClient):
    """Verify GET /api/v1/health/readiness returns 200 when DB is connected."""
    mock_db = {"status": "connected", "provider": "supabase_postgrest"}
    with patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = mock_db
        resp = await client.get("/api/v1/health/readiness")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"


@pytest.mark.asyncio
async def test_readiness_probe_endpoint_disconnected(client: AsyncClient):
    """Verify GET /api/v1/health/readiness returns 503 when DB is disconnected."""
    mock_db = {"status": "disconnected", "error": "Connection refused"}
    with patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = mock_db
        resp = await client.get("/api/v1/health/readiness")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["database"] == "disconnected"


# =============================================================================
# Production Token Strength & Redaction Tests (Part C Remediation)
# =============================================================================

def test_production_empty_token_rejected():
    """Verify production startup fails if INGESTION_TRIGGER_TOKEN is empty."""
    with pytest.raises(ValueError, match="must not be empty"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="",
            CORS_ORIGINS=["https://floodwatch.org"],
        )


def test_production_whitespace_token_rejected():
    """Verify production startup fails if INGESTION_TRIGGER_TOKEN is only whitespace."""
    with pytest.raises(ValueError, match="must not be empty"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="   \t  ",
            CORS_ORIGINS=["https://floodwatch.org"],
        )


def test_production_weak_short_token_rejected():
    """Verify production startup fails if INGESTION_TRIGGER_TOKEN is shorter than 16 chars."""
    with pytest.raises(ValueError, match="at least 16 characters"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="short-token-99",  # 14 chars < 16
            CORS_ORIGINS=["https://floodwatch.org"],
        )


def test_production_token_secret_not_printed_in_validation_error():
    """Verify validation errors never echo the attempted secret token."""
    secret_candidate = "weak-secret-candidate"
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            INGESTION_TRIGGER_TOKEN="floodwatch-dev-secret-token",
            CORS_ORIGINS=["https://floodwatch.org"],
        )
    # The default token value string must not be leaked into the message
    err_str = str(exc_info.value)
    assert "cannot be used for INGESTION_TRIGGER_TOKEN" in err_str
    assert "floodwatch-dev-secret-token" not in err_str


# =============================================================================
# Phase 2.7.4.1: Configuration, Source Health & Error Sanitization Tests
# =============================================================================

def test_env_file_resolved_from_repository_root_and_backend_dir(monkeypatch):
    """CONF-01: Verify Settings loads backend/.env consistently regardless of cwd."""
    from pathlib import Path
    from app.core.config import BACKEND_DIR, ENV_FILE_PATH

    assert ENV_FILE_PATH == BACKEND_DIR / ".env"
    assert ENV_FILE_PATH.is_absolute()

    # Clear env vars that might override .env during cwd check
    for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(k, raising=False)

    repo_root = BACKEND_DIR.parent

    # 1. Load from repository root
    monkeypatch.chdir(repo_root)
    s_root = Settings()

    # 2. Load from backend directory
    monkeypatch.chdir(BACKEND_DIR)
    s_backend = Settings()

    assert s_root.SUPABASE_URL == s_backend.SUPABASE_URL
    assert s_root.SUPABASE_ANON_KEY == s_backend.SUPABASE_ANON_KEY
    if ENV_FILE_PATH.exists():
        # Confirmed backend/.env values match regardless of working directory
        assert bool(s_root.SUPABASE_URL) is bool(s_backend.SUPABASE_URL)


def test_environment_variable_precedence_over_env_file(monkeypatch):
    """CONF-01: Verify OS environment variables override values from backend/.env."""
    monkeypatch.setenv("SUPABASE_URL", "https://os-env-override.supabase.co")
    monkeypatch.setenv("APP_NAME", "FloodWatch-Env-Override")

    s = Settings()
    assert s.SUPABASE_URL == "https://os-env-override.supabase.co"
    assert s.APP_NAME == "FloodWatch-Env-Override"


def test_production_missing_supabase_keys_rejected():
    """CONF-02: Verify production startup fails when anon or service_role keys are missing or placeholders."""
    # Missing SUPABASE_ANON_KEY
    with pytest.raises(ValueError, match="A valid SUPABASE_ANON_KEY must be configured"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            SUPABASE_ANON_KEY="",
            SUPABASE_SERVICE_ROLE_KEY="prod-service-role-secret-key-1234567890",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["https://floodwatch.org"],
        )

    # Placeholder SUPABASE_ANON_KEY
    with pytest.raises(ValueError, match="A valid SUPABASE_ANON_KEY must be configured"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            SUPABASE_ANON_KEY="your-supabase-anon-public-key",
            SUPABASE_SERVICE_ROLE_KEY="prod-service-role-secret-key-1234567890",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["https://floodwatch.org"],
        )

    # Missing SUPABASE_SERVICE_ROLE_KEY
    with pytest.raises(ValueError, match="A valid SUPABASE_SERVICE_ROLE_KEY must be configured"):
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            SUPABASE_ANON_KEY="prod-anon-public-key-1234567890",
            SUPABASE_SERVICE_ROLE_KEY="",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["https://floodwatch.org"],
        )

    # Placeholder SUPABASE_SERVICE_ROLE_KEY (and ensure secret candidate is not echoed)
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SUPABASE_URL="https://prod.supabase.co",
            SUPABASE_ANON_KEY="prod-anon-public-key-1234567890",
            SUPABASE_SERVICE_ROLE_KEY="your-supabase-service-role-secret-key",
            INGESTION_TRIGGER_TOKEN="secure-prod-token-1234567890",
            CORS_ORIGINS=["https://floodwatch.org"],
        )
    assert "A valid SUPABASE_SERVICE_ROLE_KEY must be configured" in str(exc_info.value)
    assert "your-supabase-service-role-secret-key" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_sources_health_reports_degraded_when_database_fails(client: AsyncClient):
    """CONF-03: Verify /api/v1/sources/health reports degraded when DB connectivity fails even if weather succeeds."""
    from app.api.v1.routes.weather import get_ingestion_service
    from app.db.supabase_client import SupabaseClient
    from app.ingestion.open_meteo import OpenMeteoWeatherConnector
    from app.services.ingestion_service import IngestionService

    mock_sb = SupabaseClient(supabase_url="https://fake-configured.supabase.co", anon_key="fake-anon")
    mock_conn = AsyncMock(spec=OpenMeteoWeatherConnector)
    mock_conn.base_url = "https://api.open-meteo.com/v1"
    mock_conn.fetch_precipitation.return_value = AsyncMock()

    svc = IngestionService(supabase_client=mock_sb, weather_connector=mock_conn)
    app.dependency_overrides[get_ingestion_service] = lambda: svc

    try:
        with patch("app.db.session.check_database_connectivity", new_callable=AsyncMock) as mock_db:
            mock_db.return_value = {"status": "disconnected", "provider": "supabase_postgrest"}
            resp = await client.get("/api/v1/sources/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"
            assert "Database: disconnected" in data["connectivity"]
            assert data["details"]["database_status"] == "disconnected"
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)


@pytest.mark.asyncio
async def test_weather_api_errors_do_not_expose_raw_exception_details(client: AsyncClient):
    """SEC-01: Verify weather endpoints sanitize errors and never expose secrets or raw SQL traces."""
    from app.api.v1.routes.weather import get_ingestion_service, get_weather_service

    mock_ing = AsyncMock()
    mock_ing.get_locations.side_effect = RuntimeError(
        "postgresql://postgres:topsecretpass@db.internal:5432/postgres relation 'secret_tbl' does not exist"
    )
    mock_ing.get_observations_for_location.side_effect = RuntimeError(
        "Failed calling https://secret-host.internal/api?token=leakme123"
    )
    mock_weather = AsyncMock()
    mock_weather.get_preview.side_effect = RuntimeError("Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig crashed")

    app.dependency_overrides[get_ingestion_service] = lambda: mock_ing
    app.dependency_overrides[get_weather_service] = lambda: mock_weather

    try:
        r1 = await client.get("/api/v1/weather/locations")
        assert r1.status_code == 500
        assert "topsecretpass" not in r1.text
        assert "secret_tbl" not in r1.text
        assert "postgresql://" not in r1.text

        r2 = await client.get("/api/v1/weather/observations/loc-1")
        assert r2.status_code == 500
        assert "leakme123" not in r2.text
        assert "secret-host.internal" not in r2.text

        r3 = await client.get("/api/v1/weather/preview?latitude=20.3&longitude=86.6")
        assert r3.status_code == 500
        assert "eyJhbGciOi" not in r3.text
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# Phase 2.7.4.3: Supabase Key Header Compatibility & Secret Sanitization Tests
# =============================================================================

def test_new_secret_and_publishable_key_headers_omit_bearer():
    """KEY-01: New sb_secret_ and sb_publishable_ keys are sent via apikey ONLY and absent from Authorization."""
    from app.db.supabase_client import SupabaseClient

    sb_pub = "sb_publishable_TestPublicKey_1234567890"
    sb_sec = "sb_secret_TestSecretKey_1234567890"

    client = SupabaseClient(
        supabase_url="https://test-proj.supabase.co",
        anon_key=sb_pub,
        service_role_key=sb_sec,
    )

    # 1 & 2. New secret key in apikey, absent from Authorization
    sec_headers = client._get_headers(use_service_role=True)
    assert sec_headers["apikey"] == sb_sec
    assert "Authorization" not in sec_headers

    # 3 & 4. New publishable key in apikey, absent from Authorization
    pub_headers = client._get_headers(use_service_role=False)
    assert pub_headers["apikey"] == sb_pub
    assert "Authorization" not in pub_headers


def test_legacy_jwt_keys_preserve_bearer_authorization():
    """KEY-01: Legacy anon and service_role JWTs preserve apikey and Authorization: Bearer."""
    from app.db.supabase_client import SupabaseClient

    legacy_anon = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.anon_payload.sig"
    legacy_srv = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.service_role_payload.sig"

    client = SupabaseClient(
        supabase_url="https://test-proj.supabase.co",
        anon_key=legacy_anon,
        service_role_key=legacy_srv,
    )

    # 5. Legacy service_role JWT compatibility
    srv_headers = client._get_headers(use_service_role=True)
    assert srv_headers["apikey"] == legacy_srv
    assert srv_headers["Authorization"] == f"Bearer {legacy_srv}"

    # 6. Legacy anon JWT compatibility
    anon_headers = client._get_headers(use_service_role=False)
    assert anon_headers["apikey"] == legacy_anon
    assert anon_headers["Authorization"] == f"Bearer {legacy_anon}"


def test_user_session_jwt_authorization_preserved_with_new_keys():
    """KEY-01: Genuine user session JWT Authorization is preserved alongside sb_publishable_ / sb_secret_ keys."""
    from app.db.supabase_client import SupabaseClient

    sb_pub = "sb_publishable_TestPublicKey_1234567890"
    sb_sec = "sb_secret_TestSecretKey_1234567890"
    user_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.user_session_claims.sig"

    client = SupabaseClient(
        supabase_url="https://test-proj.supabase.co",
        anon_key=sb_pub,
        service_role_key=sb_sec,
    )

    # 7. Genuine user JWT preserved in Authorization while apikey holds publishable key
    headers = client._get_headers(use_service_role=False, user_jwt=user_jwt)
    assert headers["apikey"] == sb_pub
    assert headers["Authorization"] == f"Bearer {user_jwt}"

    # Safety guard: passing an opaque sb_secret_ as user_jwt must NEVER leak into Authorization
    bad_headers = client._get_headers(use_service_role=False, user_jwt=sb_sec)
    assert bad_headers["apikey"] == sb_pub
    assert "Authorization" not in bad_headers


@pytest.mark.asyncio
async def test_session_connectivity_check_uses_protocol_compliant_headers():
    """KEY-01: check_database_connectivity uses apikey-only for sb_publishable_ keys."""
    import httpx
    from app.db.session import check_database_connectivity

    sb_pub = "sb_publishable_SessionCheck_12345"
    captured_headers = {}

    async def fake_get(url, headers=None, *args, **kwargs):
        captured_headers.update(headers or {})
        req = httpx.Request("GET", url)
        return httpx.Response(200, request=req, json=[{"id": "d1"}])

    with patch("app.db.session.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            SUPABASE_URL="https://test-proj.supabase.co",
            SUPABASE_ANON_KEY=sb_pub,
        )
        with patch("httpx.AsyncClient.get", side_effect=fake_get):
            res = await check_database_connectivity()

    assert res["status"] == "connected"
    assert captured_headers["apikey"] == sb_pub
    assert "Authorization" not in captured_headers


def test_opaque_supabase_keys_redacted_everywhere():
    """KEY-02: sb_secret_ and sb_publishable_ keys are redacted in exceptions, URLs, and headers."""
    from app.core.sanitizer import redact_headers, sanitize_error_message
    from app.db.supabase_client import SupabaseClientError

    fake_secret = "sb_secret_AbCdEf1234567890_SecretValue"
    fake_pub = "sb_publishable_XyZ987654321_PublicKey"

    # 8. Secret key redacted from raw exception string & SupabaseClientError
    raw_secret_err = f"PostgREST failed authenticating key {fake_secret} in header"
    clean_secret_err = sanitize_error_message(raw_secret_err)
    assert fake_secret not in clean_secret_err
    assert "[REDACTED]" in clean_secret_err

    client_err = SupabaseClientError(f"Connection rejected with {fake_secret}")
    assert fake_secret not in str(client_err)
    assert "[REDACTED]" in str(client_err)

    # 9. Publishable key redacted when embedded in error strings, URLs, or headers
    raw_pub_err = f"GET https://example.supabase.co/rest/v1/districts?apikey={fake_pub} failed ({fake_pub})"
    clean_pub_err = sanitize_error_message(raw_pub_err)
    assert fake_pub not in clean_pub_err
    assert "example.supabase.co" not in clean_pub_err
    assert "[REDACTED]" in clean_pub_err

    # Header redaction (both standard keys and accidental leaks in custom headers)
    headers = {
        "apikey": fake_secret,
        "Authorization": f"Bearer {fake_secret}",
        "X-Debug-Leak": f"key={fake_secret} pub={fake_pub}",
        "Content-Type": "application/json",
    }
    redacted = redact_headers(headers)
    assert redacted["apikey"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert fake_secret not in redacted["X-Debug-Leak"]
    assert fake_pub not in redacted["X-Debug-Leak"]
    assert redacted["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_no_secrets_in_health_endpoints_or_logs(client: AsyncClient, caplog):
    """KEY-02: Health, readiness, and sources/health responses and logs never leak sb_secret_ or sb_publishable_."""
    import logging

    caplog.set_level(logging.DEBUG)
    fake_secret = "sb_secret_NoLeakInHealthOrLogs_999999"
    fake_pub = "sb_publishable_NoLeakInHealthOrLogs_111111"

    test_settings = Settings(
        SUPABASE_URL="https://noleak.supabase.co",
        SUPABASE_ANON_KEY=fake_pub,
        SUPABASE_SERVICE_ROLE_KEY=fake_secret,
    )

    with patch("app.api.v1.routes.health.get_settings", return_value=test_settings), \
         patch("app.db.session.get_settings", return_value=test_settings), \
         patch("app.services.ingestion_service.get_settings", return_value=test_settings), \
         patch("app.api.v1.routes.health.check_database_connectivity", new_callable=AsyncMock) as mock_db, \
         patch("app.db.session.check_database_connectivity", new_callable=AsyncMock) as mock_sess_db:
        mock_db.return_value = {"status": "connected", "provider": "supabase_postgrest", "table_verified": "districts"}
        mock_sess_db.return_value = {"status": "connected", "provider": "supabase_postgrest", "table_verified": "districts"}

        r_health = await client.get("/api/v1/health")
        r_ready = await client.get("/api/v1/health/readiness")
        r_sources = await client.get("/api/v1/sources/health")

    for resp in (r_health, r_ready, r_sources):
        assert fake_secret not in resp.text
        assert fake_pub not in resp.text
        assert "noleak.supabase.co" not in resp.text

    assert fake_secret not in caplog.text
    assert fake_pub not in caplog.text

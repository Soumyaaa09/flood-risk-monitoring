"""Database session and connection management foundation.

Provides connection configuration and secure, credential-safe health checks
for Supabase PostgreSQL via PostgREST API and direct PostgreSQL drivers.
"""

from typing import Any, Dict, Optional
import httpx
from app.core.config import get_settings
from app.core.http_clients import get_supabase_http_client
from app.db.supabase_client import build_supabase_headers


def get_db_url() -> str:
    """Return database URL from configuration."""
    settings = get_settings()
    return settings.DATABASE_URL


async def check_database_connectivity(
    timeout_seconds: float = 5.0,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Check connectivity to Supabase PostgreSQL database.
    
    Performs a safe, read-only query against the public reference table ('districts').
    Credentials and sensitive tokens are strictly protected and never exposed in the output.
    
    Returns:
        Dict reporting connectivity status ('connected', 'disconnected', 'timeout', or 'not_configured').
    """
    settings = get_settings()
    supabase_url = settings.SUPABASE_URL.strip()
    supabase_key = settings.SUPABASE_ANON_KEY.strip()

    if not supabase_url or not supabase_key:
        return {
            "status": "not_configured",
            "message": "Supabase URL or API key not configured",
        }

    # Query public reference table 'districts' with limit=1 to test RLS read connectivity
    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/districts?select=id&limit=1"
    headers = build_supabase_headers(api_key=supabase_key, include_prefer=False)

    try:
        client = http_client if http_client is not None else get_supabase_http_client()
        resp = await client.get(endpoint, headers=headers, timeout=timeout_seconds)
        if resp.status_code == 200:
            return {
                "status": "connected",
                "provider": "supabase_postgrest",
                "table_verified": "districts",
            }
        else:
            return {
                "status": "disconnected",
                "provider": "supabase_postgrest",
                "message": f"Database endpoint returned HTTP {resp.status_code}",
            }
    except httpx.TimeoutException:
        return {
            "status": "timeout",
            "provider": "supabase_postgrest",
            "message": f"Connection timed out after {timeout_seconds}s",
        }
    except Exception as exc:
        return {
            "status": "disconnected",
            "provider": "supabase_postgrest",
            "message": f"Connection error: {exc.__class__.__name__}",
        }

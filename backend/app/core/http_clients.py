"""Shared asynchronous HTTP client pool lifecycle management.

Maintains dedicated pooled ``httpx.AsyncClient`` instances for Supabase
PostgREST and Open-Meteo endpoints, scoped per running asyncio event loop so
production ASGI lifespan reuses long-lived connections while pytest test
loops remain cleanly isolated.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional
from weakref import WeakKeyDictionary

import httpx

logger = logging.getLogger("floodwatch.http_clients")

SUPABASE_POOL_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)
OPEN_METEO_POOL_LIMITS = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=5,
    keepalive_expiry=30.0,
)

DEFAULT_SUPABASE_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DEFAULT_OPEN_METEO_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_LOOP_CLIENT_REGISTRY: WeakKeyDictionary[
    asyncio.AbstractEventLoop, Dict[str, httpx.AsyncClient]
] = WeakKeyDictionary()


def _create_client(pool_name: str) -> httpx.AsyncClient:
    if pool_name == "supabase":
        return httpx.AsyncClient(
            limits=SUPABASE_POOL_LIMITS,
            timeout=DEFAULT_SUPABASE_TIMEOUT,
        )
    if pool_name == "open_meteo":
        return httpx.AsyncClient(
            limits=OPEN_METEO_POOL_LIMITS,
            timeout=DEFAULT_OPEN_METEO_TIMEOUT,
        )
    raise ValueError(f"Unknown HTTP client pool: {pool_name}")


def _get_or_create_loop_client(pool_name: str) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    loop_clients = _LOOP_CLIENT_REGISTRY.get(loop)
    if loop_clients is None:
        loop_clients = {}
        _LOOP_CLIENT_REGISTRY[loop] = loop_clients

    client = loop_clients.get(pool_name)
    if client is None or client.is_closed:
        client = _create_client(pool_name)
        loop_clients[pool_name] = client
        logger.debug("Initialized HTTPX pool client '%s' for active event loop", pool_name)
    return client


def get_supabase_http_client() -> httpx.AsyncClient:
    """Return the shared pooled ``httpx.AsyncClient`` for Supabase calls."""
    return _get_or_create_loop_client("supabase")


def get_open_meteo_http_client() -> httpx.AsyncClient:
    """Return the shared pooled ``httpx.AsyncClient`` for Open-Meteo calls."""
    return _get_or_create_loop_client("open_meteo")


def init_http_clients() -> Dict[str, httpx.AsyncClient]:
    """Eagerly initialize both HTTPX pools on the current running event loop."""
    return {
        "supabase": get_supabase_http_client(),
        "open_meteo": get_open_meteo_http_client(),
    }


async def close_http_clients() -> int:
    """Close and deregister all pooled HTTPX clients on the active event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0

    loop_clients = _LOOP_CLIENT_REGISTRY.pop(loop, None)
    if not loop_clients:
        return 0

    closed_count = 0
    for pool_name, client in list(loop_clients.items()):
        if client is not None and not client.is_closed:
            await client.aclose()
            closed_count += 1
            logger.debug("Closed HTTPX pool client '%s'", pool_name)
    return closed_count


def get_http_client_registry_stats() -> Dict[str, Any]:
    """Return non-secret inspection statistics for active HTTPX pools."""
    try:
        loop = asyncio.get_running_loop()
        loop_clients = _LOOP_CLIENT_REGISTRY.get(loop, {})
    except RuntimeError:
        loop_clients = {}

    return {
        "active_loop_pools": {
            name: {
                "is_closed": bool(client.is_closed),
            }
            for name, client in loop_clients.items()
        },
        "tracked_loops": len(_LOOP_CLIENT_REGISTRY),
    }

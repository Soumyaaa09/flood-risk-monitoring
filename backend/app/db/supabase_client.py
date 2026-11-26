"""Secure Supabase PostgREST Client for Ingestion and Query Services.

Strict security guidelines:
- Service role key is used exclusively on the backend for trusted operations.
- Never log, print, or expose the service role key.
- Never fall back from service role key to anon key for write operations.
- Gracefully handles absent or mocked Supabase environments.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import get_settings
from app.core.http_clients import get_supabase_http_client
from app.core.sanitizer import sanitize_error_message

logger = logging.getLogger(__name__)


class SupabaseClientError(Exception):
    """Exception raised for Supabase PostgREST communication errors."""

    def __init__(self, message: str, status_code: int = 500):
        safe_message = sanitize_error_message(message) or "Supabase communication error"
        super().__init__(safe_message)
        self.message = safe_message
        self.status_code = status_code


def is_opaque_supabase_key(key: str) -> bool:
    """Return True if key uses Supabase's opaque API key format (sb_secret_ / sb_publishable_)."""
    cleaned = (key or "").strip()
    return cleaned.startswith("sb_secret_") or cleaned.startswith("sb_publishable_")


def build_supabase_headers(
    api_key: str,
    user_jwt: Optional[str] = None,
    include_prefer: bool = True,
) -> Dict[str, str]:
    """Construct protocol-compliant Supabase PostgREST HTTP headers.

    - Opaque keys (sb_secret_... / sb_publishable_...): sent via 'apikey' header only;
      never placed into 'Authorization: Bearer'.
    - Legacy JWT keys (and legacy test keys): sent via both 'apikey' and 'Authorization: Bearer'.
    - Authenticated user session JWT (user_jwt): preserved in 'Authorization: Bearer <user_jwt>'
      when provided alongside either key format.
    """
    clean_key = (api_key or "").strip()
    headers: Dict[str, str] = {
        "apikey": clean_key,
        "Content-Type": "application/json",
    }
    if include_prefer:
        headers["Prefer"] = "return=representation"

    clean_user_jwt = (user_jwt or "").strip()
    if clean_user_jwt and not is_opaque_supabase_key(clean_user_jwt):
        headers["Authorization"] = f"Bearer {clean_user_jwt}"
    elif clean_key and not is_opaque_supabase_key(clean_key):
        headers["Authorization"] = f"Bearer {clean_key}"

    return headers


class SupabaseClient:
    """Client for interacting with Supabase PostgREST endpoints."""

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        service_role_key: Optional[str] = None,
        anon_key: Optional[str] = None,
        timeout_seconds: float = 10.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        settings = get_settings()
        self.base_url = (supabase_url or settings.SUPABASE_URL).rstrip("/")
        
        # Strict credential separation: NEVER fall back from service role key to anon key!
        raw_service_role = service_role_key if service_role_key is not None else settings.SUPABASE_SERVICE_ROLE_KEY
        self.service_role_key = (raw_service_role or "").strip()
        
        raw_anon = anon_key if anon_key is not None else settings.SUPABASE_ANON_KEY
        self.anon_key = (raw_anon or "").strip()
        
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return injected HTTPX client or the shared pooled Supabase client."""
        if self._http_client is not None:
            return self._http_client
        return get_supabase_http_client()

    def _get_headers(
        self, use_service_role: bool = False, user_jwt: Optional[str] = None
    ) -> Dict[str, str]:
        """Generate safe authorization headers without logging credentials.
        
        Enforces separate credentials:
        - If use_service_role is True: strictly requires service_role_key.
        - If use_service_role is False: uses anon_key (or service_role_key if anon_key is not set).
        - Uses build_supabase_headers so opaque keys (sb_secret_ / sb_publishable_) are sent
          via 'apikey' only, while legacy JWTs and user session JWTs preserve 'Authorization: Bearer'.
        """
        if use_service_role:
            if not self.service_role_key:
                raise SupabaseClientError(
                    "Supabase service role credentials not configured. Trusted write operation denied.",
                    status_code=500,
                )
            key = self.service_role_key
        else:
            key = self.anon_key or self.service_role_key

        return build_supabase_headers(api_key=key, user_jwt=user_jwt, include_prefer=True)

    def _validate_service_role_configured(self) -> None:
        """Explicitly validate that service role credential is configured for trusted writes."""
        if not self.service_role_key:
            raise SupabaseClientError(
                "Supabase service role credentials not configured. Write operations require service-role permissions.",
                status_code=500,
            )

    async def get_data_source_by_name(self, name: str = "open_meteo_weather") -> Optional[Dict[str, Any]]:
        """Fetch data source record by unique name using read permissions."""
        if not self.base_url or (not self.anon_key and not self.service_role_key):
            return None

        url = f"{self.base_url}/rest/v1/data_sources?name=eq.{name}&select=*"
        client = self._get_http_client()
        resp = await client.get(url, headers=self._get_headers(use_service_role=False), timeout=self.timeout_seconds)
        if resp.status_code == 200:
            data = resp.json()
            return data[0] if data else None
        raise SupabaseClientError(f"Failed to fetch data source: HTTP {resp.status_code}")

    async def get_pilot_locations(self) -> List[Dict[str, Any]]:
        """Fetch registered virtual weather monitoring locations using service-role permissions.
        
        Note: monitoring_locations is a private operational table inaccessible to anon.
        """
        if not self.base_url or not self.service_role_key:
            return []

        url = (
            f"{self.base_url}/rest/v1/monitoring_locations"
            f"?select=id,district_id,basin_id,name,location_type,latitude,longitude,elevation_m,is_official_gauge,metadata,districts(name)"
            f"&location_type=eq.virtual_weather_point"
            f"&order=name.asc"
        )
        client = self._get_http_client()
        resp = await client.get(url, headers=self._get_headers(use_service_role=True), timeout=self.timeout_seconds)
        if resp.status_code == 200:
            return resp.json()
        raise SupabaseClientError(f"Failed to fetch monitoring locations: HTTP {resp.status_code}")

    async def _recover_ambiguous_ingestion_run(
        self,
        client: httpx.AsyncClient,
        idempotency_key: str,
        expected_data_source_id: str,
        expected_location_id: Optional[str],
    ) -> Dict[str, Any]:
        """Verify and recover an existing ingestion run after an HTTP 409 Conflict.

        Conflict classification rules:
        - Existing matching record in 'started' state created by ambiguous request: recover its ID.
        - Existing record with conflicting location or data source: fail safely (409).
        - Record not found after HTTP 409: return controlled error (409).
        - Lookup timeout or database failure: fail safely without assuming success (504/503).
        - Multiple matching records: treat as data-integrity error (500).
        - Existing record with 'completed' status: do NOT rerun or overwrite; fail safely (409).
        - Existing record with 'failed' status: preserve it and fail safely (409).
        """
        query_url = f"{self.base_url}/rest/v1/ingestion_runs?idempotency_key=eq.{idempotency_key}&select=*"
        headers = self._get_headers(use_service_role=True)

        try:
            query_resp = await client.get(query_url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as err:
            logger.error("Lookup failed during 409 conflict resolution: %s", err.__class__.__name__)
            raise SupabaseClientError(
                "Database lookup failed during conflict verification: network timeout or connection error.",
                status_code=504 if isinstance(err, httpx.TimeoutException) else 503,
            ) from err

        if query_resp.status_code != 200:
            raise SupabaseClientError(
                f"Failed to query existing ingestion run during conflict verification: HTTP {query_resp.status_code}",
                status_code=query_resp.status_code,
            )

        records = query_resp.json()
        if not isinstance(records, list) or len(records) == 0:
            raise SupabaseClientError(
                "Conflict resolution failed: HTTP 409 received but no matching record found with attempt key.",
                status_code=409,
            )

        if len(records) > 1:
            raise SupabaseClientError(
                "Data integrity error: Multiple ingestion runs found with the same idempotency key.",
                status_code=500,
            )

        existing_rec = records[0]

        # 1. Verify exact attempt key
        if str(existing_rec.get("idempotency_key")) != idempotency_key:
            raise SupabaseClientError(
                "Conflict resolution failed: Retrieved run attempt key does not match expected key.",
                status_code=409,
            )

        # 2. Verify data source ID
        if str(existing_rec.get("data_source_id")) != str(expected_data_source_id):
            raise SupabaseClientError(
                "Conflict resolution failed: Existing ingestion run belongs to a different data source.",
                status_code=409,
            )

        # 3. Verify location ID
        rec_loc_id = str(existing_rec.get("location_id")) if existing_rec.get("location_id") is not None else None
        exp_loc_id = str(expected_location_id) if expected_location_id is not None else None
        if rec_loc_id != exp_loc_id:
            raise SupabaseClientError(
                "Conflict resolution failed: Existing ingestion run belongs to a different location.",
                status_code=409,
            )

        # 4. Verify status classification
        existing_status = existing_rec.get("status")
        if existing_status == "completed":
            raise SupabaseClientError(
                "Conflict: Ingestion run with this attempt key has already completed.",
                status_code=409,
            )
        if existing_status == "failed":
            raise SupabaseClientError(
                "Conflict: Ingestion run with this attempt key has already failed.",
                status_code=409,
            )
        if existing_status != "started":
            raise SupabaseClientError(
                f"Conflict: Existing ingestion run has unexpected status '{existing_status}'.",
                status_code=409,
            )

        # 5. Verify presence of valid run ID
        if not existing_rec.get("id"):
            raise SupabaseClientError(
                "Conflict resolution failed: Existing ingestion run record is missing a valid run ID.",
                status_code=500,
            )

        logger.info(
            "Successfully recovered existing 'started' ingestion run '%s' for location '%s' after HTTP 409 conflict.",
            existing_rec["id"],
            expected_location_id,
        )
        return existing_rec

    async def get_ingestion_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve an ingestion run by its exact idempotency/attempt key using service-role credentials.

        Returns the single matching record, None if not found, or raises SupabaseClientError
        if multiple records exist (data integrity error) or if the query fails.
        """
        self._validate_service_role_configured()
        if not self.base_url:
            return None

        url = f"{self.base_url}/rest/v1/ingestion_runs?idempotency_key=eq.{idempotency_key}&select=*"
        client = self._get_http_client()
        resp = await client.get(url, headers=self._get_headers(use_service_role=True), timeout=self.timeout_seconds)
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                return None
            if len(data) > 1:
                raise SupabaseClientError(
                    "Data integrity error: Multiple ingestion runs found with the same idempotency key.",
                    status_code=500,
                )
            return data[0]
        raise SupabaseClientError(
            f"Failed to query ingestion run: HTTP {resp.status_code}", status_code=resp.status_code
        )

    async def get_stale_started_runs(
        self, cutoff_minutes: int = 60
    ) -> List[Dict[str, Any]]:
        """Query for started ingestion runs that have exceeded a reasonable runtime cutoff.

        Read-only inspection for operational monitoring and reconciliation; does NOT automatically
        modify historical database records.
        """
        self._validate_service_role_configured()
        if not self.base_url:
            return []

        cutoff_time = (datetime.now(timezone.utc) - timedelta(minutes=cutoff_minutes)).isoformat()
        url = (
            f"{self.base_url}/rest/v1/ingestion_runs"
            f"?status=eq.started"
            f"&started_at=lt.{cutoff_time}"
            f"&order=started_at.desc"
        )
        client = self._get_http_client()
        resp = await client.get(url, headers=self._get_headers(use_service_role=True), timeout=self.timeout_seconds)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Failed to check for stale started runs: HTTP %s", resp.status_code)
        return []

    async def create_ingestion_run(
        self,
        data_source_id: str,
        location_id: Optional[str],
        idempotency_key: str,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        """Create a new ingestion run record in 'started' state.
        
        Requires explicit service-role authorization.
        
        Reliability & Conflict Handling:
        - Reuses the identical idempotency_key during retries of ambiguous network failures (timeouts/connection errors).
        - Recovers verified existing 'started' run records after duplicate HTTP 409 conflicts.
        - Strict conflict classification rejects runs with conflicting location/data-source or completed/failed statuses.
        - Preserves strict service-role credentials and sanitizes all exception messages.
        """
        self._validate_service_role_configured()
        if not self.base_url:
            raise SupabaseClientError("Supabase URL not configured", status_code=500)

        url = f"{self.base_url}/rest/v1/ingestion_runs"
        payload = {
            "data_source_id": data_source_id,
            "location_id": location_id,
            "status": "started",
            "idempotency_key": idempotency_key,
            "records_fetched": 0,
            "records_inserted": 0,
            "records_updated": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        headers = self._get_headers(use_service_role=True)

        client = self._get_http_client()
        for attempt in range(max_retries + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else payload)

                if resp.status_code == 409:
                    # Ambiguous POST outcome recovery:
                    # Query database for existing record with this exact attempt key
                    # and verify its identity, data source, location, and status.
                    return await self._recover_ambiguous_ingestion_run(
                        client=client,
                        idempotency_key=idempotency_key,
                        expected_data_source_id=data_source_id,
                        expected_location_id=location_id,
                    )

                if resp.status_code in [401, 403]:
                    raise SupabaseClientError(
                        "Supabase service-role authentication failed during run creation.",
                        status_code=resp.status_code,
                    )

                raise SupabaseClientError(
                    f"Failed to create ingestion run: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                if attempt < max_retries:
                    logger.warning(
                        "Ambiguous network failure on run creation (attempt %d/%d). Retrying with original attempt key...",
                        attempt + 1,
                        max_retries + 1,
                    )
                    continue
                raise SupabaseClientError(
                    "Network error or timeout during ingestion run creation after retries.",
                    status_code=504 if isinstance(net_err, httpx.TimeoutException) else 503,
                ) from net_err

    async def update_ingestion_run(
        self,
        run_id: str,
        status: str,
        records_fetched: int,
        records_inserted: int,
        records_updated: int,
        data_freshness_timestamp: Optional[datetime] = None,
        error_message: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update operational audit counters and status for an ingestion run.
        
        Requires explicit service-role authorization.
        """
        self._validate_service_role_configured()
        if not self.base_url:
            raise SupabaseClientError("Supabase URL not configured", status_code=500)

        url = f"{self.base_url}/rest/v1/ingestion_runs?id=eq.{run_id}"
        payload: Dict[str, Any] = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "records_fetched": max(records_fetched, 0),
            "records_inserted": max(records_inserted, 0),
            "records_updated": max(records_updated, 0),
        }
        if data_freshness_timestamp:
            payload["data_freshness_timestamp"] = data_freshness_timestamp.isoformat()
        if error_message:
            payload["error_message"] = sanitize_error_message(error_message)[:1000]

        client = self._get_http_client()
        resp = await client.patch(
            url,
            json=payload,
            headers=self._get_headers(use_service_role=True),
            timeout=self.timeout_seconds,
        )
        if resp.status_code in [200, 204]:
            return payload
        raise SupabaseClientError(f"Failed to update ingestion run: HTTP {resp.status_code}")

    async def persist_weather_observations(
        self,
        observations: List[Dict[str, Any]],
        ingestion_run_id: str,
    ) -> Dict[str, int]:
        """Persist weather observation records to Supabase.
        
        Strategy:
        1. Validates service-role credentials.
        2. Attempts to execute Migration 003 RPC function 'upsert_weather_observations' (atomic & transactional).
        3. If RPC is not found (HTTP 404), falls back to direct PostgREST in development/test environments.
           Fallback is explicitly blocked in production to prevent non-atomic writes.
        """
        self._validate_service_role_configured()
        if not self.base_url:
            raise SupabaseClientError("Supabase URL not configured", status_code=500)

        if not observations:
            return {"records_fetched": 0, "records_inserted": 0, "records_updated": 0}

        # 1. Try PostgreSQL RPC
        rpc_url = f"{self.base_url}/rest/v1/rpc/upsert_weather_observations"
        rpc_payload = {
            "p_observations": observations,
            "p_ingestion_run_id": ingestion_run_id,
        }

        client = self._get_http_client()
        try:
            rpc_resp = await client.post(
                rpc_url,
                json=rpc_payload,
                headers=self._get_headers(use_service_role=True),
                timeout=self.timeout_seconds * 2,
            )
            if rpc_resp.status_code == 200:
                result = rpc_resp.json()
                return {
                    "records_fetched": result.get("records_fetched", len(observations)),
                    "records_inserted": result.get("records_inserted", 0),
                    "records_updated": result.get("records_updated", 0),
                }
            elif rpc_resp.status_code != 404:
                logger.warning("RPC upsert returned HTTP %s", rpc_resp.status_code)
                if get_settings().ENVIRONMENT.lower() in ["production", "prod"]:
                    raise SupabaseClientError(
                        f"Production ingestion RPC failed with HTTP {rpc_resp.status_code}",
                        status_code=rpc_resp.status_code,
                    )
        except Exception as exc:
            if isinstance(exc, SupabaseClientError):
                raise
            logger.warning("RPC upsert attempt error: %s. Evaluating persistence path.", exc.__class__.__name__)
            if get_settings().ENVIRONMENT.lower() in ["production", "prod"]:
                raise SupabaseClientError(
                    "Production ingestion requires Migration 003 RPC ('upsert_weather_observations') "
                    "for transactional atomicity and concurrency protection. "
                    "Apply database/migrations/003_weather_ingestion_rpc.sql to Supabase before live ingestion."
                ) from exc

        # In production, fallback is disabled to prevent non-atomic concurrent writes
        if get_settings().ENVIRONMENT.lower() in ["production", "prod"]:
            raise SupabaseClientError(
                "Production ingestion requires Migration 003 RPC ('upsert_weather_observations') "
                "to ensure transactional atomicity. Fallback is disabled in production."
            )

        # 2. Development / Testing Fallback: PostgREST direct persistence with bounded retry
        return await self._persist_records_direct(observations, ingestion_run_id)

    async def _persist_records_direct(
        self,
        observations: List[Dict[str, Any]],
        ingestion_run_id: str,
    ) -> Dict[str, int]:
        """Client-side fallback persistence with conflict retry handling.
        
        NOTE: This fallback is NOT transactionally atomic across records and must NEVER
        be used for production live ingestion. It provides development/test resilience.
        """
        self._validate_service_role_configured()

        inserted = 0
        updated = 0

        client = self._get_http_client()
        headers = self._get_headers(use_service_role=True)
        for obs in observations:
            loc_id = obs["location_id"]
            ds_id = obs["data_source_id"]
            meas_type = obs.get("measurement_type", "rainfall")
            obs_type = obs["observation_type"]
            valid_at = obs["valid_at"]
            ref_time = obs.get("forecast_reference_time")

            # Build query targeting the exact partial index predicate
            query_url = (
                f"{self.base_url}/rest/v1/observations"
                f"?location_id=eq.{loc_id}"
                f"&data_source_id=eq.{ds_id}"
                f"&measurement_type=eq.{meas_type}"
                f"&observation_type=eq.{obs_type}"
                f"&valid_at=eq.{valid_at}"
            )
            if obs_type == "forecast":
                if ref_time is None:
                    # Targets partial index uq_observations_forecast_active
                    query_url += "&forecast_reference_time=is.null"
                else:
                    # Targets partial index uq_observations_forecast_cycle
                    query_url += f"&forecast_reference_time=eq.{ref_time}"

            query_url += "&select=id,value,unit"

            get_resp = await client.get(query_url, headers=headers, timeout=self.timeout_seconds)
            if get_resp.status_code != 200:
                raise SupabaseClientError(
                    f"Direct observation query failed with HTTP {get_resp.status_code}",
                    status_code=get_resp.status_code,
                )

            existing = get_resp.json()

            if existing:
                # Update existing record (Triggers trg_observations_forecast_revision on value/unit change)
                rec_id = existing[0]["id"]
                patch_url = f"{self.base_url}/rest/v1/observations?id=eq.{rec_id}"
                patch_payload = {
                    "value": obs["value"],
                    "unit": obs["unit"],
                    "fetched_at": obs["fetched_at"],
                    "ingestion_run_id": ingestion_run_id,
                    "metadata": obs.get("metadata", {}),
                }
                patch_resp = await client.patch(
                    patch_url, json=patch_payload, headers=headers, timeout=self.timeout_seconds
                )
                if patch_resp.status_code not in [200, 204]:
                    raise SupabaseClientError(
                        f"Direct observation patch failed with HTTP {patch_resp.status_code}",
                        status_code=patch_resp.status_code,
                    )
                updated += 1
            else:
                # Insert new record
                post_url = f"{self.base_url}/rest/v1/observations"
                post_payload = dict(obs)
                post_payload["ingestion_run_id"] = ingestion_run_id
                post_resp = await client.post(
                    post_url, json=post_payload, headers=headers, timeout=self.timeout_seconds
                )
                if post_resp.status_code in [200, 201]:
                    inserted += 1
                elif post_resp.status_code == 409:
                    # Concurrency race: Another process inserted this observation between GET and POST.
                    # Bounded retries to recover gracefully.
                    persisted = False
                    for retry_attempt in range(3):
                        retry_resp = await client.get(
                            query_url, headers=headers, timeout=self.timeout_seconds
                        )
                        if retry_resp.status_code == 200:
                            existing_retry = retry_resp.json()
                            if existing_retry:
                                rec_id = existing_retry[0]["id"]
                                patch_url = f"{self.base_url}/rest/v1/observations?id=eq.{rec_id}"
                                patch_payload = {
                                    "value": obs["value"],
                                    "unit": obs["unit"],
                                    "fetched_at": obs["fetched_at"],
                                    "ingestion_run_id": ingestion_run_id,
                                    "metadata": obs.get("metadata", {}),
                                }
                                patch_retry = await client.patch(
                                    patch_url,
                                    json=patch_payload,
                                    headers=headers,
                                    timeout=self.timeout_seconds,
                                )
                                if patch_retry.status_code in [200, 204]:
                                    updated += 1
                                    persisted = True
                                    break
                    if not persisted:
                        raise SupabaseClientError(
                            "Failed to resolve 409 conflict during direct observation persistence.",
                            status_code=409,
                        )
                else:
                    raise SupabaseClientError(
                        f"Direct observation insert failed with HTTP {post_resp.status_code}",
                        status_code=post_resp.status_code,
                    )

        return {
            "records_fetched": len(observations),
            "records_inserted": inserted,
            "records_updated": updated,
        }

    async def get_latest_observations_for_location(
        self, location_id: str, limit: int = 72
    ) -> Dict[str, Any]:
        """Fetch current analysis and active hourly forecast horizon using service-role permissions.
        
        Note: observations is a private operational table inaccessible to anon.
        """
        if not self.base_url or not self.service_role_key:
            return {"analysis": None, "forecasts": []}

        # 1. Fetch current analysis
        analysis_url = (
            f"{self.base_url}/rest/v1/observations"
            f"?location_id=eq.{location_id}"
            f"&observation_type=eq.analysis"
            f"&measurement_type=eq.rainfall"
            f"&order=valid_at.desc"
            f"&limit=1"
        )

        # 2. Fetch active forecasts
        forecast_url = (
            f"{self.base_url}/rest/v1/observations"
            f"?location_id=eq.{location_id}"
            f"&observation_type=eq.forecast"
            f"&measurement_type=eq.rainfall"
            f"&forecast_reference_time=is.null"
            f"&order=valid_at.asc"
            f"&limit={limit}"
        )

        client = self._get_http_client()
        headers = self._get_headers(use_service_role=True)
        try:
            analysis_resp, forecast_resp = await asyncio.gather(
                client.get(analysis_url, headers=headers, timeout=self.timeout_seconds),
                client.get(forecast_url, headers=headers, timeout=self.timeout_seconds),
            )
        except httpx.TimeoutException as exc:
            logger.error("Timeout fetching latest observations for '%s'", location_id)
            raise SupabaseClientError(
                f"Timed out retrieving latest observations: {exc.__class__.__name__}",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            logger.error(
                "Network error fetching latest observations for '%s': %s",
                location_id,
                exc.__class__.__name__,
            )
            raise SupabaseClientError(
                f"Network error retrieving latest observations: {exc.__class__.__name__}",
                status_code=503,
            ) from exc

        if analysis_resp.status_code != 200:
            raise SupabaseClientError(
                f"Failed to fetch latest analysis observation: HTTP {analysis_resp.status_code}",
                status_code=analysis_resp.status_code,
            )
        if forecast_resp.status_code != 200:
            raise SupabaseClientError(
                f"Failed to fetch latest forecast observations: HTTP {forecast_resp.status_code}",
                status_code=forecast_resp.status_code,
            )

        analysis_rows = analysis_resp.json()
        forecast_rows = forecast_resp.json()
        analysis = analysis_rows[0] if isinstance(analysis_rows, list) and analysis_rows else None
        forecasts = forecast_rows if isinstance(forecast_rows, list) else []

        return {"analysis": analysis, "forecasts": forecasts}

    MAX_HISTORICAL_OBSERVATIONS_LIMIT = 5000
    HISTORICAL_OBSERVATION_TYPES = frozenset({"observed", "analysis", "reanalysis"})

    @staticmethod
    def _parse_utc_timestamp(raw_ts: Any) -> Optional[datetime]:
        """Parse an ISO-8601 string or datetime into a timezone-aware UTC datetime."""
        if raw_ts is None:
            return None
        if isinstance(raw_ts, datetime):
            dt = raw_ts
        elif isinstance(raw_ts, str):
            cleaned = raw_ts.strip()
            if not cleaned:
                return None
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            elif "+" not in cleaned and "T" in cleaned and "-" not in cleaned.split("T", 1)[1]:
                cleaned += "+00:00"
            try:
                dt = datetime.fromisoformat(cleaned)
            except ValueError:
                return None
        else:
            return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    async def get_historical_observations_for_location(
        self,
        location_id: str,
        days: int = 7,
        limit: int = 2000,
        offset: int = 0,
        reference_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch historical rainfall observations (analysis, observed, reanalysis) strictly excluding forecasts.

        Applies a UTC lower-bound timestamp filter derived from ``days``, queries newest-first so
        recent records are not lost to ascending-order truncation when row volume approaches ``limit``,
        and returns records in ascending chronological order. Uses service-role permissions as
        ``observations`` is a private operational table.
        """
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("Historical lookback 'days' must be a positive integer >= 1.")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Query 'limit' must be a positive integer >= 1.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Query 'offset' must be a non-negative integer >= 0.")

        if not self.base_url or not self.service_role_key:
            return []

        clean_loc_id = (location_id or "").strip()
        if not clean_loc_id:
            raise ValueError("Location ID must not be empty.")

        ref_utc = self._parse_utc_timestamp(reference_time) or datetime.now(timezone.utc)
        cutoff_dt = ref_utc - timedelta(days=days)
        cutoff_iso = cutoff_dt.isoformat().replace("+00:00", "Z")
        effective_limit = min(limit, self.MAX_HISTORICAL_OBSERVATIONS_LIMIT)

        url = (
            f"{self.base_url}/rest/v1/observations"
            f"?location_id=eq.{clean_loc_id}"
            f"&measurement_type=eq.rainfall"
            f"&observation_type=in.(observed,analysis,reanalysis)"
            f"&valid_at=gte.{cutoff_iso}"
            f"&order=valid_at.desc"
            f"&limit={effective_limit}"
        )
        if offset > 0:
            url += f"&offset={offset}"

        try:
            client = self._get_http_client()
            headers = self._get_headers(use_service_role=True)
            resp = await client.get(url, headers=headers, timeout=self.timeout_seconds)
        except httpx.TimeoutException as exc:
            logger.error("Timeout fetching historical observations for '%s'", clean_loc_id)
            raise SupabaseClientError(
                f"Timed out retrieving historical observations: {exc.__class__.__name__}",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            logger.error(
                "Network error fetching historical observations for '%s': %s",
                clean_loc_id,
                exc.__class__.__name__,
            )
            raise SupabaseClientError(
                f"Network error retrieving historical observations: {exc.__class__.__name__}",
                status_code=503,
            ) from exc

        if resp.status_code != 200:
            raise SupabaseClientError(
                f"Failed to fetch historical observations: HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        try:
            raw_records = resp.json()
        except Exception as exc:
            raise SupabaseClientError(
                "Invalid JSON payload received for historical observations.",
                status_code=502,
            ) from exc

        if not isinstance(raw_records, list):
            raise SupabaseClientError(
                "Unexpected historical observations payload structure from Supabase.",
                status_code=502,
            )

        filtered_with_dt: List[tuple[datetime, str, Dict[str, Any]]] = []
        for rec in raw_records:
            if not isinstance(rec, dict):
                continue
            obs_type = str(rec.get("observation_type") or "").lower()
            if obs_type and obs_type not in self.HISTORICAL_OBSERVATION_TYPES:
                continue
            valid_dt = self._parse_utc_timestamp(rec.get("valid_at"))
            if valid_dt is None or valid_dt < cutoff_dt:
                continue
            fetched_key = str(rec.get("fetched_at") or "")
            filtered_with_dt.append((valid_dt, fetched_key, rec))

        # Restore ascending chronological order (oldest -> newest) for downstream analyzers
        filtered_with_dt.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in filtered_with_dt]

    async def get_ingestion_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch recent ingestion runs sorted by start time using service-role permissions."""
        if not self.base_url or not self.service_role_key:
            return []

        url = f"{self.base_url}/rest/v1/ingestion_runs?order=started_at.desc&limit={limit}"
        client = self._get_http_client()
        resp = await client.get(url, headers=self._get_headers(use_service_role=True), timeout=self.timeout_seconds)
        return resp.json() if resp.status_code == 200 else []

    async def get_ingestion_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch specific ingestion run by UUID using service-role permissions."""
        if not self.base_url or not self.service_role_key:
            return None

        url = f"{self.base_url}/rest/v1/ingestion_runs?id=eq.{run_id}"
        client = self._get_http_client()
        resp = await client.get(url, headers=self._get_headers(use_service_role=True), timeout=self.timeout_seconds)
        data = resp.json() if resp.status_code == 200 else []
        return data[0] if data else None

    # =========================================================================
    # Phase 2.7: Risk Alerts & In-App Notification Methods
    # =========================================================================

    RISK_ALERTS_COLUMNS = frozenset({
        "id",
        "location_id",
        "alert_type",
        "severity",
        "status",
        "title",
        "description",
        "evidence",
        "fingerprint",
        "created_at",
        "updated_at",
        "acknowledged_at",
        "acknowledged_by",
        "resolved_at",
        "resolved_reason",
    })

    async def _execute_alerts_get(self, url: str) -> List[Dict[str, Any]]:
        """Execute a PostgREST GET query against risk_alerts/v_public_risk_alerts with strict error propagation."""
        if not self.service_role_key and not self.anon_key:
            raise SupabaseClientError(
                "Supabase API credentials not configured. Alert query denied.",
                status_code=500,
            )

        headers = self._get_headers(use_service_role=bool(self.service_role_key))
        try:
            client = self._get_http_client()
            resp = await client.get(url, headers=headers, timeout=self.timeout_seconds)
        except httpx.TimeoutException as exc:
            logger.error("Timeout querying alerts from Supabase")
            raise SupabaseClientError(
                f"Timed out querying alerts: {exc.__class__.__name__}",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Network error querying alerts from Supabase: %s", exc.__class__.__name__)
            raise SupabaseClientError(
                f"Network error querying alerts: {exc.__class__.__name__}",
                status_code=503,
            ) from exc

        if resp.status_code != 200:
            raise SupabaseClientError(
                f"Failed to query alerts from Supabase: HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except Exception as exc:
            raise SupabaseClientError(
                "Invalid JSON response received when querying alerts.",
                status_code=502,
            ) from exc

        if not isinstance(payload, list):
            raise SupabaseClientError(
                "Unexpected alerts payload structure received from Supabase.",
                status_code=502,
            )

        return payload

    async def get_alerts(
        self,
        location_id: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch alert records with optional filtering and pagination."""
        if not self.base_url:
            return []

        query_params = ["order=created_at.desc", f"limit={limit}", f"offset={offset}"]
        if location_id:
            query_params.append(f"location_id=eq.{location_id}")
        if status:
            query_params.append(f"status=eq.{status}")
        if severity:
            query_params.append(f"severity=eq.{severity}")
        if alert_type:
            query_params.append(f"alert_type=eq.{alert_type}")

        # Migration 005: Read from sanitized public view v_public_risk_alerts unless using service_role
        target_entity = "risk_alerts" if self.service_role_key else "v_public_risk_alerts"
        url = f"{self.base_url}/rest/v1/{target_entity}?{'&'.join(query_params)}"
        return await self._execute_alerts_get(url)

    async def get_alert_by_id(self, alert_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific alert record by UUID."""
        if not self.base_url:
            return None

        target_entity = "risk_alerts" if self.service_role_key else "v_public_risk_alerts"
        url = f"{self.base_url}/rest/v1/{target_entity}?id=eq.{alert_id}"
        data = await self._execute_alerts_get(url)
        return data[0] if data else None

    async def get_open_alerts_for_location(self, location_id: str) -> List[Dict[str, Any]]:
        """Fetch currently open (NEW, ACTIVE, ACKNOWLEDGED) alerts for a location.

        Requires trusted service-role credentials; never falls back to anon key or
        returns an empty list on database/credential failure.
        """
        if not self.base_url:
            raise SupabaseClientError(
                "Supabase URL not configured. Trusted open-alert lookup denied.",
                status_code=500,
            )
        self._validate_service_role_configured()

        url = (
            f"{self.base_url}/rest/v1/risk_alerts"
            f"?location_id=eq.{location_id}"
            f"&status=in.(NEW,ACTIVE,ACKNOWLEDGED)"
            f"&order=created_at.desc"
        )
        headers = self._get_headers(use_service_role=True)

        try:
            client = self._get_http_client()
            resp = await client.get(url, headers=headers, timeout=self.timeout_seconds)
            if resp.status_code != 200:
                raise SupabaseClientError(
                    f"Failed to query open alerts for location '{location_id}': HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )
            return resp.json()
        except SupabaseClientError:
            raise
        except Exception as exc:
            logger.warning("Error querying open alerts for '%s': %s", location_id, exc.__class__.__name__)
            raise SupabaseClientError(
                f"Database error querying open alerts: {exc.__class__.__name__}",
                status_code=503,
            ) from exc

    async def create_alert(self, alert_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new alert record using service-role permissions."""
        self._validate_service_role_configured()

        url = f"{self.base_url}/rest/v1/risk_alerts"
        headers = self._get_headers(use_service_role=True)
        headers["Prefer"] = "return=representation"

        # Strictly project only canonical risk_alerts table columns (exclude UI/joined fields)
        db_payload = {k: v for k, v in alert_dict.items() if k in self.RISK_ALERTS_COLUMNS}

        client = self._get_http_client()
        resp = await client.post(url, json=db_payload, headers=headers, timeout=self.timeout_seconds)
        if resp.status_code not in [200, 201]:
            raise SupabaseClientError(
                f"Failed to create alert record: HTTP {resp.status_code}",
                status_code=resp.status_code,
            )
        data = resp.json()
        return data[0] if isinstance(data, list) and data else db_payload

    async def update_alert(self, alert_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update fields on an existing alert using service-role permissions."""
        self._validate_service_role_configured()

        url = f"{self.base_url}/rest/v1/risk_alerts?id=eq.{alert_id}"
        headers = self._get_headers(use_service_role=True)
        headers["Prefer"] = "return=representation"

        payload = {k: v for k, v in updates.items() if k in self.RISK_ALERTS_COLUMNS}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()

        client = self._get_http_client()
        resp = await client.patch(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        if resp.status_code not in [200, 204]:
            raise SupabaseClientError(
                f"Failed to update alert '{alert_id}': HTTP {resp.status_code}",
                status_code=resp.status_code,
            )
        data = resp.json() if resp.status_code == 200 else []
        return data[0] if isinstance(data, list) and data else payload

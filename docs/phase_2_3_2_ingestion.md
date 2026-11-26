# FloodWatch — Phase 2.3.2: Real Weather Data Ingestion Specification & Implementation

**Version:** 1.0.0 (Phase 2.3.2 Final Release)  
**Status:** Implemented, Tested, and Verified Locally  
**Disclaimer:** FloodWatch is an academic, research, and decision-support prototype for flood monitoring in Odisha. It is **NOT** an official emergency-warning service.

---

## 1. Executive Summary & Objectives

Phase 2.3.2 delivers an end-to-end, resilient weather ingestion pipeline that:
1. Connects to the **Open-Meteo Weather Forecast API** for all three pilot centroids in Odisha:
   - **Paradeep Weather Grid Point** (Jagatsinghpur): `20.3160°N, 86.6110°E`
   - **Cuttack Weather Grid Point** (Cuttack): `20.4625°N, 85.8830°E`
   - **Kendrapara Weather Grid Point** (Kendrapara): `20.5017°N, 86.4223°E`
2. Enforces **strict scientific taxonomy**:
   - Open-Meteo `current.precipitation` is classified as **`analysis`** (model-derived nowcast estimate). It is **never** labeled as physical station telemetry (`observed`).
   - Open-Meteo `hourly.precipitation` is classified as **`forecast`** for future hours (`valid_at >= current_time`).
   - `forecast_reference_time` remains **`NULL`** because Open-Meteo does not expose a model initialization timestamp.
   - Past hourly records are filtered out.
3. Solves PostgREST's partial unique index limitation through a dual persistence strategy:
   - **Primary:** Atomic PostgreSQL RPC `upsert_weather_observations` ([`database/migrations/003_weather_ingestion_rpc.sql`](file:///c:/Users/rsoum/Desktop/FloodWatch/database/migrations/003_weather_ingestion_rpc.sql)).
   - **Fallback:** Direct PostgREST record reconciliation that activates the PostgreSQL trigger `trg_observations_forecast_revision` on forecast updates.
4. Protects secrets and operations with backend token authorization (`INGESTION_TRIGGER_TOKEN`) and service-role privilege isolation.

---

## 2. Scientific Data Classification & Accumulation Intervals

| Provider Metric | Canonical Type | Measurement Type | Unit | Temporal Target (`valid_at`) | Provider Accumulation Interval | Classification Rules |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `current.precipitation` | **`analysis`** | `rainfall` | `mm` | `current.time` (UTC) | 15 minutes (900 seconds) | Model-derived estimate; **never** label as `observed`. |
| `hourly.precipitation` | **`forecast`** | `rainfall` | `mm` | `hourly.time[i]` (UTC) | 1 hour | Future predictions only; `forecast_reference_time = NULL`. |

> [!IMPORTANT]
> **Accumulation Interval Distinction:**
> - Current precipitation represents a **15-minute** model-derived estimate.
> - Hourly precipitation represents a **1-hour** cumulative depth.
> These two metrics must never be summed directly without interval normalization.
> Rainfall measurements represent atmospheric precipitation depth and must **never** be interpreted alone as river water levels or discharge.

---

## 3. Database Persistence Strategy & RPC Justification

### The PostgREST Partial Unique Index Problem
In Migration 002, active forecast and analysis records are protected by partial unique indexes:
- `uq_observations_forecast_active` on `(location_id, data_source_id, measurement_type, valid_at)` WHERE `observation_type = 'forecast' AND forecast_reference_time IS NULL`
- `uq_observations_analysis` on `(location_id, data_source_id, measurement_type, valid_at)` WHERE `observation_type = 'analysis'`

PostgREST's standard `POST /table?on_conflict=...` parameter only accepts column names or table-level constraint names. It cannot express PostgreSQL partial index predicates (`WHERE observation_type = ...`). Attempting a standard PostgREST upsert raises:
```
ERROR: 42P10: there is no unique or exclusion constraint matching the ON CONFLICT specification
```

### The Dual Persistence Solution
1. **Migration 003 (`upsert_weather_observations` RPC):**
   - Implemented in [`database/migrations/003_weather_ingestion_rpc.sql`](file:///c:/Users/rsoum/Desktop/FloodWatch/database/migrations/003_weather_ingestion_rpc.sql).
   - Executes atomic SQL upserts targeting each partial unique index predicate explicitly.
   - Detects inserts vs updates via `(xmax = 0)`.
   - Triggers `trg_observations_forecast_revision` on value/unit changes, archiving historical revisions into `forecast_observation_revisions`.
2. **Direct PostgREST Fallback:**
   - Client-side reconciliation querying existing records by natural key.
   - Updates via `PATCH /observations?id=eq.{id}`, which fires the PostgreSQL trigger.
   - Guarantees pipeline continuity even before Migration 003 is executed remotely.

---

## 4. Idempotency & Fault Tolerance

1. **Ingestion Run Idempotency Key:**
   - Format: `openmeteo_{location_id}_{YYYYMMDDHH}`
   - Guarantees that repeated runs within the same operational cycle reference the same logical execution.
2. **Exponential Backoff & Retries:**
   - Bounded retries (default 3 attempts) for transient network timeouts and HTTP 5xx errors.
   - Bounded backoff delay ($t = \text{factor} \times 2^{\text{attempt}-1}$).
   - Immediate abort on HTTP 4xx client errors (no endless retry cycles).
3. **Partial Failure Isolation:**
   - Locations are processed in an isolated loop.
   - If Paradeep encounters a timeout, Kendrapara and Cuttack proceed without interruption.
   - The overall ingestion run is marked `partial` if some locations succeed and others fail.

---

## 5. Security & Service-Role Isolation

1. **`SUPABASE_SERVICE_ROLE_KEY`:**
   - Kept exclusively in `backend/.env`.
   - Never placed in frontend code, git commits, or API responses.
   - `SourceHealthResponse` confirms configuration state with a boolean (`service_role_configured: true`), redacting the actual key string.
2. **Ingestion Trigger Authentication:**
   - Endpoint: `POST /api/v1/ingestion/trigger`
   - Protected with `INGESTION_TRIGGER_TOKEN`.
   - Authenticates via header `X-Ingestion-Token` or query parameter `token`.
   - Validated using constant-time string comparison (`secrets.compare_digest`) to prevent timing attacks.

---

## 6. How to Run Ingestion Locally

### Step 1: Start Backend Server
```powershell
Set-Location "c:\Users\rsoum\Desktop\FloodWatch\backend"
& ".\.venv\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port 8000
```

### Step 2: Trigger Ingestion via API
```powershell
$Headers = @{
    "X-Ingestion-Token" = "floodwatch-dev-secret-token"
}
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/ingestion/trigger" -Method Post -Headers $Headers
```

### Step 3: Verify Ingested Records & Freshness
```powershell
# List registered pilot locations
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/weather/locations" -Method Get

# Check latest weather & freshness for a location
$LocId = "11111111-1111-1111-1111-111111111111"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/weather/latest/$LocId" -Method Get

# Check recent ingestion runs and counters
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/ingestion/runs" -Method Get
```

---

## 7. Automated Test Coverage (Phase 8)

All 17 prompt test requirements are implemented in [`backend/tests/test_ingestion.py`](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_ingestion.py):

1. **Open-Meteo successful response:** `test_open_meteo_success_and_classification`
2. **Invalid provider response:** `test_open_meteo_invalid_provider_response`
3. **Missing precipitation data:** `test_open_meteo_missing_precipitation`
4. **Negative precipitation rejection:** `test_open_meteo_negative_precipitation_rejection`
5. **HTTP timeout:** `test_open_meteo_http_timeout`
6. **HTTP 5xx retry with backoff:** `test_open_meteo_5xx_retry_with_backoff`
7. **Permanent HTTP 4xx failure:** `test_open_meteo_permanent_4xx_failure`
8. **Current analysis classification:** `test_open_meteo_success_and_classification`
9. **Future forecast classification:** `test_open_meteo_success_and_classification`
10. **Forecast reference time remains NULL:** `test_open_meteo_success_and_classification`
11. **Interval metadata preservation:** `test_open_meteo_success_and_classification`
12. **Duplicate protection & counters:** `test_ingestion_counters_and_status`
13. **Forecast revision behavior:** `test_forecast_revision_direct_persistence_behavior`
14. **Partial location failure resilience:** `test_partial_location_failure_resilience`
15. **Ingestion counters and status:** `test_ingestion_counters_and_status`
16. **Credential redaction:** `test_credential_redaction_in_health_and_errors`
17. **Ingestion trigger authorization:** `test_ingestion_trigger_authorization`

**Execution Result:** 24/24 tests passed in 7.83s.

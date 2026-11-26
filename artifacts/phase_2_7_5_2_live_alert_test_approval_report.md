# FloodWatch — Phase 2.7.5.2: Final Live Alert Test Preparation & Approval Report

**Date:** 2026-11-25  
**Author:** Senior Full-Stack Integration Engineer, Supabase Database Engineer, FastAPI QA Engineer  
**Project:** FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform  
**Objective:** Finalize read-only preflight, schema projection verification, exact API/payload sequence, and side-effect audit prior to project owner authorization  
**Verdict:** **`READY FOR OWNER APPROVAL`**

---

## 1. Executive Summary

Following the resolution of `SCHEMA-01` in Phase 2.7.5.1, all read-only preflight checks and schema projection checks have passed against the configured live Supabase backend and active FastAPI server (`127.0.0.1:8000`).

- **Live Schema & Connectivity:** `public.risk_alerts` (`15` canonical columns) returns `HTTP 200 OK` via `sb_secret_...` (`apikey`-only header), and `public.v_public_risk_alerts` (`13` public columns) returns `HTTP 200 OK` via `sb_publishable_...`.
- **Target Location Confirmed:** `Paradeep Weather Grid Point` (`3b493f43-58a7-4786-bedf-39d82b9bc260`, District: `Jagatsinghpur`), with **`0` existing alerts** in `public.risk_alerts`.
- **Approval Gate Status:** **STOPPED.** Zero live records were created, acknowledged, resolved, or deleted. Awaiting explicit project owner approval to execute the controlled mutation workflow below.

---

## 2. Read-Only Preflight Verification Results

| Check | Target / Endpoint | Result | Verified State |
| :--- | :--- | :--- | :--- |
| **15-Column Base Projection** | `GET /rest/v1/risk_alerts?select=<15_cols>&limit=0` (`sb_secret_...`) | **`200 OK`** | Matches `SupabaseClient.RISK_ALERTS_COLUMNS` 1-to-1. |
| **Public View Projection** | `GET /rest/v1/v_public_risk_alerts?limit=1` (`sb_publishable_...`) | **`200 OK`** | `13` approved public columns accessible. |
| **Pilot Locations** | `SupabaseClient.get_pilot_locations()` | **`200 OK`** | `3` stations verified (`Cuttack`, `Kendrapara`, `Paradeep`). |
| **Existing Alerts** | `SupabaseClient.get_alerts(limit=500)` | **`200 OK`** | **`0` rows** (`0` active, `0` acknowledged, `0` resolved). |
| **FastAPI Health** | `GET http://127.0.0.1:8000/api/v1/health` | **`200 OK`** | `status: "ok"`, `database.status: "connected"`, `table_verified: "districts"`. |
| **FastAPI Readiness** | `GET http://127.0.0.1:8000/api/v1/health/readiness` | **`200 OK`** | `status: "ready"`, `database: "connected"`. |
| **FastAPI Sources Health** | `GET http://127.0.0.1:8000/api/v1/sources/health` | **`200 OK`** | `status: "healthy"`, `service_role_configured: true`. |
| **Public Alerts API** | `GET http://127.0.0.1:8000/api/v1/alerts` | **`200 OK`** | `total: 0`, `alerts: []`. |

---

## 3. Exact Proposed Synthetic Test Alert Payload & Target Location

### 3.1 Target Monitoring Location (Verified in `public.monitoring_locations`)
- **`location_id`:** `3b493f43-58a7-4786-bedf-39d82b9bc260`
- **Station Name:** `Paradeep Weather Grid Point`
- **District Name:** `Jagatsinghpur`
- **Existing Alerts on Station:** `0`

### 3.2 Exact 15-Column `public.risk_alerts` Insert Payload
Generated through `AlertService.evaluate_location_assessment()` using a synthetic `RainfallRiskAssessmentResponse` with `location_name = "[TEST-AUDIT] Paradeep Weather Grid Point"` and `risk_category = RiskCategory.HIGH`:

```json
{
  "id": "<UUID_V4>",
  "location_id": "3b493f43-58a7-4786-bedf-39d82b9bc260",
  "alert_type": "HIGH_RAINFALL_RISK",
  "severity": "HIGH",
  "status": "NEW",
  "title": "High Rainfall Runoff Potential: [TEST-AUDIT] Paradeep Weather Grid Point",
  "description": "Substantial rainfall detected at [TEST-AUDIT] Paradeep Weather Grid Point (Jagatsinghpur) with high surface runoff potential (0.0 mm observed, 0.0 mm 24h forecast).",
  "evidence": {
    "risk_category": "HIGH",
    "confidence": "HIGH",
    "recent_observed_mm": 0.0,
    "forecast_6h_mm": 0.0,
    "forecast_24h_mm": 0.0,
    "observation_freshness": "fresh",
    "observation_age_minutes": 5.0,
    "forecast_completeness_pct": 100.0,
    "antecedent_category": "MODERATE_ANTECEDENT",
    "trigger_reason": "Evaluated from HIGH risk category."
  },
  "fingerprint": "3b493f43-58a7-4786-bedf-39d82b9bc260:HIGH_RAINFALL_RISK",
  "created_at": "<UTC_ISO8601>",
  "updated_at": "<UTC_ISO8601>",
  "acknowledged_at": null,
  "acknowledged_by": null,
  "resolved_at": null,
  "resolved_reason": null
}
```

- **Schema & Constraint Alignment:** Strictly 15 columns (`location_name` and `district_name` omitted from DB insert; enriched on API reads via `monitoring_locations`). Complies with all Migration 005 CHECK constraints (`alert_type`, `severity`, `status`) and partial unique index `uq_risk_alerts_active_fingerprint`.

---

## 4. Exact Execution Sequence & Expected Lifecycle Transitions

*(Note: FloodWatch does not expose a public `POST /api/v1/alerts` route; alert generation occurs internally via `AlertService.evaluate_location_assessment()`, while read, acknowledge, and resolve workflows occur over HTTP REST endpoints.)*

### Step 1: Alert Creation (`NEW`)
- **Operation:** Call `AlertService.evaluate_location_assessment(synthetic_assessment)` targeting `3b493f43-58a7-4786-bedf-39d82b9bc260`.
- **Database Effect:** Inserts `1` row into `public.risk_alerts` with `status = 'NEW'`, `fingerprint = '3b493f43-58a7-4786-bedf-39d82b9bc260:HIGH_RAINFALL_RISK'`.
- **Verification Read:** `GET http://127.0.0.1:8000/api/v1/alerts` $\rightarrow$ `total: 1`, `active_count: 1`, `location_name: "Paradeep Weather Grid Point"`, `district_name: "Jagatsinghpur"`, `"fingerprint"` absent.

### Step 2: Duplicate Prevention (`NEW` $\rightarrow$ `NEW`, No Duplicate Row)
- **Operation:** Re-invoke `AlertService.evaluate_location_assessment(synthetic_assessment)` with updated `recent_observed_rainfall_mm = 1.0`.
- **Database Effect:** `get_open_alerts_for_location()` retrieves the open DB row, matches `fingerprint`, and executes `SupabaseClient.update_alert()` (`_touch_alert()`), updating `updated_at` and `evidence` (`last_evaluated_at`, `recent_observed_mm: 1.0`).
- **Expected Outcome:** Returned `alert.id` matches Step 1; total rows in `public.risk_alerts` remains **`1`**.

### Step 3: Acknowledgement (`NEW` $\rightarrow$ `ACKNOWLEDGED`)
- **API Request:**
  ```http
  POST http://127.0.0.1:8000/api/v1/alerts/{test_alert_id}/acknowledge
  Content-Type: application/json
  X-Ingestion-Token: <configured_ingestion_token>

  {
    "acknowledged_by": "test_audit_phase_2_7_5"
  }
  ```
- **Expected Response:** `HTTP 200 OK`, `status: "ACKNOWLEDGED"`, `acknowledged_at` populated.
- **Public Privacy Check:** `GET http://127.0.0.1:8000/api/v1/alerts/{test_alert_id}` $\rightarrow$ `acknowledged_by: "operator"`, `"fingerprint"` absent.

### Step 4: Manual Resolution (`ACKNOWLEDGED` $\rightarrow$ `RESOLVED`)
- **API Request:**
  ```http
  POST http://127.0.0.1:8000/api/v1/alerts/{test_alert_id}/resolve
  Content-Type: application/json
  X-Ingestion-Token: <configured_ingestion_token>

  {
    "resolved_reason": "[TEST-AUDIT] FloodWatch Phase 2.7.5 controlled verification completed.",
    "resolved_by": "test_audit_phase_2_7_5"
  }
  ```
- **Expected Response:** `HTTP 200 OK`, `status: "RESOLVED"`, `resolved_at` populated, `resolved_reason: "[TEST-AUDIT] FloodWatch Phase 2.7.5 controlled verification completed."`.

### Step 5: Repeated Resolution Guard (`RESOLVED` $\rightarrow$ `HTTP 409`)
- **API Request:** Repeat identical `POST http://127.0.0.1:8000/api/v1/alerts/{test_alert_id}/resolve` call.
- **Expected Response:** `HTTP 409 Conflict` (`{"detail":{"error":"invalid_lifecycle_transition", ...}}`).

### Step 6: Summary & Frontend Polling Verification
- **API Check:** `GET http://127.0.0.1:8000/api/v1/alerts/summary` $\rightarrow$ `total_active_alerts: 0`, `total_acknowledged_alerts: 0`, `highest_active_severity: null`.
- **Frontend Check:** Confirm `Navbar.jsx` badge returns to `0` and `AlertsPage.jsx` **Resolved History** tab displays the `[TEST-AUDIT]` record.

---

## 5. Side Effects, Notification Audit & Record Retention

- **External Notifications:** Verified `backend/app/notifications/` contains only `__init__.py`. Zero emails, SMS, webhooks, or external alerts are dispatched.
- **Database Triggers & Other Tables:** Verified `005_risk_alerts_and_notifications.sql` defines zero triggers on `public.risk_alerts`. No rows in `monitoring_locations`, `observations`, `ingestion_runs`, or `forecast_observation_revisions` will be created or modified.
- **No `RISK_REDUCED` Side Row:** Because Step 4 uses `POST /resolve` directly (not risk de-escalation to `LOW`), no secondary `RISK_REDUCED` row is created. Exactly **`1`** row total is created in `public.risk_alerts`.
- **Genuine Alerts Safety:** `public.risk_alerts` currently contains **`0` rows**; no genuine operational alert exists or can be modified.
- **Identifiability & Retention:** The single synthetic record will remain permanently retained in `public.risk_alerts` in `RESOLVED` status (`active_count` unaffected) and clearly marked with `[TEST-AUDIT]` in `title`, `description`, `resolved_reason`, and `test_audit_phase_2_7_5` in internal `acknowledged_by`. No automated cleanup/deletion will be executed.

---

## 6. Strict Safety Confirmation

- Zero `INSERT`, `UPDATE`, `DELETE`, `UPSERT`, or mutating `RPC` operations executed.
- Zero migrations, schema changes, or remote SQL commands executed.
- Zero credentials exposed.

**Awaiting explicit project owner approval to execute Steps 1–6.**

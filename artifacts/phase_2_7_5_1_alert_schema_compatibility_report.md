# FloodWatch — Phase 2.7.5.1: Risk Alert Schema Compatibility Remediation Report

**Date:** 2026-11-25  
**Author:** Senior Supabase/PostgreSQL Engineer, FastAPI Architect, Backend Integration Engineer  
**Project:** FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform  
**Objective:** Resolve `SCHEMA-01` discovered during Phase 2.7.5 live preflight without altering database schemas, migrations, or existing Supabase records  
**Verdict:** **`PASS`**

---

## 1. Root Cause Analysis (`SCHEMA-01`)

During Phase 2.7.5 read-only schema preflight against Supabase PostgREST, `public.risk_alerts` was verified against `database/migrations/005_risk_alerts_and_notifications.sql` to contain **strictly 15 canonical columns**. Location display metadata (`location_name`, `district_name`) is normalized onto `monitoring_locations` and `districts` via foreign key `location_id REFERENCES monitoring_locations(id)`.

Previously, `AlertService._create_alert_record()` constructed the insert dictionary with `"location_name"` and `"district_name"` included and passed it directly to `SupabaseClient.create_alert()`, which forwarded the full dictionary to `POST /rest/v1/risk_alerts`. Unit tests utilizing the in-memory `_memory_alerts` dictionary masked this bug, whereas live PostgREST rejects unknown columns (`HTTP 400`, PostgreSQL `42703`).

---

## 2. Database vs. Backend Payload Comparison

| Field | `public.risk_alerts` (15 DB Columns) | Prior `create_alert` Payload | Remediated `create_alert` Payload | API `AlertResponse` Display |
| :--- | :---: | :---: | :---: | :---: |
| `id` | Supported (UUID PK) | Sent | **Sent** | Present |
| `location_id` | Supported (UUID FK) | Sent | **Sent** | Present |
| `location_name` | *Not a column* | Sent *(Caused HTTP 400)* | **Omitted** | **Preserved** (enriched via `monitoring_locations`) |
| `district_name` | *Not a column* | Sent *(Caused HTTP 400)* | **Omitted** | **Preserved** (enriched via `districts`) |
| `alert_type` | Supported | Sent | **Sent** | Present |
| `severity` | Supported | Sent | **Sent** | Present |
| `status` | Supported | Sent | **Sent** | Present |
| `title` | Supported | Sent | **Sent** | Present |
| `description` | Supported | Sent | **Sent** | Present |
| `evidence` | Supported (JSONB) | Sent | **Sent** | Present |
| `fingerprint` | Supported | Sent | **Sent** | Excluded from public JSON (`exclude=True`) |
| `created_at` | Supported | Sent | **Sent** | Present |
| `updated_at` | Supported | Sent | **Sent** | Present |
| `acknowledged_at` | Supported | Sent | **Sent** | Present |
| `acknowledged_by` | Supported | Sent | **Sent** | Masked to `"operator"` on public GET |
| `resolved_at` | Supported | Sent | **Sent** | Present |
| `resolved_reason` | Supported | Sent | **Sent** | Present |

- **Acknowledgement Payload (`acknowledge_alert`):** Sends `status`, `acknowledged_at`, `acknowledged_by`, `updated_at` — all valid `risk_alerts` columns.
- **Resolution Payload (`resolve_alert`, `_resolve_alert_record`):** Sends `status`, `resolved_at`, `resolved_reason`, `updated_at` — all valid `risk_alerts` columns (`resolved_by` is logged server-side and never sent as an unsupported column).
- **Touch Payload (`_touch_alert`):** Sends `updated_at`, `evidence` — all valid `risk_alerts` columns.

---

## 3. Files Modified

| File | Summary of Changes |
| :--- | :--- |
| `backend/app/db/supabase_client.py` | Defined `SupabaseClient.RISK_ALERTS_COLUMNS` (exact 15 canonical columns from Migration 005) and enforced strict column projection in `create_alert()` and `update_alert()`. |
| `backend/app/services/alert_service.py` | Built strict 15-column `db_record` in `_create_alert_record()` omitting `location_name` and `district_name`; added `_get_location_metadata_map()` and `_resolve_single_location_meta()` to enrich `location_name` and `district_name` on reads, creations, touches, acknowledgements, and resolutions from the existing `monitoring_locations` relationship; updated `_to_alert_response()` to accept `location_meta`. |
| `backend/tests/test_alerts.py` | Added 4 regression tests verifying all 7 requirements (`test_alert_insert_payload_uses_only_canonical_database_columns`, `test_alert_read_enriches_location_and_district_from_relationship`, `test_acknowledge_and_resolve_payloads_use_valid_db_columns`, `test_schema_remediation_production_insert_error_fails_safely`). |
| `artifacts/phase_2_7_5_1_alert_schema_compatibility_report.md` | Complete Phase 2.7.5.1 remediation report. |

---

## 4. Verification & Test Results

| Verification Check | Command / Scope | Result | Details |
| :--- | :--- | :--- | :--- |
| **Dedicated Alert Tests** | `pytest backend/tests/test_alerts.py -v` | **57 passed** (3.05s) | Uses mocks for DB failure/insert assertions; verifies 15-column payload & relationship enrichment. |
| **Full Backend Suite** | `pytest backend/tests -v` | **249 passed** (42.41s) | 0 failed, 0 skipped, 0 warnings. |
| **Python Compilation** | `compileall backend/app backend/tests` | **Exit 0** | Clean compilation across all modules and test files. |
| **Frontend Build** | `npm run build --prefix frontend` | **Exit 0** (12.81s) | Vite v5.4.21 transformed 2135 modules cleanly. |
| **Live Read-Only Schema Check** | `GET /rest/v1/risk_alerts?select=<15 RISK_ALERTS_COLUMNS>&limit=0` | **`HTTP 200 OK`** | Confirms `RISK_ALERTS_COLUMNS` matches live Supabase schema 1-to-1 (`location_name,district_name` returned `400`). |
| **Live Read-Only Server Endpoints** | `GET /api/v1/health`, `GET /api/v1/alerts` (`127.0.0.1:8000`) | **`HTTP 200 OK`** | `database.status: "connected"`, `total: 0`. |

---

## 5. Database Safety Confirmation & Remaining Blockers

- **Database Safety:** Migration 005 was not modified, no new migrations were created, no remote SQL was executed, no live schema changes occurred, and zero `INSERT`/`UPDATE`/`DELETE`/`UPSERT` queries were executed against Supabase.
- **Remaining Blockers:** Technical blocker `SCHEMA-01` is completely resolved. Only the **Phase 2.7.5 Stage 3 Project Owner Approval Gate** remains before executing controlled live alert lifecycle testing against Supabase.

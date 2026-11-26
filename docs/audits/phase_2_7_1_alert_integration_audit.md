# FloodWatch — Phase 2.7.1: End-to-End Alert Integration Audit Report

**Date:** 2026-11-24  
**Author:** Senior FastAPI, PostgreSQL/Supabase and React Integration Engineer  
**Project:** FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform  
**Audit Scope:** End-to-End Alert Generation, Database Synchronization, API Security, and Frontend Notification Infrastructure  
**Verdict:** **CONDITIONAL PASS**

---

## 1. Executive Summary & Audit Verdict

A comprehensive, read-only architectural and operational audit of the FloodWatch Alert Integration (Phase 2.7.1) was conducted across backend generation services, database access patterns, API route definitions, security policies, and frontend presentation layers.

### Verdict: **CONDITIONAL PASS**

The alert subsystem is functionally mature, architecturally sound, and exhibits robust error isolation, strict deduplication, and sound database-level security. All 37 dedicated alert test cases and the complete 217-test backend regression suite pass with zero errors. The frontend UI and navigation badge integrate cleanly with backend alert telemetry.

The **CONDITIONAL PASS** status is assigned due to four actionable integration and security considerations identified during this audit:
1. **Internal Deduplication Fingerprint Exposure:** `AlertResponse` in `backend/app/schemas/alert.py` includes `fingerprint: str`, which is synthesized and exposed to public callers even though Migration 005 explicitly withdrew public column access to `fingerprint` and excluded it from `v_public_risk_alerts`.
2. **Manual Ingestion Evaluation Disconnect:** While the automated scheduler (`ingestion_scheduler.py`) evaluates alerts post-ingestion, the manual operator trigger (`POST /api/v1/ingestion/trigger` via `ingestion_service.py`) does not invoke the alert evaluation workflow.
3. **Absence of Operator Manual Resolution Route:** While `POST /api/v1/alerts/{alert_id}/acknowledge` exists, there is no corresponding `POST /api/v1/alerts/{alert_id}/resolve` endpoint for operators to dismiss or resolve alerts manually.
4. **Frontend Real-Time Invalidation / Polling:** Frontend alert counts in `Navbar.jsx` and `AlertsPage.jsx` rely on single-shot `useEffect` mount fetches without periodic polling or automated telemetry invalidation.

---

## 2. Components Inspected

| Category | Component / File | Purpose & Responsibility |
| :--- | :--- | :--- |
| **Data Schemas** | `backend/app/schemas/alert.py` | Pydantic contracts for `AlertType`, `AlertSeverity`, `AlertStatus`, `AlertResponse`, `AlertSummaryResponse`. |
| **Alert Service** | `backend/app/services/alert_service.py` | Lifecycle orchestration, concurrency locking (`asyncio.Lock`), deduplication, escalation, and de-escalation. |
| **Risk Service** | `backend/app/services/risk_service.py` | Meteorological assessment synthesis, data quality validation, and historical context generation. |
| **Scheduler** | `backend/app/services/ingestion_scheduler.py` | Automated weather ingestion cycle and post-cycle alert evaluation dispatcher. |
| **Ingestion Service** | `backend/app/services/ingestion_service.py` | Core weather observation ingestion engine and manual execution trigger. |
| **API Routes** | `backend/app/api/v1/routes/alerts.py` | Public and authenticated REST endpoints for listing, summarizing, and acknowledging alerts. |
| **API Routes** | `backend/app/api/v1/routes/risk.py` | Public endpoints for rainfall-based surface runoff risk categorization. |
| **API Routes** | `backend/app/api/v1/routes/ingestion.py` | Protected endpoints for manual ingestion triggers and cycle monitoring. |
| **Database Access** | `backend/app/db/supabase_client.py` | PostgREST communication, service-role vs. anon role multiplexing, and base table vs. view routing. |
| **Database Migration** | `database/migrations/005_risk_alerts_and_notifications.sql` | Base table `risk_alerts`, check constraints, RLS, least-privilege grants, and sanitized view `v_public_risk_alerts`. |
| **Verification SQL** | `database/verification/005_risk_alerts_security_verification.sql` | 17 automated verification checks for schema, constraints, catalog ACLs, and execution contexts. |
| **Frontend Page** | `frontend/src/pages/AlertsPage.jsx` | Operational dashboard, status filtering tabs, severity filtering, station filtering, and operator acknowledge modal. |
| **Frontend Navbar** | `frontend/src/components/Navbar.jsx` | Navigation header displaying live animated badge for active open alerts. |
| **Frontend Routing** | `frontend/src/App.jsx` | React Router integration for `/alerts` route and disclaimer banner mounting. |
| **Backend Tests** | `backend/tests/test_alerts.py` | 37 unit and simulated integration tests validating alert logic, constraints, masking, and security boundaries. |

---

## 3. Existing Working Functionality

### 3.1 Backend Alert Evaluation & Lifecycle Orchestration
- **Risk Category Mapping:**
  - `RiskCategory.VERY_HIGH` $\rightarrow$ `AlertType.VERY_HIGH_RAINFALL_RISK` (`AlertSeverity.CRITICAL`)
  - `RiskCategory.HIGH` $\rightarrow$ `AlertType.HIGH_RAINFALL_RISK` (`AlertSeverity.HIGH`)
  - `RiskCategory.MODERATE` $\rightarrow$ `AlertType.MODERATE_RAINFALL_RISK` (`AlertSeverity.WARNING`)
- **Data Quality & Telemetry Warnings:**
  - `RiskCategory.UNAVAILABLE` with `FreshnessState.STALE` $\rightarrow$ `AlertType.DATA_STALE` (`AlertSeverity.DATA_WARNING`)
  - `RiskCategory.UNAVAILABLE` with missing observations $\rightarrow$ `AlertType.DATA_UNAVAILABLE` (`AlertSeverity.DATA_WARNING`)
  - **Failsafe Invariant:** Missing or stale telemetry **never** resolves or downgrades an active high-risk flood alert.
- **Deduplication Engine:**
  - Generates deterministic fingerprint: `{location_id}:{alert_type.value}`.
  - When an alert with the same fingerprint is already active (`NEW`, `ACTIVE`, `ACKNOWLEDGED`), `AlertService._touch_alert()` updates `updated_at` and refreshes `evidence` payload without creating duplicate rows.
- **Escalation & De-escalation:**
  - Upward transition (e.g. `MODERATE` $\rightarrow$ `HIGH` or `VERY_HIGH`) resolves lower-tier open alerts with reason `"Risk escalated to ..."` and creates the higher-tier alert.
  - Downward transition to `LOW` resolves open risk alerts with reason `"Rainfall runoff potential de-escalated to LOW."` and records an informational `AlertType.RISK_REDUCED` notification (`AlertSeverity.INFO`).
- **Thread & Concurrency Safety:**
  - `AlertService` utilizes an internal `asyncio.Lock` (`self._lock`) ensuring atomicity across evaluations.
- **Pipeline Fault Isolation:**
  - `ingestion_scheduler.py` wraps alert evaluation in independent `try...except` blocks per location. A database failure or formatting error in the alert pipeline does not interrupt or fail the underlying weather ingestion cycle.

### 3.2 Database Access & Security Architecture
- **Base Table Structure:** `public.risk_alerts` with foreign key `location_id UUID REFERENCES monitoring_locations(id) ON DELETE CASCADE`.
- **Dual-Mode PostgREST Client:**
  - When service-role key is present, backend writes directly to `risk_alerts` with header `Prefer: return=representation`.
  - When querying under anon/public permissions, `SupabaseClient.get_alerts()` routes to the sanitized public view `v_public_risk_alerts`.
- **Public View Security:**
  - `v_public_risk_alerts` is created with `WITH (security_invoker = true)` and projects exactly 13 approved columns.
  - Sensitive operational columns (`acknowledged_by`, `fingerprint`) are excluded from the public view projection.
- **Public API Data Masking:**
  - In `backend/app/api/v1/routes/alerts.py`, `list_alerts()` and `get_alert()` explicitly mask `acknowledged_by` to `"operator"`, concealing operator PII and internal personnel identifiers.

### 3.3 Frontend Notification & Dashboard Integration
- **Alerts Page (`frontend/src/pages/AlertsPage.jsx`):**
  - Live summary strip rendering Active Alerts, Acknowledged Alerts, Highest Open Severity badge, and Resolved History count.
  - Multi-tier filtering toolbar: Status tabs (`OPEN`, `ACKNOWLEDGED`, `RESOLVED`, `ALL`), Severity dropdown, and Station dropdown.
  - Statutorily required Research Prototype Disclaimer prominently displayed at the top.
  - Operator Acknowledgement modal supporting role identification and `X-Ingestion-Token` header submission.
- **Global Header Badge (`frontend/src/components/Navbar.jsx`):**
  - Fetches `/api/v1/alerts/summary` on mount.
  - Renders an animated pulse badge with active alert count next to "Risk Alerts" navigation link.

---

## 4. Confirmed Defects & Security Findings

### Finding 1: Public Exposure of Internal Deduplication Fingerprint (Minor / Low Severity)
- **Location:** `backend/app/schemas/alert.py` (`AlertResponse.fingerprint: str`) and `backend/app/services/alert_service.py` (`_to_alert_response`).
- **Description:** Migration 005 deliberately excluded `fingerprint` from `v_public_risk_alerts` and revoked public column permissions on `risk_alerts.fingerprint` to hide internal deduplication hash structures. However, `_to_alert_response` reconstructs the fingerprint if absent from the database row (`r.get("fingerprint") or f"{r.get('location_id')}:{r.get('alert_type')}"`), and `AlertResponse` serializes it in public API responses.
- **Impact:** Low information leakage. Does not allow SQL injection or privilege escalation, but violates the strict 13-column public sanitization principle established in Migration 005.
- **Remediation:** Make `fingerprint: Optional[str] = None` in `AlertResponse` and set it to `None` for unauthenticated/public API endpoints, or exclude `fingerprint` from public schemas.

### Finding 2: Manual Ingestion Bypasses Alert Evaluation (Medium Severity)
- **Location:** `backend/app/services/ingestion_service.py` (`trigger_ingestion`) and `backend/app/api/v1/routes/ingestion.py`.
- **Description:** When an operator manually triggers ingestion via `POST /api/v1/ingestion/trigger`, `IngestionService.trigger_ingestion()` downloads weather observations and upserts them into Supabase, but does **not** invoke `AlertService.evaluate_location_assessment()`. Alert evaluation only executes inside the periodic background scheduler (`ingestion_scheduler.py`).
- **Impact:** If the scheduler is disabled (the default in production until explicitly activated) and ingestion is driven via webhooks or manual triggers, risk alerts will never be generated or resolved.
- **Remediation:** Add post-ingestion alert evaluation hook in `IngestionService.trigger_ingestion()` matching the fault-isolated pattern used in `ingestion_scheduler.py`.

### Finding 3: Missing Operator Manual Resolution Endpoint (Medium Severity)
- **Location:** `backend/app/api/v1/routes/alerts.py`.
- **Description:** The system exposes `POST /api/v1/alerts/{alert_id}/acknowledge`, but provides no corresponding `POST /api/v1/alerts/{alert_id}/resolve`. While automatic resolution occurs when rainfall risk drops to `LOW`, operators have no mechanism to manually resolve false alarms, retired sensors, or acknowledged anomalies without manual SQL execution.
- **Impact:** Active data warning alerts (such as `DATA_UNAVAILABLE` caused by transient network outages) cannot be dismissed by operators even after administrative resolution.
- **Remediation:** Implement `POST /api/v1/alerts/{alert_id}/resolve` protected by `verify_ingestion_token`, requiring a resolution reason and logging operator identity.

### Finding 4: Client-Side Telemetry Polling Not Implemented (Low Severity)
- **Location:** `frontend/src/pages/AlertsPage.jsx` and `frontend/src/components/Navbar.jsx`.
- **Description:** Alert data is fetched only once during component mount. There is no `setInterval` polling, React Query revalidation, or SSE/WebSocket hook. If a flood alert is generated during an operator session, the navbar badge and alerts table remain static until the user manually clicks "Refresh" or reloads the browser.
- **Impact:** Delayed awareness for active monitoring personnel.
- **Remediation:** Introduce an optional background polling interval (e.g. 60 seconds) in `AlertsPage.jsx` and `Navbar.jsx` with window-focus revalidation.

---

## 5. Architectural & Security Verification Matrix

| Check Item | Requirement | Observed Status | Assessment |
| :--- | :--- | :--- | :--- |
| **DB Foreign Key** | `location_id` $\rightarrow$ `monitoring_locations(id)` | Validated in `005_risk_alerts_and_notifications.sql:22` | **PASS** |
| **DB Enum Constraints** | `severity` $\in$ `{CRITICAL, HIGH, WARNING, DATA_WARNING, INFO}` | Validated in Migration 005 check constraints | **PASS** |
| **RLS Enabled** | `ALTER TABLE risk_alerts ENABLE ROW LEVEL SECURITY` | Validated in Migration 005 and verification script | **PASS** |
| **Public View Invoker** | `WITH (security_invoker = true)` on `v_public_risk_alerts` | Validated in Migration 005:158 | **PASS** |
| **Approved Columns** | Exactly 13 approved non-sensitive public columns | Verified in verification SQL Check 13 | **PASS** |
| **Operator Masking** | Public API returns `"operator"` for `acknowledged_by` | Verified in `routes/alerts.py:101, 142` | **PASS** |
| **Auth Verification** | `X-Ingestion-Token` required for `/acknowledge` | Verified in `test_api_authentication_and_authorization` | **PASS** |
| **Pipeline Isolation** | Alert failure does not abort weather ingestion | Verified in `test_notification_failures_not_affecting_ingestion` | **PASS** |
| **Concurrency Lock** | `asyncio.Lock` protects state transitions | Verified in `test_concurrent_evaluation_protection` | **PASS** |
| **Deduplication** | No duplicate active alerts for same station & type | Verified in `test_duplicate_alert_prevention` | **PASS** |

---

## 6. Test Suite Execution & Evidence

### 6.1 Dedicated Alert Test Suite (`backend/tests/test_alerts.py`)
```text
backend/tests/test_alerts.py::test_alert_creation_moderate_high_very_high PASSED
backend/tests/test_alerts.py::test_duplicate_alert_prevention PASSED
backend/tests/test_alerts.py::test_risk_escalation PASSED
backend/tests/test_alerts.py::test_risk_reduction PASSED
backend/tests/test_alerts.py::test_alert_resolution PASSED
backend/tests/test_alerts.py::test_acknowledgement PASSED
backend/tests/test_alerts.py::test_missing_data_handling PASSED
backend/tests/test_alerts.py::test_stale_data_handling PASSED
backend/tests/test_alerts.py::test_invalid_risk_assessment_handling PASSED
backend/tests/test_alerts.py::test_failed_ingestion_isolation PASSED
backend/tests/test_alerts.py::test_one_location_failure_not_blocking_others PASSED
backend/tests/test_alerts.py::test_concurrent_evaluation_protection PASSED
backend/tests/test_alerts.py::test_api_authentication_and_authorization PASSED
backend/tests/test_alerts.py::test_rls_and_migration_permission_assumptions PASSED
backend/tests/test_alerts.py::test_notification_failures_not_affecting_ingestion PASSED
backend/tests/test_alerts.py::test_migration_005_explicit_severity_constraint_upgrade PASSED
backend/tests/test_alerts.py::test_data_warning_enum_accepted_and_invalid_rejected PASSED
backend/tests/test_alerts.py::test_operator_identity_masked_in_public_endpoints PASSED
backend/tests/test_alerts.py::test_safe_public_alert_evidence_and_narrative_sanitization PASSED
backend/tests/test_alerts.py::test_supabase_client_uses_v_public_risk_alerts_for_anon_queries PASSED
backend/tests/test_alerts.py::test_migration_005_strict_schema_reconciliation PASSED
backend/tests/test_alerts.py::test_migration_005_public_view_matches_frontend_contract PASSED
backend/tests/test_alerts.py::test_authentication_contract_uses_x_ingestion_token PASSED
backend/tests/test_alerts.py::test_alert_lifecycle_and_deduplication_integrity PASSED
backend/tests/test_alerts.py::test_migration_005_preserves_unrelated_check_constraints PASSED
backend/tests/test_alerts.py::test_migration_005_correct_public_column_privilege_handling PASSED
backend/tests/test_alerts.py::test_migration_005_preflight_data_validation_simulation PASSED
backend/tests/test_alerts.py::test_migration_005_view_compatibility_and_dependencies_strategy PASSED
backend/tests/test_alerts.py::test_migration_005_effective_privilege_functions_simulation PASSED
backend/tests/test_alerts.py::test_migration_005_role_privilege_matrix_and_view_compatibility PASSED
backend/tests/test_alerts.py::test_verification_script_structure_and_no_public_in_has_privilege PASSED
backend/tests/test_alerts.py::test_migration_005_base_column_pre_existence_check PASSED
backend/tests/test_alerts.py::test_migration_005_preflight_duplicate_fingerprint_check PASSED
backend/tests/test_alerts.py::test_migration_005_null_aware_preflight_validations PASSED
backend/tests/test_alerts.py::test_migration_005_transaction_boundary_and_repeatability PASSED
backend/tests/test_alerts.py::test_migration_005_rollback_on_preflight_failure_simulation PASSED
backend/tests/test_alerts.py::test_public_pseudo_role_aclexplode_simulation PASSED

============================= 37 passed in 0.30s ==============================
```

### 6.2 Full Backend Regression Suite
```text
======================= 217 passed, 1 skipped in 11.60s =======================
```

### 6.3 Frontend Production Build
```text
✓ 2135 modules transformed.
dist/index.html                   0.55 kB │ gzip:   0.36 kB
dist/assets/index-DCrgSz6-.css   51.52 kB │ gzip:  13.04 kB
dist/assets/index-U-UhA_XD.js   888.26 kB │ gzip: 243.02 kB
✓ built in 7.87s
```

---

## 7. Exact Files Requiring Changes (Remediation Roadmap)

To elevate the alert integration from **CONDITIONAL PASS** to **FULL PRODUCTION PASS**, the following targeted changes are recommended:

1. `backend/app/schemas/alert.py`:
   - Change `fingerprint: Optional[str] = None` in `AlertResponse` or mark as internal so it is omitted from public JSON serialization.
   - Add `AlertResolveRequest` schema (`resolved_reason: str`, `resolved_by: Optional[str]`).
2. `backend/app/services/alert_service.py`:
   - Add `resolve_alert(alert_id: str, reason: str, resolved_by: str) -> Optional[AlertResponse]`.
   - Update `_to_alert_response` to omit fingerprint when returning public responses.
3. `backend/app/api/v1/routes/alerts.py`:
   - Add route `POST /alerts/{alert_id}/resolve` protected by `verify_ingestion_token`.
4. `backend/app/services/ingestion_service.py`:
   - In `trigger_ingestion`, add post-ingestion alert evaluation for completed stations using the same isolated try/except pattern established in `ingestion_scheduler.py`.
5. `frontend/src/pages/AlertsPage.jsx` & `frontend/src/components/Navbar.jsx`:
   - Add a 60-second periodic background polling interval with clean timer teardown on unmount.

---

## 8. Summary Conclusion

The alert integration in FloodWatch Phase 2.7.1 is well-architected, resilient, and adheres strictly to least-privilege database principles and non-blocking pipeline boundaries. The findings identified above represent straightforward hardening adjustments rather than foundational architectural defects. Manual execution in Supabase for Migration 005 has already been verified and validated.

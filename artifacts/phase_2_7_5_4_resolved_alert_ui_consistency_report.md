# FloodWatch — Phase 2.7.5.4: Resolved Alert UI Consistency Fix

**Date:** 2026-11-25  
**Scope:** Resolved History UI display consistency, alert deduplication evidence/description synchronization, automated tests, and zero-DB-mutation verification.

---

## 1. Root Cause Summary

1. **Missing `Risk Category` (`N/A`) after deduplication updates:**
   - In `backend/app/services/alert_service.py`, `AlertService._touch_alert` previously patched `evidence` with only four fields (`recent_observed_mm`, `forecast_24h_mm`, `observation_freshness`, `last_evaluated_at`), dropping `risk_category`, `confidence`, `forecast_6h_mm`, `observation_age_minutes`, `forecast_completeness_pct`, `antecedent_category`, and `trigger_reason`.
   - Meanwhile, `frontend/src/pages/AlertsPage.jsx` read `alert.evidence.risk_category || 'N/A'`, causing existing deduplicated records (such as the untouched `[TEST-AUDIT]` Supabase row `dd028e7d-1771-424b-a984-3b1718f24886`) to display `N/A`.
2. **Narrative vs. evidence rainfall drift after deduplication updates:**
   - In `backend/app/services/alert_service.py`, `AlertService._touch_alert` updated `evidence.recent_observed_mm` and `evidence.forecast_24h_mm` without updating `description`, and `_process_assessment_internal` returned `existing_same` before applying the updated fields to the response.
   - Python float interpolation (`{recent_mm or 0}`) also rendered `1.0` instead of `1` when positive integer floats were formatted.
3. **Misleading Resolved History wording (`conditions normalized` / `Normal conditions restored`):**
   - `frontend/src/pages/AlertsPage.jsx` displayed `conditions normalized` beneath the Resolved History counter and defaulted missing `resolved_reason` values to `Normal conditions restored`, even when an alert was manually resolved by an operator.

---

## 2. Files Changed

| File | Change |
| :--- | :--- |
| [alert_service.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/app/services/alert_service.py) | Added `_format_rainfall_mm` and `_build_evidence_payload`; updated `_create_alert_record` and `_touch_alert` to persist complete `AlertEvidence` (preserving `risk_category` and other fields plus `last_evaluated_at`) and synchronized `description` text; merged `_touch_alert` updates into the `AlertResponse` returned by `_process_assessment_internal`. |
| [alertDisplay.js](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/src/utils/alertDisplay.js) | Created pure display helper module exporting `resolveDisplayRiskCategory`, `formatRainfallMetric`, `RESOLVED_HISTORY_SUBTITLE` (`'resolved alerts'`), and `DEFAULT_RESOLVED_REASON` (`'Alert marked resolved'`). |
| [AlertsPage.jsx](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/src/pages/AlertsPage.jsx) | Replaced `alert.evidence.risk_category || 'N/A'` with `resolveDisplayRiskCategory(alert)`, used `formatRainfallMetric(...)` for Observed Rainfall and 24h Forecast evidence cards, replaced `conditions normalized` with `{RESOLVED_HISTORY_SUBTITLE}`, and replaced `'Normal conditions restored'` with `DEFAULT_RESOLVED_REASON`. |
| [alertDisplay.test.mjs](file:///c:/Users/rsoum/Desktop/FloodWatch/frontend/tests/alertDisplay.test.mjs) | Added Node (`node:test`) unit tests covering `resolveDisplayRiskCategory` (explicit evidence vs. `alert_type` fallback vs. `'N/A'`), `formatRainfallMetric`, neutral resolution copy constants, and `AlertsPage.jsx` source checks. |
| [test_alerts.py](file:///c:/Users/rsoum/Desktop/FloodWatch/backend/tests/test_alerts.py) | Added `test_deduplication_touch_preserves_risk_category_and_syncs_description_rainfall` verifying deduplication preserves `evidence["risk_category"]`, synchronizes `description` rainfall values with `evidence`, and keeps `fingerprint` excluded from serialized `AlertResponse`. |

---

## 3. Safety & Lifecycle Constraints Verification

- **Database mutations executed:** None (`0` inserts, updates, resolves, or deletes executed against Supabase).
- **`[TEST-AUDIT]` record status (verified read-only via `GET /api/v1/alerts?limit=10`):**
  - ID: `dd028e7d-1771-424b-a984-3b1718f24886`
  - Status: `RESOLVED` (`resolved_count: 1`)
  - Operator masking: `acknowledged_by == 'operator'`
  - Public fingerprint protection: `'fingerprint' in alert == False`
  - Existing live evidence rainfall retained intact (`recent_observed_mm: 1.0`); because DB mutation was prohibited, its historical `description` remains untouched in Supabase while `AlertsPage.jsx` now derives `Risk Category` (`HIGH`) via `resolveDisplayRiskCategory(alert)` and all future deduplication updates keep `evidence` and `description` synchronized via `_touch_alert`.
- **Schema / alert lifecycle logic:** Unchanged.

---

## 4. Test & Build Results

All checks below ran after the final edit:

| Check | Command | Result |
| :--- | :--- | :--- |
| Frontend display tests | `node --test frontend/tests/alertDisplay.test.mjs` | **4 passed, 0 failed** (0 skipped) |
| Frontend production build | `npm run build --prefix frontend` | **Passed** (`vite build` succeeded in 10.44s, 2136 modules transformed) |
| Backend alert test module | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_alerts.py -q` | **58 passed** in 2.74s |
| Full backend test suite | `backend\.venv\Scripts\python.exe -m pytest backend/tests -v` | **250 passed** in 36.73s |
| Backend bytecode compilation | `backend\.venv\Scripts\python.exe -m compileall -q backend/app backend/tests` | **Passed** (exit code `0`) |
| Live read-only smoke check | `GET http://127.0.0.1:8000/api/v1/alerts?limit=10` & `GET http://localhost:5173/alerts` | **HTTP 200** on both |

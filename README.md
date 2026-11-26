# FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.2+-61DAFB.svg)](https://react.dev/)
[![TailwindCSS](https://img.shields.io/badge/Tailwind-3.4+-38B2AC.svg)](https://tailwindcss.com/)
[![PostgreSQL / PostGIS](https://img.shields.io/badge/PostgreSQL-15+%20PostGIS-336791.svg)](https://postgis.net/)

> [!CAUTION]
> **RESEARCH PROTOTYPE & STATUTORY DISCLAIMER:**  
> **FloodWatch is an experimental rainfall-based monitoring and hydrological decision-support prototype. It is NOT an official flood or disaster warning system.**  
> - Monitoring points are **virtual numerical weather grid points** derived from Open-Meteo model data, **not** official physical river gauges or IMD automatic weather stations.  
> - Risk categories use **provisional research thresholds** for atmospheric rainfall runoff potential and do **not** simulate river overtopping, embankment breaches, or inundation depth.  
> - Open-Meteo modelled precipitation (`analysis` / `forecast`) must **never** be described or relied upon as official ground station observations.  
> - Users and authorities must always follow official alerts and evacuation directives issued by the **India Meteorological Department (IMD)**, **Central Water Commission (CWC)**, **Special Relief Commissioner (SRC)**, and **Odisha State Disaster Management Authority (OSDMA)**.

---

## 1. Problem Statement & Objectives

Coastal districts across the lower Mahanadi Delta in Odisha experience intense monsoon depressions and cyclonic rainfall events where timely situational awareness of localized precipitation accumulation is critical. Official river-stage gauges and multi-agency bulletins are authoritative, yet research prototypes often lack transparent separation between meteorological forecasts, model nowcasts, missing-data gaps, and in-situ hydrological telemetry.

**FloodWatch** addresses this by providing a reproducible, auditable software foundation with:
- Strict distinction between missing data (`UNAVAILABLE`) and genuine zero (`0.0 mm`) rainfall.
- Segregation of rainfall depth (`mm`), water level stage (`m`), and discharge (`m^3/s`).
- Deterministic hydrological risk explanations preserving primary threshold drivers alongside 7-day history and Antecedent Rainfall Indicators (ARI).
- Least-privilege database security (PostgreSQL Row-Level Security, column-level identity masking, and service-role isolation).

---

## 2. Key Features

- **Weather Ingestion & Single-Process Scheduler:** Polls Open-Meteo nowcasts (`analysis`) and 72-hour hourly forecasts (`forecast`) with bounded exponential backoff, idempotency keys, and atomic PostgreSQL RPC upserts (`upsert_weather_observations`). Automated scheduling defaults to disabled (`ENABLE_INGESTION_SCHEDULER=false`) unless explicitly enabled on a single dedicated worker.
- **Rainfall Risk Engine & Explanations:** Evaluates observed nowcasts alongside 6h and 24h forward forecasts against configurable research thresholds (`LOW`, `MODERATE`, `HIGH`, `VERY_HIGH`, `UNAVAILABLE`), outputting plain-language explanations, threshold drivers, and explicit data-quality warnings.
- **Historical Rainfall Analytics & ARI:** Computes 24h, 72h, and 7d accumulations plus daily totals in `Asia/Kolkata` (IST), forecast-revision tracking, and an empirical Antecedent Rainfall Indicator (`0.5*24h + 0.3*72h + 0.2*7d`), excluding forecasts from historical analysis.
- **Alert Lifecycle Management:** Fingerprint-deduplicated in-app risk and telemetry-quality alerts supporting `NEW -> ACTIVE -> ACKNOWLEDGED -> RESOLVED` transitions, token-authenticated operator acknowledgement/resolution, and public operator identity masking.
- **Data Quality & Operational Monitoring:** Evaluates dual freshness policies (`valid_at` observation age `120m / 360m` vs `fetched_at` pipeline age `60m / 180m`), fault-tolerant timestamp parsing, and system/dependency health probes.
- **Reporting & Safe CSV Export:** Structured JSON reports and RFC 4180 CSV exports (UTF-8 BOM, empty strings for missing intervals, CWE-1236 formula-injection protection).

---

## 3. Pilot Locations (Mahanadi Delta, Odisha)

All three pilot coordinates represent **virtual weather grid points**, not official river gauges:

| Location | District | Latitude | Longitude | Delta Role |
| :--- | :--- | :---: | :---: | :--- |
| **Paradeep Weather Grid Point** | Jagatsinghpur | `20.3160` | `86.6110` | Coastal estuary terminus exposed to cyclonic convective rainfall |
| **Cuttack Weather Grid Point** | Cuttack | `20.4625` | `85.8830` | Upper delta bifurcation apex of the Mahanadi and Kathajodi |
| **Kendrapara Weather Grid Point** | Kendrapara | `20.5017` | `86.4223` | Interfluvial coastal floodplain between Brahmani and Mahanadi basins |

---

## 4. Technology Stack

- **Backend:** Python 3.11+, FastAPI, Pydantic v2, Pydantic Settings, HTTPX (pooled async clients), Uvicorn, Pytest
- **Frontend:** React 18, Vite 5, Tailwind CSS, React Router v6, Recharts, Leaflet / React-Leaflet, Lucide Icons, Node Test Runner (`node --test`)
- **Database:** PostgreSQL 15+ with PostGIS (Supabase), Row-Level Security (RLS), PL/pgSQL RPC functions
- **Infrastructure & CI:** Docker, Docker Compose, Nginx 1.27 Alpine, GitHub Actions

---

## 5. System Architecture

```text
Browser (React 18 SPA)
   │  relative /api/v1/* requests (no direct service-role DB access)
   ▼
Vite Dev Proxy (5173 -> 8000) / Nginx Reverse Proxy
   │
   ▼
FastAPI Backend (backend/app/main.py)
   ├── Routes (health, weather, monitoring, risk, alerts, ingestion, reports)
   ├── Services (ingestion, data_quality, monitoring, risk, alert, reporting, scheduler)
   ├── Risk Engine (rainfall_assessor, historical_analyzer, explanation_engine)
   └── Shared HTTPX Connection Pools (http_clients.py)
          ├──► Open-Meteo Forecast API (HTTPS)
          └──► Supabase PostgREST API & RPC (HTTPS + RLS)
```

For architectural invariants and data taxonomy, see [docs/architecture.md](docs/architecture.md).

---

## 6. Project Folder Structure

```text
FloodWatch/
├── .github/workflows/ci.yml       # Automated backend + frontend CI workflow
├── artifacts/                     # Engineering audit and remediation reports
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/         # FastAPI endpoints
│   │   ├── core/                  # Settings, sanitization, shared HTTPX pools
│   │   ├── db/                    # PostgREST client & connectivity probe
│   │   ├── ingestion/             # Open-Meteo client & payload validation
│   │   ├── notifications/         # In-app notification helpers
│   │   ├── risk_engine/           # Risk thresholds, historical & explanation engines
│   │   ├── schemas/               # Pydantic v2 API contracts
│   │   └── services/              # Core domain orchestration & scheduler
│   ├── tests/                     # Pytest unit & integration suites
│   ├── .env.example               # Backend environment template (placeholders only)
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── requirements.txt
├── database/
│   ├── migrations/                # Ordered SQL migrations 001 through 005
│   ├── seed/                      # Reference basin/district/location seed SQL
│   └── verification/              # Read-only RLS and schema verification SQL
├── docs/                          # Architecture, API, migration & deployment docs
├── frontend/
│   ├── src/
│   │   ├── components/            # Navbar, DisclaimerBanner, shared UI
│   │   ├── pages/                 # Dashboard, Map, Risk, Alerts, History, Monitoring, About, Preview
│   │   └── utils/                 # Pure formatting, alert display, health & UI state helpers
│   ├── tests/                     # Node unit tests for UI helpers
│   ├── .env.example               # Frontend environment template (placeholders only)
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   └── package-lock.json
├── scripts/run_dev.ps1            # Windows PowerShell dev launcher
├── docker-compose.yml
└── README.md
```

---

## 7. Screenshots

No UI screenshot image files are currently committed to the repository. Once running locally (`http://localhost:5173/`), key application views include the Overview Dashboard (`/`), Geospatial Monitoring Map (`/map`), Rainfall Risk Engine (`/risk`), Risk Alerts (`/alerts`), Historical Analytics (`/history`), Data Monitoring (`/monitoring`), and About / Official Resources (`/about`).

---

## 8. Installation & Local Development Setup

### 8.1 Prerequisites
- Python `3.11+`
- Node.js `20+` and npm
- A Supabase PostgreSQL/PostGIS project (for live persistence; backend unit tests run offline via mocks)

### 8.2 Backend Installation

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 8.3 Frontend Installation

```powershell
npm ci --prefix frontend
```

### 8.4 Environment Configuration

Copy the environment templates and populate local values without committing `.env` files:

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env
```

Key backend variables (`backend/.env.example`):
- `ENVIRONMENT` (`development` locally; `production` enforces strict startup checks)
- `DEBUG` (`false` required in production)
- `CORS_ORIGINS` (explicit allowed origins; wildcard `*` rejected with credentials / in production)
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (backend-only)
- `INGESTION_TRIGGER_TOKEN` (protects manual ingestion trigger, alert acknowledgement/resolution, and ingestion health; must be rotated from the dev default in production)
- `ENABLE_INGESTION_SCHEDULER` (`false` by default; `WORKERS=1` required if enabled)

Key frontend variables (`frontend/.env.example`):
- `VITE_API_BASE_URL` (leave empty locally to use Vite `/api` proxy)
- `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (public anonymous access only — **never** add service-role keys to `frontend/.env`)

---

## 9. Supabase Database Setup & Migration Order

Apply migrations in strict sequential order via Supabase SQL Editor or controlled `psql` (never execute migrations unreviewed against production without a backup):

1. `database/migrations/001_initial_schema.sql` — Extensions, `basins`, `districts`, `data_sources`, `monitoring_locations`, `observations`.
2. `database/migrations/002_ingestion_extensions.sql` — `ingestion_runs`, unique observation indexes, `forecast_observation_revisions`.
3. `database/migrations/003_weather_ingestion_rpc.sql` — Transactional `upsert_weather_observations(jsonb, uuid)` RPC function.
4. `database/migrations/004_least_privilege_permissions.sql` — Row-Level Security (RLS) and `service_role`-only grants for operational tables.
5. `database/migrations/005_risk_alerts_and_notifications.sql` — `risk_alerts`, active fingerprint uniqueness constraint, and `v_public_risk_alerts` view masking operator identities.

See [docs/MIGRATIONS.md](docs/MIGRATIONS.md) for verification SQL and backup steps.

---

## 10. Running Locally & API Documentation

Start backend and frontend in separate terminals (or via `scripts/run_dev.ps1`):

```powershell
# Terminal 1: FastAPI backend (http://127.0.0.1:8000)
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# Terminal 2: React / Vite dev server (http://localhost:5173/)
npm run dev --prefix frontend
```

- **Frontend UI:** `http://localhost:5173/`
- **Interactive API Docs (Swagger UI, enabled when not production):** `http://127.0.0.1:8000/docs`
- **API Reference:** [docs/api_reference.md](docs/api_reference.md)

Containerized execution instructions are documented in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## 11. Automated Testing & CI

All backend and frontend tests run using mocked data/transports and do not require live database credentials or modify remote state:

```powershell
# Backend compilation & test suite
backend\.venv\Scripts\python.exe -m compileall -q backend/app backend/tests
backend\.venv\Scripts\python.exe -m pytest backend/tests -v

# Frontend unit tests & production build
npm test --prefix frontend
npm run build --prefix frontend
```

**GitHub Actions CI (`.github/workflows/ci.yml`):** Automatically runs Python bytecode compilation, `pytest -v`, `npm ci --prefix frontend`, `npm test --prefix frontend`, and `npm run build --prefix frontend` on pushes and pull requests targeting `main`.

---

## 12. Security Notes

- Real `.env` files (`backend/.env`, `frontend/.env`, `.env.*`) are gitignored; only placeholder `.env.example` templates are committed.
- `SUPABASE_SERVICE_ROLE_KEY` and `INGESTION_TRIGGER_TOKEN` remain strictly on the backend and are never exposed to `frontend/` or bundled assets.
- Error responses and logs pass through `sanitize_error_message()` and `redact_headers()` to strip keys, tokens, bearer headers, and internal URLs.
- Public alert endpoints omit internal deduplication fingerprints and mask operator identifiers (`acknowledged_by: "operator"`).

---

## 13. Known Limitations & Future Scope

- Initial deployments have empty `observations` and `ingestion_runs` tables until an authorized operator runs an ingestion cycle (`POST /api/v1/ingestion/trigger` with `X-Ingestion-Token`) or enables the single-worker scheduler; until then, risk and monitoring endpoints honestly report `UNAVAILABLE` / `critical`.
- River-stage gauge integration (`RiverStageRiskAssessorInterface`) and combined multi-hazard modeling (`CombinedRiskAssessorInterface`) are currently explicit non-operational interfaces awaiting physical station feeds and calibrated hydrodynamic models.
- Frontend tests currently cover UI utility/state logic via `node --test`; full browser DOM component tests are planned as future work.
- Future scope includes official IMD/CWC telemetry adapters, multi-worker distributed job scheduling, and basin-calibrated hydrodynamic inundation modeling.

---

## 14. License

No `LICENSE` file has been selected or added to this repository yet. All rights reserved by the repository owner until an explicit open-source or institutional license is chosen and added.

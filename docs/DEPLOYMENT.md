# FloodWatch Operational Deployment Guide

**System:** FloodWatch — Odisha Rainfall Monitoring & Decision-Support Platform  
**Target Environments:** Local Development, Linux/Docker Staging & Production  
**Status:** Deployment-Ready Architecture (Initial Live Telemetry Pending Owner Approval)  

---

## 1. System Architecture & Technology Stack

```
[ Public Users / Mobile / Browser ]
                │
                ▼
      ┌──────────────────┐
      │   Nginx (Port 80)│ ──> Serves compiled React 18 SPA (Vite)
      └─────────┬────────┘
                │ /api/ reverse proxy
                ▼
      ┌──────────────────┐
      │  FastAPI Backend │ (Port 8000, Uvicorn 1 worker)
      └─────────┬────────┘
                │
      ┌─────────┴─────────┐
      ▼                   ▼
[ Open-Meteo API ]   [ Supabase PostgreSQL / PostGIS ]
```

### Core Components:
- **Backend Framework:** FastAPI 0.110+ on Python 3.11 with asynchronous Uvicorn server.
- **Frontend SPA:** React 18, Vite 5, Tailwind CSS, Leaflet / React-Leaflet, Recharts, Lucide Icons.
- **Persistence & Spatial Telemetry:** Supabase PostgreSQL with PostGIS, PostgREST RPC, and Row-Level Security (RLS).
- **Reverse Proxy / Static Server:** Nginx 1.27 Alpine with SPA routing and security headers.
- **Containerization:** Multi-stage Dockerfiles with non-root execution and healthcheck probes.

---

## 2. Environment Variables & Configuration

### 2.1 Backend Environment Variables (`backend/.env`)

| Variable | Type | Default (Dev) | Production Requirement | Description |
|:---|:---:|:---:|:---:|:---|
| `ENVIRONMENT` | String | `development` | `production` | Enables strict production configuration validation. |
| `DEBUG` | Boolean | `true` | `false` | Must be `false` in production. Raises `ValueError` at startup if `true`. |
| `PORT` | Integer | `8000` | `8000` | Port for the Uvicorn ASGI server. |
| `HOST` | String | `127.0.0.1` | `0.0.0.0` | Listen host IP. `0.0.0.0` inside containers. |
| `ENABLE_DOCS` | Boolean | `null` | `false` | When `null`, docs are auto-disabled in production. Set `true` only for internal testing. |
| `CORS_ORIGINS` | JSON / List | `["http://localhost:5173", ...]` | Explicit domains | Exact allowed origin domains. **Wildcard `*` is prohibited in production**. |
| `OPEN_METEO_BASE_URL` | URL | `https://api.open-meteo.com/v1` | Same | External numerical weather prediction API endpoint. |
| `OPEN_METEO_TIMEOUT_SECONDS` | Float | `10.0` | `10.0` | HTTP request timeout for Open-Meteo calls. |
| `SUPABASE_URL` | URL | `""` | Required | HTTPS URL of the Supabase project instance. |
| `SUPABASE_ANON_KEY` | Secret | `""` | Optional | Public anonymous key for client-facing read endpoints. |
| `SUPABASE_SERVICE_ROLE_KEY` | Secret | `""` | Required | Administrative service key for backend writes. **Never share with frontend.** |
| `INGESTION_TRIGGER_TOKEN` | Secret | `floodwatch-dev-secret-token` | Required (Strong) | Bearer token for triggering manual ingestion. Dev token rejected in production. |
| `ENABLE_INGESTION_SCHEDULER` | Boolean | `false` | `false` or `true`* | *See Single-Process Constraint below. |
| `INGESTION_SCHEDULE_INTERVAL_MINUTES` | Integer | `60` | `>= 15` | Interval between automated ingestion cycles. |

> [!IMPORTANT]
> **Scheduler Single-Process Constraint (Enforced Fail-Safe SCHED-01):**  
> The automated weather ingestion scheduler runs in-process via Python `asyncio`. In multi-worker deployments (e.g. `uvicorn --workers 4`, `gunicorn -w 4`, or `WEB_CONCURRENCY > 1`), multiple schedulers would run concurrently, triggering duplicate Open-Meteo polling and race conditions.
>
> **Authoritative Worker Configuration & Enforced Fail-Safe:** The application defines `WORKERS` (default: 1) as the single authoritative worker configuration, supported directly by the production launcher (`python -m app.main`). A best-effort defense-in-depth check also inspects `WEB_CONCURRENCY`, `UVICORN_WORKERS`, and CLI flags. If `ENABLE_INGESTION_SCHEDULER=true` and workers > 1 are configured, application startup **aborts immediately** with a descriptive `ValueError` / `RuntimeError`.
>
> **Multi-Replica Deployments:** In environments with horizontal autoscaling or multiple container replicas, keep `ENABLE_INGESTION_SCHEDULER=false` (the default) on all web-serving instances and trigger ingestion externally via `POST /api/v1/ingestion/trigger` (using an external cron service or AWS EventBridge), or run a single isolated worker container dedicated to scheduling.

### 2.2 Frontend Environment Variables (`frontend/.env`)

| Variable | Description |
|:---|:---|
| `VITE_API_BASE_URL` | Base URL for API calls. Leave blank when using the same origin or Nginx reverse proxy `/api`. |
| `VITE_SUPABASE_URL` | Public Supabase instance URL (for client-side read-only queries protected by RLS). |
| `VITE_SUPABASE_ANON_KEY` | Public anonymous key. (Service role keys must NEVER be added to frontend `.env`). |
| `VITE_APP_TITLE` | Application title display. |

---

## 3. Local Development Setup

### 3.1 Backend Setup (Windows PowerShell)
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

### 3.2 Frontend Setup
```powershell
cd frontend
npm install
npm run dev
# Frontend is served at http://localhost:5173 with proxy to backend port 8000
```

---

## 4. Container Build & Run Procedures

### 4.1 Running with Docker Compose
```bash
# Build and start all services in detached mode
docker compose up -d --build

# View container logs
docker compose logs -f

# Check health of containers
docker compose ps

# Graceful teardown
docker compose down
```

### 4.2 Building Individual Containers

#### Backend:
```bash
docker build -t floodwatch-backend:latest -f backend/Dockerfile backend/
docker run -d \
  --name floodwatch-api \
  -p 8000:8000 \
  --env-file backend/.env \
  floodwatch-backend:latest
```

#### Frontend:
```bash
docker build -t floodwatch-frontend:latest -f frontend/Dockerfile frontend/
docker run -d \
  --name floodwatch-web \
  -p 80:80 \
  floodwatch-frontend:latest
```

---

## 5. Health, Readiness, and Monitoring Endpoints

FloodWatch provides specialized cloud-native health probes for container orchestrators (Kubernetes, AWS ECS, Docker):

| Endpoint | Method | Purpose | Normal Response |
|:---|:---:|:---|:---:|
| `/api/v1/health/liveness` | `GET` | Process liveness probe. Fast 200 return if Uvicorn event loop is responsive. | `200 OK` |
| `/api/v1/health/readiness` | `GET` | Dependency readiness probe. Returns 200 if database is reachable; 503 if degraded. | `200 OK` or `503 Service Unavailable` |
| `/api/v1/health` | `GET` | Comprehensive diagnostic check including database status and environment metadata. Returns `ok` only when DB is connected (`degraded` otherwise). | `200 OK` (status: `ok` or `degraded`) |
| `/health` (Frontend Nginx) | `GET` | Nginx HTTP server probe. | `200 OK` (`healthy\n`) |

---

## 6. Production Readiness Checklist & First Live Ingestion Procedure

### 6.1 Pre-Deployment Readiness Checklist
- [x] **No Secret Leakage:** `backend/.env` and `frontend/.env` excluded via `.gitignore`; `.env.example` contains placeholders only; frontend bundles contain no service-role keys.
- [x] **Debug Mode Guard:** Startup validation forbids `DEBUG=true` in `ENVIRONMENT=production`.
- [x] **CORS Guard:** Wildcard `*` in `CORS_ORIGINS` is rejected with credentials and forbidden in production.
- [x] **Scheduler Isolation:** `ENABLE_INGESTION_SCHEDULER` defaults to `false` and aborts startup if `true` when `WORKERS > 1`.
- [x] **Non-Root Execution:** Backend Docker container runs under dedicated `floodwatch:10001` user.
- [x] **Graceful Lifespan:** FastAPI `lifespan` handler cleanly manages HTTPX connection pools and drains scheduler shutdown on SIGTERM/SIGINT.
- [x] **Error Sanitization:** Internal database and provider errors pass through `app.core.sanitizer`.
- [ ] **Owner-Authorized Initial Ingestion (Operational Step):** Live `observations` and `ingestion_runs` tables remain at `0` rows until an authorized operator executes Step 6.2 below.

### 6.2 Safe Procedure for Authorizing the First Live Ingestion
1. Confirm `ENVIRONMENT`, strong random `INGESTION_TRIGGER_TOKEN` (>= 16 chars), `CORS_ORIGINS`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are configured in the server runtime environment.
2. Verify dependencies before ingesting:
   - `GET /api/v1/health/readiness` returns `HTTP 200` (`status: "ready"`, `database: "connected"`).
   - `GET /api/v1/sources/health` confirms Supabase service-role connectivity and Open-Meteo reachability.
3. With explicit project-owner approval, execute one controlled manual ingestion run:
   ```bash
   curl -X POST "https://<your-api-host>/api/v1/ingestion/trigger" \
     -H "X-Ingestion-Token: <INGESTION_TRIGGER_TOKEN>"
   ```
4. Verify the recorded run via `GET /api/v1/monitoring/ingestion-health` (with `X-Ingestion-Token`) and inspect `/api/v1/monitoring/summary` and `/api/v1/risk/rainfall` to confirm fresh observations and non-`UNAVAILABLE` risk evaluations.
5. Keep `ENABLE_INGESTION_SCHEDULER=false` on multi-worker web pools; enable periodic scheduling only on a dedicated single-worker instance (`WORKERS=1`) or via external cron/EventBridge calling `POST /api/v1/ingestion/trigger`.

---

## 7. Known Limitations & Research Disclaimers

1. **Experimental Decision-Support Prototype:** FloodWatch is a research system and **NOT** an official flood warning service. Official advisories must be obtained from IMD, CWC, and OSDMA.
2. **Virtual Grid Points:** Virtual weather points in Paradeep, Cuttack, and Kendrapara evaluate atmospheric precipitation forecasts and nowcasts, not river staff gauge water levels.
3. **Scheduler Single Process:** The automated scheduler is an in-memory asyncio task. For large-scale distributed deployments, an external cron job or Celery task queue should be used instead of running the scheduler inside multiple web worker processes.

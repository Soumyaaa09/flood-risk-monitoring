# FloodWatch Data Sources & Provider Investigation

This document evaluates the upstream data sources integrated in Phase 1 or slated for investigation in Phase 2 for the Odisha pilot area (Mahanadi delta: Jagatsinghpur / Paradeep, Cuttack, Kendrapara).

---

## 1. Open-Meteo Weather API (Implemented)

### Overview
- **Provider:** Open-Meteo (open-meteo.com)
- **Service:** Weather Forecast API
- **Endpoint:** `GET https://api.open-meteo.com/v1/forecast`
- **License / Terms:** Creative Commons Attribution 4.0 International (CC-BY-4.0). Free non-commercial tier.

### Parameters Utilized
| Parameter | Value | Purpose |
| :--- | :--- | :--- |
| `latitude` | Decimal degrees (`-90.0` to `90.0`) | Geographic latitude of observation point |
| `longitude` | Decimal degrees (`-180.0` to `180.0`) | Geographic longitude of observation point |
| `current` | `temperature_2m,precipitation` | Real-time ambient 2m temperature and current precipitation accumulation |
| `hourly` | `precipitation,rain,precipitation_probability` | Hourly forecast time series for precipitation depth ($mm$) and probability ($\%$) |
| `forecast_days` | `1` to `7` (Default: `3`) | Numerical prediction horizon |
| `timezone` | `UTC` | Standardized temporal alignment across services |

### Connector Architecture
- Implemented via `app/ingestion/open_meteo.py` using `httpx.AsyncClient`.
- Configurable timeout via `OPEN_METEO_TIMEOUT_SECONDS` (default: 10.0 seconds).
- Normalizes data into `WeatherPreviewResponse` schema with explicit metadata stating units and prototype boundaries.

---

## 2. Central Water Commission (CWC) / National Water Data Portal (Investigation)

### Context & Importance
The Central Water Commission (CWC) under the Ministry of Jal Shakti, Government of India, is the apex agency managing river gauging stations across the Mahanadi basin in Odisha (e.g., Naraj, Mundali, Alipingal, Jobra, Tikarpara).

### Investigation Findings
1. **Existing Portals:**
   - **India-WRIS** (`https://indiawris.gov.in`): Comprehensive water resources database providing basin and station data.
   - **CWC Flood Forecast Portal** (`https://ffs.india-water.gov.in`): Real-time stage and danger-level monitoring during monsoon months.
2. **Technical Constraints & Limitations:**
   - Public open REST APIs with machine-readable API keys are not openly documented for third-party automated pipelines.
   - Portal web interfaces rely on dynamic ASP.NET/Angular session tokens and view states.
   - Scraping is fragile, subject to UI restructuring, and may conflict with terms of service.
3. **Phase 2 Recommendations:**
   - Investigate official API access mechanisms through the National Hydrology Project (NHP) data portal or Open Government Data (OGD) Platform India (`data.gov.in`).
   - Partner with local disaster-management researchers or state authorities to obtain authorized telemetry endpoints.
   - **STRICT PROTOCOL:** In Phase 1, **NO fake or fabricated CWC stations or thresholds have been inserted.**

---

## 3. Open-Meteo Global Flood API (Planned Connector)

### Overview
- **Provider:** Open-Meteo Global Flood API
- **Endpoint:** `GET https://flood-api.open-meteo.com/v1/flood`
- **Underlying Model:** Global Flood Awareness System (GloFAS) from the Copernicus Emergency Management Service (ECMWF).
- **Metric Provided:** River discharge ($m^3/s$).

### Integration Strategy for Phase 2:
- Query river discharge for coordinates corresponding to Mahanadi river segments.
- Model discharge ($m^3/s$) as an independent time series distinct from stage.
- Calibrate modeled discharge against historical runoff trends in the delta.

---

## 4. Policy on Gauge Data & Thresholds

> **POLICY:** Under no circumstances should non-verified river gauge danger levels, warning levels, or mock telemetry readings be inserted into the production database.
>
> Flood thresholds are established by statutory river management authorities based on levee height and flood embankments. Displaying invented thresholds could mislead decision-support personnel. All sample coordinates in Phase 1 are designated with `is_official_gauge = FALSE`.

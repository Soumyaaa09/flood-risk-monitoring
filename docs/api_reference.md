# FloodWatch API Reference (v1)

Base URL: `http://127.0.0.1:8000/api/v1`

---

## 1. Health Check

### `GET /api/v1/health`
Verifies backend operational status, active configuration environment, and prototype disclaimer metadata.

#### Parameters
None.

#### Response (200 OK)
```json
{
  "status": "ok",
  "service": "FloodWatch API",
  "version": "0.1.0",
  "environment": "development",
  "timestamp": "2026-11-17T12:00:00.000000Z",
  "database": {
    "status": "connected",
    "provider": "supabase_postgrest",
    "table_verified": "districts",
    "message": null
  },
  "prototype_notice": "FloodWatch is a research and decision-support prototype, NOT an official emergency-warning service."
}
```

#### Example (PowerShell)
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -Method Get
```

---

## 2. Weather & Rainfall Preview

### `GET /api/v1/weather/preview`
Fetches real-time temperature, precipitation accumulation, and hourly rainfall forecasts from Open-Meteo for arbitrary coordinates.

#### Query Parameters
| Parameter | Type | Required | Default | Validation Range | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `latitude` | `float` | **Yes** | — | `-90.0` to `90.0` | Latitude in decimal degrees |
| `longitude` | `float` | **Yes** | — | `-180.0` to `180.0` | Longitude in decimal degrees |
| `forecast_days`| `integer` | No | `3` | `1` to `7` | Forecast horizon in days |

#### Sample Pilot Coordinates (Testing Only)
These coordinates represent approximate administrative centroids for API connectivity verification. They are **NOT** official gauge stations.
| District | Focus Area | Latitude | Longitude |
| :--- | :--- | :--- | :--- |
| Jagatsinghpur | Paradeep | `20.316` | `86.611` |
| Cuttack | Delta Apex | `20.462` | `85.882` |
| Kendrapara | Interfluvial Plain | `20.501` | `86.422` |

#### Response (200 OK)
```json
{
  "provider": "Open-Meteo Weather API",
  "fetch_timestamp": "2026-11-17T12:05:00.000000Z",
  "latitude": 20.316,
  "longitude": 86.611,
  "elevation_m": 5.0,
  "current_temperature_c": 29.2,
  "current_precipitation_mm": 0.0,
  "data_type_info": {
    "temperature_unit": "°C",
    "precipitation_unit": "mm",
    "measurement_type": "rainfall",
    "observation_nature": "current_and_forecast"
  },
  "hourly_rainfall_forecast": [
    {
      "time": "2026-11-17T00:00",
      "precipitation_mm": 0.0,
      "rain_mm": 0.0,
      "precipitation_probability_pct": 5.0
    },
    {
      "time": "2026-11-17T01:00",
      "precipitation_mm": 1.2,
      "rain_mm": 1.0,
      "precipitation_probability_pct": 20.0
    }
  ],
  "prototype_notice": "Sample coordinates are used solely for API connectivity testing. They do NOT represent official monitoring stations or flood gauge readings."
}
```

#### Error Responses
- **`422 Unprocessable Entity`**: Coordinates missing or out of valid range (`-90` to `90`, `-180` to `180`).
  ```json
  {
    "detail": [
      {
        "type": "less_than_equal",
        "loc": ["query", "latitude"],
        "msg": "Input should be less than or equal to 90",
        "input": 95.0
      }
    ]
  }
  ```
- **`504 Gateway Timeout`**: Upstream Open-Meteo API exceeded timeout threshold (10 seconds).
  ```json
  {
    "detail": {
      "error": "provider_timeout",
      "message": "Open-Meteo request timed out after 10.0s"
    }
  }
  ```
- **`502 Bad Gateway`**: Upstream Open-Meteo network or HTTP error.
  ```json
  {
    "detail": {
      "error": "provider_error",
      "message": "Open-Meteo returned status 500: Internal Server Error"
    }
  }
  ```

#### Example (PowerShell)
```powershell
$Uri = "http://127.0.0.1:8000/api/v1/weather/preview?latitude=20.316&longitude=86.611&forecast_days=3"
Invoke-RestMethod -Uri $Uri -Method Get
```

---

## 3. Real Weather Ingestion & Operational Monitoring (Phase 2.3.2)

### `POST /api/v1/ingestion/trigger`
Triggers real-weather ingestion from Open-Meteo for all registered pilot monitoring locations. Retrieves precipitation data, classifies it into model nowcasts (`analysis`) and future hourly forecasts (`forecast`), and persists records in Supabase.

#### Security & Authentication
Requires the backend authorization token passed in header or query parameter:
- **Header:** `X-Ingestion-Token: <token>`
- **Query Parameter:** `?token=<token>`

#### Query Parameters
| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `location_id` | `UUID` | No | Optional. Ingest only for this specific monitoring location. |
| `token` | `string` | No | Optional token query alternative to `X-Ingestion-Token` header. |

#### Response (200 OK)
```json
{
  "status": "completed",
  "trigger_timestamp": "2026-11-17T12:00:00Z",
  "total_locations": 3,
  "successful_locations": 3,
  "failed_locations": 0,
  "total_records_fetched": 147,
  "total_records_inserted": 147,
  "total_records_updated": 0,
  "results": [
    {
      "location_id": "11111111-1111-1111-1111-111111111111",
      "location_name": "Paradeep Weather Grid Point",
      "district_name": "Jagatsinghpur",
      "status": "completed",
      "records_fetched": 49,
      "records_inserted": 49,
      "records_updated": 0,
      "error_message": null,
      "data_freshness_timestamp": "2026-11-17T12:00:00Z"
    }
  ]
}
```

---

### `GET /api/v1/ingestion/runs`
Lists recent ingestion execution runs with status, duration, and operational counters.

#### Query Parameters
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `limit` | `integer` | `20` | Max runs to return (1-100). |

---

### `GET /api/v1/ingestion/runs/{run_id}`
Returns audit details and operational counters for a specific ingestion run by UUID.

---

### `GET /api/v1/weather/locations`
Lists all registered virtual weather monitoring locations in the pilot districts.

#### Response (200 OK)
```json
[
  {
    "id": "11111111-1111-1111-1111-111111111111",
    "district_id": "d1",
    "district_name": "Jagatsinghpur",
    "basin_id": "b1",
    "name": "Paradeep Weather Grid Point",
    "location_type": "virtual_weather_point",
    "latitude": 20.3160,
    "longitude": 86.6110,
    "elevation_m": null,
    "is_official_gauge": false,
    "metadata": {
      "station_nature": "virtual_weather_grid",
      "disclaimer": "Virtual weather grid centroid. NOT an official IMD station or river gauge."
    }
  }
]
```

---

### `GET /api/v1/weather/latest/{location_id}`
Returns current model-derived analysis nowcast, active forecast count, next 24-hour accumulated rainfall, and data freshness state (`fresh`, `delayed`, `stale`, `unavailable`).

#### Response (200 OK)
```json
{
  "location_id": "11111111-1111-1111-1111-111111111111",
  "location_name": "Paradeep Weather Grid Point",
  "district_name": "Jagatsinghpur",
  "latitude": 20.3160,
  "longitude": 86.6110,
  "freshness": "fresh",
  "data_freshness_timestamp": "2026-11-17T12:00:00Z",
  "current_analysis": {
    "valid_at": "2026-11-17T12:00:00Z",
    "fetched_at": "2026-11-17T12:05:00Z",
    "observation_type": "analysis",
    "measurement_type": "rainfall",
    "value": 1.4,
    "unit": "mm"
  },
  "active_forecast_count": 48,
  "forecast_horizon_hours": 48,
  "next_24h_precipitation_mm": 12.8,
  "prototype_notice": "FloodWatch is a research and decision-support prototype, NOT an official emergency-warning service."
}
```

---

### `GET /api/v1/weather/observations/{location_id}`
Returns the chronological series of active analysis and forecast observations for the specified location.

#### Query Parameters
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `limit` | `integer` | `72` | Number of observation records to return (1-168). |

---

### `GET /api/v1/sources/health`
Monitors health and connectivity to external providers and Supabase PostgREST.

#### Response (200 OK)
```json
{
  "status": "healthy",
  "provider": "Open-Meteo & Supabase PostgREST",
  "connectivity": "Database: connected; WeatherAPI: operational",
  "timestamp": "2026-11-17T12:00:00Z",
  "service_role_configured": true,
  "details": {
    "open_meteo_base_url": "https://api.open-meteo.com/v1",
    "supabase_url_configured": true,
    "open_meteo_status": "operational"
  }
}
```


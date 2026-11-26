import React, { useState } from 'react';
import {
  CloudRain,
  MapPin,
  RefreshCw,
  Thermometer,
  Layers,
  Clock,
  AlertCircle,
  ExternalLink,
  Code,
} from 'lucide-react';
import RainfallChart from '../components/RainfallChart';

const PILOT_PRESETS = [
  {
    name: 'Paradeep (Sample)',
    district: 'Jagatsinghpur',
    lat: 20.316,
    lon: 86.611,
  },
  {
    name: 'Cuttack (Sample)',
    district: 'Cuttack',
    lat: 20.462,
    lon: 85.882,
  },
  {
    name: 'Kendrapara (Sample)',
    district: 'Kendrapara',
    lat: 20.501,
    lon: 86.422,
  },
];

export default function WeatherPreviewPage() {
  const [lat, setLat] = useState('20.316');
  const [lon, setLon] = useState('86.611');
  const [forecastDays, setForecastDays] = useState('3');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const [showJson, setShowJson] = useState(false);

  const fetchPreview = async (latitude = lat, longitude = lon, days = forecastDays) => {
    setLoading(true);
    setError(null);
    try {
      const parsedLat = parseFloat(latitude);
      const parsedLon = parseFloat(longitude);
      const parsedDays = parseInt(days, 10);

      if (isNaN(parsedLat) || parsedLat < -90 || parsedLat > 90) {
        throw new Error('Latitude must be a valid number between -90 and 90.');
      }
      if (isNaN(parsedLon) || parsedLon < -180 || parsedLon > 180) {
        throw new Error('Longitude must be a valid number between -180 and 180.');
      }

      const query = new URLSearchParams({
        latitude: parsedLat.toString(),
        longitude: parsedLon.toString(),
        forecast_days: parsedDays.toString(),
      });

      const res = await fetch(`/api/v1/weather/preview?${query.toString()}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        const msg = errJson?.detail?.message || errJson?.detail?.error || `HTTP error ${res.status}`;
        throw new Error(msg);
      }

      const result = await res.json();
      setData(result);
    } catch (err) {
      setError(err.message || 'Failed to fetch weather preview');
    } finally {
      setLoading(false);
    }
  };

  const handlePresetSelect = (preset) => {
    setLat(preset.lat.toString());
    setLon(preset.lon.toString());
    fetchPreview(preset.lat, preset.lon, forecastDays);
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Page Header */}
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
          <CloudRain className="w-7 h-7 text-sky-600" />
          Open-Meteo Weather & Rainfall Preview
        </h1>
        <p className="text-slate-600 text-sm mt-1">
          Endpoint: <code className="bg-slate-100 text-slate-800 px-1.5 py-0.5 rounded text-xs">GET /api/v1/weather/preview</code>.
          Verify API connectivity and inspect rainfall forecast series.
        </p>
      </div>

      {/* Preset Buttons & Notice */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Sample Pilot Coordinate Presets:
          </label>
          <span className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded border border-amber-200">
            Coordinates are sample centroids for testing; NOT official gauge stations.
          </span>
        </div>

        <div className="flex flex-wrap gap-3">
          {PILOT_PRESETS.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => handlePresetSelect(p)}
              className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-medium border border-slate-200 bg-slate-50 hover:bg-sky-50 hover:border-sky-300 hover:text-sky-800 transition"
            >
              <MapPin className="w-3.5 h-3.5 text-slate-500" />
              <span>{p.name}</span>
              <span className="text-slate-400 font-mono">({p.lat}, {p.lon})</span>
            </button>
          ))}
        </div>

        {/* Input Form */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            fetchPreview();
          }}
          className="grid grid-cols-1 sm:grid-cols-4 gap-4 pt-3 border-t border-slate-100 items-end"
        >
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Latitude (-90 to 90)
            </label>
            <input
              type="number"
              step="any"
              value={lat}
              onChange={(e) => setLat(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-sky-500 font-mono"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Longitude (-180 to 180)
            </label>
            <input
              type="number"
              step="any"
              value={lon}
              onChange={(e) => setLon(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-sky-500 font-mono"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Forecast Horizon
            </label>
            <select
              value={forecastDays}
              onChange={(e) => setForecastDays(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-sky-500"
            >
              <option value="1">1 Day</option>
              <option value="2">2 Days</option>
              <option value="3">3 Days (Default)</option>
              <option value="5">5 Days</option>
              <option value="7">7 Days</option>
            </select>
          </div>

          <div>
            <button
              type="submit"
              disabled={loading}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium rounded-lg text-white bg-sky-600 hover:bg-sky-700 disabled:opacity-50 transition"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              {loading ? 'Fetching...' : 'Query Weather'}
            </button>
          </div>
        </form>
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl flex items-start gap-3 text-sm">
          <AlertCircle className="w-5 h-5 text-rose-600 flex-shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">Query Failed:</span> {error}
          </div>
        </div>
      )}

      {/* Results View */}
      {data && (
        <div className="space-y-6">
          {/* Key Metric Highlights */}
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <span className="text-xs text-slate-500 block mb-1">Current Temperature</span>
              <div className="flex items-center gap-2">
                <Thermometer className="w-5 h-5 text-amber-500" />
                <span className="text-2xl font-bold text-slate-900">
                  {data.current_temperature_c !== null ? `${data.current_temperature_c}°C` : 'N/A'}
                </span>
              </div>
              <span className="text-xs text-slate-400 mt-1 block">Ambient 2m</span>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <span className="text-xs text-slate-500 block mb-1">Current Precipitation</span>
              <div className="flex items-center gap-2">
                <CloudRain className="w-5 h-5 text-sky-500" />
                <span className="text-2xl font-bold text-slate-900">
                  {data.current_precipitation_mm !== null ? `${data.current_precipitation_mm} mm` : '0 mm'}
                </span>
              </div>
              <span className="text-xs text-slate-400 mt-1 block">Rate / accumulation</span>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <span className="text-xs text-slate-500 block mb-1">Coordinates / Elevation</span>
              <div className="text-sm font-semibold text-slate-900 font-mono">
                {data.latitude?.toFixed(3)}°N, {data.longitude?.toFixed(3)}°E
              </div>
              <span className="text-xs text-slate-400 mt-1 block">
                Elevation: {data.elevation_m !== null ? `${data.elevation_m}m ASL` : 'Unknown'}
              </span>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <span className="text-xs text-slate-500 block mb-1">Provider Attribution</span>
              <div className="text-sm font-semibold text-slate-900">
                {data.provider}
              </div>
              <span className="text-xs text-slate-400 mt-1 block flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {new Date(data.fetch_timestamp).toLocaleTimeString()}
              </span>
            </div>
          </div>

          {/* Rainfall Forecast Chart */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h3 className="font-bold text-slate-900 text-lg">Hourly Rainfall Forecast (mm)</h3>
                <p className="text-xs text-slate-500">
                  Horizon: {data.hourly_rainfall_forecast?.length || 0} hours | Metric:{' '}
                  {data.data_type_info?.measurement_type} ({data.data_type_info?.precipitation_unit})
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowJson(!showJson)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-mono text-slate-600 hover:bg-slate-50 transition"
              >
                <Code className="w-3.5 h-3.5" />
                {showJson ? 'Hide JSON' : 'Inspect Raw JSON'}
              </button>
            </div>

            <RainfallChart data={data.hourly_rainfall_forecast} />
          </div>

          {/* Raw JSON Debug View */}
          {showJson && (
            <div className="bg-slate-900 text-slate-100 rounded-xl p-5 text-xs font-mono overflow-auto max-h-96">
              <pre>{JSON.stringify(data, null, 2)}</pre>
            </div>
          )}

          {/* Prototype Notice */}
          <div className="text-xs text-slate-500 bg-slate-50 p-4 rounded-lg border border-slate-200">
            <strong>Metadata Notice:</strong> {data.prototype_notice}
          </div>
        </div>
      )}
    </div>
  );
}

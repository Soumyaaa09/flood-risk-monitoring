import React, { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Calendar,
  CheckCircle2,
  ChevronRight,
  Clock,
  CloudRain,
  Compass,
  Droplets,
  ExternalLink,
  History,
  Info,
  Layers,
  MapPin,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
  X,
  XCircle,
} from 'lucide-react';

// Coordinates for Mahanadi Delta and Pilot Locations
const MAHANADI_DELTA_CENTER = [20.42, 86.30];
const DEFAULT_ZOOM = 9;

const PILOT_COORDINATES = {
  paradeep: { lat: 20.3160, lon: 86.6110, name: 'Paradeep Weather Grid Point', district: 'Jagatsinghpur' },
  cuttack: { lat: 20.4625, lon: 85.8830, name: 'Cuttack Weather Grid Point', district: 'Cuttack' },
  kendrapara: { lat: 20.5017, lon: 86.4223, name: 'Kendrapara Weather Grid Point', district: 'Kendrapara' },
};

// Map View Controller for animated pan/zoom
function MapController({ center, zoom }) {
  const map = useMap();
  useEffect(() => {
    if (center && Array.isArray(center) && center.length === 2) {
      map.flyTo(center, zoom, { duration: 1.0 });
    }
  }, [center, zoom, map]);
  return null;
}

// Custom Leaflet DivIcon generator for crisp, themed SVG markers
function createRiskMarkerIcon(riskCategory, isSelected, rainfallMm, locationName) {
  let mainColor = '#64748b'; // UNAVAILABLE default (slate)
  let ringColor = 'rgba(100, 116, 139, 0.25)';
  let labelBg = 'bg-slate-700';
  let isPulsing = false;

  switch (riskCategory) {
    case 'VERY_HIGH':
      mainColor = '#dc2626'; // crimson red
      ringColor = 'rgba(220, 38, 38, 0.35)';
      labelBg = 'bg-red-700';
      isPulsing = true;
      break;
    case 'HIGH':
      mainColor = '#ea580c'; // orange
      ringColor = 'rgba(234, 88, 12, 0.35)';
      labelBg = 'bg-orange-700';
      isPulsing = true;
      break;
    case 'MODERATE':
      mainColor = '#d97706'; // amber
      ringColor = 'rgba(217, 119, 6, 0.30)';
      labelBg = 'bg-amber-700';
      break;
    case 'LOW':
      mainColor = '#059669'; // emerald
      ringColor = 'rgba(5, 150, 105, 0.25)';
      labelBg = 'bg-emerald-700';
      break;
    case 'UNAVAILABLE':
    default:
      mainColor = '#64748b';
      ringColor = 'rgba(100, 116, 139, 0.25)';
      labelBg = 'bg-slate-600';
      break;
  }

  const valueDisplay =
    rainfallMm !== null && rainfallMm !== undefined
      ? `${Number(rainfallMm).toFixed(1)}m`
      : '--';

  const pulseRing = isPulsing
    ? `<div style="position: absolute; inset: -8px; border-radius: 9999px; background: ${mainColor}; opacity: 0.4; animation: ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;"></div>`
    : '';

  const selectionRing = isSelected
    ? `<div style="position: absolute; inset: -5px; border-radius: 9999px; border: 3px solid #0284c7; box-shadow: 0 0 12px rgba(2, 132, 199, 0.6);"></div>`
    : '';

  const html = `
    <div style="position: relative; display: flex; flex-direction: column; align-items: center; cursor: pointer;">
      ${pulseRing}
      ${selectionRing}
      <div style="
        width: 36px;
        height: 36px;
        border-radius: 9999px;
        background: ${mainColor};
        border: 2.5px solid white;
        box-shadow: 0 4px 10px rgba(0,0,0,0.3);
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-weight: 800;
        font-size: 10px;
        position: relative;
        z-index: 10;
        transition: transform 0.2s;
        ${isSelected ? 'transform: scale(1.15);' : ''}
      ">
        ${valueDisplay}
      </div>
      <div style="
        position: absolute;
        top: 38px;
        white-space: nowrap;
        background: rgba(15, 23, 42, 0.85);
        color: white;
        font-size: 9.5px;
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid rgba(255, 255, 255, 0.15);
        backdrop-filter: blur(4px);
        box-shadow: 0 2px 6px rgba(0,0,0,0.25);
        z-index: 5;
      ">
        ${locationName.replace(' Weather Grid Point', '')}
      </div>
    </div>
  `;

  return L.divIcon({
    html,
    className: 'custom-floodwatch-marker',
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -22],
  });
}

export default function GeospatialMapPage() {
  const [riskData, setRiskData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedLocationId, setSelectedLocationId] = useState(null);
  const [mapCenter, setMapCenter] = useState(MAHANADI_DELTA_CENTER);
  const [mapZoom, setMapZoom] = useState(DEFAULT_ZOOM);
  const [filterRiskCategory, setFilterRiskCategory] = useState('ALL');
  const [showLegend, setShowLegend] = useState(true);

  // Fetch complete rainfall risk summary across pilot locations
  const fetchRiskTelemetry = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch('/api/v1/risk/rainfall');
      if (!res.ok) {
        throw new Error(`Failed to load risk telemetry (HTTP ${res.status})`);
      }
      const data = await res.json();
      setRiskData(data);

      // Auto-select first location if none selected
      if (!selectedLocationId && data?.assessments?.length > 0) {
        setSelectedLocationId(data.assessments[0].location_id);
      }
    } catch (err) {
      setError(err.message || 'Unable to connect to risk assessment service.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRiskTelemetry();
  }, []);

  const assessments = riskData?.assessments || [];

  // Filtered locations based on category selection
  const filteredAssessments = useMemo(() => {
    if (filterRiskCategory === 'ALL') return assessments;
    return assessments.filter((loc) => loc.risk_category === filterRiskCategory);
  }, [assessments, filterRiskCategory]);

  // Selected Location detail record
  const selectedLocation = useMemo(() => {
    if (!selectedLocationId) return assessments[0] || null;
    return assessments.find((loc) => loc.location_id === selectedLocationId) || null;
  }, [assessments, selectedLocationId]);

  // Handler for location selection (from selector pills or marker clicks)
  const handleSelectLocation = (loc) => {
    if (!loc) return;
    setSelectedLocationId(loc.location_id);
    setMapCenter([loc.latitude, loc.longitude]);
    setMapZoom(11);
  };

  const handleResetMap = () => {
    setSelectedLocationId(null);
    setMapCenter(MAHANADI_DELTA_CENTER);
    setMapZoom(DEFAULT_ZOOM);
  };

  // Helper Badge Functions
  const getRiskBadge = (category) => {
    switch (category) {
      case 'VERY_HIGH':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-red-100 text-red-800 border border-red-300">
            <AlertTriangle className="w-3.5 h-3.5 text-red-600 animate-pulse" />
            Very High Risk
          </span>
        );
      case 'HIGH':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-orange-100 text-orange-800 border border-orange-300">
            <AlertCircle className="w-3.5 h-3.5 text-orange-600" />
            High Risk
          </span>
        );
      case 'MODERATE':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-amber-100 text-amber-800 border border-amber-300">
            <Info className="w-3.5 h-3.5 text-amber-600" />
            Moderate Risk
          </span>
        );
      case 'LOW':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-emerald-100 text-emerald-800 border border-emerald-300">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
            Low Risk
          </span>
        );
      case 'UNAVAILABLE':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-slate-100 text-slate-700 border border-slate-300">
            <XCircle className="w-3.5 h-3.5 text-slate-500" />
            Unavailable
          </span>
        );
    }
  };

  const getFreshnessBadge = (freshness, ageMinutes) => {
    switch (freshness) {
      case 'fresh':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
            <CheckCircle2 className="w-3 h-3 text-emerald-600" />
            FRESH {ageMinutes !== null ? `(${Math.round(ageMinutes)}m ago)` : ''}
          </span>
        );
      case 'delayed':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
            <Clock className="w-3 h-3 text-amber-600" />
            DELAYED {ageMinutes !== null ? `(${Math.round(ageMinutes)}m ago)` : ''}
          </span>
        );
      case 'stale':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-orange-700 bg-orange-50 px-2 py-0.5 rounded border border-orange-200">
            <AlertTriangle className="w-3 h-3 text-orange-600" />
            STALE {ageMinutes !== null ? `(${Math.round(ageMinutes)}m ago)` : ''}
          </span>
        );
      case 'unavailable':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
            <XCircle className="w-3 h-3 text-slate-500" />
            UNAVAILABLE
          </span>
        );
    }
  };

  const getAntecedentBadge = (cat) => {
    switch (cat) {
      case 'EXTREME_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-red-100 text-red-800 border border-red-200">Extreme Antecedent</span>;
      case 'HIGH_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-orange-100 text-orange-800 border border-orange-200">High Antecedent</span>;
      case 'MODERATE_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-amber-100 text-amber-800 border border-amber-200">Moderate Antecedent</span>;
      case 'LOW_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 border border-emerald-200">Low Antecedent</span>;
      case 'UNAVAILABLE':
      default:
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">Unavailable</span>;
    }
  };

  const getTrendBadge = (trend) => {
    switch (trend) {
      case 'INCREASING':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-red-700 bg-red-50 px-2 py-0.5 rounded border border-red-200">
            <TrendingUp className="w-3 h-3 text-red-600" /> Increasing Trend
          </span>
        );
      case 'DECREASING':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
            <TrendingDown className="w-3 h-3 text-emerald-600" /> Decreasing Trend
          </span>
        );
      case 'STEADY':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-700 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
            Steady Accumulation
          </span>
        );
      case 'NO_RAIN':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
            No Rain Observed
          </span>
        );
      case 'INSUFFICIENT_DATA':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-500 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
            Insufficient Trend Data
          </span>
        );
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
      {/* Top Header & Navigation Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 pb-4">
        <div>
          <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-700 uppercase tracking-wider bg-sky-50 px-2.5 py-1 rounded-full border border-sky-100 mb-2">
            <Compass className="w-3.5 h-3.5" />
            Phase 2.4.3 Geospatial Monitoring
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight flex items-center gap-2.5">
            Mahanadi Delta Geospatial Monitoring Map
          </h1>
          <p className="text-xs sm:text-sm text-slate-600 mt-1">
            Spatial monitoring of atmospheric precipitation, runoff risk, and antecedent moisture across Odisha pilot points.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={fetchRiskTelemetry}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50 transition shadow-sm disabled:opacity-60"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-sky-600' : 'text-slate-500'}`} />
            Refresh Telemetry
          </button>
          <Link
            to="/risk"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-600 text-xs font-semibold text-white hover:bg-sky-700 transition shadow-sm"
          >
            <BarChart3 className="w-3.5 h-3.5" />
            Risk Table View
          </Link>
        </div>
      </div>

      {/* Prototype Caveat Notice */}
      <div className="bg-amber-50/80 border border-amber-200 rounded-lg p-3 text-xs text-amber-900 flex items-start gap-2.5">
        <ShieldAlert className="w-4 h-4 text-amber-700 shrink-0 mt-0.5" />
        <div>
          <span className="font-bold">Research Prototype Disclaimer:</span> Markers represent numerical model nowcasts at pilot coordinates in the Mahanadi basin. They do <strong>not</strong> represent official ground rain-gauge stations or flood warnings. Statutory alerts are issued solely by IMD and OSDMA.
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 text-xs sm:text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-rose-600 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={fetchRiskTelemetry}
            className="px-3 py-1 bg-rose-600 text-white rounded text-xs font-semibold hover:bg-rose-700 transition"
          >
            Retry
          </button>
        </div>
      )}

      {/* Quick Location Selector & Category Filter Bar */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3 bg-white p-3 rounded-xl border border-slate-200 shadow-sm">
        {/* Pilot Station Quick-Pills */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 lg:pb-0">
          <span className="text-xs font-bold text-slate-500 uppercase tracking-wider mr-1 hidden sm:inline">
            Zoom To:
          </span>
          <button
            onClick={handleResetMap}
            className={`px-3 py-1 rounded-lg text-xs font-semibold transition whitespace-nowrap ${
              !selectedLocationId
                ? 'bg-slate-900 text-white shadow-sm'
                : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
            }`}
          >
            Mahanadi Delta (All)
          </button>
          {assessments.map((loc) => {
            const isSelected = selectedLocationId === loc.location_id;
            return (
              <button
                key={loc.location_id}
                onClick={() => handleSelectLocation(loc)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 whitespace-nowrap ${
                  isSelected
                    ? 'bg-sky-600 text-white shadow-sm'
                    : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                }`}
              >
                <MapPin className="w-3 h-3" />
                {loc.location_name.replace(' Weather Grid Point', '')}
              </button>
            );
          })}
        </div>

        {/* Risk Filter Category */}
        <div className="flex items-center gap-1.5 overflow-x-auto">
          <span className="text-xs font-bold text-slate-500 uppercase tracking-wider mr-1 hidden sm:inline">
            Filter:
          </span>
          {['ALL', 'VERY_HIGH', 'HIGH', 'MODERATE', 'LOW', 'UNAVAILABLE'].map((cat) => (
            <button
              key={cat}
              onClick={() => setFilterRiskCategory(cat)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition whitespace-nowrap ${
                filterRiskCategory === cat
                  ? 'bg-slate-800 text-white'
                  : 'bg-slate-50 text-slate-600 hover:bg-slate-100 border border-slate-200'
              }`}
            >
              {cat === 'ALL' ? 'All Markers' : cat.replace('_', ' ')}
            </button>
          ))}
        </div>
      </div>

      {/* Main Map + Details Panel Container */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Interactive Leaflet Map Canvas (7 cols on desktop) */}
        <div className="lg:col-span-7 bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden relative">
          <div className="h-[480px] sm:h-[580px] w-full relative z-10">
            {loading && !riskData ? (
              <div className="absolute inset-0 bg-slate-100/90 flex flex-col items-center justify-center gap-3 z-30">
                <RefreshCw className="w-8 h-8 text-sky-600 animate-spin" />
                <p className="text-sm font-semibold text-slate-700">Loading Geospatial Telemetry...</p>
                <p className="text-xs text-slate-500">Connecting to pilot weather grids in Odisha...</p>
              </div>
            ) : null}

            <MapContainer
              center={mapCenter}
              zoom={mapZoom}
              scrollWheelZoom={true}
              style={{ height: '100%', width: '100%' }}
              className="z-10"
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                maxZoom={18}
              />

              <MapController center={mapCenter} zoom={mapZoom} />

              {/* Render Pilot Location Markers */}
              {filteredAssessments.map((loc) => {
                const isSelected = selectedLocationId === loc.location_id;
                const markerIcon = createRiskMarkerIcon(
                  loc.risk_category,
                  isSelected,
                  loc.recent_observed_rainfall_mm,
                  loc.location_name
                );

                return (
                  <Marker
                    key={loc.location_id}
                    position={[loc.latitude, loc.longitude]}
                    icon={markerIcon}
                    eventHandlers={{
                      click: () => handleSelectLocation(loc),
                    }}
                  >
                    <Popup className="floodwatch-popup">
                      <div className="p-1 space-y-2 min-w-[200px]">
                        <div className="border-b border-slate-100 pb-1.5">
                          <h4 className="font-bold text-slate-900 text-xs">{loc.location_name}</h4>
                          <span className="text-[10px] text-slate-500 font-medium">
                            {loc.district_name || 'Odisha'} &bull; ({loc.latitude.toFixed(4)}, {loc.longitude.toFixed(4)})
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-slate-600">Risk Level:</span>
                          {getRiskBadge(loc.risk_category)}
                        </div>
                        <div className="flex items-center justify-between text-xs font-mono">
                          <span className="text-slate-600">Observed Rain:</span>
                          <span className="font-bold text-slate-900">
                            {loc.recent_observed_rainfall_mm !== null
                              ? `${loc.recent_observed_rainfall_mm.toFixed(1)} mm`
                              : 'No reading'}
                          </span>
                        </div>
                        <button
                          onClick={() => handleSelectLocation(loc)}
                          className="w-full mt-1 px-2.5 py-1 bg-sky-600 text-white rounded text-[11px] font-semibold hover:bg-sky-700 transition"
                        >
                          View Full Telemetry
                        </button>
                      </div>
                    </Popup>
                  </Marker>
                );
              })}
            </MapContainer>

            {/* Map Legend (Floating in corner) */}
            <div className="absolute bottom-3 left-3 z-20 bg-white/95 backdrop-blur-sm p-3 rounded-xl border border-slate-200/90 shadow-md text-xs space-y-2 max-w-[210px]">
              <div className="flex items-center justify-between">
                <span className="font-bold text-slate-900 text-[11px] uppercase tracking-wider flex items-center gap-1">
                  <Layers className="w-3.5 h-3.5 text-sky-600" />
                  Map Legend
                </span>
                <button
                  onClick={() => setShowLegend(!showLegend)}
                  className="text-slate-400 hover:text-slate-600 text-[10px]"
                >
                  {showLegend ? 'Hide' : 'Show'}
                </button>
              </div>

              {showLegend && (
                <div className="space-y-1.5 pt-1 border-t border-slate-100 text-[11px]">
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full bg-red-600 shrink-0 border border-white shadow-sm" />
                    <span className="text-slate-700 font-medium">Very High Risk</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full bg-orange-600 shrink-0 border border-white shadow-sm" />
                    <span className="text-slate-700 font-medium">High Risk</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full bg-amber-500 shrink-0 border border-white shadow-sm" />
                    <span className="text-slate-700 font-medium">Moderate Risk</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full bg-emerald-600 shrink-0 border border-white shadow-sm" />
                    <span className="text-slate-700 font-medium">Low Risk</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full bg-slate-500 shrink-0 border border-white shadow-sm" />
                    <span className="text-slate-700 font-medium">Data Unavailable</span>
                  </div>
                  <p className="text-[9.5px] text-slate-400 pt-1 border-t border-slate-100">
                    Values show recent observed precipitation depth in mm.
                  </p>
                </div>
              )}
            </div>

            {/* Map Reset View Control (Floating top-right) */}
            <div className="absolute top-3 right-3 z-20">
              <button
                onClick={handleResetMap}
                title="Fit All Mahanadi Delta Locations"
                className="px-2.5 py-1.5 rounded-lg bg-white/95 backdrop-blur-sm border border-slate-200 text-xs font-semibold text-slate-700 shadow-md hover:bg-slate-100 transition flex items-center gap-1.5"
              >
                <Compass className="w-3.5 h-3.5 text-sky-600" />
                Delta View
              </button>
            </div>
          </div>
        </div>

        {/* Selected Location Details Panel (5 cols on desktop) */}
        <div className="lg:col-span-5 space-y-4">
          {selectedLocation ? (
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
              {/* Header */}
              <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
                <div>
                  <div className="flex items-center gap-1.5 text-xs text-sky-700 font-semibold mb-0.5">
                    <MapPin className="w-3.5 h-3.5" />
                    {selectedLocation.district_name || 'Odisha Pilot District'}
                  </div>
                  <h2 className="text-lg font-bold text-slate-900 leading-tight">
                    {selectedLocation.location_name}
                  </h2>
                  <span className="text-[11px] font-mono text-slate-400">
                    Lat {selectedLocation.latitude.toFixed(4)}&deg;N &bull; Lon {selectedLocation.longitude.toFixed(4)}&deg;E
                  </span>
                </div>
                {getRiskBadge(selectedLocation.risk_category)}
              </div>

              {/* Risk Assessment Banner, Public-Friendly Summary & Factors (Phase 2.4.4) */}
              <div className="p-3.5 bg-slate-50 rounded-lg border border-slate-200 space-y-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-bold text-slate-800 flex items-center gap-1.5">
                    <Info className="w-3.5 h-3.5 text-sky-600" />
                    Plain-Language Assessment
                  </span>
                  <span className="font-bold text-slate-700 uppercase text-[11px] bg-white px-2 py-0.5 rounded border border-slate-200">
                    {selectedLocation.confidence} Confidence
                  </span>
                </div>

                {/* Plain-Language Public Summary */}
                <p className="text-xs text-slate-800 leading-relaxed bg-white p-2.5 rounded border border-slate-100 font-medium shadow-2xs">
                  {selectedLocation.risk_context?.public_summary || selectedLocation.assessment_explanation}
                </p>

                {/* Key Contributing Factors */}
                {selectedLocation.risk_context?.contributing_factors && selectedLocation.risk_context.contributing_factors.length > 0 && (
                  <div className="space-y-1 pt-1">
                    <span className="text-[10px] font-bold text-slate-600 uppercase tracking-wider block">
                      Contributing Factors:
                    </span>
                    <ul className="space-y-1 text-[11px] text-slate-600 list-disc list-inside">
                      {selectedLocation.risk_context.contributing_factors.map((factor, fIdx) => (
                        <li key={fIdx} className="leading-tight">
                          {factor}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Public Impact Advisory */}
                {selectedLocation.risk_context?.impact_advisory && (
                  <div className="text-[11px] text-sky-950 bg-sky-50/70 border border-sky-200/60 p-2 rounded leading-tight">
                    <span className="font-bold block text-[10px] uppercase tracking-wider mb-0.5 text-sky-800">
                      Contextual Advisory:
                    </span>
                    {selectedLocation.risk_context.impact_advisory}
                  </div>
                )}
              </div>

              {/* Data Freshness Strip */}
              <div className="flex items-center justify-between text-xs p-2.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-slate-600 font-medium">Telemetry Freshness:</span>
                {getFreshnessBadge(
                  selectedLocation.observation_freshness,
                  selectedLocation.observation_age_minutes
                )}
              </div>

              {/* STRICTLY SEGREGATED: Observed Telemetry vs Forecasts */}
              <div className="space-y-3">
                {/* Block A: Historical Observed Telemetry */}
                <div className="p-3 bg-sky-50/50 rounded-lg border border-sky-100 space-y-2.5">
                  <div className="flex items-center justify-between text-xs font-bold text-sky-950 uppercase tracking-wider">
                    <span className="flex items-center gap-1.5">
                      <History className="w-3.5 h-3.5 text-sky-700" />
                      Historical Observations (Depth in mm)
                    </span>
                    <span className="text-[10px] text-sky-600 font-normal">Past Data</span>
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-center font-mono">
                    <div className="bg-white p-2 rounded border border-sky-100 shadow-2xs">
                      <span className="text-[10px] text-slate-500 uppercase block">24h Total</span>
                      <span className="text-sm font-extrabold text-slate-900">
                        {selectedLocation.historical_context?.rainfall_24h_mm !== null &&
                        selectedLocation.historical_context?.rainfall_24h_mm !== undefined
                          ? `${selectedLocation.historical_context.rainfall_24h_mm.toFixed(1)} mm`
                          : 'N/A'}
                      </span>
                    </div>
                    <div className="bg-white p-2 rounded border border-sky-100 shadow-2xs">
                      <span className="text-[10px] text-slate-500 uppercase block">72h Total</span>
                      <span className="text-sm font-extrabold text-slate-900">
                        {selectedLocation.historical_context?.rainfall_72h_mm !== null &&
                        selectedLocation.historical_context?.rainfall_72h_mm !== undefined
                          ? `${selectedLocation.historical_context.rainfall_72h_mm.toFixed(1)} mm`
                          : 'N/A'}
                      </span>
                    </div>
                    <div className="bg-white p-2 rounded border border-sky-100 shadow-2xs">
                      <span className="text-[10px] text-slate-500 uppercase block">7d Total</span>
                      <span className="text-sm font-extrabold text-slate-900">
                        {selectedLocation.historical_context?.rainfall_7d_mm !== null &&
                        selectedLocation.historical_context?.rainfall_7d_mm !== undefined
                          ? `${selectedLocation.historical_context.rainfall_7d_mm.toFixed(1)} mm`
                          : 'N/A'}
                      </span>
                    </div>
                  </div>

                  {selectedLocation.historical_context && (
                    <div className="flex items-center justify-between text-xs pt-1 border-t border-sky-100/60">
                      <span className="text-slate-600 font-medium">Daily Trend:</span>
                      {getTrendBadge(selectedLocation.historical_context.daily_trend)}
                    </div>
                  )}

                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-600 font-medium">Consecutive Rainy Days:</span>
                    <span className="font-bold text-slate-800">
                      {selectedLocation.historical_context?.consecutive_rainy_days ?? 0} day(s){' '}
                      <span className="text-[10px] text-slate-400 font-normal">(&ge; 2.5 mm)</span>
                    </span>
                  </div>
                </div>

                {/* Block B: Forward-Looking Numerical Weather Forecast */}
                <div className="p-3 bg-emerald-50/40 rounded-lg border border-emerald-100 space-y-2">
                  <div className="flex items-center justify-between text-xs font-bold text-emerald-950 uppercase tracking-wider">
                    <span className="flex items-center gap-1.5">
                      <CloudRain className="w-3.5 h-3.5 text-emerald-700" />
                      Forward Forecast Horizon (Open-Meteo)
                    </span>
                    <span className="text-[10px] text-emerald-600 font-normal">Future</span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-center font-mono">
                    <div className="bg-white p-2 rounded border border-emerald-100 shadow-2xs">
                      <span className="text-[10px] text-slate-500 uppercase block">Next 6h Forecast</span>
                      <span className="text-sm font-extrabold text-slate-900">
                        {selectedLocation.forecast_6h_precipitation_mm !== null &&
                        selectedLocation.forecast_6h_precipitation_mm !== undefined
                          ? `${selectedLocation.forecast_6h_precipitation_mm.toFixed(1)} mm`
                          : 'Unavailable'}
                      </span>
                    </div>
                    <div className="bg-white p-2 rounded border border-emerald-100 shadow-2xs">
                      <span className="text-[10px] text-slate-500 uppercase block">Next 24h Forecast</span>
                      <span className="text-sm font-extrabold text-slate-900">
                        {selectedLocation.forecast_24h_precipitation_mm !== null &&
                        selectedLocation.forecast_24h_precipitation_mm !== undefined
                          ? `${selectedLocation.forecast_24h_precipitation_mm.toFixed(1)} mm`
                          : 'Unavailable'}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[11px] text-slate-600 pt-1">
                    <span>Forecast Horizon:</span>
                    <span className="font-semibold text-slate-800">
                      {selectedLocation.active_forecast_hours}h active ({selectedLocation.forecast_completeness_pct}% complete)
                    </span>
                  </div>
                </div>

                {/* Block C: Antecedent Rainfall Indicator */}
                {selectedLocation.antecedent_indicator && (
                  <div className="p-3 bg-purple-50/50 rounded-lg border border-purple-100 space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-bold text-purple-950 uppercase tracking-wider flex items-center gap-1">
                        <Droplets className="w-3.5 h-3.5 text-purple-700" />
                        Antecedent Indicator (ARI)
                      </span>
                      {getAntecedentBadge(selectedLocation.antecedent_indicator.indicator_category)}
                    </div>
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="text-slate-600">Weighted Moisture Index:</span>
                      <span className="font-extrabold text-purple-950 text-sm">
                        {selectedLocation.antecedent_indicator.indicator_value_mm !== null
                          ? `${selectedLocation.antecedent_indicator.indicator_value_mm.toFixed(1)} mm`
                          : 'N/A'}
                      </span>
                    </div>
                    <p className="text-[10.5px] text-slate-600 leading-tight">
                      {selectedLocation.antecedent_indicator.explanation}
                    </p>
                    <p className="text-[9.5px] text-purple-800/80 italic pt-1 border-t border-purple-100">
                      Formula: 0.5 &times; 24h + 0.3 &times; 72h + 0.2 &times; 7d precipitation depth.
                    </p>
                  </div>
                )}
              </div>

              {/* Quality Warnings */}
              {selectedLocation.quality_warnings && selectedLocation.quality_warnings.length > 0 && (
                <div className="space-y-1 pt-1">
                  {selectedLocation.quality_warnings.map((w, idx) => (
                    <div
                      key={idx}
                      className="p-2 rounded bg-amber-50 border border-amber-200 text-amber-800 text-[11px] flex items-start gap-1.5"
                    >
                      <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                      <span>{w}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Footer link to detailed location historical view */}
              <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
                <span className="text-[10px] text-slate-400">ID: {selectedLocation.location_id.slice(0, 8)}...</span>
                <Link
                  to="/risk"
                  className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:text-sky-700 transition"
                >
                  View Complete Historical Analytics
                  <ArrowRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            </div>
          ) : (
            <div className="p-8 bg-white rounded-xl border border-slate-200 text-center text-slate-500 space-y-2">
              <MapPin className="w-8 h-8 text-slate-300 mx-auto" />
              <p className="text-sm font-semibold text-slate-700">No Location Selected</p>
              <p className="text-xs text-slate-400">Click any marker on the map to inspect its rainfall risk telemetry.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

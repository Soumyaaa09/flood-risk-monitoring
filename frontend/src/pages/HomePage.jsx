import React, { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Calendar,
  CheckCircle2,
  Clock,
  CloudRain,
  Compass,
  Database,
  Droplets,
  ExternalLink,
  History,
  Info,
  Layers,
  MapPin,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Waves,
  XCircle,
} from 'lucide-react';

export default function HomePage() {
  const [riskData, setRiskData] = useState(null);
  const [monitoringSummary, setMonitoringSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedLocationId, setSelectedLocationId] = useState(null);

  // Fetch live risk telemetry and monitoring summary
  const fetchDashboardData = async () => {
    try {
      setLoading(true);
      setError(null);

      // Concurrent fetch for risk telemetry and monitoring health
      const [riskRes, monRes] = await Promise.allSettled([
        fetch('/api/v1/risk/rainfall'),
        fetch('/api/v1/monitoring/summary'),
      ]);

      if (riskRes.status === 'fulfilled' && riskRes.value.ok) {
        const rJson = await riskRes.value.json();
        setRiskData(rJson);
        if (rJson.assessments?.length > 0 && !selectedLocationId) {
          setSelectedLocationId(rJson.assessments[0].location_id);
        }
      }

      if (monRes.status === 'fulfilled' && monRes.value.ok) {
        const mJson = await monRes.value.json();
        setMonitoringSummary(mJson);
      }
    } catch (err) {
      setError(err.message || 'Failed to connect to FloodWatch telemetry services.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
  }, []);

  const assessments = riskData?.assessments || [];

  // Selected station record
  const selectedStation = useMemo(() => {
    if (!selectedLocationId) return assessments[0] || null;
    return assessments.find((loc) => loc.location_id === selectedLocationId) || assessments[0] || null;
  }, [assessments, selectedLocationId]);

  // Badge helpers
  const getRiskBadge = (category) => {
    switch (category) {
      case 'VERY_HIGH':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-red-100 text-red-800 border border-red-300">
            <AlertTriangle className="w-3.5 h-3.5 text-red-600 animate-pulse" />
            Very High Risk
          </span>
        );
      case 'HIGH':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-orange-100 text-orange-800 border border-orange-300">
            <AlertCircle className="w-3.5 h-3.5 text-orange-600" />
            High Risk
          </span>
        );
      case 'MODERATE':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-amber-100 text-amber-800 border border-amber-300">
            <Info className="w-3.5 h-3.5 text-amber-600" />
            Moderate Risk
          </span>
        );
      case 'LOW':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-emerald-100 text-emerald-800 border border-emerald-300">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
            Low Risk
          </span>
        );
      case 'UNAVAILABLE':
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-black uppercase bg-slate-100 text-slate-700 border border-slate-300">
            <XCircle className="w-3.5 h-3.5 text-slate-500" />
            Unavailable
          </span>
        );
    }
  };

  const getFreshnessPill = (state) => {
    switch (state) {
      case 'fresh':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
            <CheckCircle2 className="w-3 h-3 text-emerald-600" /> FRESH
          </span>
        );
      case 'delayed':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200">
            <Clock className="w-3 h-3 text-amber-600" /> DELAYED
          </span>
        );
      case 'stale':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-orange-700 bg-orange-50 px-2 py-0.5 rounded-full border border-orange-200">
            <AlertTriangle className="w-3 h-3 text-orange-600" /> STALE
          </span>
        );
      case 'unavailable':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200">
            <XCircle className="w-3 h-3 text-rose-600" /> UNAVAILABLE
          </span>
        );
    }
  };

  const getAntecedentBadge = (cat) => {
    switch (cat) {
      case 'EXTREME_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-red-100 text-red-800 border border-red-200">Extreme</span>;
      case 'HIGH_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-orange-100 text-orange-800 border border-orange-200">High</span>;
      case 'MODERATE_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-amber-100 text-amber-800 border border-amber-200">Moderate</span>;
      case 'LOW_ANTECEDENT':
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 border border-emerald-200">Low</span>;
      case 'UNAVAILABLE':
      default:
        return <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">N/A</span>;
    }
  };

  const getTrendBadge = (trend) => {
    switch (trend) {
      case 'INCREASING':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-red-700 bg-red-50 px-2 py-0.5 rounded border border-red-200">
            <TrendingUp className="w-3 h-3 text-red-600" /> Increasing
          </span>
        );
      case 'DECREASING':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
            <TrendingDown className="w-3 h-3 text-emerald-600" /> Decreasing
          </span>
        );
      case 'STEADY':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-700 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
            Steady
          </span>
        );
      case 'NO_RAIN':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
            No Rain
          </span>
        );
      case 'INSUFFICIENT_DATA':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-500 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
            Insufficient Data
          </span>
        );
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-10">
      {/* 1. Hero & Introduction Section */}
      <section className="bg-white rounded-3xl border border-slate-200 p-8 sm:p-12 shadow-sm relative overflow-hidden">
        <div className="absolute top-0 right-0 -mt-12 -mr-12 w-96 h-96 bg-sky-50 rounded-full blur-3xl pointer-events-none" />
        <div className="max-w-3xl space-y-5 relative">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-50 text-sky-800 text-xs font-bold uppercase tracking-wider border border-sky-100">
            <Compass className="w-3.5 h-3.5 text-sky-600" />
            Public Rainfall & Runoff Intelligence &bull; Odisha Pilot
          </div>
          <h1 className="text-3xl sm:text-5xl font-extrabold text-slate-900 tracking-tight leading-tight">
            FloodWatch Public Rainfall Monitoring
          </h1>
          <p className="text-base sm:text-lg text-slate-600 leading-relaxed">
            Transparent, open-access rainfall and surface runoff intelligence for the Mahanadi delta basin.
            Evaluates atmospheric nowcasts, numerical forecasts, and antecedent moisture across
            <strong> Paradeep, Cuttack, and Kendrapara</strong>.
          </p>

          <div className="pt-2 flex flex-wrap gap-3">
            <Link
              to="/map"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-sky-600 text-white font-bold text-sm hover:bg-sky-500 transition shadow-sm"
            >
              <Compass className="w-4 h-4" />
              Launch Monitoring Map
              <ArrowRight className="w-4 h-4" />
            </Link>
            <Link
              to="/risk"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-white border border-slate-200 text-slate-700 font-bold text-sm hover:bg-slate-50 transition shadow-sm"
            >
              <AlertTriangle className="w-4 h-4 text-amber-600" />
              Rainfall Risk Engine
            </Link>
            <Link
              to="/history"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-white border border-slate-200 text-slate-700 font-bold text-sm hover:bg-slate-50 transition shadow-sm"
            >
              <History className="w-4 h-4 text-slate-600" />
              Historical Analytics
            </Link>
          </div>
        </div>
      </section>

      {/* 2. Catchment-Wide Status KPI Strip */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
            Catchment Risk Status
          </span>
          <div className="pt-1">
            {riskData ? (
              getRiskBadge(riskData.highest_risk_category)
            ) : (
              <span className="text-sm text-slate-400">Loading...</span>
            )}
          </div>
          <span className="text-[11px] text-slate-400 block pt-1">
            Peak level across 3 pilot monitoring stations
          </span>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
            Active Pilot Stations
          </span>
          <p className="text-2xl font-black text-slate-900">3 Grid Points</p>
          <span className="text-[11px] text-slate-400 block">
            Paradeep, Cuttack & Kendrapara (Odisha)
          </span>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
            Telemetry Freshness
          </span>
          <div className="pt-1">
            {monitoringSummary ? (
              getFreshnessPill(monitoringSummary.overall_freshness)
            ) : (
              <span className="text-sm text-slate-400">Evaluating...</span>
            )}
          </div>
          <span className="text-[11px] text-slate-400 block pt-1">
            Open-Meteo atmospheric model cadence
          </span>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
            Decision Support Status
          </span>
          <p className="text-sm font-bold text-slate-800 flex items-center gap-1.5 pt-1">
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            Operational Prototype
          </p>
          <span className="text-[11px] text-slate-400 block">
            Threshold version: {riskData?.threshold_version || '2.4.1'}
          </span>
        </div>
      </section>

      {/* 3. Interactive Location Selection & Public Information Cards */}
      <section className="bg-white rounded-3xl border border-slate-200 p-6 sm:p-8 shadow-sm space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-100 pb-5">
          <div>
            <div className="flex items-center gap-2 text-xs font-bold text-sky-700 uppercase tracking-wider">
              <MapPin className="w-4 h-4" />
              Pilot Station Telemetry
            </div>
            <h2 className="text-xl sm:text-2xl font-extrabold text-slate-900 mt-1">
              Select a Pilot Location in Odisha
            </h2>
            <p className="text-xs sm:text-sm text-slate-500 mt-0.5">
              Inspect verified observations, historical totals, and forward forecasts for your selected location.
            </p>
          </div>

          {/* Location Switcher Tabs */}
          <div className="flex items-center gap-1.5 bg-slate-100 p-1 rounded-xl overflow-x-auto">
            {assessments.map((loc) => {
              const isSelected = selectedStation?.location_id === loc.location_id;
              const shortName = loc.location_name.replace(' Weather Grid Point', '');
              return (
                <button
                  key={loc.location_id}
                  onClick={() => setSelectedLocationId(loc.location_id)}
                  className={`px-3.5 py-1.5 rounded-lg text-xs font-bold transition whitespace-nowrap flex items-center gap-1.5 ${
                    isSelected
                      ? 'bg-white text-slate-900 shadow-sm'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  <MapPin className={`w-3.5 h-3.5 ${isSelected ? 'text-sky-600' : 'text-slate-400'}`} />
                  {shortName}
                </button>
              );
            })}
          </div>
        </div>

        {/* Selected Station Detailed Public Information Cards */}
        {selectedStation ? (
          <div className="space-y-6">
            {/* Location Banner */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 p-4 rounded-2xl bg-slate-50 border border-slate-200">
              <div>
                <span className="text-xs font-bold text-sky-700 bg-sky-50 px-2 py-0.5 rounded border border-sky-100">
                  {selectedStation.district_name || 'Odisha Pilot District'}
                </span>
                <h3 className="text-lg font-extrabold text-slate-900 mt-1">{selectedStation.location_name}</h3>
                <p className="text-xs text-slate-500 font-mono">
                  Coordinates: {selectedStation.latitude.toFixed(4)}&deg;N, {selectedStation.longitude.toFixed(4)}&deg;E &bull; Virtual Model Grid
                </p>
              </div>

              <div className="flex items-center gap-2">
                {getRiskBadge(selectedStation.risk_category)}
                <span className="text-xs font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200">
                  {selectedStation.confidence} Confidence
                </span>
              </div>
            </div>

            {/* Plain-Language Public Explanation Callout (Phase 2.4.4) */}
            <div className="p-4 rounded-2xl bg-sky-50/60 border border-sky-200/70 space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-bold text-sky-900 uppercase tracking-wider">
                <Info className="w-4 h-4 text-sky-700" />
                Current Condition Summary
              </div>
              <p className="text-sm font-semibold text-slate-800 leading-relaxed">
                {selectedStation.risk_context?.public_summary || selectedStation.assessment_explanation}
              </p>
              {selectedStation.risk_context?.impact_advisory && (
                <p className="text-xs text-sky-900/90 pt-1 border-t border-sky-200/50">
                  <strong>Advisory:</strong> {selectedStation.risk_context.impact_advisory}
                </p>
              )}
            </div>

            {/* Strictly Segregated Triad Cards: Observed, Historical, Forecast */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {/* Card 1: Observed Rainfall */}
              <div className="p-5 rounded-2xl border border-slate-200 bg-white shadow-2xs space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
                    <CloudRain className="w-4 h-4 text-sky-600" />
                    Observed Nowcast
                  </span>
                  {getFreshnessPill(selectedStation.observation_freshness)}
                </div>

                <div className="text-center py-2">
                  <span className="text-4xl font-black text-slate-900 font-mono">
                    {selectedStation.recent_observed_rainfall_mm !== null
                      ? `${selectedStation.recent_observed_rainfall_mm.toFixed(1)}`
                      : 'N/A'}
                  </span>
                  <span className="text-sm font-bold text-slate-500 ml-1">mm</span>
                  <p className="text-[11px] text-slate-400 mt-1">
                    {selectedStation.observation_age_minutes !== null
                      ? `Recorded ${Math.round(selectedStation.observation_age_minutes)} min ago`
                      : 'Telemetry unavailable'}
                  </p>
                </div>

                <p className="text-xs text-slate-500 pt-2 border-t border-slate-100">
                  Represents discrete accumulated nowcast depth over the latest observation window.
                </p>
              </div>

              {/* Card 2: Historical Multi-Day Rainfall */}
              <div className="p-5 rounded-2xl border border-slate-200 bg-white shadow-2xs space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
                    <History className="w-4 h-4 text-indigo-600" />
                    Historical Preceding Rain
                  </span>
                  {selectedStation.historical_context &&
                    getTrendBadge(selectedStation.historical_context.daily_trend)}
                </div>

                <div className="grid grid-cols-2 gap-2 text-center py-1 font-mono">
                  <div className="p-2 rounded-lg bg-slate-50">
                    <span className="text-[10px] text-slate-500 uppercase block font-sans">Past 24h</span>
                    <span className="text-lg font-extrabold text-slate-900">
                      {selectedStation.historical_context?.rainfall_24h_mm !== null &&
                      selectedStation.historical_context?.rainfall_24h_mm !== undefined
                        ? `${selectedStation.historical_context.rainfall_24h_mm.toFixed(1)} mm`
                        : 'N/A'}
                    </span>
                  </div>
                  <div className="p-2 rounded-lg bg-slate-50">
                    <span className="text-[10px] text-slate-500 uppercase block font-sans">Past 72h</span>
                    <span className="text-lg font-extrabold text-slate-900">
                      {selectedStation.historical_context?.rainfall_72h_mm !== null &&
                      selectedStation.historical_context?.rainfall_72h_mm !== undefined
                        ? `${selectedStation.historical_context.rainfall_72h_mm.toFixed(1)} mm`
                        : 'N/A'}
                    </span>
                  </div>
                </div>

                <div className="flex items-center justify-between text-xs text-slate-600 pt-1">
                  <span>Rainy Days Streak:</span>
                  <span className="font-bold text-slate-900">
                    {selectedStation.historical_context?.consecutive_rainy_days ?? 0} days (&ge;2.5mm)
                  </span>
                </div>
              </div>

              {/* Card 3: Upcoming Forecast Rainfall */}
              <div className="p-5 rounded-2xl border border-slate-200 bg-white shadow-2xs space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
                    <Compass className="w-4 h-4 text-emerald-600" />
                    Upcoming Forecast
                  </span>
                  <span className="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-100">
                    Next 24 Hours
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-center py-1 font-mono">
                  <div className="p-2 rounded-lg bg-slate-50">
                    <span className="text-[10px] text-slate-500 uppercase block font-sans">Next 6h</span>
                    <span className="text-lg font-extrabold text-slate-900">
                      {selectedStation.forecast_6h_precipitation_mm !== null &&
                      selectedStation.forecast_6h_precipitation_mm !== undefined
                        ? `${selectedStation.forecast_6h_precipitation_mm.toFixed(1)} mm`
                        : 'N/A'}
                    </span>
                  </div>
                  <div className="p-2 rounded-lg bg-slate-50">
                    <span className="text-[10px] text-slate-500 uppercase block font-sans">Next 24h</span>
                    <span className="text-lg font-extrabold text-slate-900">
                      {selectedStation.forecast_24h_precipitation_mm !== null &&
                      selectedStation.forecast_24h_precipitation_mm !== undefined
                        ? `${selectedStation.forecast_24h_precipitation_mm.toFixed(1)} mm`
                        : 'N/A'}
                    </span>
                  </div>
                </div>

                <p className="text-xs text-slate-500 pt-1">
                  Horizon: {selectedStation.active_forecast_hours}h active ({selectedStation.forecast_completeness_pct}% completeness)
                </p>
              </div>
            </div>

            {/* Antecedent Indicator Banner */}
            {selectedStation.antecedent_indicator && (
              <div className="p-4 rounded-xl bg-purple-50/50 border border-purple-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-purple-950 uppercase tracking-wider">
                      Antecedent Rainfall Indicator (ARI)
                    </span>
                    {getAntecedentBadge(selectedStation.antecedent_indicator.indicator_category)}
                  </div>
                  <p className="text-slate-600 mt-0.5">{selectedStation.antecedent_indicator.explanation}</p>
                </div>
                <div className="text-right sm:text-left font-mono">
                  <span className="text-xs text-slate-500 block">Weighted Moisture Index</span>
                  <span className="font-black text-purple-950 text-base">
                    {selectedStation.antecedent_indicator.indicator_value_mm !== null
                      ? `${selectedStation.antecedent_indicator.indicator_value_mm.toFixed(1)} mm`
                      : 'N/A'}
                  </span>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="p-8 text-center text-slate-400 bg-slate-50 rounded-2xl border border-slate-100">
            No station telemetry available.
          </div>
        )}
      </section>

      {/* 4. Interactive Map Launch Entry Point */}
      <section className="bg-gradient-to-r from-slate-900 via-sky-950 to-slate-900 text-white rounded-3xl p-8 sm:p-10 shadow-md relative overflow-hidden flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
        <div className="space-y-3 max-w-2xl">
          <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-300 uppercase tracking-wider bg-sky-900/60 px-3 py-1 rounded-full border border-sky-700/60">
            <Compass className="w-3.5 h-3.5" />
            Geospatial Visualizer
          </div>
          <h2 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
            Explore the Interactive Mahanadi Delta Map
          </h2>
          <p className="text-sm text-slate-300 leading-relaxed">
            View live spatial markers across Paradeep, Cuttack, and Kendrapara. Toggle risk filters,
            inspect station-level nowcasts, and zoom into vulnerable delta bifurcations.
          </p>
        </div>

        <Link
          to="/map"
          className="px-6 py-3.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-slate-950 font-black text-sm transition shadow-lg whitespace-nowrap flex items-center gap-2"
        >
          Open Interactive Map
          <ArrowRight className="w-4 h-4" />
        </Link>
      </section>

      {/* 5. Location-Wise Comparative Status Cards */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-slate-900">Station Overview Cards</h2>
            <p className="text-xs text-slate-500">Comparative look across all 3 pilot weather points</p>
          </div>
          <Link
            to="/risk"
            className="text-xs font-bold text-sky-600 hover:text-sky-700 transition flex items-center gap-1"
          >
            Detailed Risk Table <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {assessments.map((loc) => {
            const shortName = loc.location_name.replace(' Weather Grid Point', '');
            return (
              <div
                key={loc.location_id}
                onClick={() => setSelectedLocationId(loc.location_id)}
                className={`p-6 rounded-2xl border transition cursor-pointer space-y-4 shadow-sm ${
                  selectedStation?.location_id === loc.location_id
                    ? 'border-sky-500 bg-sky-50/20 ring-2 ring-sky-500/20'
                    : 'border-slate-200 bg-white hover:border-slate-300'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <span className="text-[11px] font-bold text-slate-500 uppercase">
                      {loc.district_name || 'Odisha'}
                    </span>
                    <h3 className="font-extrabold text-slate-900 text-lg">{shortName}</h3>
                  </div>
                  {getRiskBadge(loc.risk_category)}
                </div>

                <div className="grid grid-cols-2 gap-2 text-center text-xs font-mono">
                  <div className="p-2.5 rounded-xl bg-slate-50">
                    <span className="text-[10px] text-slate-400 block font-sans uppercase">Observed Rain</span>
                    <span className="text-base font-black text-slate-900">
                      {loc.recent_observed_rainfall_mm !== null ? `${loc.recent_observed_rainfall_mm.toFixed(1)} mm` : '--'}
                    </span>
                  </div>
                  <div className="p-2.5 rounded-xl bg-slate-50">
                    <span className="text-[10px] text-slate-400 block font-sans uppercase">24h Forecast</span>
                    <span className="text-base font-black text-slate-900">
                      {loc.forecast_24h_precipitation_mm !== null ? `${loc.forecast_24h_precipitation_mm.toFixed(1)} mm` : '--'}
                    </span>
                  </div>
                </div>

                <div className="flex items-center justify-between text-xs text-slate-500 pt-2 border-t border-slate-100">
                  <span>Confidence: <strong>{loc.confidence}</strong></span>
                  {getFreshnessPill(loc.observation_freshness)}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* 6. Public Safety Notice & Statutory Warning Links */}
      <section className="bg-amber-50/70 rounded-2xl border border-amber-200 p-6 sm:p-8 space-y-4">
        <div className="flex items-center gap-2 text-amber-900 font-bold text-base">
          <ShieldAlert className="w-5 h-5 text-amber-700 shrink-0" />
          Public Safety Information & Warning Authority Notice
        </div>
        <div className="text-xs sm:text-sm text-amber-950 space-y-2 leading-relaxed">
          <p>
            <strong>Decision-Support Prototype:</strong> FloodWatch is an experimental research system. It does
            <strong> NOT</strong> issue official flood or cyclone warnings.
          </p>
          <p>
            <strong>Virtual Stations:</strong> Locations are model-derived weather grid points from Open-Meteo,
            not physical ground rain-gauge stations operated by the Government of India or Odisha.
          </p>
          <p>
            For statutory flood forecasts, cyclone alerts, and evacuation instructions, refer directly to official authorities:
          </p>
          <div className="pt-2 flex flex-wrap gap-4 text-xs font-bold">
            <a
              href="https://mausam.imd.gov.in/bhubaneswar/"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-sky-800 hover:text-sky-950 underline"
            >
              IMD Meteorological Centre Bhubaneswar <ExternalLink className="w-3.5 h-3.5" />
            </a>
            <a
              href="https://www.osdma.org/"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-sky-800 hover:text-sky-950 underline"
            >
              Odisha State Disaster Management Authority (OSDMA) <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}

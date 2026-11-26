import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  BarChart3,
  Calendar,
  CheckCircle2,
  Clock,
  CloudRain,
  Compass,
  Database,
  Droplets,
  HelpCircle,
  History,
  Info,
  Layers,
  MapPin,
  Minus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Waves,
  XCircle,
} from 'lucide-react';

export default function RiskAssessmentPage() {
  const [summary, setSummary] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchRiskData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [riskResp, histResp] = await Promise.all([
        fetch('/api/v1/risk/rainfall'),
        fetch('/api/v1/risk/rainfall/history?days=7'),
      ]);

      if (!riskResp.ok) {
        const errJson = await riskResp.json().catch(() => ({}));
        throw new Error(errJson?.detail?.message || `HTTP error ${riskResp.status}`);
      }
      const data = await riskResp.json();
      setSummary(data);

      if (histResp.ok) {
        const histJson = await histResp.json();
        setHistoryData(histJson);
      }

      setLastRefreshed(new Date());
    } catch (err) {
      setError(err.message || 'Failed to load flood-risk assessment telemetry.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRiskData();
  }, []);

  const getRiskBadge = (category) => {
    switch (category) {
      case 'VERY_HIGH':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-black tracking-wide bg-rose-100 text-rose-800 border border-rose-300 shadow-sm animate-pulse">
            <AlertTriangle className="w-3.5 h-3.5 text-rose-700" />
            VERY HIGH
          </span>
        );
      case 'HIGH':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-extrabold tracking-wide bg-orange-100 text-orange-800 border border-orange-300 shadow-sm">
            <AlertTriangle className="w-3.5 h-3.5 text-orange-700" />
            HIGH
          </span>
        );
      case 'MODERATE':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold tracking-wide bg-amber-100 text-amber-800 border border-amber-300">
            <Clock className="w-3.5 h-3.5 text-amber-700" />
            MODERATE
          </span>
        );
      case 'LOW':
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold tracking-wide bg-emerald-100 text-emerald-800 border border-emerald-300">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-700" />
            LOW
          </span>
        );
      case 'UNAVAILABLE':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold tracking-wide bg-slate-100 text-slate-700 border border-slate-300">
            <XCircle className="w-3.5 h-3.5 text-slate-500" />
            UNAVAILABLE
          </span>
        );
    }
  };

  const getAntecedentBadge = (category) => {
    switch (category) {
      case 'EXTREME_ANTECEDENT':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-purple-100 text-purple-800 border border-purple-300">
            <AlertTriangle className="w-3 h-3 text-purple-700" />
            EXTREME ANTECEDENT
          </span>
        );
      case 'HIGH_ANTECEDENT':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-orange-100 text-orange-800 border border-orange-300">
            <Droplets className="w-3 h-3 text-orange-700" />
            HIGH ANTECEDENT
          </span>
        );
      case 'MODERATE_ANTECEDENT':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-300">
            <Clock className="w-3 h-3 text-amber-700" />
            MODERATE ANTECEDENT
          </span>
        );
      case 'LOW_ANTECEDENT':
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-300">
            <CheckCircle2 className="w-3 h-3 text-emerald-700" />
            LOW ANTECEDENT
          </span>
        );
      case 'UNAVAILABLE':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-300">
            <XCircle className="w-3 h-3 text-slate-500" />
            UNAVAILABLE
          </span>
        );
    }
  };

  const getTrendBadge = (trend) => {
    switch (trend) {
      case 'INCREASING':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-rose-700 bg-rose-50 px-2 py-0.5 rounded border border-rose-200">
            <TrendingUp className="w-3.5 h-3.5 text-rose-600" />
            Increasing Trend
          </span>
        );
      case 'DECREASING':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-sky-700 bg-sky-50 px-2 py-0.5 rounded border border-sky-200">
            <TrendingDown className="w-3.5 h-3.5 text-sky-600" />
            Decreasing Trend
          </span>
        );
      case 'STEADY':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
            <Minus className="w-3.5 h-3.5 text-amber-600" />
            Steady Trend
          </span>
        );
      case 'NO_RAIN':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
            No Preceding Rain
          </span>
        );
      case 'INSUFFICIENT_DATA':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-slate-600 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
            <HelpCircle className="w-3.5 h-3.5 text-slate-500" />
            Insufficient History
          </span>
        );
    }
  };

  const getConfidenceBadge = (confidence) => {
    switch (confidence) {
      case 'HIGH':
        return (
          <span className="text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
            High Confidence
          </span>
        );
      case 'MEDIUM':
        return (
          <span className="text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
            Medium Confidence
          </span>
        );
      case 'LOW':
        return (
          <span className="text-[11px] font-semibold text-orange-700 bg-orange-50 px-2 py-0.5 rounded border border-orange-200">
            Low Confidence (Delayed)
          </span>
        );
      case 'NONE':
      default:
        return (
          <span className="text-[11px] font-semibold text-slate-600 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
            No Confidence
          </span>
        );
    }
  };

  const formatTimestamp = (ts) => {
    if (!ts) return 'N/A';
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        timeZoneName: 'short',
      });
    } catch {
      return String(ts);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-700 uppercase tracking-wider bg-sky-50 px-2.5 py-1 rounded-full border border-sky-100 mb-2">
            <Activity className="w-3.5 h-3.5" />
            Phase 2.4.2 Rainfall Risk & Historical Telemetry
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
            Rainfall Risk & Historical Antecedent Analysis
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Provisional surface runoff risk and 7-day historical precipitation indicators for Paradeep, Cuttack, and Kendrapara.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          {summary && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 font-medium">Highest Risk:</span>
              {getRiskBadge(summary.highest_risk_category)}
            </div>
          )}
          <Link
            to="/map"
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-sky-600 text-sm font-semibold text-white hover:bg-sky-700 transition shadow-sm"
          >
            <Compass className="w-4 h-4" />
            Interactive Map
          </Link>
          <button
            onClick={fetchRiskData}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-white border border-slate-200 text-sm font-medium text-slate-700 hover:bg-slate-50 transition shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 text-slate-600 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 flex items-start gap-3 text-rose-800">
          <AlertCircle className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-semibold">Risk Engine Retrieval Error</p>
            <p className="text-rose-700 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Loading Skeleton */}
      {loading && !summary && (
        <div className="p-12 text-center text-slate-500 space-y-3">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto text-sky-600" />
          <p className="text-sm font-medium">Aggregating historical rainfall and evaluating thresholds...</p>
        </div>
      )}

      {summary && (
        <>
          {/* Top Metrics Row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            {/* Highest Current Risk */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Pipeline Highest Risk
              </span>
              <div className="pt-1">{getRiskBadge(summary.highest_risk_category)}</div>
              <p className="text-xs text-slate-500 pt-1">
                Forward 24h & 6h surface runoff hazard category
              </p>
            </div>

            {/* Risk Distribution Breakdown */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Category Distribution
              </span>
              <div className="flex flex-wrap gap-1.5 pt-1 text-xs">
                {summary.category_counts && (
                  <>
                    <span className="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 font-semibold border border-emerald-200">
                      {summary.category_counts.LOW || 0} Low
                    </span>
                    <span className="px-2 py-0.5 rounded bg-amber-50 text-amber-700 font-semibold border border-amber-200">
                      {summary.category_counts.MODERATE || 0} Mod
                    </span>
                    <span className="px-2 py-0.5 rounded bg-orange-50 text-orange-700 font-semibold border border-orange-200">
                      {summary.category_counts.HIGH || 0} High
                    </span>
                    <span className="px-2 py-0.5 rounded bg-rose-50 text-rose-700 font-semibold border border-rose-200">
                      {summary.category_counts.VERY_HIGH || 0} V.High
                    </span>
                    {summary.category_counts.UNAVAILABLE > 0 && (
                      <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-semibold border border-slate-200">
                        {summary.category_counts.UNAVAILABLE} Unavail
                      </span>
                    )}
                  </>
                )}
              </div>
              <p className="text-xs text-slate-400">Station categorization counts</p>
            </div>

            {/* Active Methodology & Threshold Version */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Threshold Parameters
              </span>
              <p className="text-sm font-bold text-slate-800 pt-1 font-mono">
                {summary.threshold_version}
              </p>
              <p className="text-xs text-slate-500">
                Provisional accumulation bands (15.6 / 64.5 / 115.6 mm)
              </p>
            </div>

            {/* Assessment Timestamp */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Last Telemetry Sync
              </span>
              <p className="text-sm font-bold text-slate-900 pt-1">
                {formatTimestamp(summary.assessment_timestamp)}
              </p>
              <p className="text-xs text-slate-500">
                Synchronized with latest Open-Meteo runs
              </p>
            </div>
          </div>

          {/* Section 1: Forward-Looking Rainfall Risk Cards */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold text-slate-900">Forward-Looking Surface Runoff Risk</h2>
                <p className="text-xs text-slate-500">
                  Precipitation accumulation evaluation and active hazard drivers for the next 6h and 24h.
                </p>
              </div>
              <span className="text-xs font-mono text-slate-500">
                {summary.assessments.length} grid points analyzed
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {summary.assessments.map((loc) => (
                <div
                  key={loc.location_id}
                  className="bg-white rounded-xl border border-slate-200 p-6 space-y-4 shadow-sm hover:border-slate-300 transition flex flex-col justify-between"
                >
                  <div className="space-y-4">
                    {/* Header */}
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h3 className="font-bold text-slate-900 text-base">{loc.location_name}</h3>
                        <span className="text-xs text-slate-500">District: {loc.district_name || 'Odisha'}</span>
                      </div>
                      {getRiskBadge(loc.risk_category)}
                    </div>

                    {/* Coordinates & Confidence */}
                    <div className="text-xs font-mono text-slate-600 bg-slate-50 px-2.5 py-1.5 rounded flex items-center justify-between">
                      <span>
                        {loc.latitude.toFixed(4)}N, {loc.longitude.toFixed(4)}E
                      </span>
                      <div>{getConfidenceBadge(loc.confidence)}</div>
                    </div>

                    {/* Precipitation Triad: Observed, 6h, 24h */}
                    <div className="grid grid-cols-3 gap-2 pt-2 border-t border-slate-100 text-center">
                      <div className="bg-slate-50/70 p-2 rounded-lg border border-slate-100">
                        <span className="text-[11px] text-slate-500 block">Observed</span>
                        <p className="text-base font-extrabold text-slate-900 mt-0.5">
                          {loc.recent_observed_rainfall_mm !== null
                            ? `${loc.recent_observed_rainfall_mm.toFixed(1)} mm`
                            : 'N/A'}
                        </p>
                        <span className="text-[10px] text-slate-400">nowcast</span>
                      </div>
                      <div className="bg-slate-50/70 p-2 rounded-lg border border-slate-100">
                        <span className="text-[11px] text-slate-500 block">Next 6h</span>
                        <p className="text-base font-extrabold text-slate-900 mt-0.5">
                          {loc.forecast_6h_precipitation_mm !== null
                            ? `${loc.forecast_6h_precipitation_mm.toFixed(1)} mm`
                            : 'N/A'}
                        </p>
                        <span className="text-[10px] text-slate-400">forecast</span>
                      </div>
                      <div className="bg-slate-50/70 p-2 rounded-lg border border-slate-100">
                        <span className="text-[11px] text-slate-500 block">Next 24h</span>
                        <p className="text-base font-extrabold text-slate-900 mt-0.5">
                          {loc.forecast_24h_precipitation_mm !== null
                            ? `${loc.forecast_24h_precipitation_mm.toFixed(1)} mm`
                            : 'N/A'}
                        </p>
                        <span className="text-[10px] text-slate-400">accum total</span>
                      </div>
                    </div>

                    {/* Freshness & Horizon Bar */}
                    <div className="space-y-1.5 text-xs text-slate-600 bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                      <div className="flex items-center justify-between">
                        <span className="text-slate-500 font-sans">Observation Freshness:</span>
                        <span className="font-semibold uppercase text-slate-800">
                          {loc.observation_freshness} ({loc.observation_age_minutes !== null ? `${loc.observation_age_minutes}m` : 'N/A'})
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-slate-500 font-sans">Forecast Horizon:</span>
                        <span className="font-semibold text-slate-800">
                          {loc.active_forecast_hours}h ({loc.forecast_completeness_pct}%)
                        </span>
                      </div>
                    </div>

                    {/* Public-Friendly Risk Explanation & Drivers (Phase 2.4.4) */}
                    <div className="text-xs text-slate-700 bg-slate-50 border border-slate-200 rounded-lg p-3.5 space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                          <Info className="w-3.5 h-3.5 text-sky-600" />
                          Risk Assessment Explanation
                        </span>
                        {getConfidenceBadge(loc.confidence)}
                      </div>

                      {/* Plain-Language Public Summary */}
                      <p className="text-slate-800 font-medium text-xs leading-relaxed bg-white p-2.5 rounded border border-slate-100 shadow-2xs">
                        {loc.risk_context?.public_summary || loc.assessment_explanation}
                      </p>

                      {/* Main Contributing Factors (Data-Derived) */}
                      {loc.risk_context?.contributing_factors && loc.risk_context.contributing_factors.length > 0 && (
                        <div className="space-y-1 pt-1">
                          <span className="text-[10.5px] font-bold text-slate-600 uppercase tracking-wider block">
                            Key Assessment Factors:
                          </span>
                          <ul className="space-y-1 text-[11px] text-slate-600 list-disc list-inside">
                            {loc.risk_context.contributing_factors.map((factor, fIdx) => (
                              <li key={fIdx} className="leading-tight">
                                {factor}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Public Impact Advisory */}
                      {loc.risk_context?.impact_advisory && (
                        <div className="text-[11px] text-sky-900 bg-sky-50/70 border border-sky-200/60 p-2 rounded leading-tight">
                          <span className="font-bold block text-[10.5px] uppercase tracking-wider mb-0.5 text-sky-800">
                            Contextual Advisory:
                          </span>
                          {loc.risk_context.impact_advisory}
                        </div>
                      )}
                    </div>

                    {/* Quality Warnings / Unavailable Details */}
                    {loc.quality_warnings && loc.quality_warnings.length > 0 && (
                      <div className="space-y-1 pt-1">
                        {loc.quality_warnings.map((w, idx) => (
                          <div
                            key={idx}
                            className="p-2 rounded bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-1.5"
                          >
                            <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                            <span>{w}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="text-[10px] text-slate-400 pt-2 border-t border-slate-100 flex items-center justify-between">
                    <span>Station ID: {loc.location_id.slice(0, 8)}...</span>
                    <span>Provisional Research</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Section 2: Historical Rainfall Analytics & Antecedent Indicators (Phase 2.4.2) */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-6 shadow-sm">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-slate-100 pb-4">
              <div>
                <div className="flex items-center gap-2">
                  <History className="w-5 h-5 text-sky-600" />
                  <h2 className="text-lg font-bold text-slate-900">
                    Historical Rainfall Analytics & Antecedent Indicators
                  </h2>
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  Observed historical accumulations (24h, 72h, 7d). Forecast records are strictly excluded from history.
                </p>
              </div>
              <span className="text-xs font-mono text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">
                7-Day Historical Window
              </span>
            </div>

            {/* Location-wise Comparative Cards */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {summary.assessments.map((loc) => {
                const hist = loc.historical_context;
                const ante = loc.antecedent_indicator;

                return (
                  <div
                    key={loc.location_id}
                    className="p-5 rounded-xl border border-slate-200 bg-slate-50/40 space-y-4 hover:border-slate-300 transition"
                  >
                    {/* Header */}
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h3 className="font-bold text-slate-900 text-sm">{loc.location_name}</h3>
                        <span className="text-xs text-slate-500">{loc.district_name || 'Odisha'}</span>
                      </div>
                      {ante && getAntecedentBadge(ante.indicator_category)}
                    </div>

                    {/* Antecedent Metric Strip */}
                    {ante && (
                      <div className="p-3 bg-white rounded-lg border border-slate-200/80 space-y-1.5">
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-medium text-slate-600">Antecedent Index:</span>
                          <span className="font-bold text-slate-900 text-sm">
                            {ante.indicator_value_mm !== null ? `${ante.indicator_value_mm.toFixed(1)} mm` : 'Unavailable'}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-500 leading-tight">
                          {ante.explanation}
                        </p>
                      </div>
                    )}

                    {/* Historical Accumulation Triad: 24h, 72h, 7d */}
                    <div className="grid grid-cols-3 gap-2 text-center text-xs">
                      <div className="p-2.5 rounded-lg bg-white border border-slate-200/80">
                        <span className="text-[10px] text-slate-500 block uppercase font-medium">Past 24h</span>
                        <p className="text-base font-extrabold text-slate-900 mt-0.5">
                          {hist?.rainfall_24h_mm !== null && hist?.rainfall_24h_mm !== undefined
                            ? `${hist.rainfall_24h_mm.toFixed(1)} mm`
                            : 'N/A'}
                        </p>
                        <span className="text-[10px] text-slate-400">observed</span>
                      </div>
                      <div className="p-2.5 rounded-lg bg-white border border-slate-200/80">
                        <span className="text-[10px] text-slate-500 block uppercase font-medium">Past 72h</span>
                        <p className="text-base font-extrabold text-slate-900 mt-0.5">
                          {hist?.rainfall_72h_mm !== null && hist?.rainfall_72h_mm !== undefined
                            ? `${hist.rainfall_72h_mm.toFixed(1)} mm`
                            : 'N/A'}
                        </p>
                        <span className="text-[10px] text-slate-400">observed</span>
                      </div>
                      <div className="p-2.5 rounded-lg bg-white border border-slate-200/80">
                        <span className="text-[10px] text-slate-500 block uppercase font-medium">Past 7d</span>
                        <p className="text-base font-extrabold text-slate-900 mt-0.5">
                          {hist?.rainfall_7d_mm !== null && hist?.rainfall_7d_mm !== undefined
                            ? `${hist.rainfall_7d_mm.toFixed(1)} mm`
                            : 'N/A'}
                        </p>
                        <span className="text-[10px] text-slate-400">observed</span>
                      </div>
                    </div>

                    {/* Trend & Rainy Days Indicators */}
                    {hist && (
                      <div className="space-y-2 text-xs bg-white p-3 rounded-lg border border-slate-200/80">
                        <div className="flex items-center justify-between">
                          <span className="text-slate-500 font-medium">Daily Trend:</span>
                          {getTrendBadge(hist.daily_trend)}
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-slate-500 font-medium">Consecutive Rainy Days:</span>
                          <span className="font-bold text-slate-800">
                            {hist.consecutive_rainy_days} day(s) <span className="text-[10px] text-slate-400 font-normal">(&ge; 2.5 mm)</span>
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-slate-500 font-medium">Data Coverage & Cadence:</span>
                          <span className="font-semibold text-slate-700">
                            {hist.coverage_pct}% ({hist.detected_interval_minutes ? `${Math.round(hist.detected_interval_minutes)}m` : '60m'} cadence)
                          </span>
                        </div>
                        {hist.current_day_in_progress && (
                          <div className="text-[10px] text-sky-600 bg-sky-50 px-2 py-0.5 rounded font-medium flex items-center gap-1">
                            <Activity className="w-3 h-3" />
                            Current day ({hist.timezone || 'Asia/Kolkata'}) in progress; incomplete hours excluded from trend
                          </div>
                        )}
                      </div>
                    )}

                    {/* Daily Rainfall Mini-Bar Visualizer */}
                    {hist && hist.daily_totals && hist.daily_totals.length > 0 && (
                      <div className="space-y-1.5 pt-1">
                        <div className="flex items-center justify-between text-[11px] text-slate-500 font-medium">
                          <span className="flex items-center gap-1">
                            <BarChart3 className="w-3.5 h-3.5" />
                            Daily Totals ({hist.timezone || 'Asia/Kolkata'})
                          </span>
                          <span>IMD-Consistent Threshold: 2.5 mm</span>
                        </div>
                        <div className="grid grid-cols-7 gap-1 text-center font-mono">
                          {hist.daily_totals.slice(-7).map((d) => {
                            const isRainy = d.is_rainy_day;
                            const rfVal = d.rainfall_mm ?? 0;
                            const heightPct = Math.min(100, Math.max(10, (rfVal / 25.0) * 100));
                            return (
                              <div key={d.date} className="flex flex-col items-center gap-1">
                                <div className="w-full h-12 bg-slate-100 rounded flex items-end justify-center p-0.5 relative">
                                  <div
                                    className={`w-full rounded-sm transition-all ${
                                      d.rainfall_mm === null
                                        ? 'bg-rose-100 border border-dashed border-rose-300'
                                        : isRainy
                                        ? 'bg-sky-600'
                                        : d.rainfall_mm > 0
                                        ? 'bg-sky-300'
                                        : 'bg-slate-200'
                                    }`}
                                    style={{ height: `${d.rainfall_mm !== null && d.rainfall_mm > 0 ? heightPct : 6}%` }}
                                    title={`${d.date}: ${d.rainfall_mm !== null ? `${d.rainfall_mm} mm` : 'Missing data'} (${d.observation_count} readings)${d.is_partial_day ? ' [In Progress]' : ''}`}
                                  />
                                </div>
                                <span className="text-[9px] text-slate-500 truncate w-full flex items-center justify-center gap-0.5">
                                  {d.date.slice(5)}
                                  {d.is_partial_day && <span className="text-[8px] text-amber-600 font-bold">*</span>}
                                </span>
                                <span className={`text-[10px] font-bold ${isRainy ? 'text-sky-700' : 'text-slate-600'}`}>
                                  {d.rainfall_mm !== null ? `${d.rainfall_mm.toFixed(0)}m` : '-'}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Gap Warning if Incomplete Coverage */}
                    {hist && hist.data_gap_warning && (
                      <div className="p-2 rounded bg-amber-50 border border-amber-200 text-amber-800 text-[11px] flex items-start gap-1.5">
                        <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                        <span>{hist.data_gap_warning}</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Hydrological Component Architecture Readiness */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-slate-900">Hydrological Risk Engine Modular Architecture</h2>
                <p className="text-xs text-slate-500">
                  FloodWatch strictly isolates rainfall accumulation from river water level and volumetric discharge.
                </p>
              </div>
              <span className="text-xs bg-slate-100 px-2.5 py-1 rounded-full text-slate-600 font-mono">
                3 Independent Modules
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
              {summary.component_readiness &&
                summary.component_readiness.map((comp, idx) => (
                  <div
                    key={idx}
                    className={`p-4 rounded-xl border space-y-2 ${
                      comp.status === 'OPERATIONAL'
                        ? 'bg-emerald-50/50 border-emerald-200'
                        : comp.status === 'NON_OPERATIONAL'
                        ? 'bg-amber-50/50 border-amber-200'
                        : 'bg-slate-50 border-slate-200'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-slate-900">{comp.component_name}</span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-black uppercase ${
                          comp.status === 'OPERATIONAL'
                            ? 'bg-emerald-100 text-emerald-800'
                            : comp.status === 'NON_OPERATIONAL'
                            ? 'bg-amber-100 text-amber-800'
                            : 'bg-slate-200 text-slate-700'
                        }`}
                      >
                        {comp.status}
                      </span>
                    </div>
                    <p className="text-slate-600">{comp.description}</p>
                    <p className="text-[11px] text-slate-500 italic pt-1 border-t border-slate-200/50">
                      {comp.reason}
                    </p>
                  </div>
                ))}
            </div>
          </div>

          {/* Safety & Prototype Disclaimer Protocol */}
          <section className="bg-amber-50/70 rounded-xl border border-amber-200 p-5 space-y-2.5">
            <div className="flex items-center gap-2 text-amber-900 font-bold text-sm">
              <ShieldAlert className="w-5 h-5 text-amber-700" />
              Hydrological Safety Caveats & Research Disclaimer
            </div>
            <ul className="text-xs sm:text-sm text-amber-900 space-y-1.5 list-disc list-inside">
              <li>
                <strong>Experimental Rainfall Model:</strong> FloodWatch rainfall risk categories represent local surface water runoff indicators derived from numerical weather predictions.
              </li>
              <li>
                <strong>Antecedent Precipitation vs Soil Saturation:</strong> The Antecedent Rainfall Indicator represents empirical precipitation accumulation depth over 24h, 72h, and 7d. It does <strong>not</strong> simulate geotechnical soil suction, infiltration capacity, or water table depths.
              </li>
              <li>
                <strong>No Official Warning Authority:</strong> This system does <strong>NOT</strong> issue official flood or cyclone warnings. Official alerts are issued solely by the India Meteorological Department (IMD) and Odisha State Disaster Management Authority (OSDMA).
              </li>
              <li>
                <strong>Rainfall vs River Stage Segregation:</strong> Rainfall depth (mm) is <strong>never</strong> collapsed into a single numerical index with river stage height (m) or discharge (m³/s). High rainfall does not confirm river embankment breaches.
              </li>
              <li>
                <strong>No Fake Zeroes:</strong> Missing observations or incomplete forecasts are classified as <code>UNAVAILABLE</code>, never assumed to be zero or low risk.
              </li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

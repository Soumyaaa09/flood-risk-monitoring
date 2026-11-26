import React, { useEffect, useState } from 'react';
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
  Compass,
  Download,
  Droplets,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  History,
  Info,
  MapPin,
  Printer,
  RefreshCw,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-react';

export default function HistoricalAnalyticsPage() {
  const [windowDays, setWindowDays] = useState(7);
  const [selectedLocationId, setSelectedLocationId] = useState('ALL');
  const [historySummary, setHistorySummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Report Modal state
  const [activeReportModal, setActiveReportModal] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(null);
  const [reportData, setReportData] = useState(null);
  const [reportLocationId, setReportLocationId] = useState(null);

  const fetchHistory = async () => {
    try {
      setLoading(true);
      setError(null);
      const url =
        selectedLocationId === 'ALL'
          ? `/api/v1/risk/rainfall/history?days=${windowDays}`
          : `/api/v1/risk/rainfall/history?days=${windowDays}&location_id=${selectedLocationId}`;

      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Failed to load historical analytics (HTTP ${res.status})`);
      }
      const data = await res.json();
      setHistorySummary(data);
    } catch (err) {
      setError(err.message || 'Unable to load historical rainfall telemetry.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHistory();
  }, [windowDays, selectedLocationId]);

  // Normalize locations list whether single or multi-location response
  const locationsList = historySummary
    ? historySummary.locations || (historySummary.location_id ? [historySummary] : [])
    : [];

  const handleOpenReport = async (locId) => {
    setReportLocationId(locId);
    setActiveReportModal(true);
    setReportLoading(true);
    setReportError(null);
    setReportData(null);

    try {
      const res = await fetch(
        `/api/v1/reports/rainfall?location_id=${locId}&days=${windowDays}`
      );
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail?.message || `Failed to fetch report (HTTP ${res.status})`);
      }
      const data = await res.json();
      setReportData(data);
    } catch (err) {
      setReportError(err.message || 'Unable to load structured report.');
    } finally {
      setReportLoading(false);
    }
  };

  const handlePrint = () => {
    window.print();
  };

  const getCsvDownloadUrl = (locId) => {
    return `/api/v1/reports/rainfall.csv?location_id=${locId}&days=${windowDays}`;
  };

  const getTrendBadge = (trend) => {
    switch (trend) {
      case 'INCREASING':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-700 bg-red-50 px-2.5 py-0.5 rounded-full border border-red-200">
            <TrendingUp className="w-3.5 h-3.5 text-red-600" /> Increasing Trend
          </span>
        );
      case 'DECREASING':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
            <TrendingDown className="w-3.5 h-3.5 text-emerald-600" /> Decreasing Trend
          </span>
        );
      case 'STEADY':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-slate-700 bg-slate-100 px-2.5 py-0.5 rounded-full border border-slate-200">
            Steady Accumulation
          </span>
        );
      case 'NO_RAIN':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-slate-600 bg-slate-50 px-2.5 py-0.5 rounded-full border border-slate-200">
            No Rain Observed
          </span>
        );
      case 'INSUFFICIENT_DATA':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-slate-500 bg-slate-50 px-2.5 py-0.5 rounded-full border border-slate-200">
            Insufficient Trend Data
          </span>
        );
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Header */}
      <div className="no-print flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-700 uppercase tracking-wider bg-sky-50 px-3 py-1 rounded-full border border-sky-100 mb-2">
            <History className="w-3.5 h-3.5" />
            Historical Precipitation Analytics & Reporting
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
            Observed Rainfall Time-Series & Data Export
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Historical rainfall aggregations in Indian Standard Time (Asia/Kolkata). Forecasts are strictly excluded.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchHistory}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50 transition shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-slate-500 ${loading ? 'animate-spin text-sky-600' : ''}`} />
            Refresh
          </button>
          <Link
            to="/map"
            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-sky-600 text-xs font-semibold text-white hover:bg-sky-700 transition shadow-sm"
          >
            <Compass className="w-3.5 h-3.5" />
            Map View
          </Link>
        </div>
      </div>

      {/* Control Strip: Window & Location Selectors */}
      <div className="no-print bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4">
        {/* Window Selector */}
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-slate-600 uppercase tracking-wider">
            Analysis Window:
          </span>
          {[7, 14, 30].map((d) => (
            <button
              key={d}
              onClick={() => setWindowDays(d)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                windowDays === d
                  ? 'bg-slate-900 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {d} Days
            </button>
          ))}
        </div>

        {/* Location Filter */}
        <div className="flex items-center gap-2 overflow-x-auto">
          <span className="text-xs font-bold text-slate-600 uppercase tracking-wider">
            Station:
          </span>
          <button
            onClick={() => setSelectedLocationId('ALL')}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition whitespace-nowrap ${
              selectedLocationId === 'ALL'
                ? 'bg-sky-600 text-white shadow-sm'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            All Stations (3)
          </button>
          {locationsList.map((l) => (
            <button
              key={l.location_id}
              onClick={() => setSelectedLocationId(l.location_id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition whitespace-nowrap ${
                selectedLocationId === l.location_id
                  ? 'bg-sky-600 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {l.location_name.replace(' Weather Grid Point', '')}
            </button>
          ))}
        </div>
      </div>

      {/* Reporting & Export Quick Panel */}
      <div className="no-print bg-gradient-to-r from-slate-900 via-sky-950 to-slate-900 rounded-2xl p-6 text-white shadow-md">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
          <div className="space-y-2 max-w-2xl">
            <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-300 uppercase tracking-wider bg-sky-900/60 px-3 py-1 rounded-full border border-sky-700/50">
              <FileSpreadsheet className="w-3.5 h-3.5 text-sky-400" />
              RFC 4180 CSV & Printable PDF Reports
            </div>
            <h2 className="text-xl font-bold tracking-tight text-white">
              Standardized Rainfall Data Reporting & Export
            </h2>
            <p className="text-xs sm:text-sm text-slate-300">
              Generate audit-ready precipitation reports for Mahanadi Delta virtual weather grid points.
              Includes daily aggregates in IST, missing observation preservation (nulls, never faked zeroes),
              and spreadsheet formula injection protection.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {locationsList.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {locationsList.map((loc) => (
                  <div key={loc.location_id} className="flex items-center gap-1 bg-white/10 p-1 rounded-lg border border-white/10">
                    <button
                      onClick={() => handleOpenReport(loc.location_id)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition shadow-sm"
                      title="View & Print Official Report"
                    >
                      <FileText className="w-3.5 h-3.5" />
                      {loc.location_name.replace(' Weather Grid Point', '')} Report
                    </button>
                    <a
                      href={getCsvDownloadUrl(loc.location_id)}
                      download
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium transition"
                      title="Download Sanitized RFC 4180 CSV"
                    >
                      <Download className="w-3.5 h-3.5 text-sky-400" />
                      CSV
                    </a>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="no-print p-4 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-rose-600 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={fetchHistory}
            className="px-3 py-1 bg-rose-600 text-white rounded text-xs font-semibold hover:bg-rose-700 transition"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading Skeleton */}
      {loading && !historySummary ? (
        <div className="no-print p-12 text-center text-slate-500 bg-white rounded-xl border border-slate-200 space-y-3">
          <RefreshCw className="w-8 h-8 text-sky-600 animate-spin mx-auto" />
          <p className="text-sm font-semibold text-slate-700">Loading Historical Time-Series...</p>
          <p className="text-xs text-slate-400">Aggregating discrete precipitation depths across pilot stations...</p>
        </div>
      ) : null}

      {/* Location Historical Analytics Cards */}
      <div className="no-print space-y-6">
        {locationsList.map((loc) => {
          const hist = loc.history;
          const ante = loc.antecedent;

          return (
            <div
              key={loc.location_id}
              className="bg-white rounded-2xl border border-slate-200 p-6 sm:p-8 shadow-sm space-y-6"
            >
              {/* Card Header */}
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-100 pb-4">
                <div>
                  <div className="flex items-center gap-2 text-xs font-semibold text-sky-700 mb-1">
                    <MapPin className="w-3.5 h-3.5" />
                    {loc.district_name || 'Odisha'} &bull; Lat {loc.latitude.toFixed(4)}&deg;N, Lon {loc.longitude.toFixed(4)}&deg;E
                  </div>
                  <h2 className="text-xl font-bold text-slate-900">{loc.location_name}</h2>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {hist && getTrendBadge(hist.daily_trend)}
                  <button
                    onClick={() => handleOpenReport(loc.location_id)}
                    className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-semibold transition shadow-sm"
                  >
                    <FileText className="w-3.5 h-3.5 text-sky-600" />
                    Generate Report
                  </button>
                  <a
                    href={getCsvDownloadUrl(loc.location_id)}
                    download
                    className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 text-xs font-semibold transition shadow-sm"
                  >
                    <Download className="w-3.5 h-3.5 text-emerald-600" />
                    Export CSV
                  </a>
                </div>
              </div>

              {/* Historical Triad & Key Indicators */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div className="p-4 rounded-xl bg-slate-50 border border-slate-100 text-center">
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
                    Past 24 Hours
                  </span>
                  <span className="text-2xl font-black text-slate-900 mt-1 block">
                    {hist?.rainfall_24h_mm !== null && hist?.rainfall_24h_mm !== undefined
                      ? `${hist.rainfall_24h_mm.toFixed(1)} mm`
                      : 'N/A'}
                  </span>
                  <span className="text-[10px] text-slate-400">observed accumulation</span>
                </div>

                <div className="p-4 rounded-xl bg-slate-50 border border-slate-100 text-center">
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
                    Past 72 Hours
                  </span>
                  <span className="text-2xl font-black text-slate-900 mt-1 block">
                    {hist?.rainfall_72h_mm !== null && hist?.rainfall_72h_mm !== undefined
                      ? `${hist.rainfall_72h_mm.toFixed(1)} mm`
                      : 'N/A'}
                  </span>
                  <span className="text-[10px] text-slate-400">observed accumulation</span>
                </div>

                <div className="p-4 rounded-xl bg-slate-50 border border-slate-100 text-center">
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
                    Past 7 Days
                  </span>
                  <span className="text-2xl font-black text-slate-900 mt-1 block">
                    {hist?.rainfall_7d_mm !== null && hist?.rainfall_7d_mm !== undefined
                      ? `${hist.rainfall_7d_mm.toFixed(1)} mm`
                      : 'N/A'}
                  </span>
                  <span className="text-[10px] text-slate-400">observed accumulation</span>
                </div>

                <div className="p-4 rounded-xl bg-slate-50 border border-slate-100 text-center">
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
                    Rainy Days Streak
                  </span>
                  <span className="text-2xl font-black text-sky-700 mt-1 block">
                    {hist?.consecutive_rainy_days ?? 0}
                  </span>
                  <span className="text-[10px] text-slate-400">&ge; 2.5 mm IMD threshold</span>
                </div>
              </div>

              {/* Daily Aggregation Chart Visualizer */}
              {hist && hist.daily_totals && hist.daily_totals.length > 0 && (
                <div className="space-y-3 pt-2">
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between text-xs text-slate-600 gap-1">
                    <span className="font-bold text-slate-800 flex items-center gap-1.5">
                      <BarChart3 className="w-4 h-4 text-sky-600" />
                      Daily Rainfall Breakdown (Indian Standard Time &bull; Asia/Kolkata)
                    </span>
                    <span className="text-[11px] text-slate-500">
                      Cadence: {hist.detected_interval_minutes ? `${Math.round(hist.detected_interval_minutes)}m` : '60m'} &bull; Coverage: {hist.coverage_pct}%
                    </span>
                  </div>

                  <div className="grid grid-cols-7 sm:grid-cols-14 md:grid-cols-30 gap-1.5 p-3 rounded-xl bg-slate-50 border border-slate-200">
                    {hist.daily_totals.map((day) => {
                      const isRainy = day.is_rainy_day;
                      const rfVal = day.rainfall_mm ?? 0;
                      const heightPct = Math.min(100, Math.max(12, (rfVal / 35.0) * 100));

                      return (
                        <div key={day.date} className="flex flex-col items-center gap-1">
                          <div className="w-full h-24 bg-white rounded-md border border-slate-200/80 flex items-end justify-center p-0.5 relative group">
                            <div
                              className={`w-full rounded-sm transition-all ${
                                day.rainfall_mm === null
                                  ? 'bg-rose-100 border border-dashed border-rose-300'
                                  : isRainy
                                  ? 'bg-sky-600'
                                  : day.rainfall_mm > 0
                                  ? 'bg-sky-300'
                                  : 'bg-slate-200'
                              }`}
                              style={{ height: `${day.rainfall_mm !== null && day.rainfall_mm > 0 ? heightPct : 6}%` }}
                            />
                            {/* Hover tooltip */}
                            <div className="absolute bottom-full mb-1 hidden group-hover:block bg-slate-900 text-white text-[10px] p-1.5 rounded whitespace-nowrap z-20 shadow-md">
                              <p className="font-bold">{day.date} {day.is_partial_day ? '(In Progress)' : ''}</p>
                              <p>{day.rainfall_mm !== null ? `${day.rainfall_mm.toFixed(1)} mm` : 'Missing data'} ({day.observation_count} readings)</p>
                            </div>
                          </div>
                          <span className="text-[9px] font-mono text-slate-500 truncate w-full text-center">
                            {day.date.slice(5)}
                            {day.is_partial_day && <span className="text-amber-600 font-bold">*</span>}
                          </span>
                          <span className={`text-[10px] font-bold font-mono ${isRainy ? 'text-sky-700' : 'text-slate-600'}`}>
                            {day.rainfall_mm !== null ? `${day.rainfall_mm.toFixed(0)}m` : '-'}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  <p className="text-[10.5px] text-slate-400">
                    * Asterisk indicates in-progress current day; partial morning hours are excluded from daily trend evaluations.
                  </p>
                </div>
              )}

              {/* Antecedent Context & Caveat */}
              {ante && ante.indicator_value_mm !== null && (
                <div className="p-4 bg-purple-50/50 rounded-xl border border-purple-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
                  <div>
                    <span className="font-bold text-purple-900 block text-xs uppercase tracking-wider">
                      Antecedent Rainfall Indicator: {ante.indicator_category.replace('_', ' ')}
                    </span>
                    <p className="text-slate-600 mt-0.5">{ante.explanation}</p>
                  </div>
                  <span className="font-black text-purple-900 text-lg whitespace-nowrap">
                    {ante.indicator_value_mm.toFixed(1)} mm index
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Safety Notice */}
      <div className="no-print p-4 rounded-xl bg-amber-50/70 border border-amber-200 text-amber-900 text-xs flex items-start gap-2.5">
        <ShieldAlert className="w-4 h-4 text-amber-700 shrink-0 mt-0.5" />
        <p>
          <strong>Scientific Principle:</strong> Historical observations represent discrete sampled precipitation
          depths from Open-Meteo numerical nowcast telemetry. They do not simulate physical soil moisture or water
          table depths. Forward forecasts are strictly excluded from all historical time-series analytics.
        </p>
      </div>

      {/* Printable Report Modal & Printable Area */}
      {activeReportModal && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/70 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4">
          <div className="bg-white w-full max-w-4xl rounded-2xl shadow-2xl border border-slate-200 overflow-hidden my-4 printable-area">
            {/* Modal Actions Bar (hidden when printing) */}
            <div className="no-print bg-slate-900 text-white px-6 py-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-sky-400" />
                <h3 className="font-bold text-base sm:text-lg">
                  FloodWatch Rainfall Observation Report
                </h3>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handlePrint}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-xs font-semibold text-white transition shadow-sm"
                >
                  <Printer className="w-4 h-4" />
                  Print / Save PDF
                </button>
                {reportLocationId && (
                  <a
                    href={getCsvDownloadUrl(reportLocationId)}
                    download
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-200 transition"
                  >
                    <Download className="w-4 h-4 text-emerald-400" />
                    CSV
                  </a>
                )}
                <button
                  onClick={() => setActiveReportModal(false)}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition"
                  title="Close Modal"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Modal Body / Printable Content */}
            <div className="p-6 sm:p-8 space-y-6">
              {reportLoading && (
                <div className="text-center py-16 space-y-3">
                  <RefreshCw className="w-8 h-8 text-sky-600 animate-spin mx-auto" />
                  <p className="text-sm font-semibold text-slate-700">Compiling Authoritative Report...</p>
                  <p className="text-xs text-slate-400">Verifying daily aggregation intervals and quality flags...</p>
                </div>
              )}

              {reportError && (
                <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-800 text-sm flex items-center gap-3">
                  <AlertCircle className="w-5 h-5 text-rose-600 shrink-0" />
                  <div>
                    <p className="font-semibold">Unable to Generate Report</p>
                    <p className="text-xs mt-0.5">{reportError}</p>
                  </div>
                </div>
              )}

              {reportData && !reportLoading && (
                <div className="space-y-6 text-slate-900">
                  {/* Report Header */}
                  <div className="border-b-2 border-slate-900 pb-4">
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                      <div>
                        <div className="text-xs font-black tracking-widest text-sky-800 uppercase">
                          FLOODWATCH HYDROLOGICAL RESEARCH PLATFORM
                        </div>
                        <h1 className="text-2xl font-black text-slate-950 mt-0.5">
                          HISTORICAL RAINFALL OBSERVATION REPORT
                        </h1>
                        <p className="text-xs text-slate-600 mt-1">
                          Standardized Meteorological Time-Series Aggregation &bull; Asia/Kolkata (IST)
                        </p>
                      </div>
                      <div className="text-left sm:text-right font-mono text-xs text-slate-600">
                        <div>Report ID: <span className="font-bold text-slate-900">{reportData.report_id}</span></div>
                        <div>Generated (IST): {reportData.generated_at_ist}</div>
                        <div>Timezone: {reportData.date_range.timezone} (UTC+05:30)</div>
                      </div>
                    </div>
                  </div>

                  {/* Metadata Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 bg-slate-50 p-4 rounded-xl border border-slate-200 text-xs">
                    <div>
                      <h4 className="font-bold text-slate-700 uppercase tracking-wider mb-2">Location Information</h4>
                      <div className="space-y-1">
                        <div><strong className="text-slate-600">Station Name:</strong> {reportData.location.location_name}</div>
                        <div><strong className="text-slate-600">District:</strong> {reportData.location.district_name || 'N/A'}</div>
                        <div><strong className="text-slate-600">Coordinates:</strong> {reportData.location.latitude.toFixed(4)}°N, {reportData.location.longitude.toFixed(4)}°E</div>
                        <div className="text-[11px] text-slate-500 italic mt-1">{reportData.location.virtual_grid_disclosure}</div>
                      </div>
                    </div>
                    <div>
                      <h4 className="font-bold text-slate-700 uppercase tracking-wider mb-2">Reporting Parameters</h4>
                      <div className="space-y-1">
                        <div><strong className="text-slate-600">Reporting Window:</strong> {reportData.date_range.window_days} Calendar Days</div>
                        <div><strong className="text-slate-600">Start Date:</strong> {reportData.date_range.start_date} (00:00 IST)</div>
                        <div><strong className="text-slate-600">End Date:</strong> {reportData.date_range.end_date} (24:00 IST)</div>
                        <div><strong className="text-slate-600">Rainy Day Threshold:</strong> &ge; 2.5 mm depth (IMD standard)</div>
                      </div>
                    </div>
                  </div>

                  {/* Summary Metrics Banner */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                    <div className="p-3 bg-slate-100 rounded-lg border border-slate-200">
                      <div className="text-[10px] font-bold uppercase text-slate-500">Total Window Rainfall</div>
                      <div className="text-xl font-black text-slate-900 mt-0.5">
                        {reportData.summary.total_window_rainfall_mm !== null
                          ? `${reportData.summary.total_window_rainfall_mm.toFixed(1)} mm`
                          : 'N/A'}
                      </div>
                      <div className="text-[9px] text-slate-500">cumulative observed</div>
                    </div>
                    <div className="p-3 bg-slate-100 rounded-lg border border-slate-200">
                      <div className="text-[10px] font-bold uppercase text-slate-500">Rainy Days Count</div>
                      <div className="text-xl font-black text-sky-800 mt-0.5">
                        {reportData.summary.rainy_days_count} <span className="text-xs font-normal text-slate-600">/ {reportData.date_range.window_days}d</span>
                      </div>
                      <div className="text-[9px] text-slate-500">streak: {reportData.summary.consecutive_rainy_days} days</div>
                    </div>
                    <div className="p-3 bg-slate-100 rounded-lg border border-slate-200">
                      <div className="text-[10px] font-bold uppercase text-slate-500">Past 24h / 72h</div>
                      <div className="text-sm font-black text-slate-900 mt-1">
                        {reportData.summary.rainfall_24h_mm?.toFixed(1) ?? 'N/A'} / {reportData.summary.rainfall_72h_mm?.toFixed(1) ?? 'N/A'} mm
                      </div>
                      <div className="text-[9px] text-slate-500">short-term totals</div>
                    </div>
                    <div className="p-3 bg-slate-100 rounded-lg border border-slate-200">
                      <div className="text-[10px] font-bold uppercase text-slate-500">Data Completeness</div>
                      <div className="text-xl font-black text-emerald-800 mt-0.5">
                        {reportData.summary.window_coverage_pct.toFixed(1)}%
                      </div>
                      <div className="text-[9px] text-slate-500">{reportData.summary.missing_intervals_count} missing intervals</div>
                    </div>
                  </div>

                  {/* Daily Observations Table */}
                  <div>
                    <h4 className="font-bold text-slate-900 text-sm mb-2 flex items-center justify-between">
                      <span>Daily Precipitation Time-Series (00:00 - 24:00 IST)</span>
                      <span className="text-xs font-normal text-slate-500">
                        Missing values preserved as 'Missing' (never zero)
                      </span>
                    </h4>
                    <div className="border border-slate-200 rounded-lg overflow-hidden">
                      <table className="w-full text-xs text-left border-collapse">
                        <thead>
                          <tr className="bg-slate-100 border-b border-slate-200 text-slate-700 font-bold uppercase text-[10px]">
                            <th className="py-2.5 px-3">Date (IST)</th>
                            <th className="py-2.5 px-3 text-right">Rainfall (mm)</th>
                            <th className="py-2.5 px-3 text-center">Readings</th>
                            <th className="py-2.5 px-3 text-right">Coverage %</th>
                            <th className="py-2.5 px-3 text-center">Rainy Day (&ge;2.5mm)</th>
                            <th className="py-2.5 px-3">Status / Notes</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-200">
                          {reportData.daily_records.map((rec) => (
                            <tr key={rec.date} className="hover:bg-slate-50">
                              <td className="py-2 px-3 font-mono font-medium">{rec.date}</td>
                              <td className="py-2 px-3 text-right font-mono font-bold">
                                {rec.rainfall_mm !== null ? `${rec.rainfall_mm.toFixed(2)} mm` : <span className="text-rose-600 italic">Missing</span>}
                              </td>
                              <td className="py-2 px-3 text-center font-mono">{rec.observation_count}</td>
                              <td className="py-2 px-3 text-right font-mono">{rec.coverage_pct.toFixed(1)}%</td>
                              <td className="py-2 px-3 text-center">
                                {rec.is_rainy_day ? (
                                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-sky-100 text-sky-800">
                                    YES
                                  </span>
                                ) : (
                                  <span className="text-slate-400">NO</span>
                                )}
                              </td>
                              <td className="py-2 px-3 text-slate-600 italic">
                                {rec.notes || (rec.is_partial_day ? 'Partial Day' : 'Complete')}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Context Sections: Antecedent & Risk Outlook */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                    {reportData.antecedent_indicator && (
                      <div className="p-3.5 bg-purple-50/60 rounded-lg border border-purple-200">
                        <div className="font-bold text-purple-900 uppercase text-[10px] tracking-wider mb-1">
                          Antecedent Rainfall Indicator Context
                        </div>
                        <div className="text-slate-800">
                          <strong>Category:</strong> {reportData.antecedent_indicator.category.replace('_', ' ')} &bull; <strong>Index:</strong> {reportData.antecedent_indicator.value_mm?.toFixed(1) ?? 'N/A'} mm
                        </div>
                        <p className="text-[11px] text-slate-600 mt-1">{reportData.antecedent_indicator.explanation}</p>
                        <p className="text-[10px] text-purple-700 italic mt-1.5">{reportData.antecedent_indicator.caveat}</p>
                      </div>
                    )}

                    {reportData.risk_summary && (
                      <div className="p-3.5 bg-sky-50/60 rounded-lg border border-sky-200">
                        <div className="font-bold text-sky-900 uppercase text-[10px] tracking-wider mb-1">
                          Current Surface Runoff Risk Context
                        </div>
                        <div className="text-slate-800">
                          <strong>Risk Level:</strong> {reportData.risk_summary.risk_category.replace('_', ' ')} ({reportData.risk_summary.confidence} confidence)
                        </div>
                        <div className="text-slate-800 mt-0.5">
                          <strong>Forecast 24h:</strong> {reportData.risk_summary.forecast_24h_mm?.toFixed(1) ?? 'N/A'} mm
                        </div>
                        <p className="text-[10px] text-sky-700 italic mt-1.5">{reportData.risk_summary.limitations_disclaimer}</p>
                      </div>
                    )}
                  </div>

                  {/* Quality Warnings */}
                  {reportData.data_quality_warnings && reportData.data_quality_warnings.length > 0 && (
                    <div className="p-3 bg-amber-50 rounded-lg border border-amber-200 text-amber-900 text-xs space-y-1">
                      <div className="font-bold flex items-center gap-1.5 text-amber-800">
                        <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
                        Data Quality & Coverage Notices:
                      </div>
                      <ul className="list-disc list-inside text-[11px] space-y-0.5 pl-2">
                        {reportData.data_quality_warnings.map((w, idx) => (
                          <li key={idx}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Statutory & Scientific Disclaimers */}
                  <div className="border-t border-slate-200 pt-3 text-[10px] text-slate-500 space-y-1">
                    <p className="font-bold text-slate-700">
                      RESEARCH PROTOTYPE DISCLAIMER:
                    </p>
                    <p>{reportData.methodology_and_disclosures.statutory_disclaimer}</p>
                    <p className="italic">{reportData.methodology_and_disclosures.virtual_grid_disclosure}</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

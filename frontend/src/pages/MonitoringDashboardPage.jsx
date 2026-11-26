import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Calendar,
  CheckCircle2,
  Clock,
  CloudRain,
  Database,
  Info,
  Layers,
  Lock,
  MapPin,
  RefreshCw,
  ShieldAlert,
  Timer,
  XCircle,
} from 'lucide-react';

export default function MonitoringDashboardPage() {
  const [summary, setSummary] = useState(null);
  const [scheduler, setScheduler] = useState(null);
  const [ingestionHealth, setIngestionHealth] = useState(null);
  const [ingestionAuthStatus, setIngestionAuthStatus] = useState('checking'); // 'checking', 'authorized', 'restricted', 'unavailable'
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchTelemetry = async () => {
    setLoading(true);
    setError(null);
    try {
      // 1. Fetch public monitoring summary
      const sumRes = await fetch('/api/v1/monitoring/summary');
      if (!sumRes.ok) {
        const errJson = await sumRes.json().catch(() => ({}));
        throw new Error(errJson?.detail?.message || `Summary HTTP error ${sumRes.status}`);
      }
      const sumData = await sumRes.json();
      setSummary(sumData);
      if (sumData.scheduler) {
        setScheduler(sumData.scheduler);
      }

      // 2. Fetch scheduler status directly
      try {
        const schedRes = await fetch('/api/v1/monitoring/scheduler');
        if (schedRes.ok) {
          const sJson = await schedRes.json();
          setScheduler(sJson);
        }
      } catch {
        // keep summary.scheduler if present
      }

      // 2. Fetch administrative ingestion health (requires X-Ingestion-Token header)
      try {
        const ingRes = await fetch('/api/v1/monitoring/ingestion-health');
        if (ingRes.ok) {
          const ingData = await ingRes.json();
          setIngestionHealth(ingData);
          setIngestionAuthStatus('authorized');
        } else if (ingRes.status === 401 || ingRes.status === 403) {
          setIngestionHealth(null);
          setIngestionAuthStatus('restricted');
        } else {
          setIngestionHealth(null);
          setIngestionAuthStatus('unavailable');
        }
      } catch {
        setIngestionHealth(null);
        setIngestionAuthStatus('unavailable');
      }

      setLastRefreshed(new Date());
    } catch (err) {
      setError(err.message || 'Failed to load monitoring telemetry.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTelemetry();
  }, []);

  const getFreshnessBadge = (state) => {
    switch (state) {
      case 'fresh':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
            FRESH (&le; 2h)
          </span>
        );
      case 'delayed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200">
            <Clock className="w-3.5 h-3.5 text-amber-600" />
            DELAYED (2h - 6h)
          </span>
        );
      case 'stale':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-orange-50 text-orange-700 border border-orange-200">
            <AlertTriangle className="w-3.5 h-3.5 text-orange-600" />
            STALE (&gt; 6h)
          </span>
        );
      case 'unavailable':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200">
            <XCircle className="w-3.5 h-3.5 text-rose-600" />
            UNAVAILABLE
          </span>
        );
    }
  };

  const getQualityBadge = (status) => {
    switch (status) {
      case 'pass':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
            Passed All Checks
          </span>
        );
      case 'warning':
        return (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
            Quality Warnings
          </span>
        );
      case 'fail':
      default:
        return (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-rose-700 bg-rose-50 px-2 py-0.5 rounded border border-rose-200">
            Quality Alerts
          </span>
        );
    }
  };

  const formatTimestamp = (ts) => {
    if (!ts) return 'Never / Missing';
    try {
      const d = new Date(ts);
      return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
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
            Phase 2.3.4 Operational Telemetry
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
            Data Quality, Freshness & Ingestion Monitoring
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Real-time telemetry and validation auditing for the three pilot monitoring locations in Odisha.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {summary && (
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider border ${
                summary.system_health === 'healthy'
                  ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                  : summary.system_health === 'degraded'
                  ? 'bg-amber-50 text-amber-800 border-amber-200'
                  : 'bg-rose-50 text-rose-800 border-rose-200'
              }`}
            >
              System: {summary.system_health}
            </span>
          )}
          <button
            onClick={fetchTelemetry}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white border border-slate-200 text-sm font-medium text-slate-700 hover:bg-slate-50 transition shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 text-slate-600 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 flex items-start gap-3 text-rose-800">
          <AlertCircle className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-semibold">Telemetry Retrieval Error</p>
            <p className="text-rose-700 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {loading && !summary && (
        <div className="p-12 text-center text-slate-500 space-y-3">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto text-sky-600" />
          <p className="text-sm font-medium">Auditing pilot locations and operational telemetry...</p>
        </div>
      )}

      {summary && (
        <>
          {/* Top Metrics Row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            {/* Overall Freshness */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Overall Data Freshness
              </span>
              <div className="pt-1">{getFreshnessBadge(summary.freshness_summary.overall_state)}</div>
              <p className="text-xs text-slate-600 pt-1">
                {summary.freshness_summary.fresh_count} Fresh &bull; {summary.freshness_summary.delayed_count} Delayed &bull;{' '}
                {summary.freshness_summary.unavailable_count} Unavailable
              </p>
            </div>

            {/* Latest Ingestion Run */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Latest Ingestion Run
              </span>
              <div className="flex items-center gap-2 pt-1">
                <span
                  className={`text-sm font-bold uppercase ${
                    summary.ingestion_summary.latest_attempt_status === 'completed'
                      ? 'text-emerald-700'
                      : summary.ingestion_summary.latest_attempt_status === 'failed'
                      ? 'text-rose-700'
                      : 'text-slate-700'
                  }`}
                >
                  {summary.ingestion_summary.latest_attempt_status || 'None'}
                </span>
                {summary.ingestion_summary.average_duration_seconds !== null && (
                  <span className="text-xs text-slate-500 font-mono">
                    ({summary.ingestion_summary.average_duration_seconds}s avg)
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500">
                Started: {formatTimestamp(summary.ingestion_summary.latest_attempt_at)}
              </p>
            </div>

            {/* Latest Successful Sync */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Last Successful Sync
              </span>
              <p className="text-sm font-bold text-slate-900 pt-1">
                {summary.ingestion_summary.latest_success_at
                  ? formatTimestamp(summary.ingestion_summary.latest_success_at)
                  : 'No successful runs'}
              </p>
              <p className="text-xs text-slate-500">
                {summary.ingestion_summary.successful_runs_count} of {summary.ingestion_summary.recent_runs_count} runs successful
              </p>
            </div>

            {/* Stale / Pipeline Health */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Stale & Abandoned Runs
              </span>
              <div className="flex items-center gap-2 pt-1">
                <span
                  className={`text-lg font-bold ${
                    summary.ingestion_summary.stale_started_runs_count === 0
                      ? 'text-emerald-700'
                      : 'text-amber-700'
                  }`}
                >
                  {summary.ingestion_summary.stale_started_runs_count}
                </span>
                <span className="text-xs text-slate-500 font-medium">runs &gt; 60m in started state</span>
              </div>
              <p className="text-xs text-slate-500">
                {summary.ingestion_summary.stale_started_runs_count === 0
                  ? 'Zero orphaned runs detected.'
                  : 'Reconciliation inspection recommended.'}
              </p>
            </div>
          </div>

          {/* Automated Ingestion Scheduler Status (Phase 2.6) */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-100 pb-4">
              <div className="flex items-center gap-3">
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                  scheduler?.enabled ? 'bg-sky-50 text-sky-700' : 'bg-slate-100 text-slate-500'
                }`}>
                  <Timer className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-bold text-slate-900">
                      Automated Weather Ingestion Scheduler
                    </h2>
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
                      Phase 2.6
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Periodic background collection across Paradeep, Cuttack, and Kendrapara pilot weather points.
                  </p>
                </div>
              </div>

              {/* Status Badge */}
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider border ${
                  scheduler?.enabled
                    ? scheduler?.is_cycle_in_progress
                      ? 'bg-amber-50 text-amber-700 border-amber-200 animate-pulse'
                      : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'bg-slate-100 text-slate-600 border-slate-200'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${
                    scheduler?.enabled
                      ? scheduler?.is_cycle_in_progress ? 'bg-amber-500' : 'bg-emerald-500'
                      : 'bg-slate-400'
                  }`} />
                  {scheduler?.enabled
                    ? scheduler?.is_cycle_in_progress
                      ? 'Cycle in Progress'
                      : `Enabled (${scheduler?.state || 'idle'})`
                    : 'Disabled (Manual Mode)'}
                </span>
              </div>
            </div>

            {/* Metrics Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 pt-1">
              <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-xs font-medium text-slate-500 block">Configured Schedule</span>
                <span className="text-sm font-bold text-slate-800 mt-1 block">
                  Every {scheduler?.interval_minutes || 60} Minutes
                </span>
                <span className="text-[11px] text-slate-400 block mt-0.5">
                  ENV: INGESTION_SCHEDULE_INTERVAL_MINUTES
                </span>
              </div>

              <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-xs font-medium text-slate-500 block">Last Cycle Outcome</span>
                <span className={`text-sm font-bold mt-1 block ${
                  scheduler?.last_cycle_outcome === 'SUCCESS'
                    ? 'text-emerald-700'
                    : scheduler?.last_cycle_outcome === 'PARTIAL_SUCCESS'
                    ? 'text-amber-700'
                    : scheduler?.last_cycle_outcome === 'FAILED'
                    ? 'text-rose-700'
                    : 'text-slate-600'
                }`}>
                  {scheduler?.last_cycle_outcome || 'No cycles executed'}
                </span>
                {scheduler?.last_cycle_duration_seconds !== null && scheduler?.last_cycle_duration_seconds !== undefined && (
                  <span className="text-[11px] text-slate-400 block mt-0.5">
                    Duration: {scheduler.last_cycle_duration_seconds}s
                  </span>
                )}
              </div>

              <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-xs font-medium text-slate-500 block">Last Cycle Completion</span>
                <span className="text-sm font-bold text-slate-800 mt-1 block">
                  {scheduler?.last_cycle_completed ? formatTimestamp(scheduler.last_cycle_completed) : 'Pending first cycle'}
                </span>
                <span className="text-[11px] text-slate-400 block mt-0.5">
                  Total executed: {scheduler?.total_cycles_executed || 0}
                </span>
              </div>

              <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-xs font-medium text-slate-500 block">Next Expected Cycle</span>
                <span className="text-sm font-bold text-slate-800 mt-1 block">
                  {scheduler?.enabled && scheduler?.next_expected_cycle
                    ? formatTimestamp(scheduler.next_expected_cycle)
                    : scheduler?.enabled
                    ? 'Scheduled on startup'
                    : 'Paused (Disabled)'}
                </span>
                <span className="text-[11px] text-slate-400 block mt-0.5">
                  {scheduler?.enabled ? 'Continuous timer' : 'Enable in .env to activate'}
                </span>
              </div>
            </div>

            {/* Explanatory Disclaimers */}
            <div className="p-3 rounded-lg bg-amber-50/70 border border-amber-200/80 text-xs text-amber-900 space-y-1">
              <p className="font-semibold flex items-center gap-1.5">
                <Info className="w-3.5 h-3.5 text-amber-700 shrink-0" />
                Operational Distinction & Single-Process Scope
              </p>
              <p className="text-amber-800 leading-relaxed">
                <strong>Scheduler execution status is distinct from physical weather-data freshness:</strong> The scheduler status confirms whether the background ingestion worker is running, whereas station freshness reflects the timestamp of physical atmospheric measurements retrieved from Open-Meteo.
              </p>
              <p className="text-amber-800/90 text-[11px]">
                {scheduler?.single_process_limitation_notice || 'In-process scheduler operates within a single application process. Multi-worker deployments require an external cron or distributed lock.'}
              </p>
            </div>
          </div>

          {/* Location-wise Quality & Freshness Cards */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold text-slate-900">Pilot Monitoring Locations Health</h2>
                <p className="text-xs text-slate-500">
                  Physical measurement integrity and freshness audit for Mahanadi delta pilot grid points.
                </p>
              </div>
              <span className="text-xs font-mono text-slate-500">
                {summary.location_healths.length} pilot stations tracked
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {summary.location_healths.map((loc) => (
                <div
                  key={loc.location_id}
                  className="bg-white rounded-xl border border-slate-200 p-6 space-y-4 shadow-sm hover:border-slate-300 transition"
                >
                  {/* Header */}
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h3 className="font-bold text-slate-900 text-base">{loc.location_name}</h3>
                      <span className="text-xs text-slate-500">District: {loc.district_name || 'Odisha'}</span>
                    </div>
                    {getFreshnessBadge(loc.observation_freshness || loc.freshness)}
                  </div>

                  {/* Dual Freshness & Coordinates Strip */}
                  <div className="text-xs bg-slate-50 p-2.5 rounded-lg border border-slate-100 space-y-1.5 font-mono">
                    <div className="flex items-center justify-between text-slate-600">
                      <span className="font-sans font-medium text-slate-500">Coordinates:</span>
                      <span>
                        {loc.latitude.toFixed(4)}N, {loc.longitude.toFixed(4)}E
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="font-sans font-medium text-slate-500">Physical Obs Age:</span>
                      <span className="font-semibold text-slate-800">
                        {loc.observation_age_minutes !== null ? `${loc.observation_age_minutes}m` : 'N/A'}
                        {loc.observation_freshness && (
                          <span className="ml-1 text-[10px] uppercase text-slate-500 font-sans">
                            ({loc.observation_freshness})
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="font-sans font-medium text-slate-500">Ingestion Fetch Age:</span>
                      <span className="font-semibold text-slate-800">
                        {loc.ingestion_age_minutes !== null ? `${loc.ingestion_age_minutes}m` : 'N/A'}
                        {loc.ingestion_freshness && (
                          <span className="ml-1 text-[10px] uppercase text-slate-500 font-sans">
                            ({loc.ingestion_freshness})
                          </span>
                        )}
                      </span>
                    </div>
                  </div>

                  {/* Discrepancy warning if recently fetched but old observation */}
                  {loc.ingestion_freshness === 'fresh' && loc.observation_freshness && loc.observation_freshness !== 'fresh' && (
                    <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
                      <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
                      <div>
                        <p className="font-semibold">Stale Observation with Recent Ingestion</p>
                        <p className="text-[11px] text-amber-700 mt-0.5">
                          Weather pipeline fetched successfully, but provider data is older than 2 hours. Physical observation is not fresh.
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Current Rainfall & Active Horizon */}
                  <div className="grid grid-cols-2 gap-3 pt-1 border-t border-slate-100">
                    <div>
                      <span className="text-xs text-slate-500">Current Rainfall</span>
                      <p className="text-lg font-extrabold text-slate-900">
                        {loc.current_rainfall_mm !== null ? `${loc.current_rainfall_mm.toFixed(2)} mm` : 'Unavailable'}
                      </p>
                      <span className="text-[11px] text-slate-400">15-min nowcast</span>
                    </div>
                    <div>
                      <span className="text-xs text-slate-500">Next 24h Forecast</span>
                      <p className="text-lg font-extrabold text-slate-900">
                        {loc.next_24h_precipitation_mm !== null
                          ? `${loc.next_24h_precipitation_mm.toFixed(2)} mm`
                          : 'Unavailable'}
                      </p>
                      <span className="text-[11px] text-slate-400">
                        {loc.active_forecast_hours} hourly steps
                      </span>
                    </div>
                  </div>

                  {/* Completeness Bar */}
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-xs text-slate-600">
                      <span>Horizon Completeness</span>
                      <span className="font-semibold">{loc.completeness_percentage}%</span>
                    </div>
                    <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          loc.completeness_percentage >= 90
                            ? 'bg-emerald-500'
                            : loc.completeness_percentage >= 50
                            ? 'bg-amber-500'
                            : 'bg-rose-500'
                        }`}
                        style={{ width: `${Math.min(100, loc.completeness_percentage)}%` }}
                      />
                    </div>
                  </div>

                  {/* Quality Issues / Badges */}
                  <div className="pt-2 border-t border-slate-100 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-slate-700">Quality Assessment</span>
                      {getQualityBadge(loc.quality_status)}
                    </div>

                    {loc.quality_issues && loc.quality_issues.length > 0 ? (
                      <div className="space-y-1 pt-1">
                        {loc.quality_issues.slice(0, 3).map((issue, idx) => (
                          <div
                            key={idx}
                            className={`p-2 rounded text-xs flex items-start gap-1.5 ${
                              issue.severity === 'error' || issue.severity === 'critical'
                                ? 'bg-rose-50 text-rose-700 border border-rose-100'
                                : 'bg-amber-50 text-amber-700 border border-amber-100'
                            }`}
                          >
                            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                            <span>{issue.message}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-emerald-700 flex items-center gap-1">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                        No data anomalies or physics violations detected.
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Ingestion Pipeline Telemetry Table (Authorized Only) */}
          {ingestionHealth && ingestionHealth.recent_runs && (
            <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-base font-bold text-slate-900">Recent Ingestion Pipeline Audit Log</h2>
                  <p className="text-xs text-slate-500">
                    Audit records from Supabase operational table <code className="bg-slate-100 px-1 py-0.5 rounded text-[11px]">ingestion_runs</code>.
                  </p>
                </div>
                <span className="text-xs text-slate-500">
                  Provider: {ingestionHealth.provider}
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 text-slate-600 uppercase tracking-wider font-semibold border-b border-slate-200">
                    <tr>
                      <th className="py-2.5 px-3">Run ID</th>
                      <th className="py-2.5 px-3">Started</th>
                      <th className="py-2.5 px-3">Status</th>
                      <th className="py-2.5 px-3 text-right">Fetched</th>
                      <th className="py-2.5 px-3 text-right">Inserted</th>
                      <th className="py-2.5 px-3 text-right">Updated</th>
                      <th className="py-2.5 px-3">Idempotency Key</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-slate-700 font-mono">
                    {ingestionHealth.recent_runs.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="py-4 text-center text-slate-400">
                          No recent ingestion runs recorded.
                        </td>
                      </tr>
                    ) : (
                      ingestionHealth.recent_runs.slice(0, 10).map((r) => (
                        <tr key={r.id} className="hover:bg-slate-50">
                          <td className="py-2 px-3 font-semibold text-slate-900">{r.id.slice(0, 8)}...</td>
                          <td className="py-2 px-3">{formatTimestamp(r.started_at)}</td>
                          <td className="py-2 px-3">
                            <span
                              className={`px-2 py-0.5 rounded text-[11px] font-bold uppercase ${
                                r.status === 'completed'
                                  ? 'bg-emerald-50 text-emerald-700'
                                  : r.status === 'failed'
                                  ? 'bg-rose-50 text-rose-700'
                                  : 'bg-amber-50 text-amber-700'
                              }`}
                            >
                              {r.status}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-right">{r.records_fetched}</td>
                          <td className="py-2 px-3 text-right">{r.records_inserted}</td>
                          <td className="py-2 px-3 text-right">{r.records_updated}</td>
                          <td className="py-2 px-3 text-slate-500 text-[11px] truncate max-w-xs">
                            {r.idempotency_key || 'N/A'}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Operational Ingestion Notice when Unauthorized */}
          {ingestionAuthStatus === 'restricted' && (
            <div className="bg-slate-50 rounded-xl border border-slate-200 p-6 space-y-2 text-slate-700">
              <div className="flex items-center gap-2 font-bold text-slate-900 text-sm">
                <Lock className="w-4 h-4 text-slate-500" />
                Operational Ingestion Audit Telemetry Restricted
              </div>
              <p className="text-xs text-slate-600">
                Detailed ingestion run logs, idempotency traces, and internal error metadata are protected and require administrative authorization via <code className="bg-slate-200 px-1 py-0.5 rounded text-[11px]">X-Ingestion-Token</code> header.
              </p>
              <p className="text-xs text-slate-500">
                Aggregated pipeline health, location quality metrics, and observation freshness remain available in the public telemetry panels above.
              </p>
            </div>
          )}

          {/* Missing Data & Research Prototype Protocol */}
          <section className="bg-amber-50/70 rounded-xl border border-amber-200 p-5 space-y-2.5">
            <div className="flex items-center gap-2 text-amber-900 font-bold text-sm">
              <ShieldAlert className="w-5 h-5 text-amber-700" />
              Data Quality Principles & Prototype Safety Rules
            </div>
            <ul className="text-xs sm:text-sm text-amber-900 space-y-1.5 list-disc list-inside">
              <li>
                <strong>No Fake Zeroes:</strong> Missing weather values or unavailable observations are <strong>never</strong> converted into 0.0 mm rainfall.
              </li>
              <li>
                <strong>No False Low-Risk:</strong> Missing or delayed observations are never classified as low flood-risk; they are explicitly flagged as <code>UNAVAILABLE</code>.
              </li>
              <li>
                <strong>Separate Freshness:</strong> Atmospheric data freshness is evaluated on retrieval timestamps (<code className="bg-amber-100 px-1 rounded text-xs">fetched_at</code>) and kept independent from flood hazard indices.
              </li>
              <li>
                <strong>Research Prototype:</strong> FloodWatch is an experimental research system for the Mahanadi delta; it is not an official disaster management agency or public life-safety evacuation system.
              </li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

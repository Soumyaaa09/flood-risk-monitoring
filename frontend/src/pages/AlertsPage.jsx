import React, { useEffect, useState, useMemo, useRef } from 'react';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Bell,
  CheckCircle2,
  Clock,
  CloudRain,
  ExternalLink,
  Filter,
  Info,
  Layers,
  Lock,
  MapPin,
  Radio,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react';
import {
  DEFAULT_RESOLVED_REASON,
  RESOLVED_HISTORY_SUBTITLE,
  formatRainfallMetric,
  resolveDisplayRiskCategory,
} from '../utils/alertDisplay';

export default function AlertsPage() {
  const [alertsData, setAlertsData] = useState(null);
  const [summaryData, setSummaryData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState('OPEN'); // 'OPEN' (NEW/ACTIVE), 'ACKNOWLEDGED', 'RESOLVED', 'ALL'
  const [severityFilter, setSeverityFilter] = useState('ALL');
  const [locationFilter, setLocationFilter] = useState('ALL');

  // Acknowledgement Dialog State
  const [ackModalAlert, setAckModalAlert] = useState(null);
  const [ackOperator, setAckOperator] = useState('Duty Hydrologist');
  const [ackToken, setAckToken] = useState('');
  const [ackSubmitting, setAckSubmitting] = useState(false);
  const [ackError, setAckError] = useState(null);

  // Resolution Dialog State (Phase 2.7.2)
  const [resolveModalAlert, setResolveModalAlert] = useState(null);
  const [resolveReason, setResolveReason] = useState('Ground inspection confirmed rainfall runoff subsided to normal level.');
  const [resolveOperator, setResolveOperator] = useState('Duty Hydrologist');
  const [resolveToken, setResolveToken] = useState('');
  const [resolveSubmitting, setResolveSubmitting] = useState(false);
  const [resolveError, setResolveError] = useState(null);

  const isFetchingRef = useRef(false);

  const fetchAlertsTelemetry = async (isBackground = false) => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    if (!isBackground) {
      setLoading(true);
      setError(null);
    }
    try {
      const [listRes, sumRes] = await Promise.allSettled([
        fetch('/api/v1/alerts?limit=100'),
        fetch('/api/v1/alerts/summary'),
      ]);

      if (listRes.status === 'fulfilled' && listRes.value.ok) {
        const lJson = await listRes.value.json();
        setAlertsData(lJson);
      } else if (!isBackground) {
        throw new Error('Failed to load alert records from API.');
      }

      if (sumRes.status === 'fulfilled' && sumRes.value.ok) {
        const sJson = await sumRes.value.json();
        setSummaryData(sJson);
      }
    } catch (err) {
      if (!isBackground) {
        setError(err.message || 'Error loading alert data.');
      }
    } finally {
      if (!isBackground) {
        setLoading(false);
      }
      isFetchingRef.current = false;
    }
  };

  useEffect(() => {
    fetchAlertsTelemetry(false);

    // 60-second periodic telemetry polling (Phase 2.7.2)
    const intervalId = setInterval(() => {
      fetchAlertsTelemetry(true);
    }, 60000);

    // Browser focus revalidation
    const handleFocus = () => {
      fetchAlertsTelemetry(true);
    };
    window.addEventListener('focus', handleFocus);

    return () => {
      clearInterval(intervalId);
      window.removeEventListener('focus', handleFocus);
    };
  }, []);

  // Filtered alerts memo
  const displayedAlerts = useMemo(() => {
    if (!alertsData?.alerts) return [];
    return alertsData.alerts.filter((a) => {
      // Status filter
      if (statusFilter === 'OPEN' && !(a.status === 'NEW' || a.status === 'ACTIVE')) return false;
      if (statusFilter === 'ACKNOWLEDGED' && a.status !== 'ACKNOWLEDGED') return false;
      if (statusFilter === 'RESOLVED' && a.status !== 'RESOLVED') return false;

      // Severity filter
      if (severityFilter !== 'ALL' && a.severity !== severityFilter) return false;

      // Location filter
      if (locationFilter !== 'ALL' && a.location_name && !a.location_name.toLowerCase().includes(locationFilter.toLowerCase())) {
        return false;
      }

      return true;
    });
  }, [alertsData, statusFilter, severityFilter, locationFilter]);

  // Acknowledge handler
  const handleAcknowledgeSubmit = async (e) => {
    e.preventDefault();
    if (!ackModalAlert) return;

    setAckSubmitting(true);
    setAckError(null);

    try {
      const headers = {
        'Content-Type': 'application/json',
      };
      if (ackToken.trim()) {
        headers['X-Ingestion-Token'] = ackToken.trim();
      }

      const res = await fetch(`/api/v1/alerts/${ackModalAlert.id}/acknowledge`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          acknowledged_by: ackOperator.trim() || 'duty_officer',
        }),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson?.detail?.message || `HTTP ${res.status}: Acknowledgement rejected`);
      }

      // Success
      setAckModalAlert(null);
      setAckToken('');
      await fetchAlertsTelemetry(false);
    } catch (err) {
      setAckError(err.message || 'Failed to acknowledge alert.');
    } finally {
      setAckSubmitting(false);
    }
  };

  // Resolve handler (Phase 2.7.2)
  const handleResolveSubmit = async (e) => {
    e.preventDefault();
    if (!resolveModalAlert) return;

    setResolveSubmitting(true);
    setResolveError(null);

    try {
      const headers = {
        'Content-Type': 'application/json',
      };
      if (resolveToken.trim()) {
        headers['X-Ingestion-Token'] = resolveToken.trim();
      }

      const res = await fetch(`/api/v1/alerts/${resolveModalAlert.id}/resolve`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          resolved_reason: resolveReason.trim(),
          resolved_by: resolveOperator.trim() || 'duty_officer',
        }),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson?.detail?.message || `HTTP ${res.status}: Resolution rejected`);
      }

      // Success
      setResolveModalAlert(null);
      setResolveToken('');
      await fetchAlertsTelemetry(false);
    } catch (err) {
      setResolveError(err.message || 'Failed to resolve alert.');
    } finally {
      setResolveSubmitting(false);
    }
  };

  const formatTimestamp = (ts) => {
    if (!ts) return 'N/A';
    try {
      const dt = new Date(ts);
      return dt.toLocaleString('en-IN', {
        timeZone: 'Asia/Kolkata',
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
      }) + ' IST';
    } catch {
      return String(ts);
    }
  };

  const getSeverityBadge = (sev) => {
    switch (sev) {
      case 'CRITICAL':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-rose-100 text-rose-800 border border-rose-300">
            <ShieldAlert className="w-3.5 h-3.5 text-rose-600" />
            CRITICAL
          </span>
        );
      case 'HIGH':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-orange-100 text-orange-800 border border-orange-300">
            <AlertTriangle className="w-3.5 h-3.5 text-orange-600" />
            HIGH
          </span>
        );
      case 'WARNING':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800 border border-amber-300">
            <AlertCircle className="w-3.5 h-3.5 text-amber-600" />
            WARNING
          </span>
        );
      case 'DATA_WARNING':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-violet-100 text-violet-800 border border-violet-300">
            <Radio className="w-3.5 h-3.5 text-violet-600" />
            DATA WARNING
          </span>
        );
      case 'INFO':
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold bg-sky-100 text-sky-800 border border-sky-300">
            <Info className="w-3.5 h-3.5 text-sky-600" />
            INFO
          </span>
        );
    }
  };

  const getStatusBadge = (st) => {
    switch (st) {
      case 'NEW':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 animate-pulse">
            NEW
          </span>
        );
      case 'ACTIVE':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200">
            ACTIVE
          </span>
        );
      case 'ACKNOWLEDGED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-purple-50 text-purple-700 border border-purple-200">
            ACKNOWLEDGED
          </span>
        );
      case 'RESOLVED':
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-600 border border-slate-200">
            RESOLVED
          </span>
        );
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Statutory Research Disclaimer Banner */}
      <div className="p-4 rounded-xl bg-amber-50 border-l-4 border-amber-500 text-amber-900 text-sm shadow-sm flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="font-bold text-amber-950">
            Research &amp; Decision-Support Notification Engine
          </p>
          <p className="text-amber-900 leading-relaxed text-xs sm:text-sm">
            FloodWatch provides experimental rainfall-based decision support. It is not an official flood warning service. Follow IMD and OSDMA for statutory warnings. These alerts reflect surface water runoff potential from evaluated atmospheric precipitation and numerical models; they do not represent confirmed riverine overflow or mandatory evacuation directives.
          </p>
        </div>
      </div>

      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-700 uppercase tracking-wider bg-sky-50 px-2.5 py-1 rounded-full border border-sky-100 mb-2">
            <Bell className="w-3.5 h-3.5" />
            Phase 2.7 In-App Alert Dispatcher
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
            Risk Alerts &amp; In-App Notifications
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Real-time event detection, risk escalations, and data freshness warnings across Paradeep, Cuttack, and Kendrapara.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchAlertsTelemetry}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white border border-slate-200 text-sm font-medium text-slate-700 hover:bg-slate-50 transition shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 text-slate-600 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Operational Metrics Strip */}
      {summaryData && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
              Active Alerts
            </span>
            <div className="flex items-baseline gap-2 mt-1">
              <span className={`text-2xl font-black ${summaryData.total_active_alerts > 0 ? 'text-rose-600' : 'text-slate-900'}`}>
                {summaryData.total_active_alerts}
              </span>
              <span className="text-xs text-slate-500">requiring observation</span>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
              Acknowledged Alerts
            </span>
            <div className="flex items-baseline gap-2 mt-1">
              <span className="text-2xl font-black text-purple-700">
                {summaryData.total_acknowledged_alerts}
              </span>
              <span className="text-xs text-slate-500">logged by operators</span>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
              Highest Open Severity
            </span>
            <div className="mt-1">
              {summaryData.highest_active_severity ? (
                getSeverityBadge(summaryData.highest_active_severity)
              ) : (
                <span className="text-sm font-semibold text-slate-600">None Active</span>
              )}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider block">
              Resolved History
            </span>
            <div className="flex items-baseline gap-2 mt-1">
              <span className="text-2xl font-black text-slate-700">
                {alertsData?.resolved_count || 0}
              </span>
              <span className="text-xs text-slate-500">{RESOLVED_HISTORY_SUBTITLE}</span>
            </div>
          </div>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 flex items-start gap-3 text-rose-800">
          <AlertCircle className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-semibold">Alert Telemetry Error</p>
            <p className="text-rose-700 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Filter Toolbar */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        {/* Status Tabs */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 md:pb-0">
          {[
            { id: 'OPEN', label: 'Open Alerts', count: summaryData?.total_active_alerts },
            { id: 'ACKNOWLEDGED', label: 'Acknowledged', count: summaryData?.total_acknowledged_alerts },
            { id: 'RESOLVED', label: 'Resolved History', count: alertsData?.resolved_count },
            { id: 'ALL', label: 'All Alerts', count: alertsData?.total },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setStatusFilter(tab.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-1.5 whitespace-nowrap ${
                statusFilter === tab.id
                  ? 'bg-sky-600 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              <span>{tab.label}</span>
              {tab.count !== undefined && (
                <span
                  className={`px-1.5 py-0.2 rounded-full text-[10px] ${
                    statusFilter === tab.id ? 'bg-sky-700 text-white' : 'bg-slate-200 text-slate-700'
                  }`}
                >
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Severity & Location dropdowns */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-xs">
            <Filter className="w-3.5 h-3.5 text-slate-500" />
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="text-xs bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-500"
            >
              <option value="ALL">All Severities</option>
              <option value="CRITICAL">Critical</option>
              <option value="HIGH">High</option>
              <option value="WARNING">Warning</option>
              <option value="DATA_WARNING">Data Warning</option>
              <option value="INFO">Info</option>
            </select>
          </div>

          <div className="flex items-center gap-1 text-xs">
            <MapPin className="w-3.5 h-3.5 text-slate-500" />
            <select
              value={locationFilter}
              onChange={(e) => setLocationFilter(e.target.value)}
              className="text-xs bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-500"
            >
              <option value="ALL">All Stations</option>
              <option value="Paradeep">Paradeep</option>
              <option value="Cuttack">Cuttack</option>
              <option value="Kendrapara">Kendrapara</option>
            </select>
          </div>
        </div>
      </div>

      {/* Loading state */}
      {loading && !alertsData && (
        <div className="p-12 text-center text-slate-500 space-y-3">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto text-sky-600" />
          <p className="text-sm font-medium">Auditing operational alerts and notifications...</p>
        </div>
      )}

      {/* Empty State */}
      {!loading && displayedAlerts.length === 0 && (
        <div className="bg-white rounded-xl border border-slate-200 p-12 text-center space-y-3 shadow-sm">
          <CheckCircle2 className="w-10 h-10 text-emerald-500 mx-auto" />
          <h3 className="text-base font-bold text-slate-900">No Alerts Matching Selected Filters</h3>
          <p className="text-xs text-slate-500 max-w-md mx-auto">
            There are currently no active alerts matching your criteria for the Mahanadi delta pilot stations.
          </p>
        </div>
      )}

      {/* Alerts List */}
      <div className="space-y-4">
        {displayedAlerts.map((alert) => (
          <div
            key={alert.id}
            className={`bg-white rounded-xl border p-5 shadow-sm transition space-y-4 ${
              alert.status === 'NEW'
                ? 'border-emerald-300 ring-2 ring-emerald-50'
                : alert.severity === 'CRITICAL'
                ? 'border-rose-300'
                : alert.severity === 'HIGH'
                ? 'border-orange-300'
                : alert.severity === 'WARNING'
                ? 'border-amber-300'
                : alert.severity === 'DATA_WARNING'
                ? 'border-violet-300'
                : 'border-slate-200 hover:border-slate-300'
            }`}
          >
            {/* Top row */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2.5">
              <div className="flex items-center gap-2 flex-wrap">
                {getSeverityBadge(alert.severity)}
                {getStatusBadge(alert.status)}
                <span className="text-xs font-mono text-slate-400 bg-slate-50 px-2 py-0.5 rounded border border-slate-100">
                  {alert.alert_type}
                </span>
              </div>

              <div className="flex items-center gap-2 text-xs text-slate-500">
                <Clock className="w-3.5 h-3.5 text-slate-400" />
                <span>Created: {formatTimestamp(alert.created_at)}</span>
              </div>
            </div>

            {/* Title & Location */}
            <div>
              <h3 className="text-base font-extrabold text-slate-900 tracking-tight">
                {alert.title}
              </h3>
              <div className="flex items-center gap-2 text-xs text-slate-500 mt-1">
                <MapPin className="w-3.5 h-3.5 text-sky-600" />
                <span className="font-semibold text-slate-700">{alert.location_name}</span>
                <span>&bull;</span>
                <span>District: {alert.district_name || 'Mahanadi Delta'}</span>
              </div>
            </div>

            {/* Description */}
            <p className="text-sm text-slate-700 leading-relaxed">
              {alert.description}
            </p>

            {/* Evidence details */}
            {alert.evidence && Object.keys(alert.evidence).length > 0 && (
              <div className="p-3.5 rounded-lg bg-slate-50 border border-slate-100 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div>
                  <span className="text-slate-400 block text-[11px]">Risk Category</span>
                  <span className="font-bold text-slate-800">{resolveDisplayRiskCategory(alert)}</span>
                </div>
                <div>
                  <span className="text-slate-400 block text-[11px]">Observed Rainfall</span>
                  <span className="font-bold text-slate-800">
                    {formatRainfallMetric(alert.evidence.recent_observed_mm)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400 block text-[11px]">24h Forecast</span>
                  <span className="font-bold text-slate-800">
                    {formatRainfallMetric(alert.evidence.forecast_24h_mm)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400 block text-[11px]">Observation Freshness</span>
                  <span className="font-bold uppercase text-slate-800">
                    {alert.evidence.observation_freshness || 'N/A'}
                  </span>
                </div>
              </div>
            )}

            {/* Bottom Actions & Resolution/Acknowledgement Audit */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-2 border-t border-slate-100 text-xs">
              <div className="text-slate-500 space-y-0.5">
                {alert.acknowledged_at && (
                  <p>
                    <span className="font-semibold text-purple-700">Acknowledged:</span> {formatTimestamp(alert.acknowledged_at)} by {alert.acknowledged_by || 'operator'}
                  </p>
                )}
                {alert.resolved_at && (
                  <p>
                    <span className="font-semibold text-slate-700">Resolved:</span> {formatTimestamp(alert.resolved_at)} &bull; {alert.resolved_reason || DEFAULT_RESOLVED_REASON}
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2 self-start sm:self-auto flex-wrap">
                {/* Acknowledge Button for Open Alerts */}
                {(alert.status === 'NEW' || alert.status === 'ACTIVE') && (
                  <button
                    onClick={() => {
                      setAckModalAlert(alert);
                      setAckError(null);
                    }}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-50 text-purple-700 hover:bg-purple-100 font-semibold border border-purple-200 transition text-xs shadow-sm"
                  >
                    <Lock className="w-3.5 h-3.5 text-purple-600" />
                    Acknowledge Alert
                  </button>
                )}

                {/* Resolve Button for Any Open/Unresolved Alert (Phase 2.7.2) */}
                {alert.status !== 'RESOLVED' && (
                  <button
                    onClick={() => {
                      setResolveModalAlert(alert);
                      setResolveError(null);
                    }}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100 font-semibold border border-emerald-200 transition text-xs shadow-sm"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                    Resolve Alert
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Acknowledgement Dialog Modal */}
      {ackModalAlert && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <Lock className="w-5 h-5 text-purple-600" />
                <h3 className="font-bold text-slate-900 text-base">Operator Alert Acknowledgement</h3>
              </div>
              <button
                onClick={() => setAckModalAlert(null)}
                className="text-slate-400 hover:text-slate-600 p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-2 text-xs text-slate-600">
              <p>
                You are acknowledging the following alert:
              </p>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <p className="font-bold text-slate-900">{ackModalAlert.title}</p>
                <p className="text-[11px] text-slate-500 mt-0.5">{ackModalAlert.location_name}</p>
              </div>
              <p className="text-amber-800 bg-amber-50 p-2.5 rounded border border-amber-200 text-[11px]">
                <strong>Notice:</strong> Acknowledgement marks this notification as reviewed by an authorized operator. It does not alter the underlying meteorological risk calculation.
              </p>
            </div>

            {ackError && (
              <div className="p-2.5 rounded bg-rose-50 border border-rose-200 text-xs text-rose-700">
                {ackError}
              </div>
            )}

            <form onSubmit={handleAcknowledgeSubmit} className="space-y-3 pt-1">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Operator Role / Identifier
                </label>
                <input
                  type="text"
                  value={ackOperator}
                  onChange={(e) => setAckOperator(e.target.value)}
                  required
                  className="w-full text-xs px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-purple-500 text-slate-900"
                  placeholder="e.g. Duty Hydrologist"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Administrative Token (X-Ingestion-Token)
                </label>
                <input
                  type="password"
                  value={ackToken}
                  onChange={(e) => setAckToken(e.target.value)}
                  className="w-full text-xs px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-purple-500 text-slate-900"
                  placeholder="Enter operator authorization token..."
                />
                <span className="text-[10px] text-slate-400 mt-0.5 block">
                  Operator authentication token required to record official acknowledgement.
                </span>
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setAckModalAlert(null)}
                  className="px-4 py-2 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-100 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={ackSubmitting}
                  className="px-4 py-2 rounded-lg text-xs font-bold bg-purple-600 hover:bg-purple-700 text-white transition shadow-sm disabled:opacity-50"
                >
                  {ackSubmitting ? 'Logging...' : 'Confirm Acknowledgement'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Resolution Dialog Modal (Phase 2.7.2) */}
      {resolveModalAlert && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl border border-slate-200 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-5 h-5 text-emerald-600" />
                <h3 className="font-bold text-slate-900 text-base">Operator Alert Resolution</h3>
              </div>
              <button
                onClick={() => setResolveModalAlert(null)}
                className="text-slate-400 hover:text-slate-600 p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-2 text-xs text-slate-600">
              <p>You are resolving the following operational alert:</p>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <p className="font-bold text-slate-900">{resolveModalAlert.title}</p>
                <p className="text-[11px] text-slate-500 mt-0.5">{resolveModalAlert.location_name}</p>
              </div>
              <p className="text-emerald-800 bg-emerald-50 p-2.5 rounded border border-emerald-200 text-[11px]">
                <strong>Notice:</strong> Resolving records official justification and moves this alert to historical records.
              </p>
            </div>

            {resolveError && (
              <div className="p-2.5 rounded bg-rose-50 border border-rose-200 text-xs text-rose-700">
                {resolveError}
              </div>
            )}

            <form onSubmit={handleResolveSubmit} className="space-y-3 pt-1">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Resolution Justification (Required)
                </label>
                <textarea
                  value={resolveReason}
                  onChange={(e) => setResolveReason(e.target.value)}
                  required
                  rows={3}
                  className="w-full text-xs px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-slate-900 resize-none"
                  placeholder="Provide operational justification for resolving this alert..."
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Operator Role / Identifier
                </label>
                <input
                  type="text"
                  value={resolveOperator}
                  onChange={(e) => setResolveOperator(e.target.value)}
                  className="w-full text-xs px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-slate-900"
                  placeholder="e.g. Duty Hydrologist"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Administrative Token (X-Ingestion-Token)
                </label>
                <input
                  type="password"
                  value={resolveToken}
                  onChange={(e) => setResolveToken(e.target.value)}
                  className="w-full text-xs px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-slate-900"
                  placeholder="Enter operator authorization token..."
                />
                <span className="text-[10px] text-slate-400 mt-0.5 block">
                  Operator authentication token required to record official resolution.
                </span>
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setResolveModalAlert(null)}
                  className="px-4 py-2 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-100 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={resolveSubmitting}
                  className="px-4 py-2 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-700 text-white transition shadow-sm disabled:opacity-50"
                >
                  {resolveSubmitting ? 'Resolving...' : 'Confirm Resolution'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

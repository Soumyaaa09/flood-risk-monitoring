/**
 * UI display, API response state, risk badge, freshness, and alert lifecycle helpers
 * for FloodWatch dashboard modules.
 */

const RISK_CATEGORY_META = {
  VERY_HIGH: { label: 'Very High', tone: 'critical', isEvaluated: true },
  HIGH: { label: 'High', tone: 'high', isEvaluated: true },
  MODERATE: { label: 'Moderate', tone: 'moderate', isEvaluated: true },
  LOW: { label: 'Low', tone: 'low', isEvaluated: true },
  UNAVAILABLE: { label: 'Unavailable', tone: 'unavailable', isEvaluated: false },
};

const FRESHNESS_META = {
  fresh: { label: 'Fresh', tone: 'healthy', usable: true },
  delayed: { label: 'Delayed', tone: 'warning', usable: true },
  stale: { label: 'Stale', tone: 'danger', usable: false },
  unavailable: { label: 'Unavailable', tone: 'neutral', usable: false },
};

const ALERT_LIFECYCLE_META = {
  NEW: { label: 'New', isOpen: true },
  ACTIVE: { label: 'Active', isOpen: true },
  ACKNOWLEDGED: { label: 'Acknowledged', isOpen: true },
  RESOLVED: { label: 'Resolved', isOpen: false },
};

/**
 * Derive standardized async UI loading/error/empty/ready state.
 */
export function deriveFetchState({ loading = false, error = null, data = null } = {}) {
  if (loading) {
    return { status: 'loading', hasData: false, message: 'Loading telemetry...' };
  }
  if (error) {
    return {
      status: 'error',
      hasData: false,
      message: extractApiErrorMessage(error),
    };
  }
  if (!data || (Array.isArray(data) && data.length === 0)) {
    return { status: 'empty', hasData: false, message: 'No telemetry records available.' };
  }
  return { status: 'ready', hasData: true, message: '' };
}

/**
 * Extract safe, user-facing error message without leaking raw credentials or stack details.
 */
export function extractApiErrorMessage(error, fallback = 'Service temporarily unavailable.') {
  if (!error) return fallback;
  if (typeof error === 'string') return error.trim() || fallback;
  if (typeof error.detail?.message === 'string' && error.detail.message.trim()) {
    return error.detail.message.trim();
  }
  if (typeof error.message === 'string' && error.message.trim()) {
    return error.message.trim();
  }
  return fallback;
}

/**
 * Map FloodWatch risk categories safely so UNAVAILABLE never renders as LOW.
 */
export function getRiskCategoryDisplay(category) {
  const key = String(category || 'UNAVAILABLE').toUpperCase();
  return RISK_CATEGORY_META[key] || RISK_CATEGORY_META.UNAVAILABLE;
}

/**
 * Format observation/ingestion freshness states consistently.
 */
export function getFreshnessDisplay(state, ageMinutes = null) {
  const key = String(state || 'unavailable').toLowerCase();
  const base = FRESHNESS_META[key] || FRESHNESS_META.unavailable;
  const ageText =
    typeof ageMinutes === 'number' && Number.isFinite(ageMinutes)
      ? `${Math.round(ageMinutes)}m ago`
      : null;
  return { ...base, ageText };
}

/**
 * Map alert lifecycle status labels.
 */
export function getAlertLifecycleDisplay(status) {
  const key = String(status || '').toUpperCase();
  return ALERT_LIFECYCLE_META[key] || { label: 'Unknown', isOpen: false };
}

/**
 * Summarize monitoring payload safely even when telemetry or locations are missing.
 */
export function summarizeMonitoringPayload(payload) {
  if (!payload || typeof payload !== 'object') {
    return {
      systemHealth: 'unavailable',
      locationCount: 0,
      unavailableLocations: 0,
      hasActiveData: false,
    };
  }
  const locations = Array.isArray(payload.location_healths) ? payload.location_healths : [];
  const unavailableLocations = locations.filter(
    (loc) => String(loc?.observation_freshness || 'unavailable').toLowerCase() === 'unavailable',
  ).length;
  return {
    systemHealth: String(payload.system_health || 'unavailable').toLowerCase(),
    locationCount: locations.length,
    unavailableLocations,
    hasActiveData: locations.length > 0 && unavailableLocations < locations.length,
  };
}

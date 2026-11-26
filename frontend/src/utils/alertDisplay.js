/**
 * Display helpers for FloodWatch Alerts & Resolved History cards.
 */

const ALERT_TYPE_TO_RISK_CATEGORY = {
  VERY_HIGH_RAINFALL_RISK: 'VERY_HIGH',
  HIGH_RAINFALL_RISK: 'HIGH',
  MODERATE_RAINFALL_RISK: 'MODERATE',
  DATA_STALE: 'UNAVAILABLE',
  DATA_UNAVAILABLE: 'UNAVAILABLE',
  RISK_REDUCED: 'LOW',
};

export const RESOLVED_HISTORY_SUBTITLE = 'resolved alerts';
export const DEFAULT_RESOLVED_REASON = 'Alert marked resolved';

/**
 * Resolve the Risk Category label from alert evidence, falling back to
 * the alert_type mapping when historical evidence omits risk_category.
 *
 * @param {object} alert
 * @returns {string}
 */
export function resolveDisplayRiskCategory(alert) {
  const fromEvidence = alert?.evidence?.risk_category;
  if (typeof fromEvidence === 'string' && fromEvidence.trim().length > 0) {
    return fromEvidence.trim();
  }
  const fromType = ALERT_TYPE_TO_RISK_CATEGORY[alert?.alert_type];
  return fromType || 'N/A';
}

/**
 * Format a rainfall metric in millimetres for evidence cards.
 *
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function formatRainfallMetric(value) {
  if (value === null || value === undefined || value === '') {
    return 'N/A';
  }
  return `${value} mm`;
}

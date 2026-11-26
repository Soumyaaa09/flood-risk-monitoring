/**
 * Health status derivation helpers for FloodWatch navigation indicators.
 */

export const INITIAL_API_STATUS = {
  state: 'checking',
  message: 'Checking API...',
};

/**
 * Derive Navbar API health state from /api/v1/health HTTP status and payload.
 * Never reports "online" when overall status is degraded or database dependency is down.
 *
 * @param {object} params
 * @param {boolean} params.ok
 * @param {object|null|undefined} params.data
 * @returns {{ state: 'online'|'degraded'|'offline', message: string }}
 */
export function deriveApiHealthStatus({ ok, data }) {
  if (!ok || !data || typeof data !== 'object') {
    return { state: 'offline', message: 'API Offline' };
  }

  const versionLabel =
    typeof data.version === 'string' && data.version.trim().length > 0
      ? data.version.trim()
      : 'v0.1.0';
  const serviceStatus = String(data.status || '').toLowerCase();
  const dbStatus = String(data.database?.status || '').toLowerCase();

  if (serviceStatus === 'ok' && dbStatus === 'connected') {
    return {
      state: 'online',
      message: `Online (${versionLabel})`,
    };
  }

  if (serviceStatus === 'degraded' || (dbStatus && dbStatus !== 'connected') || serviceStatus === 'ok') {
    return {
      state: 'degraded',
      message: `Degraded (${versionLabel})`,
    };
  }

  return { state: 'offline', message: 'API Offline' };
}

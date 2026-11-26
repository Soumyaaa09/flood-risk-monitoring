import test from 'node:test';
import assert from 'node:assert/strict';

import { INITIAL_API_STATUS, deriveApiHealthStatus } from '../src/utils/healthStatus.js';
import {
  deriveFetchState,
  extractApiErrorMessage,
  getAlertLifecycleDisplay,
  getFreshnessDisplay,
  getRiskCategoryDisplay,
  summarizeMonitoringPayload,
} from '../src/utils/dashboardDisplay.js';

test('deriveApiHealthStatus distinguishes healthy, degraded, and offline states accurately', () => {
  assert.equal(INITIAL_API_STATUS.state, 'checking');

  assert.deepEqual(
    deriveApiHealthStatus({
      ok: true,
      data: { status: 'ok', version: '0.1.0', database: { status: 'connected' } },
    }),
    { state: 'online', message: 'Online (0.1.0)' },
  );

  // HTTP 200 with degraded backend status or disconnected DB must NEVER show Online
  assert.deepEqual(
    deriveApiHealthStatus({
      ok: true,
      data: { status: 'degraded', version: '0.1.0', database: { status: 'disconnected' } },
    }),
    { state: 'degraded', message: 'Degraded (0.1.0)' },
  );

  assert.deepEqual(
    deriveApiHealthStatus({
      ok: true,
      data: { status: 'ok', version: '0.1.0', database: { status: 'not_configured' } },
    }),
    { state: 'degraded', message: 'Degraded (0.1.0)' },
  );

  assert.deepEqual(
    deriveApiHealthStatus({ ok: false, data: null }),
    { state: 'offline', message: 'API Offline' },
  );
});

test('deriveFetchState and extractApiErrorMessage handle loading, error, empty, and ready telemetry', () => {
  assert.equal(deriveFetchState({ loading: true }).status, 'loading');
  assert.deepEqual(
    deriveFetchState({ error: { detail: { message: 'Database unavailable' } } }),
    { status: 'error', hasData: false, message: 'Database unavailable' },
  );
  assert.equal(deriveFetchState({ data: [] }).status, 'empty');
  assert.equal(deriveFetchState({ data: { assessments: [] } }).status, 'ready');
  assert.equal(extractApiErrorMessage(null, 'Fallback'), 'Fallback');
});

test('risk category, freshness, and alert lifecycle helpers keep missing data distinct from zero risk', () => {
  assert.deepEqual(getRiskCategoryDisplay('UNAVAILABLE'), {
    label: 'Unavailable',
    tone: 'unavailable',
    isEvaluated: false,
  });
  assert.deepEqual(getRiskCategoryDisplay('HIGH'), {
    label: 'High',
    tone: 'high',
    isEvaluated: true,
  });
  assert.equal(getRiskCategoryDisplay(undefined).label, 'Unavailable');

  assert.deepEqual(getFreshnessDisplay('fresh', 14.6), {
    label: 'Fresh',
    tone: 'healthy',
    usable: true,
    ageText: '15m ago',
  });
  assert.equal(getFreshnessDisplay('stale', 420).usable, false);
  assert.equal(getFreshnessDisplay(null).label, 'Unavailable');

  assert.deepEqual(getAlertLifecycleDisplay('ACTIVE'), { label: 'Active', isOpen: true });
  assert.deepEqual(getAlertLifecycleDisplay('RESOLVED'), { label: 'Resolved', isOpen: false });
});

test('summarizeMonitoringPayload flags missing or unavailable monitoring data cleanly', () => {
  assert.deepEqual(summarizeMonitoringPayload(null), {
    systemHealth: 'unavailable',
    locationCount: 0,
    unavailableLocations: 0,
    hasActiveData: false,
  });

  const emptyObsSummary = summarizeMonitoringPayload({
    system_health: 'critical',
    location_healths: [
      { id: '1', observation_freshness: 'unavailable' },
      { id: '2', observation_freshness: 'unavailable' },
    ],
  });
  assert.equal(emptyObsSummary.systemHealth, 'critical');
  assert.equal(emptyObsSummary.locationCount, 2);
  assert.equal(emptyObsSummary.unavailableLocations, 2);
  assert.equal(emptyObsSummary.hasActiveData, false);
});

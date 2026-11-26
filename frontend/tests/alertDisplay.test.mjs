import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_RESOLVED_REASON,
  RESOLVED_HISTORY_SUBTITLE,
  formatRainfallMetric,
  resolveDisplayRiskCategory,
} from '../src/utils/alertDisplay.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test('resolveDisplayRiskCategory prefers explicit evidence.risk_category', () => {
  assert.equal(
    resolveDisplayRiskCategory({
      alert_type: 'HIGH_RAINFALL_RISK',
      evidence: { risk_category: 'VERY_HIGH' },
    }),
    'VERY_HIGH',
  );
});

test('resolveDisplayRiskCategory derives category from alert_type when historical evidence omits risk_category', () => {
  assert.equal(
    resolveDisplayRiskCategory({
      alert_type: 'HIGH_RAINFALL_RISK',
      evidence: { recent_observed_mm: 1, forecast_24h_mm: 0, observation_freshness: 'fresh' },
    }),
    'HIGH',
  );
  assert.equal(resolveDisplayRiskCategory({ alert_type: 'VERY_HIGH_RAINFALL_RISK', evidence: {} }), 'VERY_HIGH');
  assert.equal(resolveDisplayRiskCategory({ alert_type: 'MODERATE_RAINFALL_RISK', evidence: {} }), 'MODERATE');
  assert.equal(resolveDisplayRiskCategory({ alert_type: 'DATA_STALE', evidence: {} }), 'UNAVAILABLE');
  assert.equal(resolveDisplayRiskCategory({ alert_type: 'DATA_UNAVAILABLE', evidence: {} }), 'UNAVAILABLE');
  assert.equal(resolveDisplayRiskCategory({ alert_type: 'RISK_REDUCED', evidence: {} }), 'LOW');
  assert.equal(resolveDisplayRiskCategory({ alert_type: 'UNKNOWN_TYPE', evidence: {} }), 'N/A');
  assert.equal(resolveDisplayRiskCategory(null), 'N/A');
});

test('formatRainfallMetric handles zero, numbers, and missing values consistently', () => {
  assert.equal(formatRainfallMetric(0), '0 mm');
  assert.equal(formatRainfallMetric(1), '1 mm');
  assert.equal(formatRainfallMetric(12.5), '12.5 mm');
  assert.equal(formatRainfallMetric(null), 'N/A');
  assert.equal(formatRainfallMetric(undefined), 'N/A');
  assert.equal(formatRainfallMetric(''), 'N/A');
});

test('Resolved History uses neutral resolution copy instead of conditions normalized', () => {
  assert.equal(RESOLVED_HISTORY_SUBTITLE, 'resolved alerts');
  assert.equal(DEFAULT_RESOLVED_REASON, 'Alert marked resolved');

  const alertsPagePath = path.resolve(__dirname, '../src/pages/AlertsPage.jsx');
  const source = fs.readFileSync(alertsPagePath, 'utf8');
  assert.equal(source.includes('conditions normalized'), false);
  assert.equal(source.includes('Normal conditions restored'), false);
  assert.equal(source.includes('RESOLVED_HISTORY_SUBTITLE'), true);
  assert.equal(source.includes('resolveDisplayRiskCategory(alert)'), true);
});

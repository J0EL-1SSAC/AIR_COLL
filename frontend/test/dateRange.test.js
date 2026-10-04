import test from 'node:test';
import assert from 'node:assert/strict';
import {dateTimeLocalToEpoch,localDateTimeInput,validateDateRange} from '../src/dateRange.js';

test('date range rejects future, reversed and pre-recording values', () => {
  const now = Date.parse('2026-10-04T08:00:00Z') / 1000;
  const old = Date.parse('2026-10-03T12:00:00Z') / 1000;
  assert.match(validateDateRange('2026-10-04T14:00', '', now), /future/);
  assert.match(validateDateRange('2026-10-04T11:00', '2026-10-04T10:00', now), /at or before/);
  assert.match(validateDateRange('2026-10-02T12:00', '', now, old), /earlier/);
  assert.equal(validateDateRange('2026-10-04T10:00', '2026-10-04T11:00', now), null);
  const utc='2026-10-04T08:00';
  assert.equal(dateTimeLocalToEpoch(utc,'UTC'),now);
  assert.equal(localDateTimeInput(now,'UTC'),utc);
});

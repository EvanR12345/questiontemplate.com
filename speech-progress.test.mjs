import test from 'node:test';
import assert from 'node:assert/strict';
import { GenerationEstimate, measuredPace, narrationEstimate } from './speech-progress.mjs';
test('ETA waits for evidence and uses words instead of character lengths', () => {
  const estimate = new GenerationEstimate();
  assert.equal(estimate.remaining(1000, 0), null);
  estimate.record(50, 20);
  assert.equal(estimate.remaining(950, 20), null);
  estimate.record(100, 25); estimate.record(150, 30);
  assert.equal(estimate.remaining(1000, 30).seconds, 100);
  assert.equal(estimate.remaining(1000, 30).delayed, false);
  assert.equal(estimate.remaining(1000, 50).delayed, true);
  assert.equal(estimate.remaining(0, 50).seconds, 0);
});
test('new/resumed run excludes old recordings and active time excludes pauses', () => {
  const estimate = new GenerationEstimate();
  estimate.record(50, 5); estimate.record(100, 10);
  assert.equal(estimate.remaining(100, 10).seconds, 10);
  // After a long pause the caller supplies the same active seconds.
  assert.equal(estimate.remaining(100, 10).seconds, 10);
  assert.equal(new GenerationEstimate().remaining(100, 0), null);
});
test('measured voice pace adjusts duration, speed and honest initial ranges', () => {
  assert.equal(measuredPace(5, 1), null);
  assert.equal(measuredPace(100, 50), 120);
  assert.equal(measuredPace(100, 25, 2), 120);
  const initial = narrationEstimate(1000, 1);
  assert.equal(initial.measured, false); assert.ok(initial.low < initial.high);
  const calibrated = narrationEstimate(1000, 1, 120);
  assert.ok(Math.abs(calibrated.seconds - 500) < 1e-9);
  assert.ok(Math.abs(narrationEstimate(1000, 2, 120).seconds - 250) < 1e-9);
  assert.equal(calibrated.measured, true);
});

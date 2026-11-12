import test from 'node:test';
import assert from 'node:assert/strict';
import { FullFrameScreensaver } from '../apps/stream-dog/node/full-frame-screensaver.mjs';

test('wake rejects an earlier frame assembly and briefly suppresses trailing saver frames', () => {
  let now = 0;
  const saver = new FullFrameScreensaver({ now: () => now });
  const previous = saver.epoch;
  assert.equal(saver.enter(previous), true);
  assert.equal(saver.active, true);
  saver.wake();
  assert.equal(saver.active, false);
  assert.equal(saver.enter(previous), false);
  assert.equal(saver.enter(saver.epoch), false);
  now = 301;
  assert.equal(saver.enter(previous), false);
  assert.equal(saver.enter(saver.epoch), true);
});

test('host wake black-clear preserves normal surfaces once and expires after the wake boundary', () => {
  let now = 0;
  const saver = new FullFrameScreensaver({ now: () => now });
  assert.equal(saver.consumeWakeClear([0, 0, 0]), false);
  saver.enter(saver.epoch);
  assert.equal(saver.consumeWakeClear([0, 0, 255]), false);
  assert.equal(saver.active, true);
  assert.equal(saver.consumeWakeClear([0, 0, 0]), true);
  assert.equal(saver.active, false);
  assert.equal(saver.consumeWakeClear([0, 0, 0]), false);
  now = 500;
  saver.wake();
  now = 1499;
  assert.equal(saver.consumeWakeClear([0, 0, 0]), true);
  saver.wake();
  now = 2500;
  assert.equal(saver.consumeWakeClear([0, 0, 0]), false);
});

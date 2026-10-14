import test from 'node:test';
import assert from 'node:assert/strict';
import { createCandidateObservations } from '../apps/stream-dog/node/candidate-observability.mjs';
import { createCandidateOutputQueue } from '../apps/stream-dog/node/candidate-output-queue.mjs';

const frame = surfaceId => ({ type: 'frame', surfaceId, width: 4, height: 4, format: 'rgb888', data: 'private-image-bytes' });
const worker = () => ({ keyCacheCount: 1, keyCacheClears: 2,
  keys: Array.from({ length: 10 }, () => ({ attempted: 2, submitted: 1, failed: 1, lastWriter: 'lcd',
    width: 120, height: 120, meanBrightness: 0, black: true, lastVendorReturnKind: 'positive',
    vendorReturnKinds: { none: 0, zero: 1, negative: 0, positive: 1, other: 0 } })) });

test('surface observations distinguish admitted, coalesced, submitted and restored frames', async () => {
  const observations = createCandidateObservations();
  const queue = createCandidateOutputQueue({ observeFrame: observations.displayFrame,
    submit: async payload => { observations.displaySubmission(payload, false, {}); return { status: 'submitted' }; } });
  const pending = [queue.enqueue(frame('key:0')), queue.enqueue(frame('key:0')), queue.enqueue(frame('lcd'))];
  assert.deepEqual(observations.snapshot().display.surfaces['key:0'], { received: 2, coalesced: 1, submitted: 0, failed: 0, restored: 0 });
  queue.resume(); await Promise.all(pending);
  observations.displaySubmission(frame('key:0'), true, {});
  observations.displayFrame('failed', 'touch');
  observations.displayFrame('received', 'private-device-id');
  const display = observations.snapshot().display;
  assert.deepEqual(display.surfaces['key:0'], { received: 2, coalesced: 1, submitted: 1, failed: 0, restored: 1 });
  assert.equal(display.surfaces.lcd.submitted, 1);
  assert.equal(display.surfaces.touch.failed, 1);
  assert.equal(Object.keys(display.surfaces).length, 12);
  assert.ok(!JSON.stringify(display).includes('private'));
  display.surfaces['key:0'].received = 100;
  assert.equal(observations.snapshot().display.surfaces['key:0'].received, 2);
  queue.close();
});

test('worker observations retain only fixed scalar evidence, with independent snapshots', () => {
  const observations = createCandidateObservations(), source = worker();
  source.deviceId = 'private-device-id'; source.keys[0].data = 'private-image-bytes';
  source.keys[0].vendorReturnKinds.secret = 'private-credential';
  observations.displaySubmission(frame('key:0'), false, { displayObservations: source });
  source.keys[0].lastWriter = 'key';
  const snapshot = observations.snapshot();
  assert.deepEqual(snapshot.display.worker, worker());
  assert.ok(!JSON.stringify(snapshot).includes('private'));
  assert.ok(Buffer.byteLength(JSON.stringify(snapshot)) < 8192);
  snapshot.display.worker.keys[0].submitted = 99;
  assert.equal(observations.snapshot().display.worker.keys[0].submitted, 1);
  observations.resetWorkerDisplay();
  assert.equal(observations.snapshot().display.worker, null);
});

test('malformed worker evidence cannot enter status as arbitrary strings or unbounded structures', () => {
  const observations = createCandidateObservations();
  const invalid = [
    value => { value.keyCacheCount = 11; }, value => { value.keyCacheClears = true; },
    value => { value.keys.push(value.keys[0]); }, value => { value.keys[0].lastWriter = 'private-path'; },
    value => { value.keys[0].width = 801; }, value => { value.keys[0].height = 0; },
    value => { value.keys[0].meanBrightness = NaN; }, value => { value.keys[0].meanBrightness = 'private-profile'; },
    value => { value.keys[0].black = 'private-image'; }, value => { value.keys[0].attempted = Number.MAX_SAFE_INTEGER + 1; },
    value => { value.keys[0].lastVendorReturnKind = 'private-error'; },
    value => { value.keys[0].vendorReturnKinds.positive = -1; },
  ];
  for (const mutate of invalid) {
    observations.workerDisplay(worker());
    const value = worker(); mutate(value); observations.workerDisplay(value);
    assert.equal(observations.snapshot().display.worker, null);
  }
});

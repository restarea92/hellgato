import test from 'node:test';
import assert from 'node:assert/strict';
import { createCandidateOutputQueue } from '../apps/stream-dog/node/candidate-output-queue.mjs';

const frame = (surfaceId, data = 'AA==', extra = {}) => ({ type: 'frame', surfaceId, width: 4, height: 4, format: 'rgb888', data, ...extra });
const control = value => ({ type: 'control', control: 'brightness', value });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
const observe = promise => promise.then(value => ({ value }), error => ({ error }));

test('stalled host admission compacts a repeated full-frame burst before any submission', async () => {
  const submitted = [], queue = createCandidateOutputQueue({ submit: async payload => { submitted.push(payload); return { status: 'submitted' }; } });
  const replies = [];
  for (let index = 0; index < 100; index++) replies.push(queue.enqueue(frame(`key:${index % 10}`, String(index))));
  await tick(); assert.deepEqual(submitted, []); assert.equal(queue.getStatus().retainedCount, 10);
  queue.resume();
  const results = await Promise.all(replies);
  assert.equal(results.filter(result => result.status === 'superseded').length, 90);
  assert.deepEqual(submitted.map(payload => payload.data), Array.from({ length: 10 }, (_, index) => String(90 + index)));
  queue.close();
});

test('offline animation replaces covered partial regions without crossing a control barrier', async () => {
  const submitted = [], queue = createCandidateOutputQueue({ submit: async payload => { submitted.push(payload); return { status: 'submitted' }; } });
  const region = { x: 0, y: 0, width: 2, height: 4 };
  const first = queue.enqueue(frame('touch', 'before', { region })), barrier = queue.enqueue(control(50));
  const updates = Array.from({ length: 100 }, (_, index) => queue.enqueue(frame('touch', String(index), { region })));
  assert.equal(queue.getStatus().retainedCount, 3); assert.equal(queue.getStatus().closed, false);
  queue.resume();
  const results = await Promise.all([first, barrier, ...updates]);
  assert.equal(results.filter(result => result.status === 'superseded').length, 99);
  assert.deepEqual(submitted, [frame('touch', 'before', { region }), control(50), frame('touch', '99', { region })]);
  queue.close();
});

test('partial regions and controls keep their order; full frames supersede only matching suffix history', async () => {
  const submitted = [], queue = createCandidateOutputQueue({ submit: async payload => { submitted.push(payload); return { status: 'submitted' }; } });
  const left = frame('key:0', 'left', { region: { x: 0, y: 0, width: 2, height: 4 } });
  const right = frame('key:0', 'right', { region: { x: 2, y: 0, width: 2, height: 4 } });
  const before = queue.enqueue(left), barrier = queue.enqueue(control(50));
  const covered = queue.enqueue(right), latest = queue.enqueue(frame('key:0', 'full'));
  assert.deepEqual(await covered, { status: 'superseded' });
  queue.resume(); await Promise.all([before, barrier, latest]);
  assert.deepEqual(submitted, [left, control(50), frame('key:0', 'full')]);
  queue.close();

  const adjacent = [], partialQueue = createCandidateOutputQueue({ submit: async payload => { adjacent.push(payload); return {}; } });
  const a = partialQueue.enqueue(left), b = partialQueue.enqueue(right);
  partialQueue.resume(); await Promise.all([a, b]); assert.deepEqual(adjacent, [left, right]); partialQueue.close();
});

test('replacement cannot discard another format or whole-surface dimensions, or invalid partial coverage', async () => {
  const submitted = [], queue = createCandidateOutputQueue({ submit: async payload => { submitted.push(payload); return {}; } });
  const first = frame('key:0', 'first'), changedFormat = frame('key:0', 'jpeg', { format: 'jpeg' });
  const changedSize = frame('key:0', 'larger', { width: 8 });
  const replies = [queue.enqueue(first), queue.enqueue(changedFormat), queue.enqueue(changedSize)];
  await assert.rejects(queue.enqueue(frame('key:0', 'bad', { region: { x: 3, y: 0, width: 2, height: 4 } })), { code: 'CANDIDATE_OUTPUT_RESULT' });
  assert.equal(queue.getStatus().retainedCount, 3);
  queue.resume(); await Promise.all(replies); assert.deepEqual(submitted, [first, changedFormat, changedSize]); queue.close();
});

test('irreducible entry and byte overload cancels pending work with an explicit failure before dispatch', async () => {
  const submitted = [], queue = createCandidateOutputQueue({ submit: async payload => { submitted.push(payload); return {}; } });
  const replies = Array.from({ length: 32 }, (_, value) => observe(queue.enqueue(control(value))));
  queue.resume();
  await assert.rejects(queue.enqueue(control(32)), { code: 'OUTPUT_OVERFLOW' });
  assert.equal(queue.getStatus().retainedCount, 0); assert.equal(queue.getStatus().closed, true);
  assert.ok((await Promise.all(replies)).every(result => result.error.code === 'OUTPUT_OVERFLOW'));
  assert.deepEqual(submitted, []);

  const partials = createCandidateOutputQueue({ submit: async () => ({}) });
  const pieces = Array.from({ length: 32 }, (_, x) => observe(partials.enqueue(frame('key:0', String(x),
    { width: 64, height: 1, region: { x, y: 0, width: 1, height: 1 } }))));
  await assert.rejects(partials.enqueue(frame('key:0', 'extra',
    { width: 64, height: 1, region: { x: 32, y: 0, width: 1, height: 1 } })), { code: 'OUTPUT_OVERFLOW' });
  assert.equal(partials.getStatus().retainedCount, 0); assert.equal(partials.getStatus().closed, true);
  assert.ok((await Promise.all(pieces)).every(result => result.error.code === 'OUTPUT_OVERFLOW'));

  const payload = frame('key:0', 'a'.repeat(50)), bytes = Buffer.byteLength(JSON.stringify(payload));
  const small = createCandidateOutputQueue({ submit: async () => ({}), maxBytes: bytes });
  const existing = observe(small.enqueue(payload));
  await assert.rejects(small.enqueue(frame('key:1', 'another')), { code: 'OUTPUT_OVERFLOW' });
  assert.equal(small.getStatus().retainedCount, 0); assert.equal(small.getStatus().closed, true);
  assert.equal((await existing).error.code, 'OUTPUT_OVERFLOW');

  const replacement = createCandidateOutputQueue({ submit: async () => ({}), maxBytes: bytes });
  const replaced = observe(replacement.enqueue(payload));
  await assert.rejects(replacement.enqueue(frame('key:0', 'a'.repeat(51))), { code: 'OUTPUT_OVERFLOW' });
  assert.equal((await replaced).error.code, 'OUTPUT_OVERFLOW'); assert.equal(replacement.getStatus().closed, true);
});

test('one in-flight submission consumes entry and byte credit until its ACK settles', async () => {
  const ack = deferred(), submitted = [], payload = frame('key:0');
  const bytes = Buffer.byteLength(JSON.stringify(payload));
  const queue = createCandidateOutputQueue({ submit: item => { submitted.push(item); return ack.promise; }, maxEntries: 1, maxBytes: bytes });
  const first = observe(queue.enqueue(payload)); queue.resume(); await tick();
  assert.equal(submitted.length, 1); assert.equal(queue.getStatus().inFlight, true);
  await assert.rejects(queue.enqueue(frame('key:0')), { code: 'OUTPUT_OVERFLOW' });
  assert.equal((await first).error.code, 'OUTPUT_OVERFLOW');
  assert.equal(queue.getStatus().inFlight, true); assert.equal(queue.getStatus().bytes, bytes);
  ack.resolve({ status: 'submitted', verification: 'unconfirmed' });
  await tick();
  assert.equal(queue.getStatus().retainedCount, 0); assert.equal(queue.getStatus().bytes, 0); queue.close();

  const byteAck = deferred(), byteQueue = createCandidateOutputQueue({ submit: () => byteAck.promise, maxBytes: bytes });
  const retained = observe(byteQueue.enqueue(payload)); byteQueue.resume(); await tick();
  await assert.rejects(byteQueue.enqueue(frame('key:1')), { code: 'OUTPUT_OVERFLOW' });
  assert.equal((await retained).error.code, 'OUTPUT_OVERFLOW'); assert.equal(byteQueue.getStatus().bytes, bytes);
  byteAck.resolve({}); await tick(); assert.equal(byteQueue.getStatus().retainedCount, 0);
});

test('reset cancels old callers while retaining in-flight credit and ignores a stale ACK rejection', async () => {
  const ack = deferred(), submitted = [], queue = createCandidateOutputQueue({ submit: payload => { submitted.push(payload); return submitted.length === 1 ? ack.promise : { status: 'submitted' }; } });
  const old = observe(queue.enqueue(frame('key:0', 'old'))), oldPending = observe(queue.enqueue(control(50)));
  queue.resume(); await tick(); queue.reset();
  assert.equal((await old).error.code, 'CANDIDATE_HOST_DISCONNECTED');
  assert.equal((await oldPending).error.code, 'CANDIDATE_HOST_DISCONNECTED');
  assert.equal(queue.getStatus().inFlight, true); assert.equal(queue.getStatus().pendingCount, 0);
  const next = queue.enqueue(frame('key:0', 'new')); queue.resume(); await tick(); assert.equal(submitted.length, 1);
  ack.reject(new Error('stale submission failure'));
  assert.deepEqual(await next, { status: 'submitted' });
  assert.deepEqual(submitted.map(payload => payload.data), ['old', 'new']); assert.equal(queue.getStatus().closed, false); queue.close();
});

test('USB reset retains pending host intent while cancelling the old in-flight submission', async () => {
  const ack = deferred(), submitted = [], queue = createCandidateOutputQueue({ submit: payload => {
    submitted.push(payload); return submitted.length === 1 ? ack.promise : { status: 'submitted' };
  } });
  const active = observe(queue.enqueue(frame('key:0', 'old'))), pending = queue.enqueue(frame('key:1', 'pending'));
  queue.resume(); await tick();
  queue.reset(Object.assign(new Error('USB lost'), { code: 'CANDIDATE_SESSION_ENDED' }), { retainPending: true });
  assert.equal((await active).error.code, 'CANDIDATE_SESSION_ENDED');
  assert.equal(queue.getStatus().pendingCount, 1); assert.equal(queue.getStatus().paused, true);
  const offline = queue.enqueue(frame('key:0', 'offline'));
  queue.resume(); ack.reject(new Error('stale worker failure'));
  await Promise.all([pending, offline]);
  assert.deepEqual(submitted.map(payload => payload.data), ['old', 'pending', 'offline']);
  assert.equal(queue.getStatus().closed, false); queue.close();
});

test('close cancels callers immediately and never submits pending output after a late ACK', async () => {
  const ack = deferred(), submitted = [], queue = createCandidateOutputQueue({ submit: payload => { submitted.push(payload); return ack.promise; } });
  const first = observe(queue.enqueue(frame('key:0'))), waiting = observe(queue.enqueue(control(50)));
  queue.resume(); await tick(); queue.close();
  assert.equal((await first).error.code, 'CANDIDATE_SESSION_ENDED'); assert.equal((await waiting).error.code, 'CANDIDATE_SESSION_ENDED');
  await assert.rejects(queue.enqueue(frame('key:1')), { code: 'CANDIDATE_SESSION_ENDED' });
  queue.resume(); ack.resolve({ status: 'submitted' }); await tick();
  assert.equal(submitted.length, 1); assert.equal(queue.getStatus().retainedCount, 0);
});

test('current submit failure closes the queue, while synchronous completion cannot recurse or bypass pacing', async () => {
  const failure = Object.assign(new Error('submission refused'), { code: 'ERR_DEVICE_OPERATION' });
  const failed = createCandidateOutputQueue({ submit: () => { throw failure; } });
  const one = observe(failed.enqueue(frame('key:0'))), two = observe(failed.enqueue(control(50))); failed.resume();
  assert.equal((await one).error, failure); assert.equal((await two).error, failure); assert.equal(failed.getStatus().closed, true);

  let inside = false; const submitted = [], queue = createCandidateOutputQueue({ submit: payload => {
    assert.equal(inside, false); inside = true; submitted.push(payload); inside = false; return { status: 'submitted' };
  } });
  const payload = { ...frame('key:0'), deviceId: 'old-device', sessionId: 'old-session', generation: 9, sequence: 42 };
  const frameReply = queue.enqueue(payload), controlReply = queue.enqueue(control(50));
  payload.data = 'changed-after-enqueue'; queue.resume(); await Promise.all([frameReply, controlReply]);
  assert.deepEqual(submitted, [frame('key:0'), control(50)]); queue.close();
});

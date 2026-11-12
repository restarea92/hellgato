import test from 'node:test';
import assert from 'node:assert/strict';
import { FrameSessionSupervisor } from '../apps/stream-dog/node/frame-session-supervisor.mjs';
import { deferred, until } from './helpers/frame-runtime-fixture.mjs';

test('recovery uses fresh generations and disconnect cancels retries rather than resurrecting a session', async () => {
  const starts = [], completions = [];
  const supervisor = new FrameSessionSupervisor({ retryMs: () => 5, start: async ({ generation }) => {
    starts.push(generation);
    if (starts.length === 1) throw new Error('USB absent');
    const done = deferred(); completions.push(done);
    return { completion: done.promise, stop: async () => done.resolve(), control: async value => value };
  } });
  supervisor.connect();
  await until(() => supervisor.status().state === 'running');
  assert.deepEqual(starts, [1, 2]);
  completions[0].reject(new Error('Worker exited'));
  await until(() => starts.length === 3 && supervisor.status().state === 'running');
  await supervisor.disconnect();
  const count = starts.length;
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(starts.length, count);
  assert.equal(supervisor.status().desired, false);
  assert.equal(supervisor.status().state, 'stopped');
});

test('disconnect aborts startup and closes a late session before it can forward anything', async () => {
  const started = deferred(), gate = deferred(); let stopped = 0, signal;
  const supervisor = new FrameSessionSupervisor({ start: async context => {
    signal = context.signal; started.resolve(); await gate.promise;
    return { completion: Promise.resolve(), stop: async () => { stopped++; } };
  } });
  supervisor.connect(); await started.promise;
  const closing = supervisor.disconnect();
  assert.equal(signal.aborted, true);
  gate.resolve(); await closing;
  assert.equal(stopped, 1);
  assert.equal(supervisor.status().state, 'stopped');
  await assert.rejects(supervisor.control({ control: 'wake' }), /not connected/);
});

test('a requested reconnect during shutdown starts only after the previous session closes', async () => {
  const gate = deferred(); let count = 0;
  const supervisor = new FrameSessionSupervisor({ start: async () => {
    const current = ++count, done = deferred();
    return { completion: done.promise, stop: async () => {
      if (current === 1) await gate.promise;
      done.resolve();
    } };
  } });
  supervisor.connect(); await until(() => count === 1);
  const closing = supervisor.disconnect(); supervisor.connect();
  assert.equal(count, 1); gate.resolve(); await closing;
  await until(() => count === 2 && supervisor.status().state === 'running');
  assert.equal(supervisor.status().desired, true);
  await supervisor.disconnect();
});

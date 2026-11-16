import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PersistentFrameAdapter, virtualFrameDevice } from '../apps/stream-dog/node/frame-persistent-adapter.mjs';
import { validateDeviceDescriptor } from '../packages/core/src/contracts/index.mts';
import { target } from './helpers/frame-runtime-fixture.mjs';
import { until } from './helpers/frame-runtime-fixture.mjs';

test('offline device descriptors obey the public SDK schema with or without touch and dials', () => {
  for (const geometry of [target.geometry, { ...target.geometry, encoderCount: 0, touchWidth: 0, touchHeight: 0 }]) {
    const { virtual, ...device } = virtualFrameDevice('fixture', { ...target, geometry });
    assert.equal(virtual, true);
    assert.equal(validateDeviceDescriptor(device).id, 'virtual:fixture');
  }
});

test('USB absence and worker loss retain the virtual session and replay only its latest complete frame', async t => {
  let present = false;
  const workers = [], writes = [];
  class Usb extends EventEmitter {
    status() { return { pid: 1 }; }
    async start() { return { layout: { width: 800, height: 480 } }; }
    async request(method, params) {
      if (method === 'discover') return { devices: present ? [{ id: 'usb-1' }] : [] };
      if (!present) throw new Error('USB unavailable');
      writes.push([method, params]); return {};
    }
    async close() { this.closed = true; }
  }
  const adapter = new PersistentFrameAdapter({}, { retryMs: 5, create: () => { const worker = new Usb(); workers.push(worker); return worker; } });
  t.after(() => adapter.close());
  let failed = false; adapter.on('failure', () => { failed = true; });
  await adapter.start();
  await adapter.request('open', { deviceId: 'usb-1', sessionId: 'same-identity', generation: 4, virtualDevice: { id: 'usb-1' } });
  await adapter.request('control', { control: 'brightness', value: 72 });
  await adapter.request('frame', { jpeg: 'obsolete' });
  await adapter.request('frame', { jpeg: 'latest' });
  assert.equal(adapter.status().physicalConnected, false);
  present = true;
  await until(() => adapter.status().physicalConnected);
  assert.equal(writes.find(([method]) => method === 'open')[1].sessionId, 'same-identity');
  assert.equal(writes.filter(([method]) => method === 'frame').length, 1);
  assert.equal(writes.find(([method]) => method === 'frame')[1].jpeg, 'latest');
  assert.equal(writes.find(([method]) => method === 'control')[1].value, 72);
  present = false; workers.at(-1).emit('failure', new Error('USB removed'));
  assert.equal(adapter.status().physicalConnected, false);
  assert.equal(failed, false);
  await adapter.close();
  assert.ok(workers.every(worker => worker.closed));
});

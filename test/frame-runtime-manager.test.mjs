import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { FrameRuntimeManager } from '../apps/stream-dog/node/frame-runtime-manager.mjs';
import { adapterFixture, deferred, until } from './helpers/frame-runtime-fixture.mjs';

async function managerFixture(t) {
  const fixture = await adapterFixture(t);
  const config = { stateDir: fixture.root, settingsPath: join(fixture.root, 'settings.json'),
    builtInAdapters: [fixture.path], fake: true, retryMs: () => 5,
    legacyIdentityPath: join(fixture.root, 'legacy.json') };
  const identity = { dockSerial: 'HGDOCK000001', childSerial: 'HGMOCK000003', macAddress: [2, 0, 0, 0, 0, 1] };
  await writeFile(config.legacyIdentityPath, JSON.stringify({ identity }));
  const devices = [1, 2].map(index => ({ id: `device-${index}`, adapterId: fixture.manifest.id, modelId: 'fixture', capabilities: {} }));
  const starts = [];
  const createSession = async (value, options) => {
    starts.push(value); const done = deferred();
    options.onState({ deviceConnected: true, hostConnected: true, networkListening: true });
    const stop = async () => { options.onState({ deviceConnected: false, hostConnected: false }); done.resolve(); };
    return { stop, completion: done.promise, control: async () => {} };
  };
  const manager = new FrameRuntimeManager(config, { createSession, discover: async () => devices });
  t.after(() => manager.close());
  return { ...fixture, config, manager, starts, identity, devices, createSession };
}

test('device selection preserves the existing paired identity and persists explicit connection intent', async t => {
  const f = await managerFixture(t);
  assert.deepEqual(f.manager.getState().settings.identity, f.identity);
  assert.equal(f.starts.length, 0);
  await assert.rejects(f.manager.command('connect'), /registered adapter/);
  const discovered = await f.manager.command('discover', { adapterId: f.manifest.id });
  assert.equal(discovered.devices.length, 2);
  await assert.rejects(f.manager.command('select', { adapterId: f.manifest.id, deviceId: 'unknown' }), /explicit discovery/);
  await f.manager.command('select', { adapterId: f.manifest.id, deviceId: 'device-2' });
  await f.manager.command('connect');
  await until(() => f.manager.getState().runtime.state === 'running');
  assert.equal(f.starts[0].deviceId, 'device-2');
  assert.equal(f.starts[0].target.config.childSerialNumber, f.identity.childSerial);
  const activeDiscovery = await f.manager.command('discover', { adapterId: f.manifest.id });
  assert.equal(activeDiscovery.devices.length, 2);
  assert.equal(f.starts.length, 1);
  await f.manager.command('disconnect');
  const saved = JSON.parse(await readFile(f.config.settingsPath, 'utf8'));
  assert.equal(saved.connectionDesired, false);
  assert.deepEqual(saved.identity, f.identity);
});

test('settings changes restart one session with fresh generation and reject invalid values atomically', async t => {
  const f = await managerFixture(t);
  await f.manager.command('discover', { adapterId: f.manifest.id });
  await f.manager.command('select', { adapterId: f.manifest.id, deviceId: 'device-1' });
  await f.manager.command('connect'); await until(() => f.starts.length === 1);
  await assert.rejects(f.manager.command('settings', { brightness: 101 }), /Invalid saved/);
  assert.equal(f.manager.getState().settings.brightness, 52);
  await f.manager.command('settings', { brightness: 70, maxFps: 30 });
  await until(() => f.starts.length === 2);
  assert.equal(f.starts[1].brightness, 70);
  assert.equal(f.starts[1].maxFps, 30);
  assert.ok(f.starts[1].generation > f.starts[0].generation);
});

test('saved connection intent resumes after a runtime restart without generating a new identity', async t => {
  const f = await managerFixture(t);
  await f.manager.command('discover', { adapterId: f.manifest.id });
  await f.manager.command('select', { adapterId: f.manifest.id, deviceId: 'device-1' });
  await f.manager.command('connect'); await until(() => f.starts.length === 1);
  await f.manager.close();
  const restarted = new FrameRuntimeManager(f.config, { createSession: f.createSession });
  t.after(() => restarted.close());
  restarted.resume(); await until(() => f.starts.length === 2);
  assert.deepEqual(restarted.getState().settings.identity, f.identity);
  await restarted.command('disconnect');
});

test('persistent legacy settings preserve paired IDs after temporary IPC configuration has been removed', async t => {
  const f = await adapterFixture(t);
  const legacy = join(f.root, 'legacy-settings.json');
  await writeFile(legacy, JSON.stringify({ version: 1, identity: 3, dockIdentity: 1, stripMode: 'frame' }));
  const manager = new FrameRuntimeManager({ stateDir: f.root, settingsPath: join(f.root, 'new-settings.json'), legacyIdentityPath: legacy });
  t.after(() => manager.close());
  assert.deepEqual(manager.getState().settings.identity, { dockSerial: 'HGDOCK000001', childSerial: 'HGMOCK000003', macAddress: [2, 0, 0, 0, 0, 1] });
  await writeFile(legacy, JSON.stringify({ version: 1, identity: 0, dockIdentity: 1 }));
  assert.throws(() => new FrameRuntimeManager({ stateDir: f.root, settingsPath: join(f.root, 'invalid-new-settings.json'), legacyIdentityPath: legacy }), /refusing to replace/);
});

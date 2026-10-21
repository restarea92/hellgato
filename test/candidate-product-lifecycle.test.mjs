import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtempSync, mkdirSync, cpSync, existsSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const temp = mkdtempSync(join(tmpdir(), 'hellgato-product-lifecycle-'));
after(() => rmSync(temp, { force: true, recursive: true }));
const runtime = join(temp, 'sdk'); mkdirSync(runtime);
for (const [source, destination] of [['packages/core', 'node_modules/@hellgato/core'], ['packages/hellgato', 'node_modules/hellgato']]) {
  const target = join(runtime, destination); mkdirSync(target, { recursive: true });
  for (const name of ['package.json', 'index.mjs', 'src', 'dist', 'schemas']) if (existsSync(join(root, source, name))) cpSync(join(root, source, name), join(target, name), { recursive: true });
}
for (const name of readdirSync(join(root, 'apps/stream-dog/node')).filter(name => /^candidate.*\.mjs$/.test(name))) cpSync(join(root, 'apps/stream-dog/node', name), join(runtime, name));
const load = name => import(pathToFileURL(join(runtime, name)));
const { createCandidateRunner, createDisplayJournal, discoverProductDevices } = await load('candidate-session.mjs');
const { validateCandidateConfig } = await load('candidate-config.mjs');
const { createSimulator } = await import(pathToFileURL(join(runtime, 'node_modules/hellgato/src/testing/index.mjs')));
const tick = () => new Promise(resolve => setImmediate(resolve));
const waitUntil = async predicate => {
  const end = Date.now() + 5000;
  while (!await predicate()) { if (Date.now() > end) throw new Error('Timed out'); await new Promise(resolve => setTimeout(resolve, 5)); }
};
let serial = 0;
function config(extra = {}) {
  const folder = join(temp, `case-${++serial}`); mkdirSync(folder);
  return { schemaVersion: 1, deviceId: 'fake:buttons-dial', identity: { dockSerial: 'HGDOCK000042', childSerial: 'HGMOCK000099', macAddress: [2, 0, 0, 0, 0, 42] },
    runtime: { command: process.execPath, manifestPath: join(folder, 'manifest.json'), args: [] },
    readiness: { command: process.execPath, args: [] }, statePath: join(folder, 'settings.json'), diagnosticsPath: join(folder, 'events.jsonl'),
    initialControls: { brightness: 100, knobBrightness: 100, followDisplayBrightness: true, sleepDuration: 0 },
    reconnect: { maxAttempts: 2, delayMs: 1 }, ...extra };
}
function fixture(extra = {}) {
  const device = { id: 'fake:buttons-dial', adapterId: 'hellgato.fake-sidecar', modelId: 'buttons-dial', firmware: 'simulation',
    capabilities: { keys: 2, dials: 1, touch: null, dialPress: 'native', dialRelease: 'native',
      displays: [{ id: 'key:0', width: 4, height: 4, format: 'rgb888', partial: true }, { id: 'key:1', width: 4, height: 4, format: 'rgb888', partial: true }],
      features: Object.fromEntries(['brightness', 'knobBrightness', 'followDisplayBrightness', 'sleepDuration', 'clear', 'fill', 'sleep', 'wake', 'screensaver', 'showLogo'].map(key => [key, { status: 'emulated' }])) } };
  if (extra.fullProfile) Object.assign(device.capabilities, {
    keys: 10, dials: 4, touch: { width: 800, height: 100 },
    displays: [...Array.from({ length: 10 }, (_, index) => ({ id: `key:${index}`, width: 120, height: 120, format: 'rgb888', partial: false })),
      { id: 'touch', width: 800, height: 100, format: 'rgb888', partial: true },
      { id: 'lcd', width: 4, height: 4, format: 'rgb888', partial: false }],
  });
  const adapter = createSimulator({ device });
  const initial = () => ({ brightness: 100, knobBrightness: 100, followDisplayBrightness: true, sleepDuration: 0, idle: false, sleeping: false });
  let settings = initial();
  const setControl = adapter.setControl.bind(adapter), getState = adapter.getState.bind(adapter), open = adapter.open.bind(adapter);
  adapter.open = async (...args) => { settings = initial(); return open(...args); };
  adapter.setControl = async value => {
    const result = await setControl(value);
    if (value.control === 'brightness') {
      if (value.value === 0) settings.idle = settings.sleeping = true;
      else if (!settings.idle || value.value >= settings.brightness) { settings.brightness = value.value; settings.idle = settings.sleeping = false; }
    }
    if (value.control === 'knobBrightness') { settings.knobBrightness = value.value; settings.followDisplayBrightness = false; }
    if (value.control === 'followDisplayBrightness') { if (!value.value && settings.followDisplayBrightness) settings.knobBrightness = settings.brightness; settings.followDisplayBrightness = value.value; }
    if (value.control === 'sleepDuration') settings.sleepDuration = value.value;
    if (['sleep', 'screensaver', 'wake'].includes(value.control)) settings.idle = settings.sleeping = value.control !== 'wake';
    return { ...result, verification: 'simulation', idle: settings.idle, sleeping: settings.sleeping, screensaverActive: false, logoActive: false,
      ...(['clear', 'fill'].includes(value.control) ? { normalStateEffect: value.surfaceId ? 'surface' : 'replace-all' } : {}) };
  };
  adapter.getState = async () => ({ ...await getState(), ...settings, knobBrightness: settings.followDisplayBrightness ? settings.brightness : settings.knobBrightness });
  const bridges = [], events = [];
  const value = config({ productMode: true, ...extra.config });
  const readiness = async ({ profile }) => ({ ready: true, targetId: profile.target.id, profileId: profile.id,
    host: { pid: 12, creationTime: 'fresh', executableDigest: 'a'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] });
  const runner = createCandidateRunner(value, { selfTest: !extra.fullProfile, adapter, readiness: extra.readiness ?? readiness,
    bridgeFactory: options => {
      const bridge = new EventEmitter(); bridge.options = options; bridge.inputs = []; bridge.sequence = 0;
      bridge.startCount = 0; bridge.stopCount = 0; bridge.releaseCount = 0; bridge.heldKeys = new Set();
      bridge.primaryConnected = false; bridge.childConnected = false;
      for (const [event, connected] of [['clientConnected', true], ['clientDisconnected', false]]) bridge.on(event, detail => {
        bridge[detail?.role === 'primary' ? 'primaryConnected' : 'childConnected'] = connected;
      });
      bridge.ports = { primary: 5343, child: 5344 };
      bridge.start = async () => { bridge.startCount++; }; bridge.stop = async () => { bridge.stopCount++; };
      bridge.releaseInputs = () => { bridge.releaseCount++; bridge.heldKeys.clear(); };
      bridge.sendInput = event => {
        bridge.inputs.push(event);
        if (event.type === 'key') event.state === 'down' ? bridge.heldKeys.add(event.index) : bridge.heldKeys.delete(event.index);
        return true;
      };
      bridge.output = payload => bridge.emit('output', { ...payload, deviceId: device.id, sessionId: options.sessionId,
        generation: options.generation, sequence: ++bridge.sequence });
      bridges.push(bridge); return bridge;
    } });
  runner.on('product', event => events.push(event));
  return { runner, adapter, bridges, events, config: value };
}

const frame = (surfaceId, byte = 1) => ({ type: 'frame', surfaceId, format: 'rgb888', width: 4, height: 4,
  data: Buffer.alloc(48, byte).toString('base64') });

test('default display journal replaces key and touch history with an acknowledged full LCD frame', () => {
  const journal = createDisplayJournal();
  journal.prepare(frame('key:0'))(); journal.prepare(frame('touch', 2))();
  const lcd = frame('lcd', 3); journal.prepare(lcd)();
  assert.deepEqual(journal.entries, [lcd]);
});

test('streamed screensaver LCD frames preserve desired key and touch history without consuming the restore bound', () => {
  const journal = createDisplayJournal({ lcdMode: 'stream-screensaver', maxEntries: 2 });
  const key = frame('key:0'), touch = frame('touch', 2);
  journal.prepare(key)(); journal.prepare(touch)();
  for (let byte = 3; byte < 10; byte++) assert.equal(journal.prepare(frame('lcd', byte)), null);
  assert.deepEqual(journal.entries, [key, touch]); assert.equal(journal.length, 2);
  const snapshot = journal.entries; snapshot[0].data = 'changed';
  assert.deepEqual(journal.entries, [key, touch]);
  journal.prepare({ type: 'control', control: 'clear' }, { normalStateEffect: 'background-only' })();
  assert.deepEqual(journal.entries, [key, touch]);
});

test('streamed screensaver journal retains normal surface and global clear or fill effects', () => {
  const journal = createDisplayJournal({ lcdMode: 'stream-screensaver' });
  const key = frame('key:0'), touch = frame('touch', 2);
  journal.prepare(key)(); journal.prepare(touch)();
  const surfaceFill = { type: 'control', control: 'fill', surfaceId: 'key:0', color: [2, 3, 4] };
  journal.prepare(surfaceFill, { normalStateEffect: 'surface' })();
  assert.deepEqual(journal.entries, [touch, surfaceFill]);
  const globalFill = { type: 'control', control: 'fill', color: [3, 4, 5] };
  journal.prepare(globalFill, { normalStateEffect: 'replace-all' })();
  assert.deepEqual(journal.entries, [globalFill]);
  journal.prepare(key)(); assert.equal(journal.prepare(frame('lcd', 3)), null);
  const clear = { type: 'control', control: 'clear', surfaceId: 'lcd' };
  journal.prepare(clear, { normalStateEffect: 'replace-all' })();
  assert.deepEqual(journal.entries, [clear]);
});

test('ordinary product events require an explicit boolean product mode', async () => {
  const value = config();
  assert.equal(validateCandidateConfig(value).productMode, false);
  assert.equal(validateCandidateConfig({ ...value, productMode: true }).productMode, true);
  for (const productMode of ['true', 1, null, {}]) assert.throws(() => validateCandidateConfig({ ...value, productMode }));
  const f = fixture({ config: { productMode: false } });
  try {
    await f.runner.start(); f.bridges[0].emit('clientConnected', { role: 'primary' });
    f.bridges[0].emit('clientDisconnected', { role: 'primary' });
    f.adapter.emitInput({ type: 'key', index: 0, state: 'up' }); assert.equal(f.bridges[0].inputs.length, 1);
    await f.runner.control({ control: 'brightness', value: 71 });
    await f.runner.reconnect(); await f.runner.stop();
    assert.deepEqual(f.events, []);
  } finally { await f.runner.stop(); }
});

test('listening events include bridge ports and device readiness waits for restored controls', async () => {
  const f = fixture();
  let release;
  const setControl = f.adapter.setControl.bind(f.adapter);
  f.adapter.setControl = async value => {
    if (value.control === 'brightness') await new Promise(resolve => { release = resolve; });
    return setControl(value);
  };
  const started = f.runner.start();
  try {
    await waitUntil(() => release !== undefined);
    assert.deepEqual(f.events.map(event => event.event), ['bridgeReady', 'started']);
    for (const event of f.events) {
      assert.equal(event.schemaVersion, 1); assert.equal(event.generation, 1);
    }
    assert.deepEqual(f.events.find(event => event.event === 'started').ports, { primary: 5343, child: 5344 });
    assert.equal(f.runner.getStatus().state, 'starting');
    assert.deepEqual(f.adapter.controls.map(value => value.control), ['clear']);
    release(); await started;
    const ready = f.events.filter(event => event.event === 'deviceReady');
    assert.equal(ready.length, 1); assert.equal(ready[0].generation, 1);
    assert.equal(ready[0].deviceId, f.adapter.device.id); assert.equal(ready[0].firmware, 'simulation');
    assert.deepEqual(ready[0].capabilities, { features: {
      brightness: { status: 'emulated' }, knobBrightness: { status: 'emulated' }, followDisplayBrightness: { status: 'emulated' },
      sleepDuration: { status: 'emulated' }, screensaver: { status: 'emulated' }, showLogo: { status: 'emulated' },
      sleep: { status: 'emulated' }, wake: { status: 'emulated' },
    } });
    assert.deepEqual(f.adapter.controls.map(value => value.control), ['clear', 'brightness', 'knobBrightness', 'followDisplayBrightness', 'sleepDuration']);
    f.bridges[0].emit('clientConnected', { role: 'primary' }); await tick();
    assert.equal(f.events.filter(event => event.event === 'deviceReady').length, 1);
  } finally { release?.(); await f.runner.stop(); await started.catch(() => {}); }
  assert.equal(f.events.at(-1).event, 'stopped');
});

test('a primary probe disconnect during USB open still publishes device readiness before fresh host approval', async () => {
  const f = fixture();
  let release;
  const open = f.adapter.open.bind(f.adapter);
  f.adapter.open = async (...args) => {
    const result = await open(...args);
    await new Promise(resolve => { release = resolve; });
    return result;
  };
  const started = f.runner.start();
  try {
    await waitUntil(() => release !== undefined);
    const bridge = f.bridges[0];
    bridge.emit('clientConnected', { role: 'primary' });
    bridge.emit('clientDisconnected', { role: 'primary' });
    bridge.output(frame('key:0', 7));
    release(); await started;
    assert.equal(f.events.filter(event => event.event === 'deviceReady').length, 1);
    assert.equal(f.runner.getStatus().state, 'ready');
    assert.equal(f.runner.getStatus().counters.frameWrites, 0);
    assert.equal((await f.runner.getProductWorkerState()).state.brightness, 100);
    await assert.rejects(f.runner.control({ control: 'brightness', value: 50 }), { code: 'CANDIDATE_CONTROL_NOT_READY' });
    bridge.emit('clientConnected', { role: 'child' });
    await tick(); bridge.output(frame('key:0', 8));
    await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
    assert.equal(f.adapter.outputs.at(-1).data, frame('key:0', 8).data);
    assert.equal(f.events.filter(event => event.event === 'deviceReady').length, 1);
    assert.equal(f.events.filter(event => event.event === 'deviceReconnected').length, 0);
    assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
  } finally { release?.(); await f.runner.stop(); await started.catch(() => {}); }
});

test('acknowledged saved-source changes switch whole LCD relay without restarting or journaling saver frames', async () => {
  for (const initialMode of ['native', 'stream']) {
    const f = fixture({ fullProfile: true, config: { screensaverMode: initialMode } });
    let workerSaver = { screensaverReady: initialMode === 'native', screensaverRevision: 0, screensaverLoading: false, screensaverError: null };
    const getState = f.adapter.getState.bind(f.adapter);
    f.adapter.getState = async () => ({ ...await getState(), ...workerSaver });
    const image = (surfaceId, width, height) => ({ type: 'frame', surfaceId, format: 'rgb888', width, height,
      data: Buffer.alloc(width * height * 3, 7).toString('base64') });
    try {
      await f.runner.start();
      const bridge = f.bridges[0];
      bridge.output(image('key:0', 120, 120));
      await tick(); assert.equal(f.runner.getStatus().state, 'ready', JSON.stringify(f.runner.getStatus()));
      await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
      const sendSaver = async native => {
        const previous = native ? f.adapter.controls.length : f.adapter.outputs.length;
        bridge.emit('lcdFrameStarted'); bridge.output(image('lcd', 4, 4));
        await tick(); assert.equal(f.runner.getStatus().state, 'ready', JSON.stringify(f.runner.getStatus()));
        await waitUntil(() => (native ? f.adapter.controls.length : f.adapter.outputs.length) > previous);
        await tick();
        if (native) assert.equal(f.adapter.controls.at(-1).control, 'screensaver');
        else assert.equal(f.adapter.outputs.at(-1).surfaceId, 'lcd');
      };
      await sendSaver(initialMode === 'native');
      for (const ready of [false, true, false]) {
        const previousReady = workerSaver.screensaverReady;
        workerSaver = { ...workerSaver, screensaverReady: ready, screensaverRevision: workerSaver.screensaverRevision + 1 };
        await sendSaver(previousReady);
        assert.equal((await f.runner.getProductWorkerState()).state.screensaverReady, ready);
        await sendSaver(ready);
      }
      workerSaver = { ...workerSaver, screensaverLoading: true };
      await f.runner.getProductWorkerState(); await sendSaver(false);
      workerSaver = { ...workerSaver, screensaverLoading: false, screensaverError: 'ERR_ASSET_INVALID' };
      assert.equal((await f.runner.getProductWorkerState()).state.screensaverError, 'ERR_ASSET_INVALID');
      await sendSaver(false);
      assert.equal(f.runner.getStatus().generation, 1);
      assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
      assert.equal(f.adapter.calls.filter(call => call === 'open').length, 1);
      await f.runner.reconnect();
      const restored = f.adapter.outputs.filter(value => value.generation === 2);
      assert.deepEqual(restored.map(value => value.surfaceId), ['key:0']);
      assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
    } finally { await f.runner.stop(); }
  }
});

test('primary and child pairing events remain distinct while fresh child readiness gates controls and inputs', async () => {
  let checks = 0, release;
  const f = fixture({ readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++checks), executableDigest: 'b'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return checks === 1 ? report : new Promise(resolve => { release = () => resolve(report); });
  } });
  try {
    await f.runner.start();
    f.bridges[0].emit('clientConnected', { role: 'primary', detail: ['private-host-details'] });
    f.bridges[0].emit('clientDisconnected', { role: 'primary', detail: ['private-host-details'] });
    f.adapter.emitInput({ type: 'key', index: 0, state: 'up' });
    assert.equal(f.bridges[0].inputs.length, 0);
    f.bridges[0].emit('clientDisconnected', { role: 'child', detail: ['private-host-details'] });
    f.bridges[0].emit('clientConnected', { role: 'child', detail: ['private-host-details'] });
    f.adapter.emitInput({ type: 'key', index: 1, state: 'down' });
    assert.equal(f.bridges[0].inputs.length, 0);
    await assert.rejects(f.runner.control({ control: 'brightness', value: 50 }), { code: 'CANDIDATE_CONTROL_NOT_READY' });
    release(); await tick();
    f.adapter.emitInput({ type: 'key', index: 1, state: 'up' });
    assert.equal(f.bridges[0].inputs.length, 1); assert.equal(checks, 2);
    const paired = f.events.filter(event => event.event.includes('.client'));
    assert.deepEqual(paired.map(event => event.event), ['primary.clientConnected', 'primary.clientDisconnected', 'child.clientDisconnected', 'child.clientConnected']);
    for (const event of paired) { assert.equal(event.schemaVersion, 1); assert.equal(event.generation, 1); }
    assert.ok(!JSON.stringify(paired).includes('private-host-details'));
  } finally { release?.(); await f.runner.stop(); }
});

test('physical disconnect publishes recovery and restored readiness with a fresh generation', async () => {
  const f = fixture();
  try {
    await f.runner.start();
    await f.runner.control({ control: 'brightness', value: 71 });
    f.bridges[0].output(frame('key:0', 7));
    await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
    f.adapter.disconnect();
    await waitUntil(() => f.runner.getStatus().state === 'ready' && f.runner.getStatus().generation === 2);
    const lifecycle = f.events.filter(event => ['deviceReady', 'deviceDisconnected', 'reconnecting', 'deviceReconnected'].includes(event.event));
    assert.deepEqual(lifecycle.map(event => event.event), ['deviceReady', 'deviceDisconnected', 'reconnecting', 'deviceReconnected']);
    assert.equal(lifecycle[1].generation, 1); assert.equal(lifecycle[1].code, 'ADAPTER_DISCONNECTED');
    assert.equal(lifecycle[2].attempt, 1); assert.equal(lifecycle[3].generation, 2);
    assert.equal(lifecycle[3].deviceId, f.adapter.device.id);
    assert.equal((await f.runner.getWorkerState()).brightness, 71);
    assert.equal(f.adapter.outputs.at(-1).data, frame('key:0', 7).data); assert.equal(f.adapter.outputs.at(-1).generation, 2);
    assert.equal(f.bridges.length, 1); assert.equal(f.bridges[0].startCount, 1); assert.equal(f.bridges[0].stopCount, 0);
    f.bridges[0].emit('clientConnected', { role: 'child' }); await tick();
    assert.equal(f.events.at(-1).event, 'child.clientConnected');
  } finally { await f.runner.stop(); }
  assert.equal(f.bridges[0].stopCount, 1);
  assert.equal(f.events.at(-1).event, 'stopped'); assert.equal(f.events.at(-1).generation, 2);
});

test('offline host frames coalesce on the retained bridge and resume with stable input sequence without held replay', async () => {
  const f = fixture({ config: { reconnect: { maxAttempts: 1, delayMs: 10 } } });
  let missing = false;
  const discover = f.adapter.discover.bind(f.adapter);
  f.adapter.discover = options => missing ? Promise.resolve([]) : discover(options);
  try {
    await f.runner.start(); const bridge = f.bridges[0];
    f.adapter.emitInput({ type: 'key', index: 0, state: 'down' });
    bridge.output(frame('key:0', 7)); await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
    missing = true; f.adapter.disconnect();
    await waitUntil(() => f.runner.getStatus().state === 'reconnecting' && bridge.releaseCount > 0);
    const afterRelease = bridge.inputs.length;
    assert.equal(bridge.heldKeys.size, 0); assert.deepEqual(bridge.inputs.map(event => event.state), ['down', 'up']);
    for (let byte = 10; byte < 20; byte++) bridge.output(frame('key:0', byte));
    await tick();
    assert.equal(f.runner.getStatus().counters.frameWrites, 1); assert.equal(bridge.stopCount, 0);
    missing = false;
    await waitUntil(() => f.runner.getStatus().state === 'ready' && f.runner.getStatus().counters.frameWrites === 2);
    const generation = f.runner.getStatus().generation;
    assert.ok(generation >= 2); assert.equal(f.bridges.length, 1); assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
    assert.equal(f.adapter.outputs.at(-1).data, frame('key:0', 19).data); assert.equal(f.adapter.outputs.at(-1).generation, generation);
    assert.equal(bridge.inputs.length, afterRelease); assert.equal(bridge.heldKeys.size, 0);
    f.adapter.emitInput({ type: 'key', index: 1, state: 'down' }); f.adapter.emitInput({ type: 'key', index: 1, state: 'up' });
    assert.deepEqual(bridge.inputs.map(event => event.sequence), [1, 2, 3, 4]);
    assert.deepEqual(bridge.inputs.map(event => event.generation), [1, 1, 1, 1]);
    assert.equal(bridge.inputs.filter(event => event.index === 0 && event.state === 'down').length, 1);
  } finally { missing = false; await f.runner.stop(); }
  assert.equal(f.bridges[0].stopCount, 1);
});

test('primary disconnect gates product input until an already-connected child passes fresh readiness', async () => {
  let checks = 0, release;
  const f = fixture({ readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++checks), executableDigest: 'b'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return checks <= 2 ? report : new Promise(resolve => { release = () => resolve(report); });
  } });
  try {
    await f.runner.start(); const bridge = f.bridges[0];
    bridge.emit('clientConnected', { role: 'child' }); await tick();
    f.adapter.emitInput({ type: 'key', index: 0, state: 'up' }); assert.equal(bridge.inputs.length, 1);
    bridge.emit('clientDisconnected', { role: 'primary' });
    f.adapter.emitInput({ type: 'key', index: 0, state: 'down' }); assert.equal(bridge.inputs.length, 1);
    await assert.rejects(f.runner.control({ control: 'brightness', value: 50 }), { code: 'CANDIDATE_CONTROL_NOT_READY' });
    bridge.emit('clientConnected', { role: 'primary' });
    assert.equal(checks, 3); assert.equal(bridge.childConnected, true);
    f.adapter.emitInput({ type: 'key', index: 1, state: 'down' }); assert.equal(bridge.inputs.length, 1);
    release(); await tick();
    f.adapter.emitInput({ type: 'key', index: 1, state: 'up' }); assert.equal(bridge.inputs.length, 2);
    assert.equal(f.bridges.length, 1); assert.equal(bridge.startCount, 1);
  } finally { release?.(); await f.runner.stop(); }
});

test('host pause releases the worker with the host-exit reason while its bridge stays open for restored resume', async () => {
  const f = fixture();
  const close = f.adapter.close.bind(f.adapter), closes = [];
  f.adapter.close = async options => { closes.push(options); return close(options); };
  try {
    await f.runner.start(); const bridge = f.bridges[0];
    await f.runner.control({ control: 'brightness', value: 71 });
    bridge.output(frame('key:0', 7)); await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
    assert.deepEqual(await f.runner.pauseDevice(), { status: 'paused' });
    assert.equal(f.runner.getStatus().state, 'hostPaused');
    assert.deepEqual(closes.filter(value => value?.releaseReason), [{ releaseReason: 'host-exit' }]);
    assert.equal(f.adapter.calls.filter(value => value === 'stop').length, 1);
    assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0); assert.equal(f.bridges.length, 1);
    assert.deepEqual(bridge.ports, { primary: 5343, child: 5344 });
    assert.equal(f.events.filter(event => event.event === 'deviceDisconnected').at(-1).code, 'HOST_NOT_READY');
    assert.deepEqual(await f.runner.resumeDevice(), { status: 'ready' });
    assert.equal(f.runner.getStatus().state, 'ready'); assert.equal(f.runner.getStatus().generation, 2);
    assert.equal(f.bridges.length, 1); assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
    assert.equal(f.adapter.outputs.at(-1).data, frame('key:0', 7).data); assert.equal(f.adapter.outputs.at(-1).generation, 2);
    assert.equal((await f.runner.getWorkerState()).brightness, 71);
  } finally { await f.runner.stop(); }
  assert.equal(f.bridges[0].stopCount, 1);
});

test('host restart readiness accepts interleaved saved screensaver and key redraw bursts', async () => {
  let checks = 0, approve;
  const f = fixture({ fullProfile: true, config: { screensaverMode: 'native' }, readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++checks), executableDigest: 'a'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return checks === 1 ? report : new Promise(resolve => { approve = () => resolve(report); });
  } });
  let resumed;
  try {
    await f.runner.start();
    const bridge = f.bridges[0];
    await f.runner.pauseDevice();
    resumed = f.runner.resumeDevice();
    await waitUntil(() => approve !== undefined);
    for (let update = 0; update < 100; update++) {
      bridge.emit('lcdFrameStarted');
      bridge.output({ type: 'frame', surfaceId: `key:${update % 10}`, format: 'rgb888', width: 120, height: 120,
        data: Buffer.alloc(120 * 120 * 3, update).toString('base64') });
    }
    await tick();
    assert.equal(f.runner.getStatus().state, 'starting');
    assert.equal(f.runner.getStatus().counters.frameWrites, 0);
    approve(); await resumed;
    await waitUntil(() => f.runner.getStatus().counters.frameWrites === 10);
    assert.equal(f.runner.getStatus().state, 'ready');
    assert.equal(f.runner.getStatus().generation, 2);
    assert.equal(f.adapter.controls.filter(value => value.control === 'screensaver').length, 1);
    assert.deepEqual(f.adapter.outputs.map(value => Buffer.from(value.data, 'base64')[0]), Array.from({ length: 10 }, (_, index) => 90 + index));
    assert.equal(f.bridges.length, 1);
    assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
  } finally { approve?.(); await resumed?.catch(() => {}); await f.runner.stop(); }
});

test('host pause cancels a pending USB recovery delay until an explicit resume', async () => {
  const f = fixture({ config: { reconnect: { maxAttempts: 1, delayMs: 200 } } });
  try {
    await f.runner.start(); const bridge = f.bridges[0];
    f.adapter.disconnect(); await waitUntil(() => f.runner.getStatus().state === 'reconnecting');
    assert.deepEqual(await f.runner.pauseDevice(), { status: 'paused' });
    const paused = f.runner.getStatus(), discoveries = f.adapter.calls.filter(value => value === 'discover').length;
    await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(f.runner.getStatus().state, 'hostPaused'); assert.equal(f.runner.getStatus().generation, paused.generation);
    assert.equal(f.runner.getStatus().counters.reconnectAttempts, paused.counters.reconnectAttempts);
    assert.equal(f.adapter.calls.filter(value => value === 'discover').length, discoveries);
    assert.equal(f.bridges.length, 1); assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
    assert.deepEqual(await f.runner.resumeDevice(), { status: 'ready' });
    assert.equal(f.runner.getStatus().state, 'ready'); assert.ok(f.runner.getStatus().generation > paused.generation);
    assert.equal(f.bridges.length, 1); assert.equal(bridge.startCount, 1); assert.equal(bridge.stopCount, 0);
  } finally { await f.runner.stop(); }
});

test('knob preview submits observed output without changing saved settings or reconnect intent', async () => {
  const f = fixture();
  try {
    await f.runner.start();
    await f.runner.control({ control: 'knobBrightness', value: 15 });
    const saved = readFileSync(f.config.statePath, 'utf8');
    const changed = f.events.filter(event => event.event === 'settingsChanged');
    const expected = { brightness: 100, knobBrightness: 15, followDisplayBrightness: false, sleepDuration: 0 };
    assert.deepEqual(changed.at(-1).settings, expected);
    const count = changed.length;
    changed.at(-1).settings.knobBrightness = 99;
    await f.runner.control({ control: 'knobBrightness', value: 42, persist: false });
    assert.equal((await f.runner.getWorkerState()).knobBrightness, 42);
    assert.equal(readFileSync(f.config.statePath, 'utf8'), saved);
    assert.equal(f.events.filter(event => event.event === 'settingsChanged').length, count);
    assert.ok(f.adapter.controls.every(value => !Object.hasOwn(value, 'persist')));
    await f.runner.reconnect();
    assert.equal((await f.runner.getWorkerState()).knobBrightness, 15);
    assert.deepEqual(JSON.parse(readFileSync(f.config.statePath, 'utf8')).controls, expected);
    await assert.rejects(f.runner.control({ control: 'brightness', value: 50, persist: false }), { code: 'CANDIDATE_CONTROL_INVALID' });
    await assert.rejects(f.runner.control({ control: 'knobBrightness', value: 50, persist: 'false' }), { code: 'CANDIDATE_CONTROL_INVALID' });
  } finally { await f.runner.stop(); }
});

test('host controls after a knob preview preserve the saved knob pair until an explicit knob or follow commit', async () => {
  const f = fixture({ config: { initialControls: { brightness: 100, knobBrightness: 15, followDisplayBrightness: true, sleepDuration: 0 } } });
  const saved = () => JSON.parse(readFileSync(f.config.statePath, 'utf8')).controls;
  try {
    await f.runner.start();
    await f.runner.control({ control: 'knobBrightness', value: 42, persist: false });
    assert.equal((await f.runner.getWorkerState()).knobBrightness, 42);
    assert.equal((await f.runner.getWorkerState()).followDisplayBrightness, false);
    f.bridges[0].output({ type: 'control', control: 'brightness', value: 71 });
    await waitUntil(() => f.runner.getStatus().counters.controlWrites === 2);
    assert.deepEqual(saved(), { brightness: 71, knobBrightness: 15, followDisplayBrightness: true, sleepDuration: 0 });
    f.bridges[0].output({ type: 'control', control: 'sleepDuration', value: 60 });
    await waitUntil(() => f.runner.getStatus().counters.controlWrites === 3);
    const desired = { brightness: 71, knobBrightness: 15, followDisplayBrightness: true, sleepDuration: 60 };
    assert.deepEqual(saved(), desired);
    assert.deepEqual(f.events.filter(event => event.event === 'settingsChanged').map(event => event.settings),
      [{ ...desired, sleepDuration: 0 }, desired]);
    await f.runner.reconnect();
    const restored = f.adapter.controls.filter(value => value.generation === 2);
    assert.equal(restored.find(value => value.control === 'knobBrightness').value, 15);
    assert.equal(restored.find(value => value.control === 'followDisplayBrightness').value, true);
    assert.deepEqual(saved(), desired);
    const state = await f.runner.getWorkerState();
    assert.equal(state.brightness, 71); assert.equal(state.knobBrightness, 71); assert.equal(state.followDisplayBrightness, true); assert.equal(state.sleepDuration, 60);
    await f.runner.control({ control: 'knobBrightness', value: 23 });
    assert.deepEqual(saved(), { ...desired, knobBrightness: 23, followDisplayBrightness: false });
    await f.runner.control({ control: 'knobBrightness', value: 42, persist: false });
    await f.runner.control({ control: 'followDisplayBrightness', value: true });
    assert.deepEqual(saved(), { ...desired, knobBrightness: 71 });
    assert.deepEqual(f.events.filter(event => event.event === 'settingsChanged').at(-1).settings, { ...desired, knobBrightness: 71 });
  } finally { await f.runner.stop(); }
});

test('ordinary product keeps retrying an absent device and restores saved intent when it returns', async () => {
  const f = fixture({ config: { reconnect: { maxAttempts: 1, delayMs: 1 } } });
  let discoveries = 0;
  const discover = f.adapter.discover.bind(f.adapter);
  f.adapter.discover = options => ++discoveries === 2 || discoveries === 3 ? Promise.resolve([]) : discover(options);
  const states = []; f.runner.on('state', status => states.push(status.state));
  try {
    await f.runner.start(); await f.runner.control({ control: 'brightness', value: 71 });
    f.bridges[0].output(frame('key:0', 7));
    await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
    const saved = readFileSync(f.config.statePath, 'utf8');
    f.adapter.disconnect();
    await waitUntil(() => f.runner.getStatus().state === 'ready' && f.runner.getStatus().generation >= 4);
    assert.equal(discoveries, 4); assert.equal(f.runner.getStatus().counters.reconnectAttempts, 3);
    assert.ok(!states.includes('failed'));
    assert.equal((await f.runner.getWorkerState()).brightness, 71);
    assert.equal(readFileSync(f.config.statePath, 'utf8'), saved);
    assert.equal(f.adapter.outputs.at(-1).data, frame('key:0', 7).data);
    assert.equal(f.adapter.outputs.at(-1).generation, 4);
    const recovered = f.events.filter(event => event.event === 'deviceReconnected');
    assert.equal(recovered.length, 1); assert.equal(recovered[0].generation, 4);
  } finally { await f.runner.stop(); }
});

test('product discovery requires no selection and returns only declared adapter models before stopping', async () => {
  const f = fixture();
  const devices = [f.adapter.device, { ...f.adapter.device, id: 'fake:foreign', adapterId: 'foreign-adapter' },
    { ...f.adapter.device, id: 'fake:undeclared', modelId: 'undeclared-model' }];
  f.adapter.discover = async () => devices;
  let stops = 0; const stop = f.adapter.stop.bind(f.adapter);
  f.adapter.stop = async () => { stops++; await stop(); };
  const found = await discoverProductDevices({ ...f.config, deviceId: undefined }, { selfTest: true, adapter: f.adapter });
  assert.deepEqual(found, [f.adapter.device]); assert.equal(stops, 1);
  assert.deepEqual(f.adapter.calls, ['preflight', 'start', 'stop']);
  found[0].capabilities.features.brightness.status = 'unavailable';
  assert.equal(devices[0].capabilities.features.brightness.status, 'emulated');
  await assert.rejects(discoverProductDevices({ ...f.config, deviceId: undefined, productMode: false },
    { selfTest: true, adapter: f.adapter }), { code: 'CANDIDATE_DEVICE_REQUIRED' });
  await assert.rejects(discoverProductDevices({ ...f.config, productMode: false },
    { selfTest: true, adapter: f.adapter }), { code: 'CANDIDATE_CONFIG_INVALID' });
  assert.equal(stops, 1);
});

test('product discovery stops its adapter after startup or discovery failure and rejects oversized inventories', async () => {
  for (const phase of ['preflight', 'start', 'discover']) {
    const f = fixture(); let stops = 0;
    f.adapter[phase] = async () => { throw new Error(`fixture ${phase} failure`); };
    f.adapter.stop = async () => { stops++; };
    await assert.rejects(discoverProductDevices({ ...f.config, deviceId: undefined }, { selfTest: true, adapter: f.adapter }),
      new RegExp(`fixture ${phase} failure`));
    assert.equal(stops, 1);
  }
  const f = fixture(); let stops = 0;
  f.adapter.discover = async () => Array.from({ length: 65 }, () => f.adapter.device);
  const stop = f.adapter.stop.bind(f.adapter); f.adapter.stop = async () => { stops++; await stop(); };
  await assert.rejects(discoverProductDevices({ ...f.config, deviceId: undefined }, { selfTest: true, adapter: f.adapter }),
    { code: 'INVALID_DISCOVERY' });
  assert.equal(stops, 1);
});

test('product worker queries expose bounded controls and state without private worker payloads', async () => {
  const f = fixture();
  await assert.rejects(f.runner.getProductWorkerState(), { code: 'CANDIDATE_CONTROL_NOT_READY' });
  try {
    await f.runner.start();
    const getState = f.adapter.getState.bind(f.adapter);
    f.adapter.getState = async () => ({ ...await getState(), connected: true, screensaverActive: false, screensaverReady: true, logoActive: false,
      screensaverRevision: 3, screensaverLoading: false, screensaverError: null,
      screenBrightness: 100, knobBrightnessLevel: 100, stripMode: 'panels', privateText: 'PRIVATE-LOG', data: 'PRIVATE-IMAGE',
      displayObservations: { privateVendorDetails: 'PRIVATE-VENDOR' } });
    const query = await f.runner.getProductWorkerState();
    assert.deepEqual(query.state, { brightness: 100, knobBrightness: 100, followDisplayBrightness: true, sleepDuration: 0,
      connected: true, idle: false, sleeping: false, screensaverActive: false, screensaverReady: true, logoActive: false,
      screensaverRevision: 3, screensaverLoading: false, screensaverError: null,
      screenBrightness: 100, knobBrightnessLevel: 100, stripMode: 'panels' });
    assert.ok(!JSON.stringify(query).includes('PRIVATE'));
    assert.ok(!Object.hasOwn(query.state, 'deviceId')); assert.ok(Buffer.byteLength(JSON.stringify(query)) < 2048);
    query.state.brightness = 3; query.capabilities.features.brightness.status = 'unavailable';
    assert.equal((await f.runner.getProductWorkerState()).state.brightness, 100);
    assert.equal((await f.runner.getProductWorkerState()).capabilities.features.brightness.status, 'emulated');
    f.adapter.getState = async () => ({ ...await getState(), screensaverRevision: -1, screensaverLoading: 'true', screensaverError: 'C:/PRIVATE/source.png' });
    const invalid = (await f.runner.getProductWorkerState()).state;
    for (const key of ['screensaverRevision', 'screensaverLoading', 'screensaverError']) assert.ok(!Object.hasOwn(invalid, key));
    f.adapter.getState = async () => ({ ...await getState(), brightness: 101 });
    await assert.rejects(f.runner.getProductWorkerState(), { code: 'CANDIDATE_SETTINGS_INVALID' });
  } finally { await f.runner.stop(); }
  await assert.rejects(f.runner.getProductWorkerState(), { code: 'CANDIDATE_CONTROL_NOT_READY' });
});

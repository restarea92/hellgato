import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createSession } from '../packages/hellgato/index.mjs';
import { createButtonSimulator, createNonN4Simulator, createSimulatorProfile, replayInputs, runAdapterConformance } from '../packages/hellgato/src/testing/index.mjs';
import { createN4ProMappingProfile, createMiraboxN4ProAdapter, miraboxN4ProSupport } from '../packages/hellgato/src/adapters/mirabox-n4-pro/index.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(options = {}) {
  const adapter = options.adapter ?? createButtonSimulator();
  const profile = options.profile ?? createSimulatorProfile(adapter.device);
  const calls = [];
  const bridges = [];
  const bridgeFactory = config => {
    calls.push('bridgeFactory');
    const bridge = new EventEmitter();
    bridge.config = config; bridge.inputs = []; bridge.ports = { primary: null, child: null };
    bridge.start = async () => { calls.push('bridge.start'); bridge.ports = { primary: 1, child: 2 }; };
    bridge.stop = async () => { calls.push('bridge.stop'); bridge.ports = { primary: null, child: null }; };
    bridge.sendInput = event => { bridge.inputs.push(structuredClone(event)); return true; };
    bridges.push(bridge); return bridge;
  };
  const session = createSession({ adapter, deviceId: adapter.device.id, profile, bridgeFactory, ...options });
  return { adapter, profile, calls, bridges, session };
}
const prepared = (profile, extra = {}) => ({ ready: true, targetId: profile.target.id, profileId: profile.id,
  host: { pid: 42, creationTime: '2026-10-14T00:00:00Z', executableDigest: 'a'.repeat(64) }, preparation: ['model-ready'], ...extra });
const envelope = session => ({ deviceId: session.device.id, sessionId: session.id, generation: session.generation, sequence: 1 });

test('createSession requires explicit selections and performs no discovery or port binding', () => {
  assert.throws(() => createSession(), /requires explicit/);
  const f = fixture(); assert.deepEqual(f.adapter.calls, []); assert.deepEqual(f.calls, []);
  assert.equal(f.session.state, 'idle'); assert.deepEqual(f.session.ports, { primary: null, child: null });
  assert.throws(() => createSession({ adapter: f.adapter, profile: f.profile }), /deviceId/);
});

test('button-only session orders preflight, discovery, readiness, ports and explicit open', async () => {
  const adapter = createButtonSimulator();
  const profile = createSimulatorProfile(adapter.device);
  const events = [];
  for (const name of ['preflight', 'start', 'discover', 'open', 'close', 'stop']) {
    const original = adapter[name]; adapter[name] = async (...args) => { events.push(name); return original(...args); };
  }
  const f = fixture({ adapter, profile, readiness: () => { events.push('readiness'); return prepared(profile); } });
  await f.session.start();
  assert.deepEqual(events.slice(0, 5), ['preflight', 'start', 'discover', 'readiness', 'open']);
  assert.equal(f.session.state, 'ready'); assert.equal(f.bridges.length, 1);
  adapter.emitInput({ type: 'key', index: 5, state: 'down' });
  assert.equal(f.bridges[0].inputs[0].index, 5);
  await f.session.stop();
  assert.deepEqual(events.slice(5), ['close', 'stop', 'close']);
  assert.equal(f.session.state, 'stopped');
});

test('concurrent start/stop is idempotent and restart creates a new generation', async () => {
  const f = fixture();
  await Promise.all([f.session.start(), f.session.start(), f.session.start()]);
  assert.equal(f.bridges.length, 1); assert.equal(f.adapter.calls.filter(x => x === 'open').length, 1);
  const first = f.session.generation;
  await Promise.all([f.session.stop(), f.session.stop()]);
  await f.session.start(); assert.equal(f.session.generation, first + 1);
  assert.equal(f.bridges.length, 2); await f.session.stop();
});

test('missing required host readiness fails before bridge construction', async () => {
  const f = fixture(); const profile = f.profile; profile.requirements.hostPreparation = ['model-ready'];
  const g = fixture({ adapter: f.adapter, profile });
  await assert.rejects(g.session.start(), { code: 'HOST_READINESS_REQUIRED' });
  assert.equal(g.calls.length, 0); assert.deepEqual(g.adapter.calls, ['preflight', 'start', 'discover', 'stop']);
  assert.equal(g.session.state, 'failed');
});

for (const [name, report, code] of [
  ['not ready', { ready: false }, 'HOST_NOT_READY'],
  ['wrong target', { targetId: 'another-target' }, 'HOST_IDENTITY_MISMATCH'],
  ['wrong profile', { profileId: 'another-profile' }, 'HOST_IDENTITY_MISMATCH'],
  ['missing identity', { host: undefined }, 'HOST_IDENTITY_REQUIRED'],
  ['missing preparation', { preparation: [] }, 'HOST_NOT_READY'],
]) test(`host preflight rejects ${name} before ports`, async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device); profile.requirements.hostPreparation = ['model-ready'];
  const f = fixture({ adapter, profile, readiness: () => prepared(profile, report) });
  await assert.rejects(f.session.start(), { code }); assert.equal(f.bridges.length, 0);
});

test('static adapter preflight failure does not start worker or bridge', async () => {
  const adapter = createButtonSimulator(); adapter.preflight = () => { throw new Error('digest mismatch'); };
  const f = fixture({ adapter }); await assert.rejects(f.session.start(), /digest mismatch/);
  assert.deepEqual(adapter.calls, []); assert.equal(f.bridges.length, 0);
});

test('absent and duplicate device IDs never select an arbitrary device', async () => {
  const a = createButtonSimulator(); a.discover = async () => [];
  const f = fixture({ adapter: a }); await assert.rejects(f.session.start(), { code: 'DEVICE_NOT_FOUND' }); assert.equal(f.bridges.length, 0);
  const b = createButtonSimulator(); b.discover = async () => [b.device, b.device];
  const g = fixture({ adapter: b }); await assert.rejects(g.session.start(), { code: 'AMBIGUOUS_DEVICE' }); assert.equal(g.bridges.length, 0);
});

test('mapping must explicitly cover physical controls, allowing null omissions', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device);
  profile.controls.keys.pop();
  const f = fixture({ adapter, profile }); await assert.rejects(f.session.start(), { code: 'INCOMPLETE_MAPPING' });
  profile.controls.keys = [1, null, 0, 3, 4, 5];
  const g = fixture({ adapter, profile }); await g.session.start();
  adapter.emitInput({ type: 'key', index: 1, state: 'down' }); adapter.emitInput({ type: 'key', index: 0, state: 'down' });
  assert.equal(g.bridges[0].inputs.length, 1); assert.equal(g.bridges[0].inputs[0].index, 1); await g.session.stop();
});

test('unverified capability cannot satisfy mapping requirement', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device);
  profile.requirements.capabilities = ['haptics'];
  const f = fixture({ adapter, profile }); await assert.rejects(f.session.start(), { code: 'CAPABILITY_MISMATCH' });
  assert.equal(f.bridges.length, 0);
});

test('partial bridge start and adapter open failures roll back all acquired resources', async () => {
  const f = fixture(); f.adapter.open = async () => { throw new Error('USB busy'); };
  await assert.rejects(f.session.start(), /USB busy/);
  assert.deepEqual(f.calls, ['bridgeFactory', 'bridge.start', 'bridge.stop']);
  assert.equal(f.adapter.calls.at(-1), 'stop'); assert.equal(f.session.state, 'failed');
  const adapter = createButtonSimulator(); let stopped = 0;
  const bridge = new EventEmitter(); Object.assign(bridge, { sendInput: () => false, start: async () => { throw new Error('port busy'); }, stop: async () => { stopped++; } });
  const g = fixture({ adapter, bridgeFactory: () => bridge }); await assert.rejects(g.session.start(), /port busy/);
  assert.equal(stopped, 1); assert.ok(!adapter.calls.includes('open')); assert.equal(adapter.calls.at(-1), 'stop');
});

test('stop cancels a pending discovery without ever opening ports', async () => {
  const adapter = createButtonSimulator(); let began;
  const started = new Promise(resolve => { began = resolve; });
  adapter.discover = ({ signal }) => new Promise((_, reject) => { began(); signal.addEventListener('abort', () => reject(signal.reason), { once: true }); });
  const f = fixture({ adapter }); const promise = f.session.start(); await started;
  const stopping = f.session.stop(); await assert.rejects(promise, { code: 'ABORTED' }); await stopping;
  assert.equal(f.bridges.length, 0); assert.equal(f.session.state, 'stopped');
});

test('stale generations, foreign IDs and decreasing sequence numbers cannot replay input', async () => {
  const f = fixture(); await f.session.start();
  const event = { ...envelope(f.session), type: 'key', index: 0, state: 'down' };
  f.adapter.injectInput({ ...event, generation: 0 }); f.adapter.injectInput({ ...event, deviceId: 'other' });
  f.adapter.injectInput({ ...event, sessionId: 'other' }); f.adapter.injectInput(event); f.adapter.injectInput(event);
  f.adapter.injectInput({ ...event, sequence: 0 });
  assert.equal(f.bridges[0].inputs.length, 1); await f.session.stop();
});

test('invalid current-generation input fails closed and releases forwarded presses', async () => {
  const f = fixture(); await f.session.start();
  f.adapter.emitInput({ type: 'key', index: 0, state: 'down' });
  f.adapter.injectInput({ ...envelope(f.session), sequence: 2, type: 'key', index: 99, state: 'down' });
  await tick(); assert.equal(f.session.state, 'failed');
  assert.deepEqual(f.bridges[0].inputs.map(input => input.state), ['down', 'up']);
});

test('non-N4 simulator maps dial and touch with its own dimensions', async () => {
  const adapter = createNonN4Simulator(); const profile = createSimulatorProfile(adapter.device); profile.touch.rotation = 180;
  const f = fixture({ adapter, profile }); await f.session.start();
  adapter.emitInput({ type: 'dial', kind: 'rotate', index: 1, delta: -1 });
  adapter.emitInput({ type: 'touch', gesture: 'swipe', x: 0, y: 0, endX: 319, endY: 59 });
  assert.deepEqual(f.bridges[0].inputs.map(x => x.type), ['dial', 'touch']);
  const touch = f.bridges[0].inputs[1]; assert.equal(touch.x, 799); assert.equal(touch.y, 99); assert.equal(touch.endX, 0); assert.equal(touch.endY, 0);
  await f.session.stop();
});

test('reconnect refreshes readiness and drops input from previous generation', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device); let inspections = 0;
  const f = fixture({ adapter, profile, readiness: () => { inspections++; return prepared(profile); } });
  await f.session.start(); const old = { ...envelope(f.session), type: 'key', index: 0, state: 'down' };
  await f.session.reconnect(); assert.equal(inspections, 2);
  adapter.injectInput(old); assert.equal(f.bridges[1].inputs.length, 0); await f.session.stop();
});

test('host reconnect gates input until fresh readiness resolves', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device); let release; let inspections = 0;
  const f = fixture({ adapter, profile, readiness: () => ++inspections === 1 ? prepared(profile) : new Promise(resolve => { release = resolve; }) });
  await f.session.start(); f.bridges[0].emit('clientDisconnected'); f.bridges[0].emit('clientConnected');
  adapter.emitInput({ type: 'key', index: 0, state: 'down' }); assert.equal(f.bridges[0].inputs.length, 0);
  release(prepared(profile)); await tick(); adapter.emitInput({ type: 'key', index: 1, state: 'down' });
  assert.equal(f.bridges[0].inputs.length, 1); await f.session.stop();
});

test('adapter disconnect fails safely and explicit reconnect works', async () => {
  const f = fixture(); await f.session.start(); f.adapter.disconnect(); await tick();
  assert.equal(f.session.state, 'failed'); await f.session.reconnect(); assert.equal(f.session.state, 'ready'); await f.session.stop();
});

test('control output is serialized in order and unsupported operations fail closed', async () => {
  const f = fixture(); await f.session.start();
  for (const [sequence, control] of [[1, 'sleep'], [2, 'wake']]) f.bridges[0].emit('output', { ...envelope(f.session), sequence, type: 'control', control });
  await tick(); assert.deepEqual(f.adapter.controls.map(item => item.control), ['sleep', 'wake']);
  f.bridges[0].emit('output', { ...envelope(f.session), sequence: 3, type: 'control', control: 'firmwareWrite' });
  await tick(); assert.equal(f.session.state, 'failed');
});

test('bounded output queue stops instead of silently dropping partial updates', async () => {
  const f = fixture({ maxOutputQueue: 1 }); let release;
  f.adapter.setControl = () => new Promise(resolve => { release = resolve; });
  await f.session.start();
  for (const sequence of [1, 2, 3]) f.bridges[0].emit('output', { ...envelope(f.session), sequence, type: 'control', control: 'sleep' });
  release({ status: 'applied' }); await tick(); assert.equal(f.session.state, 'failed'); assert.equal(f.session.status.lastError.code, 'OUTPUT_OVERFLOW');
});

test('replay validates entire fixture before delivery and retains generation unless explicitly remapped', async () => {
  const event = { deviceId: 'fixture', generation: 1, sequence: 1, type: 'key', index: 0, state: 'down' };
  const events = [];
  await assert.rejects(replayInputs([event, { broken: true }], x => events.push(x))); assert.equal(events.length, 0);
  const result = await replayInputs([event], x => events.push(x), { generation: 2 });
  assert.equal(events[0].generation, 2); assert.equal(result.physicalHardwareVerified, false);
});

test('button and non-N4 adapter conformance clean up and do not claim physical verification', async () => {
  for (const adapter of [createButtonSimulator(), createNonN4Simulator()]) {
    const result = await runAdapterConformance(adapter, { deviceId: adapter.device.id });
    assert.equal(result.physicalHardwareVerified, false); assert.ok(result.checks.includes('explicit-open'));
    assert.equal(adapter.calls.at(-1), 'stop');
  }
});

test('N4 binding is explicit, pulse-only and marked hardware-gated', () => {
  assert.throws(() => createN4ProMappingProfile(), /virtualSerial/);
  const profile = createN4ProMappingProfile({ virtualSerial: 'EXPLICITDOCK', childSerial: 'EXPLICITCHILD' });
  assert.equal(profile.controls.keys.length, 10); assert.equal(profile.controls.dials.length, 4);
  assert.deepEqual(profile.requirements.hostPreparation, ['streamdeck-plus-ten-key']);
  assert.equal(miraboxN4ProSupport.physicalHardwareVerified, false);
  assert.throws(() => createMiraboxN4ProAdapter({ command: '/usr/bin/python3' }), /manifest/);
});

test('bridge-start output waits for successful device open and then drains without a later frame', async () => {
  const adapter = createButtonSimulator(); let openRelease; let opening;
  const began = new Promise(resolve => { opening = resolve; }); const originalOpen = adapter.open;
  adapter.open = async (...args) => { opening(); await new Promise(resolve => { openRelease = resolve; }); return originalOpen(...args); };
  let bridge;
  const f = fixture({ adapter, bridgeFactory: config => {
    bridge = new EventEmitter(); bridge.sendInput = () => true; bridge.stop = async () => {};
    // Emit the public output envelope, never the bridge construction options.
    bridge.start = async () => bridge.emit('output', { deviceId: config.device.id, sessionId: config.sessionId, generation: config.generation, sequence: 1, type: 'control', control: 'brightness', value: 25 });
    return bridge;
  } });
  const starting = f.session.start(); await began;
  assert.equal(adapter.controls.length, 0); openRelease(); await starting; await tick();
  assert.deepEqual(adapter.controls.map(x => x.value), [25]); await f.session.stop();
});

test('stale readiness result after disconnect never authorizes input or output', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device); let release; let inspections = 0;
  const f = fixture({ adapter, profile, readiness: () => ++inspections === 1 ? prepared(profile) : new Promise(resolve => { release = resolve; }) });
  await f.session.start(); const bridge = f.bridges[0]; bridge.emit('clientConnected'); bridge.emit('clientDisconnected');
  release(prepared(profile)); await tick();
  adapter.emitInput({ type: 'key', index: 0, state: 'down' });
  bridge.emit('output', { ...envelope(f.session), type: 'control', control: 'brightness', value: 50 });
  await tick(); assert.equal(bridge.inputs.length, 0); assert.equal(adapter.controls.length, 0);
  assert.equal(f.session.status.host, 'disconnected'); await f.session.stop();
});

test('only latest connection readiness can authorize its queued output', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device); const approvals = []; let inspections = 0;
  const f = fixture({ adapter, profile, readiness: () => ++inspections === 1 ? prepared(profile) : new Promise(resolve => approvals.push(resolve)) });
  await f.session.start(); const bridge = f.bridges[0];
  bridge.emit('clientConnected'); bridge.emit('clientDisconnected'); bridge.emit('clientConnected');
  bridge.emit('output', { ...envelope(f.session), type: 'control', control: 'brightness', value: 50 });
  approvals[0](prepared(profile)); await tick(); assert.equal(adapter.controls.length, 0);
  approvals[1](prepared(profile)); await tick(); assert.equal(adapter.controls.length, 1); await f.session.stop();
});

test('same adapter cannot be owned by two active sessions', async () => {
  const adapter = createButtonSimulator(); const a = fixture({ adapter }); const b = fixture({ adapter });
  await a.session.start(); await assert.rejects(b.session.start(), { code: 'ADAPTER_BUSY' });
  assert.equal(a.session.state, 'ready'); assert.ok(!adapter.calls.includes('stop'));
  await a.session.stop(); await b.session.start(); await b.session.stop();
});

test('scoped controls use the same display mapping as frames', async () => {
  const adapter = createButtonSimulator(); const profile = createSimulatorProfile(adapter.device);
  profile.displays[0].target = 'key:1'; profile.displays[1].target = 'key:0';
  const f = fixture({ adapter, profile }); await f.session.start();
  f.bridges[0].emit('output', { ...envelope(f.session), type: 'control', control: 'fill', color: [1, 2, 3], surfaceId: 'key:0' });
  await tick(); assert.equal(adapter.controls[0].surfaceId, 'key:1'); await f.session.stop();
});

test('unsupported controls never reach adapter even if payload is schema-valid', async () => {
  const base = createButtonSimulator(); const device = base.device; delete device.capabilities.features.brightness;
  const adapter = createButtonSimulator({ device }); const f = fixture({ adapter }); await f.session.start();
  f.bridges[0].emit('output', { ...envelope(f.session), type: 'control', control: 'brightness', value: 50 });
  await tick(); assert.equal(adapter.controls.length, 0); assert.equal(f.session.status.lastError.code, 'UNSUPPORTED_CONTROL');
});

test('cleanup failure is reported and retry releases resources before another start', async () => {
  const f = fixture(); const stop = f.adapter.stop; let failed = false;
  f.adapter.stop = async () => { if (!failed) { failed = true; throw new Error('worker still running'); } return stop(); };
  await f.session.start(); await assert.rejects(f.session.stop(), /cleanup failed/); assert.equal(f.session.state, 'failed');
  await f.session.stop(); assert.equal(f.session.state, 'stopped'); await f.session.start(); await f.session.stop();
});

test('required-only adapter retains ownership after close failure without optional stop', async () => {
  const adapter = createButtonSimulator(); const start = adapter.start; await start();
  delete adapter.start; delete adapter.stop;
  const close = adapter.close; let failed = false;
  adapter.close = async () => { if (!failed) { failed = true; throw new Error('handle still held'); } return close(); };
  const a = fixture({ adapter }); const b = fixture({ adapter });
  await a.session.start(); await assert.rejects(a.session.stop(), /cleanup failed/);
  await assert.rejects(b.session.start(), { code: 'ADAPTER_BUSY' });
  await a.session.stop(); await b.session.start(); await b.session.stop();
});

test('actual core starts both simulator profiles with ephemeral ports', async () => {
  for (const adapter of [createButtonSimulator(), createNonN4Simulator()]) {
    const session = createSession({ adapter, deviceId: adapter.device.id, profile: createSimulatorProfile(adapter.device), port: 0, childPort: 0 });
    await session.start(); assert.ok(session.ports.primary > 0); assert.ok(session.ports.child > 0);
    await session.stop(); assert.deepEqual(session.ports, { primary: null, child: null });
  }
});

test('invalid host output during bridge startup rejects start and does not open hardware', async () => {
  const adapter = createButtonSimulator();
  const session = createSession({ adapter, deviceId: adapter.device.id, profile: createSimulatorProfile(adapter.device), bridgeFactory: config => {
    const bridge = new EventEmitter(); bridge.sendInput = () => false; bridge.stop = async () => {};
    bridge.start = async () => bridge.emit('output', { deviceId: config.device.id, generation: config.generation, sequence: 1, type: 'control', control: 'firmwareWrite' });
    return bridge;
  } });
  await assert.rejects(session.start()); assert.ok(!adapter.calls.includes('open')); assert.equal(session.state, 'failed');
});

test('touch mapping cannot target a disabled host surface', async () => {
  const adapter = createNonN4Simulator(); const profile = createSimulatorProfile(adapter.device);
  profile.target.geometry.touchWidth = 0; profile.target.geometry.touchHeight = 0;
  const f = fixture({ adapter, profile }); await assert.rejects(f.session.start(), { code: 'UNSUPPORTED_MAPPING' });
  assert.equal(f.bridges.length, 0);
});

for (const terminal of ['disconnected', 'failed', 'crashed']) test(`adapter ${terminal} during open rejects startup and cleans all resources`, async () => {
  const adapter = createButtonSimulator(); const open = adapter.open; let reportState;
  const subscribe = adapter.onState;
  adapter.onState = callback => { reportState = callback; return subscribe(callback); };
  adapter.open = async (...args) => {
    await open(...args);
    reportState({ state: terminal, deviceId: adapter.device.id, generation: args[1].generation });
  };
  const f = fixture({ adapter });
  await assert.rejects(f.session.start(), { code: 'ADAPTER_DISCONNECTED' });
  assert.equal(f.session.state, 'failed');
  assert.deepEqual(f.calls, ['bridgeFactory', 'bridge.start', 'bridge.stop']);
  assert.ok(adapter.calls.includes('close')); assert.ok(adapter.calls.includes('stop'));
});

test('host output received after disconnect cannot replay on the next connection', async () => {
  const f = fixture(); await f.session.start(); const bridge = f.bridges[0];
  bridge.emit('clientDisconnected');
  bridge.emit('output', { ...envelope(f.session), type: 'control', control: 'brightness', value: 17 });
  bridge.emit('clientConnected'); await tick();
  assert.deepEqual(f.adapter.controls, []);
  bridge.emit('output', { ...envelope(f.session), sequence: 2, type: 'control', control: 'brightness', value: 42 });
  await tick(); assert.deepEqual(f.adapter.controls.map(output => output.value), [42]);
  await f.session.stop();
});

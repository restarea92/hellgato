import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter, once } from 'node:events';
import { mkdtempSync, mkdirSync, cpSync, readFileSync, writeFileSync, readdirSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('..', import.meta.url));
const temp = mkdtempSync(join(tmpdir(), 'hellgato-candidate-'));
after(() => rmSync(temp, { force: true, recursive: true }));
const runtime = join(temp, 'sdk'); mkdirSync(runtime);
for (const [source, destination] of [['packages/core', 'node_modules/@hellgato/core'], ['packages/hellgato', 'node_modules/hellgato']]) {
  const target = join(runtime, destination); mkdirSync(target, { recursive: true });
  for (const name of ['package.json', 'index.mjs', 'src', 'dist', 'schemas']) if (existsSync(join(root, source, name))) cpSync(join(root, source, name), join(target, name), { recursive: true });
}
for (const name of readdirSync(join(root, 'apps/stream-dog/node')).filter(name => /^candidate.*\.mjs$/.test(name))) cpSync(join(root, 'apps/stream-dog/node', name), join(runtime, name));
const load = name => import(pathToFileURL(join(runtime, name)));
const { createCandidateRunner, createCandidateProfile, createDisplayJournal } = await load('candidate-session.mjs');
const { validateCandidateConfig, loadCandidateManifest, createCandidateDiagnostics, loadCandidateSettings, saveCandidateSettings, diagnosticCode } = await load('candidate-config.mjs');
const { createCandidateReadiness } = await load('candidate-readiness.mjs');
const { createDurationHistogram, DURATION_BUCKETS_MS } = await load('candidate-observability.mjs');
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
    reconnect: { maxAttempts: 2, delayMs: 1 }, ...extra };
}
function fixture(extra = {}) {
  const device = { id: 'fake:buttons-dial', adapterId: 'hellgato.fake-sidecar', modelId: 'buttons-dial', firmware: 'simulation',
    capabilities: { keys: 2, dials: 1, touch: null, dialPress: 'native', dialRelease: 'native',
      displays: [{ id: 'key:0', width: 4, height: 4, format: 'rgb888', partial: true }, { id: 'key:1', width: 4, height: 4, format: 'rgb888', partial: true }],
      features: Object.fromEntries(['brightness', 'knobBrightness', 'followDisplayBrightness', 'sleepDuration', 'clear', 'fill', 'sleep', 'wake', 'screensaver'].map(key => [key, { status: 'emulated' }])) } };
  const adapter = createSimulator({ device });
  // The real worker exposes acknowledged desired controls, distinct from physical
  // readback. Give the injected simulator that same minimal state contract.
  let settings = { brightness: 100, knobBrightness: 100, followDisplayBrightness: true, sleepDuration: 0, idle: false, sleeping: false };
  const setControl = adapter.setControl.bind(adapter), getState = adapter.getState.bind(adapter), open = adapter.open.bind(adapter);
  adapter.open = async (...args) => { settings = { brightness: 100, knobBrightness: 100, followDisplayBrightness: true, sleepDuration: 0, idle: false, sleeping: false }; return open(...args); };
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
    return { ...result, idle: settings.idle, sleeping: settings.sleeping, screensaverActive: false, logoActive: false,
      ...(['clear', 'fill'].includes(value.control) ? { normalStateEffect: value.surfaceId ? 'surface' : 'replace-all' } : {}) };
  };
  adapter.getState = async () => ({ ...await getState(), ...settings, knobBrightness: settings.followDisplayBrightness ? settings.brightness : settings.knobBrightness });
  const bridges = []; let checks = 0;
  const value = config(extra.config);
  const readiness = async ({ profile }) => { checks++; return { ready: true, targetId: profile.target.id, profileId: profile.id,
    host: { pid: 12, creationTime: 'fresh', executableDigest: 'a'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] }; };
  const runner = createCandidateRunner(value, { selfTest: true, adapter, readiness: extra.readiness ?? readiness,
    ...(extra.record ? { record: extra.record } : {}), bridgeFactory: options => {
    const bridge = new EventEmitter(); bridge.options = options; bridge.inputs = []; bridge.sequence = 0;
    bridge.start = async () => {}; bridge.stop = async () => {};
    bridge.sendInput = event => { bridge.inputs.push(event); return true; };
    bridge.output = payload => bridge.emit('output', { ...payload, deviceId: device.id, sessionId: options.sessionId,
      generation: options.generation, sequence: ++bridge.sequence });
    bridges.push(bridge); return bridge;
  } });
  return { runner, adapter, bridges, config: value, get checks() { return checks; } };
}

test('candidate requires explicit selection and preserves independent existing dock/child/MAC', () => {
  const value = config();
  for (const field of ['deviceId', 'identity', 'runtime', 'readiness', 'statePath']) assert.throws(() => validateCandidateConfig({ ...value, [field]: undefined }));
  assert.throws(() => validateCandidateConfig({ ...value, runtime: { ...value.runtime, args: ['--backend=fake'] } }), { code: 'CANDIDATE_BACKEND_RESERVED' });
  const profile = createCandidateProfile(value.identity);
  assert.equal(profile.target.config.serialNumber, 'HGDOCK000042'); assert.equal(profile.target.config.childSerialNumber, 'HGMOCK000099');
  assert.deepEqual(profile.target.config.macAddress, [2, 0, 0, 0, 0, 42]);
});

test('candidate construction starts no worker or bridge and stop is idempotent', async () => {
  const f = fixture(); assert.deepEqual(f.adapter.calls, []); assert.equal(f.bridges.length, 0);
  await f.runner.start(); assert.equal(f.runner.getStatus().state, 'ready');
  await Promise.all([f.runner.stop(), f.runner.stop()]); assert.equal(f.runner.getStatus().counters.cleanStops, 1);
});

test('observations use bounded histogram storage, explicit quantile bounds and process-only resources', async () => {
  const histogram = createDurationHistogram();
  for (const value of [0, 0.2, 1, 10, 60001]) histogram.observe(value);
  histogram.observe(NaN); histogram.observe(-1);
  const report = histogram.snapshot();
  assert.equal(report.count, 5); assert.equal(report.p50, 1); assert.equal(report.p95, 60001);
  assert.equal(report.buckets.length, DURATION_BUCKETS_MS.length + 1);
  assert.equal(report.buckets.reduce((sum, count) => sum + count, 0), 5);
  const f = fixture(); await f.runner.start();
  f.adapter.emitInput({ type: 'key', index: 0, state: 'down' });
  await f.runner.control({ control: 'brightness', value: 51 });
  const snapshot = f.runner.getStatus();
  assert.equal(snapshot.observations.nodeInputForwardMs.count, 1);
  assert.equal(snapshot.observations.controlSubmitMs.count, 2);
  assert.equal(snapshot.observations.queue.peakPendingConsoleControls, 1);
  assert.equal(snapshot.observations.quantilesAreUpperBounds, true);
  assert.ok(snapshot.observations.resources.rssBytes > 0);
  assert.ok(snapshot.observations.resources.cpuUserMs >= 0);
  assert.ok(Buffer.byteLength(JSON.stringify(snapshot)) < 8192);
  assert.ok(!JSON.stringify(snapshot).includes('HGDOCK'));
  await f.runner.stop();
});

test('reconnect restores acknowledged settings and frames with fresh sequence and no input replay', async () => {
  const f = fixture(); await f.runner.start();
  const data = Buffer.alloc(48, 120).toString('base64');
  f.bridges[0].output({ type: 'frame', surfaceId: 'key:0', format: 'rgb888', width: 4, height: 4, data });
  f.bridges[0].output({ type: 'control', control: 'brightness', value: 25 });
  f.bridges[0].output({ type: 'control', control: 'sleepDuration', value: 60 });
  await waitUntil(() => f.runner.getStatus().counters.controlWrites === 2);
  const old = { deviceId: f.adapter.device.id, sessionId: f.bridges[0].options.sessionId, generation: 1, sequence: 10, type: 'key', index: 0, state: 'down' };
  f.adapter.injectInput(old);
  await f.runner.reconnect();
  assert.equal(f.checks, 2); assert.equal(f.bridges[1].options.sleepDuration, 60);
  assert.equal(f.adapter.outputs.at(-1).data, data); assert.equal(f.adapter.outputs.at(-1).generation, 2);
  const restored = [...f.adapter.controls, ...f.adapter.outputs].filter(event => event.generation === 2).sort((a, b) => a.sequence - b.sequence);
  assert.deepEqual(restored.map(event => event.sequence), [1, 2, 3, 4, 5, 6]);
  f.adapter.injectInput(old); assert.equal(f.bridges[1].inputs.length, 0);
  const saved = readFileSync(f.config.statePath, 'utf8'); assert.ok(!saved.includes('HGDOCK')); assert.ok(!saved.includes(data));
  assert.equal(JSON.parse(saved).controls.brightness, 25);
  assert.deepEqual(f.runner.getStatus().observations.display.surfaces['key:0'],
    { received: 1, coalesced: 0, submitted: 1, failed: 0, restored: 1 });
  await f.runner.stop();
});

test('child host reconnect runs a fresh inspection and gates input until it approves', async () => {
  let calls = 0, release;
  const f = fixture({ readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++calls), executableDigest: 'b'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return calls === 1 ? report : new Promise(resolve => { release = () => resolve(report); });
  } });
  await f.runner.start(); f.bridges[0].emit('clientDisconnected', { role: 'child' }); f.bridges[0].emit('clientConnected', { role: 'child' });
  f.adapter.emitInput({ type: 'key', index: 0, state: 'down' }); assert.equal(f.bridges[0].inputs.length, 0);
  await assert.rejects(f.runner.control({ control: 'brightness', value: 50 }), { code: 'CANDIDATE_CONTROL_NOT_READY' });
  release(); await tick(); f.adapter.emitInput({ type: 'key', index: 1, state: 'down' }); assert.equal(f.bridges[0].inputs.length, 1);
  assert.equal(calls, 2); await f.runner.stop();
});

test('product console controls share ordered SDK validation and acknowledged settings persistence', async () => {
  const f = fixture(); await f.runner.start();
  f.bridges[0].output({ type: 'control', control: 'brightness', value: 40 });
  const result = await f.runner.control({ control: 'brightness', value: 71 });
  assert.equal(result.status, 'applied');
  assert.deepEqual(f.adapter.controls.map(event => event.sequence), [1, 2, 3]);
  assert.equal(JSON.parse(readFileSync(f.config.statePath, 'utf8')).controls.brightness, 71);
  for (const control of [{ control: 'brightness', value: 101 }, { control: 'firmwareWrite' }, { control: 'sleep', path: '/private/file' }]) {
    await assert.rejects(f.runner.control(control), { code: 'CANDIDATE_CONTROL_INVALID' });
  }
  assert.equal(f.runner.getStatus().state, 'ready'); await f.runner.stop();
});

test('pairing bursts stay bounded during fresh inspection and submit only the latest full surface', async () => {
  let calls = 0, release;
  const f = fixture({ readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++calls), executableDigest: 'b'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return calls === 1 ? report : new Promise(resolve => { release = () => resolve(report); });
  } });
  await f.runner.start();
  f.bridges[0].emit('clientConnected', { role: 'child' });
  for (let index = 0; index < 100; index++) {
    f.bridges[0].output({ type: 'frame', surfaceId: 'key:0', format: 'rgb888', width: 4, height: 4,
      data: Buffer.alloc(48, index).toString('base64') });
  }
  await tick();
  assert.equal(f.adapter.outputs.length, 0);
  assert.equal(f.runner.getStatus().state, 'ready');
  release();
  await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
  assert.equal(f.adapter.outputs.length, 1);
  assert.equal(f.adapter.outputs[0].data, Buffer.alloc(48, 99).toString('base64'));
  assert.equal(f.runner.getStatus().counters.readinessChecks, 2);
  assert.deepEqual(f.runner.getStatus().observations.display.surfaces['key:0'],
    { received: 100, coalesced: 99, submitted: 1, failed: 0, restored: 0 });
  await f.runner.stop();
});

test('unmergeable pairing output fails closed and releases the worker instead of dropping controls', async () => {
  let calls = 0;
  const f = fixture({ readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++calls), executableDigest: 'b'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return calls === 1 ? report : new Promise(() => {});
  } });
  await f.runner.start();
  f.bridges[0].emit('clientConnected', { role: 'child' });
  for (let index = 0; index < 33; index++) f.bridges[0].output({ type: 'control', control: 'brightness', value: index });
  await waitUntil(() => f.runner.getStatus().state === 'failed');
  assert.equal(f.runner.getStatus().code, 'OUTPUT_OVERFLOW');
  assert.equal(f.runner.getStatus().counters.controlWrites, 0);
  assert.ok(f.adapter.calls.some(call => call === 'stop'));
  await f.runner.stop();
});

test('a superseded host inspection cannot release output belonging to the next child connection', async () => {
  let calls = 0;
  const approvals = [];
  const f = fixture({ readiness: ({ profile }) => {
    const report = { ready: true, targetId: profile.target.id, profileId: profile.id,
      host: { pid: 12, creationTime: String(++calls), executableDigest: 'b'.repeat(64) }, preparation: ['streamdeck-plus-ten-key'] };
    return calls === 1 ? report : new Promise(resolve => { approvals.push(() => resolve(report)); });
  } });
  await f.runner.start();
  f.bridges[0].emit('clientConnected', { role: 'child' });
  f.bridges[0].output({ type: 'frame', surfaceId: 'key:0', format: 'rgb888', width: 4, height: 4,
    data: Buffer.alloc(48, 1).toString('base64') });
  f.bridges[0].emit('clientConnected', { role: 'child' });
  f.bridges[0].output({ type: 'frame', surfaceId: 'key:0', format: 'rgb888', width: 4, height: 4,
    data: Buffer.alloc(48, 2).toString('base64') });
  approvals[0](); await tick();
  assert.equal(f.adapter.outputs.length, 0);
  approvals[1]();
  await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
  assert.equal(f.adapter.outputs.length, 1);
  assert.equal(f.adapter.outputs[0].data, Buffer.alloc(48, 2).toString('base64'));
  assert.equal(f.runner.getStatus().counters.readinessChecks, 3);
  await f.runner.stop();
});

test('startup output waits for device restoration instead of consuming the submission timeout', async () => {
  const f = fixture();
  const open = f.adapter.open.bind(f.adapter);
  let release;
  f.adapter.open = async (...args) => {
    f.bridges[0].emit('clientConnected', { role: 'child' });
    f.bridges[0].output({ type: 'frame', surfaceId: 'key:0', format: 'rgb888', width: 4, height: 4,
      data: Buffer.alloc(48, 5).toString('base64') });
    await new Promise(resolve => { release = resolve; });
    return open(...args);
  };
  const started = f.runner.start();
  await waitUntil(() => release !== undefined);
  await new Promise(resolve => setTimeout(resolve, 7100));
  assert.equal(f.runner.getStatus().state, 'starting');
  assert.equal(f.adapter.outputs.length, 0);
  release(); await started;
  await waitUntil(() => f.runner.getStatus().counters.frameWrites === 1);
  assert.equal(f.runner.getStatus().state, 'ready');
  await f.runner.stop();
});

test('persisted intent keeps normal brightness through idle fades and captures knob follow changes', async () => {
  const f = fixture(); await f.runner.start();
  await f.runner.control({ control: 'brightness', value: 70 });
  await f.runner.control({ control: 'brightness', value: 0 });
  await f.runner.control({ control: 'brightness', value: 10 });
  assert.equal(JSON.parse(readFileSync(f.config.statePath, 'utf8')).controls.brightness, 70);
  await f.runner.control({ control: 'wake' });
  await f.runner.control({ control: 'knobBrightness', value: 15 });
  assert.equal(JSON.parse(readFileSync(f.config.statePath, 'utf8')).controls.followDisplayBrightness, false);
  await f.runner.reconnect();
  const state = await f.runner.getWorkerState(); assert.equal(state.brightness, 70); assert.equal(state.knobBrightness, 15); assert.equal(state.followDisplayBrightness, false);
  await f.runner.stop();
});

test('saved-source policy forwards saver control and suppresses host LCD only in native mode', async () => {
  // The SDK schema is built before the full suite, including the saver control.
  const f = fixture({ config: { screensaverMode: 'native' } }); await f.runner.start();
  f.bridges[0].emit('lcdFrameStarted');
  f.bridges[0].output({ type: 'frame', surfaceId: 'lcd', format: 'jpeg', width: 800, height: 480, data: 'not-a-real-image' });
  await waitUntil(() => f.runner.getStatus().counters.controlWrites === 1 || f.runner.getStatus().state === 'failed');
  assert.equal(f.runner.getStatus().state, 'ready'); assert.equal(f.adapter.controls.at(-1).control, 'screensaver');
  assert.equal(f.adapter.outputs.length, 0); await f.runner.stop();
});

test('automatic recovery has a total bounded budget across repeated successful recoveries', async () => {
  const f = fixture(); await f.runner.start();
  f.adapter.disconnect(); await waitUntil(() => f.runner.getStatus().generation === 2 && f.runner.getStatus().state === 'ready');
  f.adapter.disconnect(); await waitUntil(() => f.runner.getStatus().generation === 3 && f.runner.getStatus().state === 'ready');
  f.adapter.disconnect(); await f.runner.completed;
  assert.equal(f.runner.getStatus().state, 'failed'); assert.equal(f.runner.getStatus().counters.reconnectAttempts, 2);
  await f.runner.stop();
});

test('exhausted reconnect settles failed completion even when final cleanup rejects', async () => {
  const f = fixture({ config: { reconnect: { maxAttempts: 0, delayMs: 1 } } }); await f.runner.start();
  let cleanupAttempts = 0;
  f.bridges[0].stop = async () => { cleanupAttempts++; throw new Error('private cleanup details'); };
  f.adapter.disconnect();
  let timer;
  const report = await Promise.race([f.runner.completed, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Completion hung')), 1000); })]); clearTimeout(timer);
  assert.equal(report.state, 'failed'); assert.equal(report.code, 'CLEANUP_FAILED');
  assert.equal(cleanupAttempts, 2);
  assert.ok(!JSON.stringify(report).includes('private cleanup details'));
  await assert.rejects(f.runner.stop());
  assert.equal(cleanupAttempts, 3);
});

test('diagnostic storage failure closes resources and settles completion without callback throws', async () => {
  let records = 0;
  const f = fixture({ record: () => { if (++records > 2) throw new Error('private disk path'); } });
  await f.runner.start().catch(() => {});
  const report = await f.runner.completed;
  assert.equal(report.state, 'failed'); assert.equal(report.code, 'CANDIDATE_DIAGNOSTICS_WRITE');
  await assert.rejects(f.runner.stop(), { code: 'CANDIDATE_DIAGNOSTICS_WRITE' });
});

test('readiness refusal never constructs bridge and stop cancels pending retry', async () => {
  const f = fixture({ config: { reconnect: { maxAttempts: 2, delayMs: 10000 } }, readiness: async () => ({ ready: false }) });
  const start = f.runner.start(); await waitUntil(() => f.runner.getStatus().state === 'reconnecting');
  await f.runner.stop(); await start.catch(() => {}); assert.equal(f.bridges.length, 0); assert.equal(f.runner.getStatus().state, 'stopped');
});

test('state is identity-bound and accepts only bounded saved controls', () => {
  const value = validateCandidateConfig(config({ initialControls: { brightness: 80 } }));
  assert.deepEqual(loadCandidateSettings(value), { brightness: 80 });
  saveCandidateSettings(value, { brightness: 25, followDisplayBrightness: false });
  assert.equal(loadCandidateSettings(value).brightness, 25);
  assert.throws(() => loadCandidateSettings({ ...value, identity: { ...value.identity, childSerial: 'DIFFERENT' } }), { code: 'CANDIDATE_STATE_IDENTITY' });
  assert.throws(() => saveCandidateSettings(value, { arbitraryPayload: 'secret' }), { code: 'CANDIDATE_SETTINGS_INVALID' });
});

test('display journal retains partial order, compacts coverage and fails closed at bounded history', () => {
  const journal = createDisplayJournal({ maxEntries: 3 });
  const frame = (region, byte = 1) => ({ type: 'frame', surfaceId: 'touch', width: 4, height: 4, region, format: 'rgb888', data: Buffer.alloc(region.width * region.height * 3, byte).toString('base64') });
  const a = frame({ x: 0, y: 0, width: 2, height: 2 }); const b = frame({ x: 2, y: 2, width: 2, height: 2 });
  journal.prepare(a)(); journal.prepare(b)(); journal.prepare(a)(); assert.equal(journal.length, 2);
  journal.prepare({ type: 'control', control: 'fill', surfaceId: 'key:0', color: [1, 2, 3] })();
  assert.throws(() => journal.prepare({ ...a, surfaceId: 'key:1' }), { code: 'CANDIDATE_RESTORE_LIMIT' }); assert.equal(journal.length, 3);
  journal.prepare({ type: 'control', control: 'clear' })(); assert.equal(journal.length, 1);
});

test('special wake-clear keeps LCD-derived strip and key history with a bounded output-only barrier', () => {
  const journal = createDisplayJournal();
  const lcd = { type: 'frame', surfaceId: 'lcd', format: 'jpeg', width: 800, height: 480, data: 'encoded-full-lcd' };
  const key = { type: 'frame', surfaceId: 'key:0', format: 'jpeg', width: 120, height: 120, data: 'encoded-key' };
  journal.prepare(lcd)(); journal.prepare(key)();
  journal.prepare({ type: 'control', control: 'clear' }, { normalStateEffect: 'background-only' })();
  assert.deepEqual(journal.entries, [lcd, key, { type: 'control', control: 'sleep' }, { type: 'control', control: 'clear' }]);
  journal.prepare({ type: 'control', control: 'clear' }, { normalStateEffect: 'background-only' })(); assert.equal(journal.length, 4);
  journal.prepare(lcd)(); assert.deepEqual(journal.entries, [lcd]);
  journal.prepare({ type: 'control', control: 'clear' }, { normalStateEffect: 'replace-all' })(); assert.equal(journal.length, 1);
});

test('diagnostics rotate bounded files and exclude identities, arbitrary strings and payload', () => {
  const value = config(); const record = createCandidateDiagnostics(value.diagnosticsPath, 256);
  for (let count = 0; count < 100; count++) record('diagnostic', { code: 'NORMAL_CODE', generation: count, message: 'SECRET-SERIAL', payload: 'PRIVATE-IMAGE' });
  for (const name of [value.diagnosticsPath, `${value.diagnosticsPath}.1`]) {
    const text = readFileSync(name, 'utf8'); assert.ok(Buffer.byteLength(text) <= 256); assert.ok(!/SECRET|PRIVATE/.test(text));
  }
  assert.equal(diagnosticCode({ code: 'ERR_PRIVATE_SERIAL' }), 'CANDIDATE_FAILED');
});

test('portable manifest rejects escape, missing executable inventory and digest mismatch before launch', () => {
  const value = config(); const folder = resolve(value.runtime.manifestPath, '..'); const command = join(folder, 'worker.bin');
  writeFileSync(command, 'worker-bytes'); const hash = createHash('sha256').update(readFileSync(command)).digest('hex');
  const manifest = { schemaVersion: 1, platform: process.platform, arch: process.arch, buildId: 'candidate-test', executableSha256: hash,
    files: [{ path: 'worker.bin', sha256: hash }] };
  writeFileSync(value.runtime.manifestPath, JSON.stringify(manifest));
  assert.equal(loadCandidateManifest({ command, manifestPath: value.runtime.manifestPath }).files[0].path, command);
  writeFileSync(value.runtime.manifestPath, JSON.stringify({ ...manifest, files: [{ path: '../escape', sha256: hash }] }));
  assert.throws(() => loadCandidateManifest({ command, manifestPath: value.runtime.manifestPath }), { code: 'CANDIDATE_MANIFEST_INVALID' });
});

test('modified inventoried worker is rejected before any executable can start', async () => {
  const value = config(); const folder = resolve(value.runtime.manifestPath, '..'); const command = join(folder, 'worker.bin');
  writeFileSync(command, 'original-executable');
  const hash = createHash('sha256').update(readFileSync(command)).digest('hex');
  writeFileSync(value.runtime.manifestPath, JSON.stringify({ schemaVersion: 1, buildId: 'tamper-test', platform: process.platform,
    arch: process.arch, executableSha256: hash, files: [{ path: 'worker.bin', sha256: hash }] }));
  value.runtime.command = command;
  const { createCandidateSelfTest } = await load('candidate-self-test.mjs');
  const { runner } = createCandidateSelfTest(value);
  writeFileSync(command, 'modified-executable');
  await assert.rejects(runner.start(), { code: 'ERR_WORKER_DIGEST' });
  assert.equal(runner.getStatus().counters.workerStarts, 0); assert.equal(runner.getStatus().counters.readinessChecks, 0); await runner.stop();
});

test('readiness subprocess validates nonce and identity and supports cancellation', async () => {
  const script = join(temp, 'inspector.mjs');
  writeFileSync(script, `let data='';process.stdin.on('data', x=>data+=x);process.stdin.on('end',()=>{const req=JSON.parse(data);process.stdout.write(JSON.stringify({...req,ready:true,preparation:[],host:{pid:1,creationTime:'new',executableDigest:'a'.repeat(64)}}));});`);
  const profile = createCandidateProfile(config().identity);
  const inspect = createCandidateReadiness({ command: process.execPath, args: [script] });
  const first = await inspect({ profile }); const second = await inspect({ profile }); assert.equal(first.ready, true); assert.equal(second.ready, true);
  writeFileSync(script, `process.stdin.resume();process.stdin.on('end',()=>process.stdout.write(JSON.stringify({schemaVersion:1,requestId:'stale',ready:true,targetId:'${profile.target.id}',profileId:'${profile.id}'})));`);
  await assert.rejects(inspect({ profile }), { code: 'CANDIDATE_READINESS_IDENTITY' });
  const abort = new AbortController(); abort.abort(); await assert.rejects(inspect({ profile, signal: abort.signal }), { code: 'ABORTED' });
  writeFileSync(script, 'process.stdin.resume();setInterval(()=>{},1000);');
  const timed = createCandidateReadiness({ command: process.execPath, args: [script] }, { timeoutMs: 50 });
  await assert.rejects(timed({ profile }), { code: 'CANDIDATE_READINESS_TIMEOUT' });
  const runningAbort = new AbortController();
  const pending = inspect({ profile, signal: runningAbort.signal });
  setTimeout(() => runningAbort.abort(), 30);
  await assert.rejects(pending, { code: 'ABORTED' });
});

test('supervised CLI does not read config before start and rejects malformed/oversize commands', async () => {
  for (const command of ['{"command":"stop"}\n', 'x'.repeat(1025)]) {
    const child = spawn(process.execPath, [join(runtime, 'candidate-runner.mjs'), '--config', join(temp, 'missing.json'), '--supervised']);
    let error = ''; child.stderr.on('data', chunk => { error += chunk; }); child.stdin.end(command);
    const [code] = await once(child, 'close'); assert.equal(code, 1); assert.match(error, /CANDIDATE_(?:START_REQUIRED|COMMAND_LIMIT)/); assert.ok(!error.includes('ENOENT'));
  }
});

test('detached candidate runs actual Python fake worker with manifested runtime and restoration', { skip: process.platform === 'win32' }, async () => {
  const value = config(); const folder = resolve(value.runtime.manifestPath, '..');
  const python = execFileSync(process.env.PYTHON ?? 'python', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim();
  // Source-tree integration uses an inventoried launcher for the installed test
  // Python. Windows artifact CI separately runs the actual frozen executable.
  const command = join(folder, 'worker-python');
  writeFileSync(command, `#!/bin/sh\nexec '${python.replaceAll("'", "'\\''")}' "$@"\n`, { mode: 0o755 });
  const source = join(folder, 'python'); cpSync(join(root, 'adapters/mirabox-python/src'), source, { recursive: true, filter: path => !path.includes('__pycache__') });
  const files = [];
  function inventory(directory) { for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name); if (entry.isDirectory()) inventory(path);
    else files.push({ path: path.slice(folder.length + 1).replaceAll('\\', '/'), sha256: createHash('sha256').update(readFileSync(path)).digest('hex') });
  } }
  inventory(folder);
  const hash = files.find(file => file.path === 'worker-python').sha256;
  writeFileSync(value.runtime.manifestPath, JSON.stringify({ schemaVersion: 1, platform: process.platform, arch: process.arch,
    buildId: 'candidate-test', executableSha256: hash, files }));
  value.runtime.command = command;
  value.runtime.args = ['-I', '-B', '-c', `import sys;sys.path.insert(0,${JSON.stringify(source)});from hellgato_mirabox.worker import main;raise SystemExit(main())`, '--build-id', 'candidate-test'];
  const configPath = join(folder, 'config.json'); writeFileSync(configPath, JSON.stringify(value));
  let output;
  try { output = execFileSync(process.execPath, [join(runtime, 'candidate-runner.mjs'), '--config', configPath, '--self-test'], { encoding: 'utf8', timeout: 30000 }); }
  catch (error) { if (existsSync(value.diagnosticsPath)) error.message += '\n' + readFileSync(value.diagnosticsPath, 'utf8'); throw error; }
  const report = output.trim().split('\n').map(line => JSON.parse(line)).find(record => record.checks);
  assert.equal(report.state, 'stopped'); assert.equal(report.counters.readinessChecks, 3); assert.equal(report.physicalHardwareVerified, false);
  assert.ok(report.checks.includes('no-input-replay')); assert.ok(!output.includes(value.identity.dockSerial));

  // Keep a fake session open in a detached copy of the CLI fixture so EOF is the
  // stopping condition, rather than completion of the normal self-test script.
  const eofRuntime = join(temp, 'eof-sdk'); cpSync(runtime, eofRuntime, { recursive: true });
  const selfTestPath = join(eofRuntime, 'candidate-self-test.mjs');
  const sourceText = readFileSync(selfTestPath, 'utf8');
  assert.ok(sourceText.includes('await runner.start();'));
  writeFileSync(selfTestPath, sourceText.replace('await runner.start();', 'await runner.start(); await runner.completed; return runner.getStatus();'));
  const child = spawn(process.execPath, [join(eofRuntime, 'candidate-runner.mjs'), '--config', configPath, '--self-test', '--supervised']);
  const closed = once(child, 'close'); let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; }); child.stderr.on('data', chunk => { stderr += chunk; });
  child.stdin.write('{"command":"start"}\n');
  await waitUntil(() => stdout.split('\n').some(line => { try { return JSON.parse(line).state === 'ready'; } catch { return false; } }));
  child.stdin.end();
  const [exitCode] = await closed;
  assert.equal(exitCode, 0, stderr); assert.match(stdout, /"state":"stopped"/);
});

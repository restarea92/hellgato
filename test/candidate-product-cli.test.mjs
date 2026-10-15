import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const temp = mkdtempSync(join(tmpdir(), 'hellgato-product-cli-'));
after(() => rmSync(temp, { force: true, recursive: true }));
const runnerUrl = pathToFileURL(join(root, 'apps/stream-dog/node/candidate-runner.mjs')).href;
const configUrl = pathToFileURL(join(root, 'apps/stream-dog/node/candidate-config.mjs')).href;
const devices = [{ id: 'fixture:device', adapterId: 'fixture:adapter', modelId: 'fixture:model', firmware: 'simulation',
  capabilities: { keys: 2, dials: 1, touch: null, displays: [], dialPress: 'native', dialRelease: 'native', features: {} } }];
const workerState = { generation: 1, state: { brightness: 71, knobBrightness: 15, followDisplayBrightness: false, sleepDuration: 60 },
  capabilities: { features: { brightness: { status: 'emulated' } } } };
const wrapper = `
import { EventEmitter } from 'node:events';
import { runCandidateCli } from ${JSON.stringify(runnerUrl)};
import { diagnosticError } from ${JSON.stringify(configUrl)};
const write = value => process.stdout.write(JSON.stringify(value) + '\\n');
const error = (code, message = 'PRIVATE-FIXTURE-ERROR') => Object.assign(new Error(message), { code });
const module = {
  createCandidateRunner(config) {
    write({ event: 'fixtureRunnerCreated' });
    const runner = new EventEmitter(); let state = 'idle', finish, queries = 0, hostTransitions = 0, starts = 0;
    const completed = new Promise(resolve => { finish = resolve; });
    Object.assign(runner, {
      completed,
      getStatus: () => ({ schemaVersion: 1, engine: 'fixture', state, generation: 1 }),
      async start() {
        starts++; write({ event: 'fixtureRunnerStarted' });
        if (config.scenario === 'fatal-start') throw error('PRIVATE-ERROR-CODE');
        if (config.scenario === 'firmware-start') throw Object.assign(error('ERR_FIRMWARE_UNVERIFIED', 'Missing firmware'), { details: { operation: 'discover' } });
        if (config.scenario === 'cleanup-failure') throw error('ERR_FIRMWARE_CHANGED', 'Firmware changed');
        state = 'ready';
        runner.emit('product', { schemaVersion: 1, event: 'bridgeReady', generation: 1 });
        runner.emit('product', { schemaVersion: 1, event: 'started', generation: 1, ports: { primary: 5343, child: 5344 } });
        runner.emit('state', runner.getStatus());
      },
      async control(value) {
        if (value.value === 13) throw error('CANDIDATE_CONTROL_NOT_READY');
        return { status: 'submitted', verification: 'unconfirmed' };
      },
      async getProductWorkerState() {
        if (config.firstStateFailure && ++queries === 1) throw error('CANDIDATE_CONTROL_NOT_READY');
        return ${JSON.stringify(workerState)};
      },
      async pauseDevice() {
        if (config.firstHostFailure && ++hostTransitions === 1) throw error('CANDIDATE_CONTROL_NOT_READY');
        state = 'hostPaused'; return { status: 'paused' };
      },
      async resumeDevice() {
        if (config.resumePending) { state = 'reconnecting'; return new Promise(() => {}); }
        if (!starts) await runner.start(); state = 'ready'; return { status: 'ready' };
      },
      async stop() {
        if (config.scenario === 'cleanup-failure') throw error('CLEANUP_FAILED', 'Cleanup did not close');
        if (state === 'stopped') return;
        state = 'stopped';
        runner.emit('product', { schemaVersion: 1, event: 'stopped', generation: 1 });
        runner.emit('state', runner.getStatus()); finish(runner.getStatus());
      },
    });
    return runner;
  },
  async discoverProductDevices(config, { signal }) {
    write({ event: 'fixtureDiscoveryCalled', productMode: config.productMode, hasAbortSignal: signal instanceof AbortSignal });
    if (config.scenario === 'fatal-discovery') throw error('ERR_WORKER_START');
    return ${JSON.stringify(devices)};
  },
};
write({ event: 'fixtureWrapperReady' });
await runCandidateCli(process.argv.slice(2), { loadModule: async options => {
  if (process.env.HELLGATO_TEST_MODULE_DELAY_MS) await new Promise(resolve => setTimeout(resolve, Number(process.env.HELLGATO_TEST_MODULE_DELAY_MS)));
  write({ event: 'fixtureModuleLoaded', selfTest: options.selfTest }); return module;
} }).catch(error => {
  process.stderr.write(JSON.stringify({ event: 'fixtureCliFailed', ...diagnosticError(error) }) + '\\n');
  process.exitCode = 1;
});
`;
let serial = 0;
function fixture(t, { config = { productMode: true }, writeConfig = true, args = [], loadDelayMs = 0 } = {}) {
  const folder = join(temp, `case-${++serial}`); mkdirSync(folder);
  const configPath = join(folder, 'config.json'), wrapperPath = join(folder, 'runner-fixture.mjs');
  writeFileSync(wrapperPath, wrapper); if (writeConfig) writeFileSync(configPath, JSON.stringify(config));
  const child = spawn(process.execPath, [wrapperPath, '--config', configPath, '--supervised', ...args],
    { timeout: 10000, env: { ...process.env, HELLGATO_TEST_MODULE_DELAY_MS: String(loadDelayMs) } });
  const records = [], invalidLines = []; let stdout = '', stderr = '', pending = '', closed;
  child.stdout.on('data', chunk => {
    const text = chunk.toString(); stdout += text; pending += text;
    let newline;
    while ((newline = pending.indexOf('\n')) >= 0) {
      const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
      try { records.push(JSON.parse(line)); } catch { invalidLines.push(line); }
    }
  });
  child.stderr.on('data', chunk => { stderr += chunk; });
  const completed = new Promise((resolve, reject) => {
    child.once('error', reject); child.once('close', (code, signal) => { closed = { code, signal }; resolve(closed); });
  });
  t.after(async () => { if (!closed) { child.stdin.end(); child.kill(); } await completed; });
  const wait = async predicate => {
    const end = Date.now() + 5000;
    while (!predicate()) {
      if (closed) throw new Error(`CLI exited before expected reply: ${JSON.stringify(closed)} ${stdout} ${stderr}`);
      if (Date.now() > end) throw new Error(`Timed out waiting for CLI reply: ${stdout} ${stderr}`);
      await new Promise(resolve => setTimeout(resolve, 5));
    }
  };
  return { child, configPath, records, invalidLines, completed, wait,
    send: command => child.stdin.write(JSON.stringify(command) + '\n'),
    get stdout() { return stdout; }, get stderr() { return stderr; }, get closed() { return closed; } };
}
const reply = (f, requestId) => f.records.find(record => record.requestId === requestId);

test('supervised CLI waits for start before reading config or loading its runner module', { timeout: 10000 }, async t => {
  const f = fixture(t, { writeConfig: false });
  await f.wait(() => f.records.some(record => record.event === 'fixtureWrapperReady'));
  assert.ok(!f.records.some(record => record.event === 'fixtureModuleLoaded'));
  writeFileSync(f.configPath, JSON.stringify({ productMode: true })); f.send({ command: 'start' });
  await f.wait(() => f.records.some(record => record.event === 'started'));
  assert.deepEqual(f.records.find(record => record.event === 'fixtureModuleLoaded'), { event: 'fixtureModuleLoaded', selfTest: false });
  assert.deepEqual(f.records.find(record => record.event === 'started').ports, { primary: 5343, child: 5344 });
  f.send({ command: 'stop' });
  assert.deepEqual(await f.completed, { code: 0, signal: null });
  assert.ok(f.records.some(record => record.event === 'stopped')); assert.deepEqual(f.invalidLines, []); assert.equal(f.stderr, '');
});

test('commands before the supervised start gate fail without reading missing config or loading modules', { timeout: 10000 }, async t => {
  const f = fixture(t, { writeConfig: false });
  await f.wait(() => f.records.some(record => record.event === 'fixtureWrapperReady'));
  f.send({ command: 'status', requestId: 1 });
  assert.deepEqual(await f.completed, { code: 1, signal: null });
  assert.ok(!f.records.some(record => record.event === 'fixtureModuleLoaded'));
  assert.equal(JSON.parse(f.stderr).code, 'CANDIDATE_START_REQUIRED');
  assert.ok(!f.stderr.includes('ENOENT'));
});

test('host pause arriving with start waits through module loading and prevents initial worker startup', { timeout: 10000 }, async t => {
  const f = fixture(t, { loadDelayMs: 30 });
  f.child.stdin.write(JSON.stringify({ command: 'start' }) + '\n' + JSON.stringify({ command: 'hostUnavailable', requestId: 21 }) + '\n');
  await f.wait(() => reply(f, 21));
  assert.deepEqual(reply(f, 21), { schemaVersion: 1, event: 'hostUnavailable', requestId: 21, status: 'paused' });
  assert.ok(f.records.some(record => record.event === 'fixtureModuleLoaded'));
  assert.ok(!f.records.some(record => record.event === 'fixtureRunnerStarted'));
  f.send({ command: 'status', requestId: 22 }); await f.wait(() => reply(f, 22));
  assert.equal(reply(f, 22).state, 'hostPaused'); assert.equal(f.closed, undefined);
  f.send({ command: 'hostAvailable', requestId: 23 }); await f.wait(() => reply(f, 23));
  assert.deepEqual(reply(f, 23), { schemaVersion: 1, event: 'hostAvailable', requestId: 23, status: 'starting' });
  assert.equal(f.records.filter(record => record.event === 'fixtureRunnerStarted').length, 1);
  f.send({ command: 'status', requestId: 24 }); await f.wait(() => reply(f, 24)); assert.equal(reply(f, 24).state, 'ready');
  f.send({ command: 'stop' }); assert.deepEqual(await f.completed, { code: 0, signal: null }); assert.equal(f.stderr, '');
});

test('product CLI correlates successful and failed controls and worker queries without ending the session', { timeout: 10000 }, async t => {
  const f = fixture(t, { config: { productMode: true, firstStateFailure: true, firstHostFailure: true } });
  f.send({ command: 'start' }); await f.wait(() => f.records.some(record => record.state === 'ready'));
  f.send({ command: 'control', requestId: 11, control: { control: 'knobBrightness', value: 13 } });
  await f.wait(() => reply(f, 11));
  assert.deepEqual(reply(f, 11), { schemaVersion: 1, event: 'controlFailed', requestId: 11, code: 'CANDIDATE_CONTROL_NOT_READY' });
  f.send({ command: 'control', requestId: 12, control: { control: 'brightness', value: 71 } });
  await f.wait(() => reply(f, 12));
  assert.deepEqual(reply(f, 12), { schemaVersion: 1, event: 'controlSubmitted', requestId: 12, status: 'submitted', verification: 'unconfirmed' });
  f.send({ command: 'workerState', requestId: 13 }); await f.wait(() => reply(f, 13));
  assert.deepEqual(reply(f, 13), { schemaVersion: 1, event: 'workerStateFailed', requestId: 13, code: 'CANDIDATE_CONTROL_NOT_READY' });
  f.send({ command: 'workerState', requestId: 14 }); await f.wait(() => reply(f, 14));
  assert.deepEqual(reply(f, 14), { schemaVersion: 1, event: 'workerState', requestId: 14, ...workerState });
  f.send({ command: 'status', requestId: 15 }); await f.wait(() => reply(f, 15));
  assert.equal(reply(f, 15).state, 'ready'); assert.equal(f.closed, undefined);
  f.send({ command: 'hostUnavailable', requestId: 16 }); await f.wait(() => reply(f, 16));
  assert.deepEqual(reply(f, 16), { schemaVersion: 1, event: 'hostTransitionFailed', requestId: 16, code: 'CANDIDATE_CONTROL_NOT_READY' });
  f.send({ command: 'hostUnavailable', requestId: 17 }); await f.wait(() => reply(f, 17));
  assert.deepEqual(reply(f, 17), { schemaVersion: 1, event: 'hostUnavailable', requestId: 17, status: 'paused' });
  f.send({ command: 'status', requestId: 18 }); await f.wait(() => reply(f, 18)); assert.equal(reply(f, 18).state, 'hostPaused');
  f.send({ command: 'hostAvailable', requestId: 19 }); await f.wait(() => reply(f, 19));
  assert.deepEqual(reply(f, 19), { schemaVersion: 1, event: 'hostAvailable', requestId: 19, status: 'starting' });
  f.send({ command: 'status', requestId: 20 }); await f.wait(() => reply(f, 20)); assert.equal(reply(f, 20).state, 'ready');
  assert.equal(f.closed, undefined);
  assert.ok(!f.stdout.includes('PRIVATE')); assert.ok(!f.records.some(record => record.event === 'productFailed'));
  f.send({ command: 'stop' }); assert.deepEqual(await f.completed, { code: 0, signal: null });
  assert.equal(f.records.filter(record => record.event === 'stopped').length, 1);
  const diagnostics = f.stderr.trim().split('\n').map(JSON.parse);
  assert.equal(diagnostics.length, 3);
  for (const detail of diagnostics) {
    assert.equal(detail.event, 'diagnostic'); assert.equal(detail.code, 'CANDIDATE_CONTROL_NOT_READY');
    assert.equal(detail.originalCode, 'CANDIDATE_CONTROL_NOT_READY'); assert.equal(detail.message, 'PRIVATE-FIXTURE-ERROR');
    assert.ok(detail.stack.includes('PRIVATE-FIXTURE-ERROR'));
  }
});

test('host availability acknowledges immediately while an unresolved device resume leaves status pause and stop responsive', { timeout: 10000 }, async t => {
  const f = fixture(t, { config: { productMode: true, resumePending: true } });
  f.send({ command: 'start' }); await f.wait(() => f.records.some(record => record.state === 'ready'));
  f.send({ command: 'hostUnavailable', requestId: 31 }); await f.wait(() => reply(f, 31));
  assert.equal(reply(f, 31).status, 'paused');
  f.send({ command: 'hostAvailable', requestId: 32 }); await f.wait(() => reply(f, 32));
  assert.deepEqual(reply(f, 32), { schemaVersion: 1, event: 'hostAvailable', requestId: 32, status: 'starting' });
  f.send({ command: 'status', requestId: 33 }); await f.wait(() => reply(f, 33));
  assert.equal(reply(f, 33).state, 'reconnecting'); assert.equal(f.closed, undefined);
  f.send({ command: 'hostUnavailable', requestId: 34 }); await f.wait(() => reply(f, 34));
  assert.deepEqual(reply(f, 34), { schemaVersion: 1, event: 'hostUnavailable', requestId: 34, status: 'paused' });
  f.send({ command: 'status', requestId: 35 }); await f.wait(() => reply(f, 35)); assert.equal(reply(f, 35).state, 'hostPaused');
  f.send({ command: 'stop' }); assert.deepEqual(await f.completed, { code: 0, signal: null });
  assert.ok(!f.records.some(record => record.event === 'productFailed')); assert.equal(f.stderr, '');
});

test('discovery CLI uses the discovery module with cancellation and exits after emitting descriptors', { timeout: 10000 }, async t => {
  const f = fixture(t, { args: ['--discover'] });
  f.send({ command: 'start' }); assert.deepEqual(await f.completed, { code: 0, signal: null });
  assert.deepEqual(f.records.find(record => record.event === 'fixtureDiscoveryCalled'),
    { event: 'fixtureDiscoveryCalled', productMode: true, hasAbortSignal: true });
  assert.deepEqual(f.records.find(record => record.event === 'devices-discovered'), { schemaVersion: 1, event: 'devices-discovered', devices });
  assert.ok(!f.records.some(record => record.event === 'fixtureRunnerCreated')); assert.deepEqual(f.invalidLines, []); assert.equal(f.stderr, '');
});

test('fatal product failures keep bounded stdout codes and detailed original errors on stderr', { timeout: 10000 }, async t => {
  for (const [scenario, args, code] of [['fatal-start', [], 'CANDIDATE_FAILED'], ['fatal-discovery', ['--discover'], 'ERR_WORKER_START'], ['firmware-start', [], 'ERR_FIRMWARE_UNVERIFIED']]) {
    const f = fixture(t, { config: { productMode: true, scenario }, args });
    f.send({ command: 'start' }); assert.deepEqual(await f.completed, { code: 1, signal: null });
    assert.deepEqual(f.records.filter(record => record.event === 'productFailed'),
      [{ schemaVersion: 1, event: 'productFailed', state: 'failed', code }]);
    assert.ok(Buffer.byteLength(JSON.stringify(f.records.find(record => record.event === 'productFailed'))) < 256);
    const detail = JSON.parse(f.stderr);
    assert.equal(detail.code, code); assert.ok(detail.message); assert.ok(detail.stack);
    if (scenario === 'firmware-start') {
      assert.equal(detail.originalCode, 'ERR_FIRMWARE_UNVERIFIED'); assert.equal(detail.message, 'Missing firmware');
      assert.deepEqual(detail.details, { operation: 'discover' });
    }
    assert.ok(!f.stdout.includes('PRIVATE')); assert.deepEqual(f.invalidLines, []);
  }
});

test('CLI cleanup failure is logged without replacing the original firmware failure', { timeout: 10000 }, async t => {
  const f = fixture(t, { config: { productMode: true, scenario: 'cleanup-failure' } });
  f.send({ command: 'start' }); assert.deepEqual(await f.completed, { code: 1, signal: null });
  const details = f.stderr.trim().split('\n').map(JSON.parse);
  assert.equal(details[0].code, 'CLEANUP_FAILED'); assert.equal(details[0].message, 'Cleanup did not close');
  assert.equal(details[1].code, 'ERR_FIRMWARE_CHANGED'); assert.equal(details[1].message, 'Firmware changed');
});

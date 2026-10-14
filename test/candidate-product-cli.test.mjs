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
import { diagnosticCode } from ${JSON.stringify(configUrl)};
const write = value => process.stdout.write(JSON.stringify(value) + '\\n');
const error = (code, message = 'PRIVATE-FIXTURE-ERROR') => Object.assign(new Error(message), { code });
const module = {
  createCandidateRunner(config) {
    write({ event: 'fixtureRunnerCreated' });
    const runner = new EventEmitter(); let state = 'idle', finish, queries = 0;
    const completed = new Promise(resolve => { finish = resolve; });
    Object.assign(runner, {
      completed,
      getStatus: () => ({ schemaVersion: 1, engine: 'fixture', state, generation: 1 }),
      async start() {
        if (config.scenario === 'fatal-start') throw error('PRIVATE-ERROR-CODE');
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
      async stop() {
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
  write({ event: 'fixtureModuleLoaded', selfTest: options.selfTest }); return module;
} }).catch(error => {
  process.stderr.write(JSON.stringify({ event: 'fixtureCliFailed', code: diagnosticCode(error) }) + '\\n');
  process.exitCode = 1;
});
`;
let serial = 0;
function fixture(t, { config = { productMode: true }, writeConfig = true, args = [] } = {}) {
  const folder = join(temp, `case-${++serial}`); mkdirSync(folder);
  const configPath = join(folder, 'config.json'), wrapperPath = join(folder, 'runner-fixture.mjs');
  writeFileSync(wrapperPath, wrapper); if (writeConfig) writeFileSync(configPath, JSON.stringify(config));
  const child = spawn(process.execPath, [wrapperPath, '--config', configPath, '--supervised', ...args], { timeout: 10000 });
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
  assert.deepEqual(JSON.parse(f.stderr), { event: 'fixtureCliFailed', code: 'CANDIDATE_START_REQUIRED' });
  assert.ok(!f.stderr.includes('ENOENT'));
});

test('product CLI correlates successful and failed controls and worker queries without ending the session', { timeout: 10000 }, async t => {
  const f = fixture(t, { config: { productMode: true, firstStateFailure: true } });
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
  assert.ok(!f.stdout.includes('PRIVATE')); assert.ok(!f.records.some(record => record.event === 'productFailed'));
  f.send({ command: 'stop' }); assert.deepEqual(await f.completed, { code: 0, signal: null });
  assert.equal(f.records.filter(record => record.event === 'stopped').length, 1); assert.equal(f.stderr, '');
});

test('discovery CLI uses the discovery module with cancellation and exits after emitting descriptors', { timeout: 10000 }, async t => {
  const f = fixture(t, { args: ['--discover'] });
  f.send({ command: 'start' }); assert.deepEqual(await f.completed, { code: 0, signal: null });
  assert.deepEqual(f.records.find(record => record.event === 'fixtureDiscoveryCalled'),
    { event: 'fixtureDiscoveryCalled', productMode: true, hasAbortSignal: true });
  assert.deepEqual(f.records.find(record => record.event === 'devices-discovered'), { schemaVersion: 1, event: 'devices-discovered', devices });
  assert.ok(!f.records.some(record => record.event === 'fixtureRunnerCreated')); assert.deepEqual(f.invalidLines, []); assert.equal(f.stderr, '');
});

test('fatal product startup and discovery failures expose only bounded diagnostic codes on stdout', { timeout: 10000 }, async t => {
  for (const [scenario, args, code] of [['fatal-start', [], 'CANDIDATE_FAILED'], ['fatal-discovery', ['--discover'], 'ERR_WORKER_START']]) {
    const f = fixture(t, { config: { productMode: true, scenario }, args });
    f.send({ command: 'start' }); assert.deepEqual(await f.completed, { code: 1, signal: null });
    assert.deepEqual(f.records.filter(record => record.event === 'productFailed'),
      [{ schemaVersion: 1, event: 'productFailed', state: 'failed', code }]);
    assert.ok(Buffer.byteLength(JSON.stringify(f.records.find(record => record.event === 'productFailed'))) < 256);
    assert.ok(!f.stdout.includes('PRIVATE')); assert.ok(!f.stderr.includes('PRIVATE')); assert.deepEqual(f.invalidLines, []);
  }
});

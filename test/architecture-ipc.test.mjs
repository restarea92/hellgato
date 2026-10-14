import assert from 'node:assert/strict';
import { test, after } from 'node:test';
import { mkdtemp, cp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { once } from 'node:events';

const root = fileURLToPath(new URL('..', import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), 'hellgato-ipc-'));
after(() => rm(temporary, { recursive: true, force: true }));
const core = join(temporary, 'node_modules/@hellgato/core');
const facade = join(temporary, 'node_modules/hellgato');
await mkdir(join(core, 'dist/contracts'), { recursive: true });
await cp(join(root, 'packages/core/src/contracts/index.mjs'), join(core, 'dist/contracts/index.mjs'));
await cp(join(root, 'packages/core/schemas'), join(core, 'schemas'), { recursive: true });
await writeFile(join(core, 'package.json'), JSON.stringify({ type: 'module', exports: { './contracts': './dist/contracts/index.mjs' } }));
await cp(join(root, 'packages/hellgato/src/sidecar'), join(facade, 'src/sidecar'), { recursive: true });
await writeFile(join(facade, 'package.json'), JSON.stringify({ type: 'module', version: '0.1.0', exports: './src/sidecar/index.mjs' }));
// The real child only sees a detached package copy, proving no app/GUI/repository imports.
const pythonSource = join(temporary, 'python');
await cp(join(root, 'adapters/mirabox-python/src'), pythonSource, { recursive: true });
const python = execFileSync(process.env.PYTHON ?? 'python', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim();
const { createSidecarAdapter, validateRpcMessage } = await import(pathToFileURL(join(facade, 'src/sidecar/index.mjs')));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const adapters = new Set();
after(async () => { await Promise.all([...adapters].map(adapter => adapter.stop())); });
function adapter(extra = {}, workerArgs = []) {
  const value = createSidecarAdapter({ command: python,
    args: ['-m', 'hellgato_mirabox.worker', '--backend', 'fake', '--build-id', 'integration-test', ...workerArgs],
    env: { PYTHONPATH: pythonSource }, expectedBuildId: 'integration-test', requestTimeoutMs: 3000, stopTimeoutMs: 150,
    ...extra });
  adapters.add(value); return value;
}
const frame = (color = [0, 0, 0], sequence = 1, region) => ({ type: 'frame', surfaceId: 'key:0',
  format: 'rgb888', width: 4, height: 4, sequence, ...(region ? { region } : {}),
  data: Buffer.from(Array.from({ length: (region?.width ?? 4) * (region?.height ?? 4) }, () => color).flat()).toString('base64') });
async function open(value) { await value.start(); const [device] = await value.discover(); await value.open(device.id); return device; }

test('shared IPC fixtures validate identically across the Node and Python implementations', async () => {
  const fixtures = JSON.parse(await readFile(join(root, 'test/fixtures/architecture-ipc.json'), 'utf8'));
  for (const value of fixtures.valid) assert.equal(validateRpcMessage(value), value);
  for (const value of fixtures.invalid) assert.throws(() => validateRpcMessage(value));
  const source = 'import json,sys\nfrom hellgato_mirabox.protocol import validate_rpc_message\nf=json.load(open(sys.argv[1]))\nfor x in f["valid"]: validate_rpc_message(x)\nfor x in f["invalid"]:\n try: validate_rpc_message(x)\n except ValueError: pass\n else: raise AssertionError(x)\n';
  execFileSync(python, ['-c', source, join(root, 'test/fixtures/architecture-ipc.json')], { env: { ...process.env, PYTHONPATH: pythonSource } });
});

test('construction is inert and preflight rejects bad digest before process startup', async () => {
  const value = adapter({ manifest: { schemaVersion: 1, buildId: 'integration-test', platform: process.platform,
    arch: process.arch, executableSha256: '0'.repeat(64) } });
  assert.equal(value.status, 'idle'); assert.equal(value.pid, null);
  await assert.rejects(value.start(), { code: 'ERR_WORKER_DIGEST' }); assert.equal(value.pid, null);
  const hash = createHash('sha256').update(await readFile(python)).digest('hex');
  const verified = adapter({ manifest: { schemaVersion: 1, buildId: 'integration-test', platform: process.platform,
    arch: process.arch, executableSha256: hash } });
  assert.deepEqual(await verified.preflight(), { verified: true, buildId: 'integration-test' });
  await verified.stop();
});

test('real Node-Python handshake, explicit discovery/open, concurrent start and repeated stop', async () => {
  const value = adapter();
  const results = await Promise.all([value.start(), value.start(), value.start()]);
  assert.equal(results[0].protocol.major, 1); assert.equal(results[0].buildId, 'integration-test');
  assert.equal(value.status, 'ready');
  const devices = await value.discover(); assert.equal(devices.length, 1);
  await assert.rejects(value.open('unknown'), { code: 'ERR_DEVICE_NOT_FOUND' });
  const opened = await value.open(devices[0].id); assert.equal(opened.status, 'open');
  await assert.rejects(value.open(devices[0].id), { code: 'ERR_DEVICE_BUSY' });
  assert.equal((await value.getState()).connected, true);
  await Promise.all([value.stop(), value.stop()]);
  assert.equal(value.pid, null); assert.equal(value.status, 'stopped');
});

test('worker snapshots normalize typed adapter state while retaining backend diagnostics', async () => {
  const value = adapter(); const device = await open(value);
  const connected = await value.getState();
  assert.equal(connected.state, 'connected');
  assert.equal(connected.deviceId, device.id);
  assert.equal(connected.generation, value.generation);
  assert.equal(connected.writeCount, 0);
  assert.deepEqual(connected.frames, {});
  await value.close();
  const disconnected = await value.getState();
  assert.equal(disconnected.state, 'disconnected');
  assert.equal(disconnected.deviceId, undefined);
  assert.equal(disconnected.connected, false);
  await value.stop();
});

test('ordered input notifications preserve all key down/up transitions', async () => {
  const value = adapter({}, ['--fake-input-count', '20']); const input = [];
  value.onInput(event => input.push(event));
  await open(value);
  assert.equal(input.length, 20);
  for (let index = 0; index < input.length; index++) {
    assert.equal(input[index].sequence, index + 1);
    assert.equal(input[index].state, index % 2 === 0 ? 'down' : 'up');
  }
  await value.stop();
});

test('partial frames compose without losing regions, control barriers retain order', async () => {
  const value = adapter(); await open(value);
  await value.setOutput(frame([255, 0, 0], 1, { x: 0, y: 0, width: 2, height: 2 }));
  await value.setOutput(frame([0, 255, 0], 2, { x: 2, y: 2, width: 2, height: 2 }));
  let state = await value.getState(); let pixels = Buffer.from(state.frames['key:0'], 'base64');
  assert.deepEqual([...pixels.subarray(0, 3)], [255, 0, 0]);
  assert.deepEqual([...pixels.subarray(45, 48)], [0, 255, 0]);
  await value.setControl({ type: 'control', control: 'clear', sequence: 3 });
  await value.setOutput(frame([0, 0, 255], 4, { x: 1, y: 1, width: 1, height: 1 }));
  state = await value.getState(); pixels = Buffer.from(state.frames['key:0'], 'base64');
  assert.deepEqual([...pixels.subarray(0, 3)], [0, 0, 0]);
  assert.deepEqual([...pixels.subarray(15, 18)], [0, 0, 255]);
  assert.deepEqual(state.writes.map(write => write.type === 'frame' ? 'frame' : write.control), ['frame', 'frame', 'clear', 'frame']);
  await value.stop();
});

test('unsupported controls, stale generations and invalid surfaces fail explicitly', async () => {
  const value = adapter(); await open(value);
  await assert.rejects(value.setControl({ type: 'control', control: 'showLogo' }), { code: 'ERR_UNSUPPORTED_OPERATION' });
  assert.throws(() => value.setControl({ type: 'control', control: 'firmwareWrite' }), { code: 'ERR_CONTRACT' });
  assert.throws(() => value.setOutput({ ...frame(), generation: 0 }), { code: 'ERR_STALE_GENERATION' });
  await assert.rejects(value.setOutput({ ...frame(), surfaceId: 'missing' }), { code: 'ERR_UNSUPPORTED_OPERATION' });
  await value.setOutput(frame());
  await assert.rejects(value.setOutput(frame()), { code: 'ERR_OUTPUT_ORDER' });
  await value.close(); await value.open('fake:buttons-dial');
  assert.equal(value.generation, 2); await value.setOutput(frame()); await value.stop();
});

test('expired queued/native writes never start after deadline and are never retried', async () => {
  const value = adapter({}, ['--fake-write-delay-ms', '160']); await open(value);
  await assert.rejects(value.setOutput(frame(), { deadlineMs: 35 }), { code: 'ERR_REQUEST_TIMEOUT' });
  await delay(200);
  assert.equal((await value.getState()).writeCount, 0);
  await value.stop();
});

test('pending requests and frame bytes remain bounded under slow-writer flood; stop stays bounded', async () => {
  const value = adapter({ maxPendingRequests: 2 }, ['--fake-write-delay-ms', '100']); await open(value);
  const started = Date.now();
  const results = await Promise.allSettled(Array.from({ length: 40 }, (_, index) => value.setOutput(frame([0, 0, 0], index))));
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 2);
  assert.equal(results.filter(result => result.status === 'rejected' && result.reason.code === 'ERR_BACKPRESSURE').length, 38);
  await value.stop(); assert.ok(Date.now() - started < 2500);
});

test('worker crash rejects pending requests; explicit restart uses fresh process and generation', async () => {
  const value = adapter({}, ['--fake-write-delay-ms', '200']); await open(value);
  const oldPid = value.pid;
  const output = value.setOutput(frame());
  process.kill(oldPid, 'SIGKILL');
  await assert.rejects(output, error => ['ERR_WORKER_EXIT', 'ERR_WORKER_EOF', 'ERR_WORKER_PIPE'].includes(error.code));
  await value.stop(); await open(value);
  assert.notEqual(value.pid, oldPid); assert.equal(value.generation, 2);
  await value.stop();
});

test('build mismatch fails hello and cleans the worker process', async () => {
  const value = adapter({ expectedBuildId: 'different-build' });
  await assert.rejects(value.start(), { code: 'ERR_WORKER_BUILD_ID' });
  assert.equal(value.pid, null); await value.stop();
});

test('malformed or oversized worker stdout fails closed with bounded buffering', async () => {
  for (const body of ['console.log("not-json")', 'process.stdout.write("x".repeat(1500001))']) {
    const value = adapter({ command: process.execPath, args: ['-e', body], env: {}, expectedBuildId: undefined });
    await assert.rejects(value.start(), error => ['ERR_IPC_PROTOCOL', 'ERR_MESSAGE_TOO_LARGE'].includes(error.code));
    assert.equal(value.pid, null); await value.stop();
  }
});

test('stdin EOF closes a live standalone Python worker cleanly', async () => {
  const child = spawn(python, ['-m', 'hellgato_mirabox.worker', '--backend', 'fake'], {
    env: { ...process.env, PYTHONPATH: pythonSource }, stdio: ['pipe', 'pipe', 'pipe'] });
  const output = []; let partial = '';
  child.stdout.on('data', chunk => {
    partial += chunk.toString(); let index;
    while ((index = partial.indexOf('\n')) >= 0) { output.push(JSON.parse(partial.slice(0, index))); partial = partial.slice(index + 1); }
  });
  child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 'hello', method: 'hello', params: {
    protocol: { major: 1, minor: 0 }, deadlineAt: Date.now() + 3000 } })}\n`);
  const deadline = Date.now() + 3000;
  while (!output.length && Date.now() < deadline) await delay(10);
  assert.equal(output[0]?.id, 'hello');
  child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 'open', method: 'open', params: {
    deviceId: 'fake:buttons-dial', generation: 1, deadlineAt: Date.now() + 3000 } })}\n`);
  while (output.length < 2 && Date.now() < deadline) await delay(10);
  assert.equal(output[1]?.result.status, 'open');
  const closed = once(child, 'close'); child.stdin.end();
  const [code] = await closed; assert.equal(code, 0);
});

test('failed worker ignoring SIGTERM is reaped before an immediate restart can spawn', async () => {
  const body = `process.on('SIGTERM', () => {}); process.stdin.resume();
let pending=''; process.stdin.on('data', c => { pending+=c; let i; while((i=pending.indexOf('\\n'))>=0) {
const q=JSON.parse(pending.slice(0,i)); pending=pending.slice(i+1);
if(q.method==='hello') { console.log(JSON.stringify({jsonrpc:'2.0',id:q.id,result:{protocol:{major:1,minor:0},implementationVersion:'test',buildId:'integration-test',features:['deadlines','generation','base64-frames'],maxMessageBytes:1500000,maxFrameBytes:1048576}}));
setTimeout(()=>console.log('{}'),40); } }}); setInterval(()=>{},1000);`;
  const value = adapter({ command: process.execPath, args: ['-e', body], stopTimeoutMs: 30 });
  const failures = [];
  value.onState(state => { if (state.state === 'failed') failures.push(state); });
  await value.start(); const oldPid = value.pid;
  const limit = Date.now() + 3000;
  while (!failures.length && Date.now() < limit) await delay(5);
  assert.ok(failures.length);
  await value.start();
  assert.notEqual(value.pid, oldPid);
  assert.throws(() => process.kill(oldPid, 0), error => error.code === 'ESRCH');
  await value.stop(); assert.equal(value.pid, null);
});

test('facade session composes with the real standalone Python worker and controls', async () => {
  const { EventEmitter } = await import('node:events');
  const { createSession } = await import('../packages/hellgato/src/session/index.mjs');
  const { createSimulatorProfile } = await import('../packages/hellgato/src/testing/index.mjs');
  const value = adapter({ descriptor: { id: 'hellgato.fake-sidecar', version: '0.0.0-dev', modelIds: ['buttons-dial'],
    platforms: ['any'], architectures: ['any'], apiVersion: 1, protocol: { major: 1, minor: 0 } } });
  await value.start(); const [device] = await value.discover();
  const bridge = new EventEmitter();
  bridge.ports = { primary: null, child: null };
  bridge.start = async () => {}; bridge.stop = async () => {}; bridge.sendInput = () => true;
  const session = createSession({ adapter: value, deviceId: device.id, profile: createSimulatorProfile(device), bridgeFactory: () => bridge });
  try {
    await session.start(); assert.equal(session.state, 'ready');
    bridge.emit('output', { type: 'control', control: 'brightness', value: 42, sequence: 1,
      deviceId: device.id, sessionId: session.id, generation: session.generation });
    const limit = Date.now() + 2000;
    while ((await value.getState()).brightness !== 42 && Date.now() < limit) await delay(10);
    assert.equal((await value.getState()).brightness, 42);
  } finally { await session.stop(); }
  assert.equal(value.pid, null);
});

test('stopped sidecar can be owned by a new facade session with its own generation', async () => {
  const { EventEmitter } = await import('node:events');
  const { createSession } = await import('../packages/hellgato/src/session/index.mjs');
  const { createSimulatorProfile } = await import('../packages/hellgato/src/testing/index.mjs');
  const value = adapter({ descriptor: { id: 'hellgato.fake-sidecar', version: '0.0.0-dev', modelIds: ['buttons-dial'],
    platforms: ['any'], architectures: ['any'], apiVersion: 1, protocol: { major: 1, minor: 0 } } });
  await value.start(); const [device] = await value.discover();
  const bridgeFactory = () => {
    const bridge = new EventEmitter();
    return Object.assign(bridge, { ports: { primary: null, child: null }, start: async () => {}, stop: async () => {}, sendInput: () => true });
  };
  for (let index = 0; index < 2; index++) {
    const session = createSession({ adapter: value, deviceId: device.id, profile: createSimulatorProfile(device), bridgeFactory });
    try { await session.start(); assert.equal(session.state, 'ready'); assert.equal(session.generation, 1); }
    finally { await session.stop(); }
    assert.equal(value.pid, null);
  }
});

test('concurrent close is idempotent and cannot clear a subsequently opened device', async () => {
  const body = `let pending=''; let closes=0;
process.stdin.on('data', c => { pending+=c; let i; while((i=pending.indexOf('\\n'))>=0) {
const q=JSON.parse(pending.slice(0,i)); pending=pending.slice(i+1);
const reply=result=>console.log(JSON.stringify({jsonrpc:'2.0',id:q.id,result}));
if(q.method==='hello') reply({protocol:{major:1,minor:0},implementationVersion:'test',buildId:'integration-test',features:['deadlines','generation','base64-frames'],maxMessageBytes:1500000,maxFrameBytes:1048576});
else if(q.method==='close' && ++closes===2) setTimeout(()=>reply({status:'closed'}),30);
else reply({status:q.method==='open'?'open':q.method==='close'?'closed':'applied'});
}});`;
  const value = adapter({ command: process.execPath, args: ['-e', body] });
  await value.start(); await value.open('fixture');
  const first = value.close(); const second = value.close();
  await first; await value.open('fixture'); await second;
  assert.equal((await value.setControl({ type: 'control', control: 'sleep' })).status, 'applied');
  await value.stop();
});

function protocolWorker(handler = '', helloOverrides = {}) {
  const greeting = { protocol: { major: 1, minor: 0 }, implementationVersion: 'test', buildId: 'integration-test',
    features: ['deadlines', 'generation', 'base64-frames'], maxMessageBytes: 1500000, maxFrameBytes: 1048576, ...helloOverrides };
  return `let pending=''; let count=0; process.stdin.on('data', c => { pending+=c; let i;
while((i=pending.indexOf('\\n'))>=0) {
const q=JSON.parse(pending.slice(0,i)); pending=pending.slice(i+1);
const reply=result=>console.log(JSON.stringify({jsonrpc:'2.0',id:q.id,result}));
const fail=()=>console.log(JSON.stringify({jsonrpc:'2.0',id:q.id,error:{code:-32000,message:'Close failed',data:{code:'ERR_CLOSE_FAILED'}}}));
if(q.method==='hello') { const greeting=${JSON.stringify(greeting)}; ${handler.includes('DELAY_HELLO') ? 'setTimeout(()=>reply(greeting),80)' : 'reply(greeting)'}; }
else { ${handler.replace('DELAY_HELLO', '')} reply({status:q.method==='close'?'closed':q.method==='open'?'open':'applied'}); }
}});`;
}

test('failed close retains ownership and permits a cleanup retry', async () => {
  const value = adapter({ command: process.execPath, args: ['-e', protocolWorker("if(q.method==='close' && count++===0) { fail(); continue; }")] });
  await value.start(); await value.open('fixture');
  await assert.rejects(value.close(), { code: 'ERR_CLOSE_FAILED' });
  await assert.rejects(value.open('fixture'), { code: 'ERR_DEVICE_BUSY' });
  assert.equal((await value.close()).status, 'closed');
  await value.open('fixture'); await value.stop();
});

test('open cannot reach a worker before the hello handshake is verified', async () => {
  const value = adapter({ command: process.execPath, args: ['-e', protocolWorker('DELAY_HELLO')] });
  const starting = value.start();
  const deadline = Date.now() + 2000;
  while (!value.pid && Date.now() < deadline) await delay(1);
  assert.ok(value.pid); assert.equal(value.status, 'starting');
  await assert.rejects(value.open('fixture'), { code: 'ERR_WORKER_OFFLINE' });
  await starting; await value.open('fixture'); await value.stop();
});

test('negotiated worker frame limit is enforced before transmitting output', async () => {
  const value = adapter({ command: process.execPath, args: ['-e', protocolWorker('', { maxFrameBytes: 3 })] });
  await value.start(); await value.open('fixture');
  assert.throws(() => value.setOutput(frame()), { code: 'ERR_FRAME_TOO_LARGE' });
  assert.equal((await value.setOutput(frame([1, 2, 3], 1, { x: 0, y: 0, width: 1, height: 1 }))).status, 'applied');
  await value.stop();
});

test('expected build override cannot contradict a verified manifest identity', async () => {
  const executableSha256 = createHash('sha256').update(await readFile(process.execPath)).digest('hex');
  const value = adapter({ command: process.execPath, args: ['-e', protocolWorker()],
    manifest: { schemaVersion: 1, buildId: 'different-manifest-build', platform: process.platform, arch: process.arch, executableSha256 } });
  await assert.rejects(value.start(), { code: 'ERR_WORKER_BUILD_ID' });
  assert.equal(value.pid, null); await value.stop();
});

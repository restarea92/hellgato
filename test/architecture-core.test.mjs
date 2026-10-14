import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { connect, createServer } from 'node:net';
import { once } from 'node:events';
import { createDeviceBridge, createBridge, createN4ProProfile } from '../packages/core/src/bridge.mjs';
import * as contracts from '../packages/core/src/contracts/index.mjs';
import { encodeCoraFrame } from '../work/cora-runtime/cora/frame.js';
const fixtures = JSON.parse(readFileSync(new URL('./fixtures/architecture-contracts.json', import.meta.url)));
const device = fixtures.valid.find(f => f.contract === 'DeviceDescriptor').value;
function target() { return {
  id: 'example-two-keys',
  geometry: { rows: 1, columns: 2, keyCount: 2, keyWidth: 2, keyHeight: 2, encoderCount: 0, touchWidth: 0, touchHeight: 0, productName: 'Fixture' },
  config: { serialNumber: 'TESTDOCK', childSerialNumber: 'TESTCHILD', dockFirmwareVersion: '1.0', childFirmwareVersion: '1.0', productId: 0x80, macAddress: [2, 0, 0, 0, 0, 1] },
}; }
function bridge() { return createDeviceBridge({ device, target: target(), sessionId: 'fixture-session', port: 0, childPort: 0 }); }
async function child(bridge) {
  const connection = once(bridge, 'clientConnected');
  const socket = connect(bridge.ports.child, '127.0.0.1');
  await connection; socket.resume(); return socket;
}

test('canonical contracts accept/reject shared fixtures without coercion', () => {
  for (const { contract, value } of fixtures.valid) assert.equal(contracts[`validate${contract}`](value), value);
  for (const { contract, value } of fixtures.invalid) assert.throws(() => contracts[`validate${contract}`](value), { code: 'ERR_CONTRACT' });
  assert.throws(() => contracts.validateDeviceDescriptor({ ...device, malicious: true }), /unknown property/);
});

test('strict bridge requires explicit device and target and construction has no listening I/O', () => {
  assert.throws(() => createDeviceBridge(), /required/);
  assert.throws(() => createDeviceBridge({ device }), { code: 'ERR_CONTRACT' });
  const value = bridge();
  assert.equal(value.state, 'idle');
  assert.deepEqual(value.ports, { primary: null, child: null });
  assert.equal(value.primaryConnected, false); assert.equal(value.childConnected, false);
  assert.equal(value.deviceId, 'buttons-1'); assert.equal(value.sessionId, 'fixture-session');
  assert.equal(value.sendKey(0, 'down'), false);
  assert.throws(() => value.sendDial({ index: 0, kind: 'rotate', delta: 1 }), RangeError);
  assert.throws(() => value.sendTouch({ type: 'tap', x: 0, y: 0 }), RangeError);
});

test('physical descriptors may exceed Stream Deck encoder limit; selected targets cannot', () => {
  const candidate = structuredClone(device);
  candidate.capabilities.dials = 8;
  candidate.capabilities.dialPress = 'native'; candidate.capabilities.dialRelease = 'native';
  assert.equal(contracts.validateDeviceDescriptor(candidate), candidate);
  assert.doesNotThrow(() => createDeviceBridge({ device: candidate, target: target() }));
  const badTarget = target(); badTarget.geometry.encoderCount = 8;
  assert.throws(() => createDeviceBridge({ device: candidate, target: badTarget }), /Stream Deck target/);
});

test('strict bridge serializes lifecycle and drops stale identity, generation, and sequence', async () => {
  const value = bridge();
  await Promise.all([value.start(), value.start()]);
  let socket = await child(value);
  const event = { deviceId: value.deviceId, sessionId: value.sessionId, generation: value.generation, sequence: 1, type: 'key', index: 0, state: 'down' };
  try {
    assert.equal(value.submitInput({ ...event, deviceId: 'other' }), false);
    assert.equal(value.submitInput({ ...event, sessionId: 'other' }), false);
    assert.equal(value.submitInput({ ...event, generation: 0 }), false);
    assert.equal(value.submitInput(event), true);
    assert.equal(value.submitInput(event), false);
    assert.equal(value.sendInput({ ...event, sequence: 2, state: 'up' }), true);
    await Promise.all([value.stop(), value.stop()]);
    assert.equal(value.state, 'stopped');
    await value.start(); socket = await child(value);
    assert.equal(value.generation, 2);
    assert.equal(value.submitInput({ ...event, sequence: 3 }), false);
    assert.equal(value.submitInput({ ...event, generation: 2, sequence: 1 }), true);
  } finally { socket.destroy(); await value.stop(); }
});

test('strict bridge failed-start rollback frees acquired child port and permits retry', async () => {
  const occupied = createServer(); occupied.listen(0, '127.0.0.1'); await once(occupied, 'listening');
  const value = createDeviceBridge({ device, target: target(), port: occupied.address().port, childPort: 0 });
  try {
    await assert.rejects(value.start(), { code: 'EADDRINUSE' });
    assert.equal(value.state, 'failed'); assert.deepEqual(value.ports, { primary: null, child: null });
  } finally { await new Promise(resolve => occupied.close(resolve)); }
  try { await value.start(); assert.equal(value.generation, 1); } finally { await value.stop(); }
});

test('display output is normalized and validated, independent of vendor event objects', async () => {
  const value = bridge(); await value.start(); const socket = await child(value);
  try {
    const promise = once(value, 'output');
    const fixture = fixtures.valid.find(f => f.contract === 'DisplayFrame').value;
    const bytes = Buffer.from(fixture.data, 'base64');
    const payload = Buffer.alloc(8 + bytes.length); payload.set([2, 7, 1, 1]); payload.writeUInt16LE(bytes.length, 4); bytes.copy(payload, 8);
    socket.write(encodeCoraFrame(payload, 0, 0, 1));
    const [output] = await promise;
    assert.equal(output.surfaceId, 'key:1'); assert.equal(output.type, 'frame');
    assert.equal(output.width, 2); assert.equal(output.height, 2); assert.equal(output.data, fixture.data);
    assert.equal(output.deviceId, device.id); assert.equal(output.sessionId, value.sessionId);
    assert.equal(contracts.validateDisplayFrame(output), output);
  } finally { socket.destroy(); await value.stop(); }
});

test('legacy root exports and no-argument default remain callable', async () => {
  const options = createN4ProProfile(); assert.equal(options.geometry.keyCount, 10);
  const value = await createBridge(); assert.deepEqual(value.ports, { primary: null, child: null });
});

test('core canonical source excludes app, physical runtime, work paths and strict legacy imports', () => {
  const root = new URL('../packages/core/src/', import.meta.url);
  const files = readdirSync(root, { recursive: true }).filter(path => path.endsWith('.mjs') && !path.startsWith('vendor/'));
  for (const path of files) {
    const source = readFileSync(new URL(path, root), 'utf8');
    assert.doesNotMatch(source, /(?:from\s+|import\()["'][^"']*(?:\/app\/|\/apps\/|\/adapters\/|\/work\/)/, path);
    if (path.startsWith('bridge/') || path.startsWith('hosts/') || path.startsWith('contracts/')) assert.doesNotMatch(source, /(?:from\s+|import\()["'][^"']*\/legacy\//, path);
  }
  const build = readFileSync(new URL('../scripts/build-npm.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(build, /app\/cora|source\.replace/);
});


test('large compressed payload checks stay bounded and malformed padding is rejected', () => {
  const frame = fixtures.valid.find(f => f.contract === 'DisplayFrame').value;
  assert.throws(() => contracts.validateDisplayFrame({ ...frame, data: 'A'.repeat(1_398_104) }), { code: 'ERR_CONTRACT' });
  assert.throws(() => contracts.validateDisplayFrame({ ...frame, data: 'AAAA=' }), { code: 'ERR_CONTRACT' });
  assert.throws(() => contracts.validateDisplayFrame({ ...frame, data: 'A'.repeat(1_398_108) }), { code: 'ERR_CONTRACT' });
});

test('legacy restart preserves updated sleep duration without an output subscriber', async () => {
  const value = await createBridge({ port: 0, childPort: 0 }); await value.start();
  let socket = await child(value);
  try {
    const duration = once(value, 'sleepDuration');
    socket.write(encodeCoraFrame(Buffer.from([3, 0x0d, 75, 0, 0, 0]), 0, 1, 20));
    assert.equal((await duration)[0], 75);
    await value.stop(); await value.start(); socket = await child(value);
    const received = new Promise(resolve => {
      let bytes = Buffer.alloc(0);
      socket.on('data', chunk => {
        bytes = Buffer.concat([bytes, chunk]);
        while (bytes.length >= 16) {
          const length = bytes.readUInt32LE(12);
          if (bytes.length < 16 + length) return;
          const payload = bytes.subarray(16, 16 + length); bytes = bytes.subarray(16 + length);
          if (payload[0] === 0x0a) resolve(payload.readInt32LE(2));
        }
      });
    });
    socket.write(encodeCoraFrame(Buffer.from([0x0a]), 0x8000, 2, 21));
    assert.equal(await received, 75);
  } finally { socket.destroy(); await value.stop(); }
});


test('schema evaluator rejects unsupported keywords, types and references at import', async () => {
  const schema = JSON.parse(readFileSync(new URL('../packages/core/schemas/contracts.schema.json', import.meta.url)));
  const source = readFileSync(new URL('../packages/core/src/contracts/index.mjs', import.meta.url), 'utf8');
  for (const [field, value, message] of [['minProperties', 1, /Unsupported contract schema keyword/], ['type', 'number', /Unsupported contract schema type/], ['$ref', '#/$defs/Missing', /Unknown contract schema reference/]]) {
    const copy = structuredClone(schema); copy.$defs.Capability[field] = value;
    const altered = source.replace(/^import (\w+) from ['"][^'"]+contracts\.schema\.json['"][^\n]+/m, (_, binding) => `const ${binding} = ${JSON.stringify(copy)};`);
    await assert.rejects(import(`data:text/javascript;base64,${Buffer.from(altered).toString('base64')}`), message);
  }
});

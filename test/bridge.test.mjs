import test from 'node:test';
import assert from 'node:assert/strict';
import { connect, createServer } from 'node:net';
import { once } from 'node:events';
import { createBridge, createN4ProProfile } from '../app/cora/bridge.mjs';
import { encodeCoraFrame } from '../work/cora-runtime/cora/frame.js';

async function connectChild(bridge) {
  const connected = once(bridge, 'clientConnected');
  const socket = connect(bridge.ports.child, '127.0.0.1');
  await once(socket, 'connect');
  const [{ role }] = await connected;
  assert.equal(role, 'child');
  return socket;
}

function receive(socket, predicate) {
  return new Promise((resolve, reject) => {
    let pending = Buffer.alloc(0);
    const timeout = setTimeout(() => finish(new Error('Expected frame was not received')), 2000);
    function finish(error, value) {
      clearTimeout(timeout);
      socket.off('data', data);
      socket.off('error', fail);
      if (error) reject(error); else resolve(value);
    }
    function fail(error) { finish(error); }
    function data(chunk) {
      pending = Buffer.concat([pending, chunk]);
      while (pending.length >= 16) {
        const size = pending.readUInt32LE(12);
        if (pending.length < 16 + size) return;
        const payload = pending.subarray(16, 16 + size);
        pending = pending.subarray(16 + size);
        if (predicate(payload)) return finish(null, payload);
      }
    }
    socket.on('data', data);
    socket.on('error', fail);
  });
}

test('bridge starts explicitly, serializes lifecycle calls, and restarts', async () => {
  const bridge = await createBridge({ port: 0, childPort: 0 });
  assert.deepEqual(bridge.ports, { primary: null, child: null });
  assert.equal(bridge.sendKey(0, 'down'), false);
  await Promise.all([bridge.start(), bridge.start()]);
  assert.ok(bridge.ports.primary > 0);
  assert.ok(bridge.ports.child > 0);
  assert.notEqual(bridge.ports.primary, bridge.ports.child);
  await Promise.all([bridge.stop(), bridge.stop()]);
  assert.deepEqual(bridge.ports, { primary: null, child: null });
  await bridge.start();
  await bridge.stop();
});

test('public bridge delivers key, dial, touch, and received image bytes over TCP', async () => {
  const bridge = await createBridge({ ...createN4ProProfile(42, 7), port: 0, childPort: 0 });
  await bridge.start();
  let socket;
  try {
    socket = await connectChild(bridge);
    assert.equal(bridge.childConnected, true);
    const key = receive(socket, payload => payload[0] === 1 && payload[1] === 0);
    assert.equal(bridge.sendKey(9, 'down'), true);
    assert.equal((await key)[13], 1);
    bridge.sendKey(9, 'up');
    const dial = receive(socket, payload => payload[0] === 1 && payload[1] === 3);
    bridge.sendDial({ kind: 'rotate', index: 3, delta: -1 });
    assert.equal((await dial).readInt8(8), -1);
    const touch = receive(socket, payload => payload[0] === 1 && payload[1] === 2);
    bridge.sendTouch({ type: 'swipe', x: 799, y: 99, endX: 0, endY: 0 });
    assert.equal((await touch).readUInt16LE(6), 799);
    const image = once(bridge, 'image');
    const bytes = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);
    const payload = Buffer.alloc(8 + bytes.length);
    payload.set([2, 7, 9, 1]);
    payload.writeUInt16LE(bytes.length, 4);
    bytes.copy(payload, 8);
    socket.write(encodeCoraFrame(payload, 0, 0, 10));
    const [event] = await image;
    assert.equal(event.keyIndex, 9);
    assert.deepEqual(event.data, bytes);
    assert.equal(event.format, 'jpeg');
  } finally {
    socket?.destroy();
    await bridge.stop();
  }
});

test('display controls are decoded, acknowledged, and sleep timing is readable', { timeout: 5000 }, async () => {
  const bridge = await createBridge({ port: 0, childPort: 0 });
  await bridge.start();
  let socket;
  try {
    socket = await connectChild(bridge);
    const levels = [];
    const commands = [];
    const durations = [];
    bridge.on('brightness', level => levels.push(level));
    bridge.on('displayCommand', command => commands.push(command));
    bridge.on('sleepDuration', seconds => durations.push(seconds));
    const ack = receive(socket, payload => payload.length === 4);
    socket.write(encodeCoraFrame(Buffer.from([3, 8, 35]), 0xc000, 1, 101));
    await ack;
    const fillAck = receive(socket, payload => payload.length === 4);
    socket.write(encodeCoraFrame(Buffer.from([3, 5, 1, 2, 3]), 0xc000, 1, 102));
    await fillAck;
    const sleepAck = receive(socket, payload => payload.length === 4);
    socket.write(encodeCoraFrame(Buffer.from([3, 0x0d, 60, 0, 0, 0]), 0xc000, 1, 103));
    await sleepAck;
    assert.deepEqual(levels, [35]);
    assert.deepEqual(commands, [{ type: 'fill', color: [1, 2, 3] }]);
    assert.deepEqual(durations, [60]);
    const logoAck = receive(socket, payload => payload.length === 4);
    socket.write(encodeCoraFrame(Buffer.from([3, 2]), 0xc000, 1, 104));
    await logoAck;
    const keyAck = receive(socket, payload => payload.length === 4);
    socket.write(encodeCoraFrame(Buffer.from([3, 6, 9, 4, 5, 6]), 0xc000, 1, 105));
    await keyAck;
    assert.deepEqual(commands.slice(1), [{ type: 'showLogo' }, { type: 'fill', keyIndex: 9, color: [4, 5, 6] }]);
    const duration = receive(socket, payload => payload[0] === 0x0a);
    socket.write(encodeCoraFrame(Buffer.from([0x0a]), 0x8000, 2, 106));
    assert.equal((await duration).readInt32LE(2), 60);
  } finally {
    socket?.destroy();
    await bridge.stop();
  }
});

test('full LCD frames cross TCP and incomplete transfers do not cross sessions', async () => {
  const bridge = await createBridge({ port: 0, childPort: 0, sleepDuration: 120 });
  await bridge.start();
  let socket;
  const images = [];
  let started = 0;
  bridge.on('lcdFrameStarted', () => started++);
  bridge.on('lcdImage', event => images.push(event));
  function chunk(index, bytes, last = false) {
    const payload = Buffer.alloc(8 + bytes.length);
    payload.set([2, 8, 0, Number(last)]);
    payload.writeUInt16LE(bytes.length, 4);
    payload.writeUInt16LE(index, 6);
    bytes.copy(payload, 8);
    socket.write(encodeCoraFrame(payload, 0x4000, 0, 200 + index));
  }
  try {
    socket = await connectChild(bridge);
    const duration = receive(socket, payload => payload[0] === 0x0a);
    socket.write(encodeCoraFrame(Buffer.from([0x0a]), 0x8000, 2, 199));
    assert.equal((await duration).readInt32LE(2), 120);
    let ack = receive(socket, payload => payload.length === 4);
    chunk(0, Buffer.from([0xff, 0xd8]));
    await ack;
    assert.equal(started, 1);
    assert.equal(images.length, 0);
    const received = once(bridge, 'lcdImage');
    chunk(1, Buffer.from([0xff, 0xd9]), true);
    assert.deepEqual((await received)[0], { data: Buffer.from([0xff, 0xd8, 0xff, 0xd9]), format: 'jpeg' });
    ack = receive(socket, payload => payload.length === 4);
    chunk(0, Buffer.from([1, 2]));
    await ack;
    const disconnected = once(bridge, 'clientDisconnected');
    socket.destroy();
    await disconnected;
    socket = await connectChild(bridge);
    ack = receive(socket, payload => payload.length === 4);
    chunk(1, Buffer.from([3, 4]), true);
    await ack;
    assert.equal(images.length, 1);
    assert.equal(started, 2);
  } finally {
    socket?.destroy();
    await bridge.stop();
  }
});

test('failed startup frees its child port and permits retry', async () => {
  const occupied = createServer();
  occupied.listen(0, '127.0.0.1');
  await once(occupied, 'listening');
  const port = occupied.address().port;
  const bridge = await createBridge({ port, childPort: 0 });
  try {
    await assert.rejects(bridge.start(), { code: 'EADDRINUSE' });
    assert.deepEqual(bridge.ports, { primary: null, child: null });
  } finally {
    await new Promise(resolve => occupied.close(resolve));
  }
  await bridge.start();
  await bridge.stop();
});

test('public input validation rejects invalid geometry, ports, keys, and gestures', async () => {
  await assert.rejects(createBridge({ port: -1 }), RangeError);
  await assert.rejects(createBridge({ geometry: { keyCount: 11 } }), RangeError);
  await assert.rejects(createBridge({ childPort: 5343 }), /different/);
  const bridge = await createBridge();
  for (const index of [-1, 10, 1.5, '1']) assert.throws(() => bridge.sendKey(index, 'down'), RangeError);
  assert.throws(() => bridge.sendKey(0, 'held'), TypeError);
  assert.throws(() => bridge.sendDial({ kind: 'rotate', index: 0, delta: 128 }), RangeError);
  assert.throws(() => bridge.sendTouch({ type: 'tap', x: 800, y: 0 }), RangeError);
  assert.throws(() => bridge.sendTouch({ type: 'swipe', x: 0, y: 0 }), RangeError);
});

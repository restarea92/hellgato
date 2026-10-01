import test from 'node:test';
import assert from 'node:assert/strict';
import { connect } from 'node:net';
import { once } from 'node:events';
import { profile } from '../app/cora/profiles.mjs';
import { buildCapabilitiesPacket } from '../work/cora-runtime/cora/responses.js';
import { buildEncoderRotateReport, buildEncoderPressReport } from '../work/cora-runtime/cora/plus-reports.js';

globalThis.tjs = { env: { DECKBRIDGE_BIND: '127.0.0.1' } };
const { ElgatoServer } = await import('../work/cora-runtime/cora/primary-server.js');
const { ElgatoChildServer } = await import('../work/cora-runtime/cora/child-server.js');

test('saved identities retain their original serial format', () => {
  const { config } = profile('target', 42);
  assert.equal(config.serialNumber, 'HGDOCK000042');
  assert.equal(config.childSerialNumber, 'HGMOCK000042');
  for (const invalid of [0, -1, 1000000, 'not-a-number']) {
    assert.throws(() => profile('target', invalid), /Identity/);
  }
});

test('back-to-back dial clicks deliver both release edges over TCP', { timeout: 5000 }, async () => {
  const { geometry, config } = profile('target');
  const child = new ElgatoChildServer(geometry, 0, config, false);
  let socket;
  try {
    await child.start();
    const accepted = once(child, 'clientConnected');
    socket = connect(child.server.address().port, '127.0.0.1');
    await once(socket, 'connect');
    await accepted;
    const expected = [];
    const reports = new Promise((resolve, reject) => {
      let pending = Buffer.alloc(0);
      const states = [];
      const timer = setTimeout(() => reject(new Error('Missing dial release reports')), 2000);
      socket.on('data', chunk => {
        pending = Buffer.concat([pending, chunk]);
        while (pending.length >= 16) {
          const size = pending.readUInt32LE(12);
          if (pending.length < 16 + size) break;
          const payload = pending.subarray(16, 16 + size);
          if (payload[0] === 1 && payload[1] === 3 && payload[4] === 0) {
            states.push([...payload.subarray(5, 9)]);
          }
          pending = pending.subarray(16 + size);
        }
        if (states.length === 16) {
          clearTimeout(timer);
          resolve(states);
        }
      });
    });
    for (let index = 0; index < 4; index++) {
      for (let click = 0; click < 2; click++) {
        const pressed = [0, 0, 0, 0];
        pressed[index] = 1;
        expected.push(pressed, [0, 0, 0, 0]);
        child.sendDial({ kind: 'press', index, state: 'down' });
        child.sendDial({ kind: 'press', index, state: 'up' });
      }
    }
    assert.deepEqual(await reports, expected);
  } finally {
    socket?.destroy();
    await child.stop();
  }
});

function receive(socket, predicate) {
  return new Promise((resolve, reject) => {
    let pending = Buffer.alloc(0);
    const timer = setTimeout(() => finish(new Error('Expected CORA frame was not received')), 2000);
    function finish(error, value) {
      clearTimeout(timer); socket.off('data', data); socket.off('error', fail);
      if (error) reject(error); else resolve(value);
    }
    function fail(error) { finish(error); }
    function data(chunk) {
      pending = Buffer.concat([pending, chunk]);
      while (pending.length >= 16) {
        if (pending.subarray(0, 4).toString('hex') !== '43938a41') return finish(new Error('Bad magic'));
        const size = pending.readUInt32LE(12);
        if (pending.length < size + 16) return;
        const frame = { flags: pending.readUInt16LE(4), id: pending.readUInt32LE(8), payload: pending.subarray(16, size + 16) };
        pending = pending.subarray(size + 16);
        if (predicate(frame)) return finish(null, frame);
      }
    }
    socket.on('data', data); socket.on('error', fail);
  });
}

for (const name of ['control', 'target']) {
  test(`${name}: capability response and all key/encoder inputs cross TCP`, { timeout: 10000 }, async () => {
    const { geometry, config } = profile(name);
    const child = new ElgatoChildServer(geometry, 0, config, false);
    await child.start();
    const childPort = child.server.address().port;
    const primary = new ElgatoServer(geometry, 0, true, { childPort });
    primary.setDeviceConfig(config);
    let socket, childSocket;
    try {
      await primary.start();
      socket = connect(primary.server.address().port, '127.0.0.1');
      await once(socket, 'connect');
      const response = receive(socket, frame => frame.id === 42);
      const header = Buffer.alloc(16);
      Buffer.from('43938a41', 'hex').copy(header);
      header[6] = 2; header.writeUInt32LE(42, 8); header.writeUInt32LE(2, 12);
      socket.write(Buffer.concat([header, Buffer.from([3, 0x1c])]));
      const frame = await response;
      assert.equal(frame.flags, 0x100);
      assert.equal(frame.payload.length, 1024);
      assert.deepEqual([...frame.payload.subarray(5, 8)], [2, geometry.columns, geometry.keyCount]);
      assert.equal(frame.payload.readUInt16LE(28), 0x84);
      assert.equal(frame.payload.readUInt16LE(126), childPort);
      const accepted = once(child, 'clientConnected');
      childSocket = connect(childPort, '127.0.0.1');
      await once(childSocket, 'connect');
      await accepted;
      for (let index = 0; index < geometry.keyCount; index++) {
        for (const state of ['down', 'up']) {
          const received = receive(childSocket, f => f.payload[0] === 1 && f.payload[1] === 0);
          child.sendKeyEvent(index, state);
          const { payload } = await received;
          assert.equal(payload[2], geometry.keyCount);
          assert.equal(payload[4 + index], state === 'down' ? 1 : 0);
        }
      }
      for (let index = 0; index < 4; index++) {
        for (const delta of [-128, -1, 1, 127]) {
          const received = receive(childSocket, f => f.payload[1] === 3);
          child.sendDial({ kind: 'rotate', index, delta });
          const { payload } = await received;
          assert.deepEqual([...payload.subarray(0, 5)], [1, 3, 5, 0, 1]);
          for (let other = 0; other < 4; other++) assert.equal(payload.readInt8(5 + other), other === index ? delta : 0);
        }
        for (const state of ['down', 'up']) {
          const received = receive(childSocket, f => f.payload[1] === 3);
          child.sendDial({ kind: 'press', index, state });
          const { payload } = await received;
          assert.equal(payload[4], 0);
          assert.equal(payload[5 + index], state === 'down' ? 1 : 0);
        }
      }
      const first = receive(childSocket, f => f.payload[1] === 3);
      child.sendDial({ kind: 'press', index: 0, state: 'down' });
      await first;
      const second = receive(childSocket, f => f.payload[1] === 3);
      child.sendDial({ kind: 'press', index: 3, state: 'down' });
      assert.deepEqual([...(await second).payload.subarray(5, 9)], [1, 0, 0, 1]);
      for (const [type, code] of [['tap', 1], ['hold', 2], ['swipe', 3]]) {
        const touch = receive(childSocket, f => f.payload[1] === 2);
        child.sendTouch({ type, x: 799, y: 99, endX: 0, endY: 0 });
        const { payload } = await touch;
        assert.equal(payload[4], code);
        assert.equal(payload.readUInt16LE(6), 799);
        assert.equal(payload.readUInt16LE(8), 99);
        if (type === 'swipe') {
          assert.equal(payload.readUInt16LE(10), 0);
          assert.equal(payload.readUInt16LE(12), 0);
        }
      }
    } finally {
      socket?.destroy(); childSocket?.destroy();
      await Promise.allSettled([primary.stop(), child.stop()]);
    }
  });
}

test('encoderCount is not serialized in upstream capabilities', () => {
  const { geometry, config } = profile('target');
  assert.deepEqual(buildCapabilitiesPacket(config, 5344, geometry), buildCapabilitiesPacket(config, 5344, { ...geometry, encoderCount: 0 }));
});
test('changing only key geometry changes capability bytes 6 and 7', () => {
  const { geometry, config } = profile('control');
  const control = buildCapabilitiesPacket(config, 5344, geometry);
  const target = buildCapabilitiesPacket(config, 5344, { ...geometry, columns: 5, keyCount: 10 });
  assert.deepEqual([...control.keys()].filter(i => control[i] !== target[i]), [6, 7]);
});
test('rotation saturates signed bytes and press reports preserve simultaneous buttons', () => {
  assert.equal(buildEncoderRotateReport(4, 0, -129).readInt8(5), -128);
  assert.equal(buildEncoderRotateReport(4, 3, 128).readInt8(8), 127);
  assert.deepEqual([...buildEncoderPressReport(4, 9).subarray(5, 9)], [1, 0, 0, 1]);
});

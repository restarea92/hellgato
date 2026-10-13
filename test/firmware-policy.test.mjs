import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter, once } from 'node:events';
import { connect } from 'node:net';
import { unsupportedWrite, withFirmwarePolicy } from '../app/cora/firmware-policy.mjs';
import { createBridge } from '../app/cora/bridge.mjs';
import { encodeCoraFrame, CoraFrameReader } from '../work/cora-runtime/cora/frame.js';

class StubServer extends EventEmitter {
  forwarded = [];
  disconnected = 0;
  client = { destroy: () => this.disconnected++ };
  handleCoraPacket(...arguments_) { this.forwarded.push(arguments_); }
}

test('unsupported child writes never reach display handlers or success ACKs', () => {
  const Server = withFirmwarePolicy(StubServer);
  const server = new Server();
  const rejected = [];
  server.on('operationRejected', event => rejected.push(event));
  for (const payload of [[3, 0x09], [3, 0xff], [2, 0x09, 1, 2, 3], [0x11, 1, 2, 3]]) {
    server.handleCoraPacket(0xc000, payload[0] === 2 ? 0 : 1, 42, Buffer.from(payload));
  }
  assert.equal(server.forwarded.length, 0);
  assert.equal(server.disconnected, 4);
  assert.equal(rejected.length, 4);
  assert.ok(rejected.every(event => event.reason === 'unsupported'));
});

test('firmware/version reads, discovery, and supported display writes retain their routing', () => {
  for (const role of ['primary', 'child']) {
    const Server = withFirmwarePolicy(StubServer, role);
    const server = new Server();
    for (const payload of [[5], [0xa4], [3, 0x87], [3, 0xfe]]) {
      server.handleCoraPacket(0x8000, 2, 42, Buffer.from(payload));
    }
    server.handleCoraPacket(0, 0, 43, Buffer.from([3, 0x1a]));
    server.handleCoraPacket(0, 0, 44, Buffer.from([3, 0x1c]));
    assert.equal(server.forwarded.length, 6);
    assert.equal(server.disconnected, 0);
  }
  for (const command of [2, 5, 6, 8, 0x0d]) {
    assert.equal(unsupportedWrite('child', 0xc000, 1, Buffer.from([3, command])), false);
  }
  for (const command of [1, 7, 8, 0x0b, 0x0c]) {
    assert.equal(unsupportedWrite('child', 0xc000, 0, Buffer.from([2, command])), false);
  }
  assert.equal(unsupportedWrite('child', 0x8000, 1, Buffer.from([5, 0x55, 0xaa, 0xd1, 0, 35])), false);
  assert.equal(unsupportedWrite('primary', 0, 0, Buffer.from([3, 0xfe])), false);
});

test('primary write guard precedes the generic feature RESULT response', () => {
  const Server = withFirmwarePolicy(StubServer, 'primary');
  const server = new Server();
  server.handleCoraPacket(0xc000, 1, 45, Buffer.from([3, 0xfe, 1, 2, 3]));
  assert.equal(server.forwarded.length, 0);
  assert.equal(server.disconnected, 1);
  server.handleCoraPacket(0x8000, 1, 46, Buffer.from([3, 0x87, 1, 2, 3]));
  assert.equal(server.forwarded.length, 0);
  assert.equal(server.disconnected, 2);
});

test('unsupported write closes its TCP request and reconnection still reads firmware', { timeout: 5000 }, async () => {
  const bridge = await createBridge({ port: 0, childPort: 0 });
  await bridge.start();
  let socket;
  try {
    for (const role of ['primary', 'child']) {
      const connected = once(bridge, 'clientConnected');
      socket = connect(bridge.ports[role], '127.0.0.1');
      await once(socket, 'connect');
      await connected;
      const rejected = once(bridge, 'operationRejected');
      const closed = once(socket, 'close');
      const received = [];
      const reader = new CoraFrameReader();
      socket.on('data', bytes => { reader.append(bytes); received.push(...reader.drainFrames()); });
      socket.write(encodeCoraFrame(Buffer.from([3, 0xfe, 1, 2, 3]), 0xc000, 1, 77));
      const [event] = await rejected;
      assert.equal(event.role, role);
      assert.equal(event.detail[0].reason, 'unsupported');
      await closed;
      assert.ok(received.every(frame => frame.messageId !== 77));

      socket = connect(bridge.ports[role], '127.0.0.1');
      await once(socket, 'connect');
      const reply = new Promise(resolve => {
        const next = new CoraFrameReader();
        socket.on('data', bytes => {
          next.append(bytes);
          const frame = next.drainFrames().find(item => item.messageId === 78);
          if (frame) resolve(frame);
        });
      });
      socket.write(encodeCoraFrame(Buffer.from(role === 'child' ? [5] : [3, 0x87]), 0x8000, 2, 78));
      const frame = await reply;
      assert.ok(frame.payload.includes(Buffer.from('2.00.026')));
      socket.destroy();
      await once(socket, 'close');
    }
  } finally {
    socket?.destroy();
    await bridge.stop();
  }
});

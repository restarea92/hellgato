import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter, once } from 'node:events';
import { connect } from 'node:net';
import { readFileSync } from 'node:fs';
import { watchStreamDeckPairing } from '../packages/core/src/hosts/streamdeck/target.mjs';
import { createDeviceBridge } from '../packages/core/src/bridge.mjs';
import { CoraFrameReader } from '../work/cora-runtime/cora/frame.js';
import { buildCapabilitiesPacket } from '../work/cora-runtime/cora/responses.js';

class Endpoint extends EventEmitter {
  hasClient = false;
  capabilities = 0;
  drops = 0;
  connect() { this.hasClient = true; this.emit('clientConnected'); }
  disconnect() { this.hasClient = false; this.emit('clientDisconnected'); }
  pushChildCapabilities() { this.capabilities++; }
  dropClient() { this.drops++; this.disconnect(); }
}

function fixture(t) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const primary = new Endpoint(), child = new Endpoint();
  const dispose = watchStreamDeckPairing(primary, child);
  t.after(dispose);
  return { primary, child, dispose, tick: ms => t.mock.timers.tick(ms) };
}

test('a stalled primary gets capabilities before only its socket is reset', t => {
  const f = fixture(t);
  f.primary.connect(); f.tick(2999);
  assert.equal(f.primary.capabilities, 0); assert.equal(f.primary.drops, 0);
  f.tick(1);
  assert.equal(f.primary.capabilities, 1); assert.equal(f.primary.drops, 0);
  f.tick(2999); assert.equal(f.primary.drops, 0);
  f.tick(1); assert.equal(f.primary.drops, 1); assert.equal(f.child.drops, 0);
});

test('the ordinary short probe reopened immediately never resets its primary', t => {
  const f = fixture(t);
  f.primary.connect(); f.child.connect(); f.tick(3); f.child.disconnect();
  f.tick(1); f.child.connect(); f.tick(10000);
  assert.equal(f.primary.capabilities, 0); assert.equal(f.primary.drops, 0);
});

test('a child arriving after the capability refresh cancels the pending reset', t => {
  const f = fixture(t);
  f.primary.connect(); f.tick(3000);
  assert.equal(f.primary.capabilities, 1);
  f.child.connect(); f.tick(10000);
  assert.equal(f.primary.drops, 0);
});

test('aborted child handshakes retry at most three times per failure streak', t => {
  const f = fixture(t);
  for (let i = 0; i < 5; i++) {
    f.primary.connect(); f.child.connect(); f.tick(100); f.child.disconnect();
    f.tick(3000); f.tick(3000);
  }
  assert.equal(f.primary.capabilities, 3); assert.equal(f.primary.drops, 3);
});

test('a stable child resets the budget while its normal unpair remains untouched', t => {
  const f = fixture(t);
  for (let i = 0; i < 3; i++) { f.primary.connect(); f.tick(3000); f.tick(3000); }
  f.primary.connect(); f.child.connect(); f.tick(2000); f.child.disconnect(); f.tick(10000);
  assert.equal(f.primary.drops, 3); assert.equal(f.primary.capabilities, 3);
  f.child.connect(); f.tick(10); f.child.disconnect(); f.tick(3000); f.tick(3000);
  assert.equal(f.primary.drops, 4); assert.equal(f.primary.capabilities, 4);
});

test('a primary loss cancels recovery without reconnecting either endpoint', t => {
  const f = fixture(t);
  f.primary.connect(); f.tick(3000); f.primary.disconnect(); f.tick(10000);
  assert.equal(f.primary.capabilities, 1); assert.equal(f.primary.drops, 0);
  assert.equal(f.child.hasClient, false); assert.equal(f.primary.hasClient, false);
});

test('disposing recovery cancels timers and removes only its own listeners', t => {
  const f = fixture(t), external = () => {};
  f.primary.on('clientConnected', external);
  f.primary.connect(); f.tick(3000); f.dispose(); f.dispose(); f.tick(10000);
  assert.equal(f.primary.drops, 0); assert.equal(f.primary.capabilities, 1);
  assert.equal(f.primary.listenerCount('clientConnected'), 1);
  assert.equal(f.primary.listenerCount('clientDisconnected'), 0);
  assert.equal(f.child.listenerCount('clientConnected'), 0);
  assert.equal(f.child.listenerCount('clientDisconnected'), 0);
  f.primary.connect(); f.tick(10000); assert.equal(f.primary.capabilities, 1);
});

test('real bridge recovery preserves listeners and a replacement primary can attach', { timeout: 10000 }, async () => {
  const fixtures = JSON.parse(readFileSync(new URL('./fixtures/architecture-contracts.json', import.meta.url)));
  const device = fixtures.valid.find(f => f.contract === 'DeviceDescriptor').value;
  const target = { id: 'recovery-fixture',
    geometry: { rows: 1, columns: 2, keyCount: 2, keyWidth: 2, keyHeight: 2, encoderCount: 0, touchWidth: 0, touchHeight: 0, productName: 'Fixture' },
    config: { serialNumber: 'TESTDOCK', childSerialNumber: 'TESTCHILD', dockFirmwareVersion: '1.0', childFirmwareVersion: '1.0', productId: 0x80, macAddress: [2, 0, 0, 0, 0, 1] } };
  const bridge = createDeviceBridge({ device, target, port: 0, childPort: 0 });
  await bridge.start();
  const ports = bridge.ports, frames = [];
  let socket = connect(ports.primary, '127.0.0.1');
  const reader = new CoraFrameReader();
  socket.on('data', bytes => { reader.append(bytes); frames.push(...reader.drainFrames()); });
  try {
    await once(socket, 'connect');
    const disconnected = once(bridge, 'clientDisconnected');
    await once(socket, 'close');
    await disconnected;
    const capabilities = buildCapabilitiesPacket(target.config, ports.child, target.geometry);
    assert.ok(frames.some(frame => frame.payload.equals(capabilities) && frame.messageId === 0));
    assert.equal(bridge.state, 'ready'); assert.deepEqual(bridge.ports, ports);
    assert.equal(bridge.primaryConnected, false); assert.equal(bridge.childConnected, false);
    const connected = once(bridge, 'clientConnected');
    socket = connect(ports.primary, '127.0.0.1'); socket.resume();
    const [event] = await connected;
    assert.equal(event.role, 'primary'); assert.equal(bridge.primaryConnected, true);
  } finally { socket.destroy(); await bridge.stop(); }
});

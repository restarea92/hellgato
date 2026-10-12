import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { withDisplayCommands } from '../app/cora/display-server.mjs';
import { loadDisplayState, saveDisplayState } from '../app/cora/display-state.mjs';

class StubServer extends EventEmitter {
  childGeometry = { keyCount: 10 };
  sendAckNak() {}
  onClientConnected() {}
}
const DisplayServer = withDisplayCommands(StubServer);

function chunk(index, size, last = false) {
  const payload = Buffer.alloc(8 + size, 0x55);
  payload.set([2, 8, 0, Number(last)]);
  payload.writeUInt16LE(size, 4);
  payload.writeUInt16LE(index, 6);
  return payload;
}

test('full LCD assembler rejects missing, repeated, malformed and oversized chunks', () => {
  const server = new DisplayServer();
  assert.equal(server.acceptLcdChunk(chunk(1, 1, true)), null);
  server.acceptLcdChunk(chunk(0, 1));
  assert.equal(server.acceptLcdChunk(chunk(2, 1, true)), null);
  server.acceptLcdChunk(chunk(0, 1));
  server.acceptLcdChunk(chunk(1, 1));
  assert.equal(server.acceptLcdChunk(chunk(1, 1, true)), null);
  const malformed = chunk(0, 1, true);
  malformed.writeUInt16LE(2, 4);
  assert.equal(server.acceptLcdChunk(malformed), null);
  assert.equal(server.acceptLcdChunk(chunk(0, 0, true)), null);
  for (let index = 0; index < 17; index++) assert.equal(server.acceptLcdChunk(chunk(index, 65535)), null);
  assert.equal(server.acceptLcdChunk(chunk(17, 1, true)), null);
  assert.deepEqual(server.acceptLcdChunk(chunk(0, 2, true)), Buffer.from([0x55, 0x55]));
});

test('invalid display controls do not emit events or acknowledge success', () => {
  const server = new DisplayServer();
  const events = [];
  for (const event of ['brightness', 'sleepDuration', 'displayCommand']) server.on(event, () => events.push(event));
  let acknowledged = 0;
  server.sendAckNak = () => acknowledged++;
  for (const payload of [[3, 5, 1], [3, 6, 10, 1, 2, 3], [3, 8, 101], [3, 0x0d, 0], [3, 0x0d, 255, 255, 255, 255]]) {
    server.handleSendReport(Buffer.from(payload), 0x4000, 1);
  }
  assert.deepEqual(events, []);
  assert.equal(acknowledged, 0);
});

test('LCD frame start is emitted once per nonempty valid first chunk before completion', () => {
  const server = new DisplayServer();
  const events = [];
  server.on('lcdFrameStarted', () => events.push('start'));
  server.on('lcdImage', () => events.push('image'));
  const malformed = chunk(0, 1);
  malformed.writeUInt16LE(2, 4);
  for (const payload of [Buffer.alloc(7), malformed, chunk(0, 0, true), chunk(1, 1, true)]) {
    server.handleOutputReport(8, 0, 0, 1, payload);
  }
  assert.deepEqual(events, []);
  server.handleOutputReport(8, 0, 0, 1, chunk(0, 2));
  assert.deepEqual(events, ['start']);
  server.handleOutputReport(8, 0, 0, 2, chunk(1, 2, true));
  assert.deepEqual(events, ['start', 'image']);
  server.handleOutputReport(8, 0, 0, 3, chunk(0, 2, true));
  assert.deepEqual(events, ['start', 'image', 'start', 'image']);
});

test('sleep duration reloads from disk without modifying other device settings', () => {
  const directory = mkdtempSync(join(tmpdir(), 'hellgato-display-'));
  try {
    const path = pathToFileURL(join(directory, 'display-HGMOCK000042.json'));
    const other = pathToFileURL(join(directory, 'display-HGMOCK000043.json'));
    assert.deepEqual(loadDisplayState(path), { sleepDuration: 0 });
    saveDisplayState(path, { sleepDuration: 600 });
    assert.deepEqual(loadDisplayState(path), { sleepDuration: 600 });
    assert.deepEqual(loadDisplayState(other), { sleepDuration: 0 });
    const original = readFileSync(path, 'utf8');
    assert.throws(() => saveDisplayState(path, { sleepDuration: -1 }), RangeError);
    assert.equal(readFileSync(path, 'utf8'), original);
    writeFileSync(path, '{"sleepDuration":"600"}');
    assert.throws(() => loadDisplayState(path), RangeError);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

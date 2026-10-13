import { createInterface } from 'node:readline';
import { mkdirSync, appendFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { profile } from './profiles.mjs';
import { createBridge } from './bridge.mjs';
import { loadDisplayState, saveDisplayState } from './display-state.mjs';

const name = process.argv[2] ?? 'target';
const { geometry, config } = profile(name, process.argv[3]);
// Dock and child identities are independently persisted by the launcher.
if (process.argv[4] !== undefined) config.childSerialNumber = profile(name, process.argv[4]).config.childSerialNumber;
const stateRoot = process.env.HELLGATO_STATE_DIR
  ? pathToFileURL(resolve(process.env.HELLGATO_STATE_DIR) + '/')
  : new URL('../../work/', import.meta.url);
mkdirSync(new URL('traces/', stateRoot), { recursive: true });
const displaySettings = new URL(`display-${config.childSerialNumber}.json`, stateRoot);
const savedDisplay = loadDisplayState(displaySettings);
const bridge = await createBridge({ geometry, config, sleepDuration: savedDisplay.sleepDuration ?? 0 });
const runId = process.env.HELLGATO_RUN_ID ?? String(Date.now());
if (!/^\d+$/.test(runId)) throw new Error('HELLGATO_RUN_ID must contain decimal digits only');
const trace = new URL(`traces/${name}-${runId}.jsonl`, stateRoot);
function record(event, detail) {
  const line = JSON.stringify({ time: new Date().toISOString(), profile: name, event, detail });
  appendFileSync(trace, line + '\n');
  if (!event.endsWith('.comm') && !event.endsWith('.log')) console.log(line);
}
for (const event of ['clientConnected', 'clientDisconnected', 'log', 'comm', 'operationRejected']) {
  bridge.on(event, ({ role, detail }) => record(`${role}.${event}`, detail));
}
const images = new URL(`images/${config.childSerialNumber}-${runId}/`, stateRoot);
mkdirSync(images, { recursive: true });
let imageSequence = 0;
let nativeScreensaver = false;
bridge.on('brightness', level => record('brightness', { level }));
bridge.on('sleepDuration', seconds => {
  saveDisplayState(displaySettings, { sleepDuration: seconds });
  record('sleepDuration', { seconds });
});
bridge.on('displayCommand', detail => record('displayCommand', detail));
bridge.on('image', event => {
  const file = `key-${event.keyIndex}.${event.format === 'jpeg' ? 'jpg' : 'bin'}`;
  writeFileSync(new URL(file, images), event.data);
  record('keyImage', { ...event, data: { bytes: event.data?.length }, file });
});
bridge.on('touchImage', event => {
  const file = `strip-${imageSequence++}.jpg`;
  writeFileSync(new URL(file, images), event.data);
  record('touchImage', { bytes: event.data.length,
    region: event.region ?? { x: 0, y: 0, w: 800, h: 100 }, file });
});
bridge.on('lcdFrameStarted', () => {
  if (nativeScreensaver) record('screensaver', {});
});
bridge.on('lcdImage', event => {
  if (nativeScreensaver) return;
  const file = `lcd-${imageSequence++}.jpg`;
  writeFileSync(new URL(file, images), event.data);
  record('lcdImage', { bytes: event.data.length, file });
});
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  await bridge.stop();
}
try { await bridge.start(); }
catch (error) { await stop(); throw error; }
record('started', { geometry, config, images: images.pathname });
record('sleepDuration', { seconds: savedDisplay.sleepDuration ?? 0 });
console.log('Hellgato: pair 127.0.0.1:5343 in Stream Deck on first use.');
const input = createInterface({ input: process.stdin, output: process.stdout });
input.on('line', line => {
  const [command, indexText, value, ...extra] = line.trim().split(/\s+/);
  if (command === 'quit') { input.close(); return; }
  if (command === 'status') { record('status', { primaryConnected: bridge.primaryConnected, childConnected: bridge.childConnected }); return; }
  if (command === 'screensaver') {
    if (value !== undefined || !['native', 'stream'].includes(indexText)) {
      console.error('Use screensaver native|stream'); return;
    }
    nativeScreensaver = indexText === 'native';
    return;
  }
  if (command === 'touch') {
    const numbers = [value, ...extra].map(Number);
    const count = indexText === 'swipe' ? 4 : 2;
    if (!['tap', 'hold', 'swipe'].includes(indexText) || numbers.length !== count ||
        !numbers.every(Number.isInteger) || numbers.some((n, i) => n < 0 || n >= (i % 2 ? geometry.touchHeight : geometry.touchWidth))) {
      console.error('Use touch tap|hold <x> <y>, or touch swipe <x> <y> <endX> <endY>'); return;
    }
    if (!bridge.childConnected) { console.error('No child client attached; touch was not sent'); return; }
    const [x, y, endX, endY] = numbers;
    record('touchInput', { type: indexText, x, y, endX, endY });
    bridge.sendTouch({ type: indexText, x, y, endX, endY });
    return;
  }
  const index = Number(indexText);
  const limit = command === 'key' ? geometry.keyCount : geometry.encoderCount;
  if (extra.length || !indexText || !Number.isInteger(index) || index < 0 || index >= limit ||
      !['key', 'rotate', 'press'].includes(command) ||
      (command === 'rotate' ? !value || !Number.isInteger(Number(value)) || Number(value) < -128 || Number(value) > 127 : !['down', 'up'].includes(value))) {
    console.error('Invalid command or index'); return;
  }
  if (!bridge.childConnected) { console.error('No child client attached; input was not sent'); return; }
  record('input', { command, index, value });
  if (command === 'key') bridge.sendKey(index, value);
  else bridge.sendDial(command === 'rotate' ? { kind: 'rotate', index, delta: Number(value) } : { kind: 'press', index, state: value });
});
input.on('close', () => { void stop(); });
process.on('SIGINT', () => input.close());
process.on('SIGTERM', () => input.close());

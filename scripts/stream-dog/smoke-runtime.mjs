import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = fileURLToPath(new URL('../../', import.meta.url));
const args = process.argv.slice(2), index = args.indexOf('--bundle');
assert.ok(index >= 0 && args[index + 1], '--bundle is required');
const bundle = resolve(args[index + 1]);
const adapterId = args.includes('--adapter') ? args[args.indexOf('--adapter')+1] : args.includes('--mirabox') ? 'mirabox' : 'hellgato-simulator';
const { startFrameRuntime } = await import(pathToFileURL(resolve(bundle, 'runtime/frame-runtime.mjs')));
const template = JSON.parse(readFileSync(resolve(bundle, 'runtime.json'), 'utf8'));
mkdirSync(resolve(root, 'work'), { recursive: true });
const stateDir = mkdtempSync(resolve(root, 'work/frame-runtime-smoke-'));
const config = { ...template.service, stateDir, settingsPath: resolve(stateDir, 'settings.json'),
  builtInAdapters: template.adapters.map(path => resolve(bundle, path)), fake: true, apiPort: 0, previewPort: 0, previewEnabled: true };
const waitFor = async (runtime, predicate) => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const state = runtime.manager.getState();
    if (predicate(state)) return state;
    await new Promise(resolveWait => setTimeout(resolveWait, 25));
  }
  throw new Error('Runtime smoke condition did not settle: ' + JSON.stringify(runtime.manager.getState().runtime));
};
let runtime = await startFrameRuntime(config);
const command = async (action, params = {}) => {
  const response = await fetch(runtime.descriptor.endpoint + '/api/command', { method: 'POST',
    headers: { Authorization: `Bearer ${runtime.descriptor.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, params }) });
  const value = await response.json(); assert.equal(response.status, 200, JSON.stringify(value)); return value.result;
};
try {
  const initial = runtime.manager.getState().settings.identity;
  const { devices } = await command('discover', { adapterId });
  assert.equal(devices.length, 1);
  await command('select', { adapterId, deviceId: devices[0].id });
  await command('connect');
  const connected = await waitFor(runtime, state => state.runtime.state === 'running' && state.screensaverRestores >= 1);
  const jpeg = Buffer.from(await (await fetch(runtime.preview.url + 'frame.jpg')).arrayBuffer());
  const require = createRequire(resolve(bundle, 'runtime/package.json'));
  const metadata = await require('sharp')(jpeg).metadata();
  assert.deepEqual([metadata.width, metadata.height], [connected.display.width, connected.display.height]);
  const knobControls = devices[0].capabilities?.features?.knobBrightness?.status === 'native';
  if (knobControls) {
    const headers = { Authorization: `Bearer ${runtime.descriptor.token}` };
    assert.equal((await (await fetch(runtime.descriptor.endpoint + '/state', { headers })).json()).available, true);
    const knob = async (value, persist) => {
      const response = await fetch(runtime.descriptor.endpoint + '/control', { method: 'POST', headers,
        body: JSON.stringify({ serial: initial.childSerial, action: 'knobBrightness', value, persist }) });
      const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result));
      assert.equal(result.ok, true); assert.equal(result.state.knobBrightness, value);
    };
    await knob(29, false);
    assert.equal(runtime.manager.getState().settings.knobBrightness, null);
    await knob(61, true);
    assert.equal(runtime.manager.getState().settings.knobBrightness, 61);
  }
  const oldPid = connected.adapterPid, oldAttempt = connected.runtime.attempt;
  process.kill(oldPid);
  const recovered = await waitFor(runtime, state => state.runtime.state === 'running' && state.runtime.attempt > oldAttempt && state.adapterPid !== oldPid && state.screensaverRestores >= 1);
  assert.ok(recovered.adapterPid > 0);
  if (knobControls) assert.equal(runtime.manager.nativeState().knobBrightness, 61);
  await command('settings', { brightness: 65, maxFps: 30 });
  const configured = await waitFor(runtime, state => state.runtime.state === 'running' && state.settings.brightness === 65 && state.maxFps === 30);
  assert.equal(configured.runtime.attempt, recovered.runtime.attempt);
  await runtime.close();
  runtime = await startFrameRuntime(config);
  const resumed = await waitFor(runtime, state => state.runtime.state === 'running' && state.screensaverRestores >= 1);
  assert.deepEqual(resumed.settings.identity, initial);
  if (knobControls) assert.equal(runtime.manager.nativeState().knobBrightness, 61);
  await command('disconnect');
  const attempt = runtime.manager.getState().runtime.attempt;
  await new Promise(resolveWait => setTimeout(resolveWait, 800));
  assert.equal(runtime.manager.getState().runtime.attempt, attempt);
  assert.equal(runtime.manager.getState().runtime.desired, false);
  writeFileSync(resolve(stateDir, 'preview.jpg'), jpeg);
  const report = { adapterId, image: [metadata.width, metadata.height], recovery: true, savedSettings: true,
    identityPreserved: true, disconnectCancelsRetry: true, nativeKnobControls: knobControls, stateDir };
  writeFileSync(resolve(stateDir, 'smoke-result.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally { await runtime.close(); }

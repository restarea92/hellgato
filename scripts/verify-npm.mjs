import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, MANIFEST, assertNpmVersion, runTool, verifyArtifacts, digest, invariant, parseArgs, artifactOptions } from './npm-release.mjs';

export function smokeConsumer(artifactDir, manifest) {
  assertNpmVersion();
  const consumer = realpathSync(mkdtempSync(resolve(tmpdir(), 'hellgato-npm-consumer-')));
  const fromRoot = relative(realpathSync(ROOT), consumer);
  invariant(isAbsolute(fromRoot) || fromRoot === '..' || fromRoot.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`), 'Consumer must be outside the repository/workspace');
  // An empty cache and both local tarballs make any hidden registry dependency fail.
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^npm_config_/i.test(key) && !['NODE_PATH', 'NODE_OPTIONS', 'NODE_AUTH_TOKEN', 'NPM_TOKEN'].includes(key)));
  try {
    writeFileSync(resolve(consumer, 'package.json'), JSON.stringify({ private: true, type: 'module' }));
    writeFileSync(resolve(consumer, 'user.npmrc'), '');
    writeFileSync(resolve(consumer, 'global.npmrc'), '');
    runTool('npm', ['install', '--ignore-scripts', '--no-audit', '--no-fund', '--offline', '--package-lock=false', '--cache', resolve(consumer, 'cache'), '--userconfig', resolve(consumer, 'user.npmrc'), '--globalconfig', resolve(consumer, 'global.npmrc'), ...manifest.packages.map(pkg => resolve(artifactDir, pkg.filename))], { cwd: consumer, env: environment, stdio: 'inherit', timeout: 120000 });
    // Only this builder-side consumer check executes the pinned compiler. The
    // release publisher's verifyArtifacts path stays static and dependency-free.
    const compiler = resolve(ROOT, 'node_modules/typescript/bin/tsc');
    invariant(JSON.parse(readFileSync(resolve(ROOT, 'node_modules/typescript/package.json'), 'utf8')).version === '5.9.3', 'Consumer typecheck requires pinned TypeScript 5.9.3');
    invariant(JSON.parse(readFileSync(resolve(ROOT, 'node_modules/@types/node/package.json'), 'utf8')).version === '24.10.1', 'Consumer typecheck requires pinned @types/node 24.10.1');
    writeFileSync(resolve(consumer, 'consumer.mts'), `
import { createDeviceBridge, createBridge, type Bridge } from '@hellgato/core';
import { validateDeviceDescriptor, type DeviceDescriptor, type InputEvent, type MappingProfile } from '@hellgato/core/contracts';
import { createSession, createSidecarAdapter, type Adapter, type HostReadiness, type SidecarOptions } from 'hellgato';
import { createNonN4Simulator, createButtonSimulator, createSimulatorProfile, runAdapterConformance, replayInputs } from 'hellgato/testing';
import { createMiraboxN4ProAdapter, createN4ProMappingProfile, miraboxN4ProSupport } from 'hellgato/adapters/mirabox-n4-pro';
import schema from '@hellgato/core/schemas/contracts.schema.json' with { type: 'json' };
const simulator = createNonN4Simulator();
const adapter: Adapter = simulator;
const device: DeviceDescriptor = validateDeviceDescriptor(simulator.device);
const profile: MappingProfile = createSimulatorProfile(device);
const bridge: Bridge = createDeviceBridge({ device, target: profile.target, port: 0, childPort: 0 });
const readiness: HostReadiness = { ready: true, targetId: profile.target.id, profileId: profile.id };
const session = createSession({ adapter, deviceId: device.id, profile, readiness: async () => readiness });
session.on('input', input => { const delivered: boolean = input.delivered; void delivered; });
session.on('state', state => { const generation: number = state.generation; void generation; });
const input: InputEvent = { deviceId: device.id, generation: 1, sequence: 1, type: 'key', index: 0, state: 'down' };
bridge.submitInput(input);
simulator.emitInput({ type: 'touch', gesture: 'swipe', x: 0, y: 0, endX: 10, endY: 10 });
const sidecar: SidecarOptions = { command: '/explicit/worker', descriptor: adapter.descriptor };
void createSidecarAdapter(sidecar);
void createN4ProMappingProfile({ virtualSerial: 'DOCK', childSerial: 'CHILD' });
void runAdapterConformance(createButtonSimulator(), { deviceId: 'selected-device' });
void replayInputs([input], () => {});
void createBridge(); void schema.$defs; void miraboxN4ProSupport;
// @ts-expect-error Neutral core requires an explicit device and host target.
createDeviceBridge();
// @ts-expect-error Public sessions require explicit physical selection.
createSession({ adapter, profile });
// @ts-expect-error N4 binding requires a verified runtime manifest.
createMiraboxN4ProAdapter(sidecar);
// @ts-expect-error An arbitrary state is not a supported physical input.
simulator.emitInput({ type: 'key', index: 0, state: 'pressed' });
// @ts-expect-error Source internals must remain blocked by public package exports.
import { createSession as privateSession } from 'hellgato/src/session/index.mjs';
void privateSession;
`);
    execFileSync(process.execPath, [compiler, '--noEmit', '--strict', '--module', 'NodeNext', '--moduleResolution', 'NodeNext', '--target', 'ES2022', '--resolveJsonModule', '--types', 'node', '--typeRoots', resolve(ROOT, 'node_modules/@types'), resolve(consumer, 'consumer.mts')], { cwd: consumer, env: environment, stdio: 'inherit', timeout: 30000 });
    writeFileSync(resolve(consumer, 'smoke.mjs'), `
import assert from 'node:assert/strict';
import { realpathSync, readFileSync, lstatSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBridge, createN4ProProfile, createSession, createSidecarAdapter } from 'hellgato';
import * as core from '@hellgato/core';
import * as contracts from '@hellgato/core/contracts';
import { createNonN4Simulator, createButtonSimulator, createSimulatorProfile, runAdapterConformance } from 'hellgato/testing';
import { createMiraboxN4ProAdapter, createN4ProMappingProfile, miraboxN4ProSupport } from 'hellgato/adapters/mirabox-n4-pro';
import contractSchema from '@hellgato/core/schemas/contracts.schema.json' with { type: 'json' };
for (const name of ['hellgato', '@hellgato/core']) {
  const directory = resolve('node_modules', name);
  assert.equal(lstatSync(directory).isSymbolicLink(), false);
  assert.equal(JSON.parse(readFileSync(resolve(directory, 'package.json'))).version, ${JSON.stringify(manifest.version)});
  assert.ok(realpathSync(fileURLToPath(import.meta.resolve(name))).startsWith(realpathSync(directory) + sep));
}
for (const [specifier, name] of [['@hellgato/core/contracts', '@hellgato/core'], ['@hellgato/core/schemas/contracts.schema.json', '@hellgato/core'], ['hellgato/testing', 'hellgato'], ['hellgato/adapters/mirabox-n4-pro', 'hellgato']]) {
  assert.ok(realpathSync(fileURLToPath(import.meta.resolve(specifier))).startsWith(realpathSync(resolve('node_modules', name)) + sep));
}
for (const privatePath of ['@hellgato/core/dist/legacy/bridge.mjs', 'hellgato/src/session/index.mjs']) {
  await assert.rejects(import(privatePath), error => error.code === 'ERR_PACKAGE_PATH_NOT_EXPORTED');
}
assert.ok(contractSchema.$defs.DeviceDescriptor && contractSchema.$defs.MappingProfile);
assert.equal(typeof createSidecarAdapter, 'function');
assert.equal(typeof core.createDeviceBridge, 'function');
assert.throws(() => core.createDeviceBridge(), /device|target|required/i);
assert.throws(() => createSession(), /explicit|requires/i);
assert.throws(() => createMiraboxN4ProAdapter(), /explicit runtime manifest/i);
assert.equal(miraboxN4ProSupport.physicalHardwareVerified, false);
const n4Profile = createN4ProMappingProfile({ virtualSerial: 'HGN4DOCK000001', childSerial: 'HGN4CHILD000001' });
contracts.validateMappingProfile(n4Profile);
assert.equal(n4Profile.target.geometry.keyCount, 10);
assert.equal(createBridge, core.createBridge);
assert.equal(createN4ProProfile, core.createN4ProProfile);
const bridge = await createBridge({ ...createN4ProProfile(42), port: 0, childPort: 0 });
assert.deepEqual(bridge.ports, { primary: null, child: null });
try {
  await Promise.all([bridge.start(), bridge.start()]);
  assert.ok(bridge.ports.primary > 0 && bridge.ports.child > 0);
  assert.notEqual(bridge.ports.primary, bridge.ports.child);
  await Promise.all([bridge.stop(), bridge.stop()]);
  assert.deepEqual(bridge.ports, { primary: null, child: null });
  await bridge.start();
} finally { await bridge.stop(); }
assert.deepEqual(bridge.ports, { primary: null, child: null });
// A non-N4 adapter, profile and real bridge session run only from installed
// tarballs. This fixture exercises public contracts, never a physical device.
const simulator = createNonN4Simulator();
const profile = createSimulatorProfile(simulator.device);
contracts.validateDeviceDescriptor(simulator.device);
contracts.validateMappingProfile(profile);
assert.equal(simulator.device.capabilities.keys, 4);
assert.equal(simulator.device.capabilities.dials, 2);
assert.deepEqual(simulator.device.capabilities.touch, { width: 320, height: 60 });
const session = createSession({ adapter: simulator, deviceId: simulator.device.id, profile, port: 0, childPort: 0 });
assert.equal(session.state, 'idle');
assert.deepEqual(session.ports, { primary: null, child: null });
assert.deepEqual(simulator.calls, []);
const inputs = [];
session.on('input', event => inputs.push(event));
try {
  await Promise.all([session.start(), session.start()]);
  assert.equal(session.state, 'ready');
  assert.ok(session.ports.primary > 0 && session.ports.child > 0);
  simulator.emitInput({ type: 'key', index: 0, state: 'down' });
  simulator.emitInput({ type: 'key', index: 0, state: 'up' });
  assert.deepEqual(inputs.map(event => event.state), ['down', 'up']);
  assert.ok(inputs.every(event => event.deviceId === simulator.device.id));
  const generation = session.generation;
  await Promise.all([session.stop(), session.stop()]);
  assert.equal(session.state, 'stopped');
  assert.deepEqual(session.ports, { primary: null, child: null });
  await session.start();
  assert.ok(session.generation > generation);
  simulator.injectInput({ type: 'key', index: 0, state: 'down', deviceId: simulator.device.id, generation, sequence: 100 });
  assert.equal(inputs.length, 2, 'A restarted session must not replay stale-generation input');
} finally { await session.stop(); }
assert.deepEqual(session.ports, { primary: null, child: null });
const buttons = createButtonSimulator({ keys: 3 });
const conformance = await runAdapterConformance(buttons, { deviceId: buttons.device.id });
assert.equal(conformance.physicalHardwareVerified, false);
assert.ok(conformance.checks.includes('cleanup'));
console.log('Isolated installed tarballs: public exports/types/contracts/subpaths, legacy compatibility, non-N4 session, lifecycle, restart and stale-input rejection OK; no hardware tested');
`);
    execFileSync(process.execPath, [resolve(consumer, 'smoke.mjs')], { cwd: consumer, env: environment, stdio: 'inherit', timeout: 30000 });
  } finally { rmSync(consumer, { recursive: true, force: true }); }
}
export function verifyConsumerArtifacts(options = {}) {
  options = { artifactDir: resolve(ROOT, 'dist/npm'), ...options };
  const manifest = verifyArtifacts(options);
  smokeConsumer(options.artifactDir, manifest);
  // Recheck after execution before recording which exact files passed the smoke.
  const rechecked = verifyArtifacts(options);
  invariant(JSON.stringify(manifest) === JSON.stringify(rechecked), 'Artifacts changed during consumer verification');
  manifest.verification = { consumerSmoke: 'passed', packagesSha256: digest(JSON.stringify(manifest.packages)) };
  writeFileSync(resolve(options.artifactDir, MANIFEST), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const flags = parseArgs(process.argv.slice(2));
    const options = artifactOptions(flags);
    if (flags['no-smoke']) verifyArtifacts({ ...options, requireConsumerSmoke: true });
    else verifyConsumerArtifacts(options);
    console.log('Verified existing npm artifacts; no build or pack was performed');
  } catch (error) { console.error(`npm verification failed: ${error.message}`); process.exitCode = 1; }
}

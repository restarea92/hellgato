import { execFileSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
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
    writeFileSync(resolve(consumer, 'smoke.mjs'), `
import assert from 'node:assert/strict';
import { realpathSync, readFileSync, lstatSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBridge, createN4ProProfile } from 'hellgato';
import * as core from '@hellgato/core';
for (const name of ['hellgato', '@hellgato/core']) {
  const directory = resolve('node_modules', name);
  assert.equal(lstatSync(directory).isSymbolicLink(), false);
  assert.equal(JSON.parse(readFileSync(resolve(directory, 'package.json'))).version, ${JSON.stringify(manifest.version)});
  assert.ok(realpathSync(fileURLToPath(import.meta.resolve(name))).startsWith(realpathSync(directory) + sep));
}
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
console.log('Isolated installed tarballs: exports, shared API, lifecycle and restart OK');
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

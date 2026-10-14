// The Windows product consumes the exact offline-installed npm artifacts. No source
// tree, workspace symlinks, lifecycle scripts, registry access or import rewriting.
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, digest, invariant, json, runTool, verifyArtifacts } from './npm-release.mjs';
import { packArtifacts } from './pack-npm.mjs';
import { verifyConsumerArtifacts } from './verify-npm.mjs';

export function stageInventory(root) {
  const inventory = [];
  function visit(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = resolve(directory, entry.name);
      invariant(!entry.isSymbolicLink(), 'Windows SDK staging cannot include links');
      if (entry.isDirectory()) visit(path);
      else {
        invariant(entry.isFile(), 'Windows SDK staging requires regular files');
        const name = relative(root, path).replaceAll('\\', '/');
        if (name === 'product-build-manifest.json') continue;
        const bytes = readFileSync(path);
        inventory.push({ path: name, size: bytes.length, sha256: digest(bytes) });
      }
    }
  }
  visit(root);
  return inventory.sort((a, b) => a.path.localeCompare(b.path, 'en'));
}

export function verifyWindowsSdkStage(directory, { releaseSha, version } = {}) {
  invariant(!lstatSync(directory).isSymbolicLink(), 'Staging root cannot be a symlink');
  const manifest = json(resolve(directory, 'product-build-manifest.json'));
  invariant(manifest.schemaVersion === 1 && manifest.component === 'stream-dog-sdk' && manifest.engine === 'legacy-compatibility', 'Unexpected product SDK manifest');
  invariant(/^[a-f0-9]{40}$/.test(manifest.releaseSha) && (!releaseSha || releaseSha === manifest.releaseSha), 'Product source SHA mismatch');
  invariant(!version || manifest.productVersion === version, 'Product version mismatch');
  invariant(Array.isArray(manifest.sdk) && manifest.sdk.length === 2, 'Missing fixed SDK pair');
  for (const name of ['@hellgato/core', 'hellgato']) {
    const record = manifest.sdk.find(item => item.name === name);
    invariant(record && /^[a-f0-9]{64}$/.test(record.sha256), 'Missing SDK archive provenance');
    const installed = json(resolve(directory, 'node_modules', name, 'package.json'));
    invariant(installed.name === name && installed.version === record.version, 'Installed SDK metadata mismatch');
  }
  invariant(JSON.stringify(stageInventory(directory)) === JSON.stringify(manifest.files), 'Staged SDK inventory/digest mismatch');
  return manifest;
}

export function stageWindowsSdk({ root = ROOT, artifactDir, destination = resolve(root, 'work/stream-dog-sdk') } = {}) {
  const work = resolve(root, 'work');
  mkdirSync(work, { recursive: true });
  const scratch = mkdtempSync(resolve(work, 'sdk-stage-'));
  const consumerCache = mkdtempSync(resolve(tmpdir(), 'hellgato-stage-cache-'));
  try {
    if (!artifactDir) {
      execFileSync(process.execPath, [resolve(root, 'scripts/build-npm.mjs')], { cwd: root, stdio: 'inherit' });
      artifactDir = resolve(scratch, 'artifacts');
      packArtifacts({ root, artifactDir });
      verifyConsumerArtifacts({ artifactDir });
    }
    const releaseSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
    const artifacts = verifyArtifacts({ artifactDir, releaseSha, requireConsumerSmoke: true });
    const stage = resolve(scratch, 'runtime');
    mkdirSync(stage);
    writeFileSync(resolve(stage, 'package.json'), JSON.stringify({ name: 'stream-dog-runtime', private: true, type: 'module' }) + '\n');
    const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^npm_config_/i.test(key) && !['NODE_PATH', 'NODE_OPTIONS', 'NODE_AUTH_TOKEN', 'NPM_TOKEN'].includes(key)));
    const userconfig = resolve(consumerCache, 'user.npmrc');
    const globalconfig = resolve(consumerCache, 'global.npmrc');
    writeFileSync(userconfig, ''); writeFileSync(globalconfig, '');
    runTool('npm', ['install', '--ignore-scripts', '--offline', '--no-audit', '--no-fund', '--package-lock=false', '--cache', consumerCache, '--userconfig', userconfig, '--globalconfig', globalconfig,
      ...artifacts.packages.map(pkg => resolve(artifactDir, pkg.filename))], { cwd: stage, env: environment, stdio: 'inherit', timeout: 120000 });
    // Installation metadata can contain checkout-local file: sources. The deployed
    // product does not install anything; keep only runtime files and fixed packages.
    writeFileSync(resolve(stage, 'package.json'), JSON.stringify({ name: 'stream-dog-runtime', private: true, type: 'module' }) + '\n');
    rmSync(resolve(stage, 'node_modules/.package-lock.json'), { force: true });
    for (const name of ['legacy-server.mjs', 'display-state.mjs', 'profiles.mjs']) cpSync(resolve(root, 'apps/stream-dog/node', name), resolve(stage, name));
    execFileSync(process.execPath, ['--input-type=module', '--eval', `
      import assert from 'node:assert/strict';
      import { createLegacyProductProfile } from './profiles.mjs';
      const target = createLegacyProductProfile('target', 42, 99);
      assert.equal(target.config.serialNumber, 'HGDOCK000042');
      assert.equal(target.config.childSerialNumber, 'HGMOCK000099');
      assert.deepEqual(target.config.macAddress, [2, 0, 0, 0, 0, 42]);
      assert.equal(target.geometry.keyCount, 10);
      const control = createLegacyProductProfile('control');
      assert.equal(control.config.childSerialNumber, 'HGMOCK000001');
      assert.equal(control.geometry.keyCount, 8);
      console.log('Packed product identity compatibility verified');
    `], { cwd: stage, env: environment, stdio: 'inherit', timeout: 30000 });
    const manifest = {
      schemaVersion: 1, component: 'stream-dog-sdk', engine: 'legacy-compatibility',
      productVersion: json(resolve(root, 'packages/windows-release/package.json')).version,
      releaseSha, sdk: artifacts.packages.map(({ name, version, sha256, integrity }) => ({ name, version, sha256, integrity })),
      files: stageInventory(stage),
    };
    writeFileSync(resolve(stage, 'product-build-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
    verifyWindowsSdkStage(stage, { releaseSha, version: manifest.productVersion });
    // Only replace generated output after the candidate passed all verification.
    mkdirSync(dirname(destination), { recursive: true });
    rmSync(destination, { recursive: true, force: true });
    renameSync(stage, destination);
    return manifest;
  } finally {
    rmSync(scratch, { recursive: true, force: true });
    rmSync(consumerCache, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    invariant(process.argv.length === 2 || (process.argv.length === 4 && process.argv[2] === '--verify'), 'Use stage-windows-sdk.mjs [--verify directory]');
    const result = process.argv[2] === '--verify' ? verifyWindowsSdkStage(resolve(process.argv[3])) : stageWindowsSdk();
    console.log(`Verified Windows SDK staging: ${result.sdk.map(pkg => `${pkg.name}@${pkg.version}`).join(', ')}`);
  } catch (error) { console.error(`Windows SDK staging refused: ${error.message}`); process.exitCode = 1; }
}

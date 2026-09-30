import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const destination = resolve(root, 'work/npm-packs');
mkdirSync(destination, { recursive: true });
const windowsNpm = process.platform === 'win32'
  ? execFileSync('where.exe', ['npm.cmd'], { encoding: 'utf8' }).trim().split(/\r?\n/)
    .map(path => resolve(dirname(path), 'node_modules/npm/bin/npm-cli.js')).find(existsSync)
  : null;
if (process.platform === 'win32' && !windowsNpm) throw new Error('npm CLI was not found');
function run(args, cwd) {
  return windowsNpm
    ? execFileSync(process.execPath, [windowsNpm, ...args], { cwd, encoding: 'utf8' })
    : execFileSync('npm', args, { cwd, encoding: 'utf8' });
}
const archives = [];
for (const directory of ['core', 'hellgato']) {
  const [packed] = JSON.parse(run(['pack', '--json', '--pack-destination', destination], resolve(root, 'packages', directory)));
  for (const { path } of packed.files) {
    if (path !== 'README.md' && path !== 'package.json' && path !== 'LICENSE' &&
        path !== 'index.mjs' && path !== 'THIRD-PARTY-NOTICES.txt' && !path.startsWith('dist/')) {
      throw new Error(`Unexpected published file: ${path}`);
    }
  }
  if (!packed.files.some(file => file.path === 'LICENSE')) throw new Error('Missing package license');
  if (directory === 'core' && !packed.files.some(file => file.path === 'dist/vendor/LICENSE')) {
    throw new Error('Missing upstream license');
  }
  archives.push(resolve(destination, packed.filename));
  console.log(`${packed.name}: ${packed.files.length} files, ${packed.size} bytes packed`);
}
const consumer = resolve(root, 'work/npm-consumer');
mkdirSync(consumer, { recursive: true });
writeFileSync(resolve(consumer, 'package.json'), JSON.stringify({ private: true, type: 'module' }));
run(['install', '--ignore-scripts', '--no-audit', '--no-fund', '--offline', ...archives], consumer);
writeFileSync(resolve(consumer, 'smoke.mjs'), `
import assert from 'node:assert/strict';
import { createBridge, createN4ProProfile } from 'hellgato';
import * as core from '@hellgato/core';
assert.equal(createBridge, core.createBridge);
const bridge = await createBridge({ ...createN4ProProfile(42), port: 0, childPort: 0 });
assert.deepEqual(bridge.ports, { primary: null, child: null });
await bridge.start();
assert.ok(bridge.ports.primary > 0 && bridge.ports.child > 0);
await bridge.stop();
assert.deepEqual(bridge.ports, { primary: null, child: null });
console.log('Installed tarballs: shared API and lifecycle OK');
`);
execFileSync(process.execPath, [resolve(consumer, 'smoke.mjs')], { stdio: 'inherit' });

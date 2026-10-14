import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { stageInventory, verifyWindowsSdkStage } from '../scripts/stage-windows-sdk.mjs';
import { checkArchitecture } from '../scripts/check-architecture.mjs';

test('canonical core and product import boundaries', () => assert.equal(checkArchitecture(), true));

function fixture() {
  const root = mkdtempSync(resolve(tmpdir(), 'hellgato-stage-fixture-'));
  const sdk = [];
  for (const name of ['@hellgato/core', 'hellgato']) {
    const path = resolve(root, 'node_modules', name);
    mkdirSync(path, { recursive: true });
    writeFileSync(resolve(path, 'package.json'), JSON.stringify({ name, version: '0.1.0' }));
    sdk.push({ name, version: '0.1.0', sha256: 'a'.repeat(64) });
  }
  writeFileSync(resolve(root, 'legacy-server.mjs'), 'export {};\n');
  const manifest = { schemaVersion: 1, component: 'stream-dog-sdk', engine: 'legacy-compatibility', productVersion: '0.1.2', releaseSha: 'b'.repeat(40), sdk, files: stageInventory(root) };
  writeFileSync(resolve(root, 'product-build-manifest.json'), JSON.stringify(manifest));
  return root;
}

test('Windows staging binds SDK versions, exact files and source identity', () => {
  const root = fixture();
  try {
    assert.equal(verifyWindowsSdkStage(root, { releaseSha: 'b'.repeat(40), version: '0.1.2' }).sdk.length, 2);
    assert.throws(() => verifyWindowsSdkStage(root, { releaseSha: 'c'.repeat(40) }), /SHA mismatch/);
    assert.throws(() => verifyWindowsSdkStage(root, { version: '9.0.0' }), /version mismatch/);
    writeFileSync(resolve(root, 'legacy-server.mjs'), 'changed');
    assert.throws(() => verifyWindowsSdkStage(root), /inventory\/digest mismatch/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('Windows staging refuses untracked files and workspace links', t => {
  const root = fixture();
  try {
    writeFileSync(resolve(root, 'unexpected.exe'), 'unapproved');
    assert.throws(() => verifyWindowsSdkStage(root), /inventory\/digest mismatch/);
    rmSync(resolve(root, 'unexpected.exe'));
    try { symlinkSync(resolve(root, 'legacy-server.mjs'), resolve(root, 'link.mjs')); }
    catch (error) { if (error.code === 'EPERM') { t.diagnostic('Symlink creation unavailable on this host'); return; } throw error; }
    assert.throws(() => verifyWindowsSdkStage(root), /links/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

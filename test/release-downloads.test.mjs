import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { downloadFiles } from '../scripts/update-downloads.mjs';

const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
const latest = { version: '0.1.2', tag: 'v0.1.2', url: 'https://github.com/restarea92/hellgato/releases/tag/v0.1.2', installerUrl: 'https://github.com/restarea92/hellgato/releases/download/v0.1.2/Hellgato-0.1.2-Setup.exe', checksumUrl: 'https://github.com/restarea92/hellgato/releases/download/v0.1.2/Hellgato-0.1.2-Setup.exe.sha256' };
test('confirmed download update is limited to README and publication records', () => {
  const files = downloadFiles(readme, latest, 'a'.repeat(40));
  assert.deepEqual(Object.keys(files), ['README.md', '.release/published-windows.json', '.release/no-release/windows-download-0-1-2.json']);
  assert.equal((files['README.md'].match(/releases\/download\/v0\.1\.2/g) || []).length, 2);
  assert.match(files['README.md'], /\*\*Version:\*\* `0.1.2`/);
  assert.equal(JSON.parse(files['.release/published-windows.json']).version, '0.1.2');
});
test('unconfirmed, foreign and malformed download identities fail closed', () => {
  for (const value of [null, { ...latest, version: '0.1.3' }, { ...latest, installerUrl: 'https://evil.example/setup.exe' }, { ...latest, tag: 'hellgato@0.1.2' }]) assert.throws(() => downloadFiles(readme, value, 'a'.repeat(40)));
  assert.throws(() => downloadFiles('README structure changed', latest, 'a'.repeat(40)), /markers/);
});

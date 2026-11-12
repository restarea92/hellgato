import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = fileURLToPath(new URL('../../', import.meta.url));
const args = process.argv.slice(2), index = args.indexOf('--exe');
const executable = index >= 0 ? resolve(args[index + 1]) : resolve(root, 'apps/stream-dog/rust/target/release/HellgatoStreamDog.exe');
mkdirSync(resolve(root, 'work'), { recursive: true });
const destination = mkdtempSync(resolve(root, 'work/rust-shell-qa-'));
const run = args => new Promise((yes, no) => {
  const child = spawn(executable, args, { windowsHide: true, stdio: 'ignore' });
  const timer = setTimeout(() => { child.kill(); no(new Error('Native renderer timed out')); }, 25000);
  child.once('error', error => { clearTimeout(timer); no(error); });
  child.once('exit', code => { clearTimeout(timer); code === 0 ? yes() : no(new Error(`Native renderer exited ${code}`)); });
});
const cases = [['normal', 960, 660, 1, 'connected'], ['minimum', 720, 500, 1, 'disconnected'],
  ['scaled', 960, 660, 1.5, 'connected'], ['settings', 960, 660, 1, 'settings'], ['error', 960, 660, 1, 'error'], ['adapters',960,660,1,'adapters'], ['menu',960,660,1,'menu']];
const results = [];
for (const [name, width, height, scale, scenario] of cases) {
  const directory = resolve(destination, name);
  await run(['--ui-qa', directory, '--qa-width', String(width), '--qa-height', String(height), '--qa-scale', String(scale), '--qa-case', scenario]);
  const layout = JSON.parse(readFileSync(resolve(directory, 'layout.json'), 'utf8'));
  const { data, info } = await sharp(resolve(directory, 'window.png')).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  assert.equal(layout.pixelsPerPoint, scale);
  assert.deepEqual([info.width, info.height], [width * scale, height * scale]);
  assert.ok(Math.abs(layout.content[2] - (width - 3)) < 1);
  const pixel = (x, y) => {
    const offset = (Math.floor(y * scale) * info.width + Math.floor(x * scale)) * info.channels;
    return [...data.subarray(offset, offset + 3)];
  };
  assert.deepEqual(pixel(8, 210), [83, 83, 83], 'Sidebar color differs from the reference shell');
  assert.deepEqual(pixel(width - 10, 50), [83, 83, 83], 'Toolbar color differs from the reference shell');
  assert.deepEqual(pixel(width - 10, 90), [66, 66, 66], 'Tab strip color differs from the reference shell');
  assert.deepEqual(pixel(width - 10, height - 70), [48, 48, 48], 'Workspace color differs from the reference');
  assert.deepEqual(pixel(width / 2, height - 15), [83, 83, 83], 'Status bar color differs from the reference shell');
  const [left, top] = layout.boundary;
  for (const [dx, gray] of [[0, 56], [1, 71], [2, 56]]) {
    const tolerance = dx === 1 && !Number.isInteger(scale) ? 4 : 0;
    assert.ok(pixel(left + dx + 0.5, top + 180).every(value => Math.abs(value - gray) <= tolerance),
      'Sidebar boundary lost its three reference strokes, allowing fractional-scale antialiasing');
  }
  for (const key of ['device:n4-test', 'tab:device', 'adapters', 'settings']) {
    const bounds = layout.hitboxes[key]; assert.ok(bounds && bounds[0] >= 0 && bounds[2] <= width, `Missing or clipped ${key}`);
  }
  results.push({ name, pixels: layout.pixels, referenceColors: true, threePixelBoundary: true });
}
writeFileSync(resolve(destination, 'result.json'), JSON.stringify({ nativeRenderer: true, computerUse: false, results }, null, 2) + '\n');
console.log(JSON.stringify({ destination, nativeRenderer: true, cases: results.length }));

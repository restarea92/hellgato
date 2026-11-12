import { mkdtemp, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { sha256 } from '../../apps/stream-dog/node/frame-adapter-registry.mjs';

export const target = { id: 'test-ten-key', displays: [{ id: 'lcd', width: 800, height: 480, format: 'jpeg', partial: false }],
  geometry: { rows: 2, columns: 5, keyCount: 10, keyWidth: 120, keyHeight: 120, encoderCount: 4,
    touchWidth: 800, touchHeight: 100, productName: 'Test device' },
  config: { productId: 132, dockFirmwareVersion: '1.01.016', childFirmwareVersion: '2.00.026' } };

export async function adapterFixture(t, id = 'fixture-adapter') {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-frame-fixture-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'vendor'));
  await writeFile(join(root, 'worker.bin'), 'fixture executable');
  await writeFile(join(root, 'vendor/transport.dll'), 'fixture library');
  const manifest = { schemaVersion: 1, kind: 'hellgato-frame-adapter', id, name: 'Fixture adapter', version: '1.0.0',
    protocolVersion: 1, platforms: [process.platform], architectures: [process.arch], license: 'MIT',
    entry: { command: 'worker.bin', args: ['--dll', '${root}/vendor/transport.dll'], fakeArgs: ['--fake'] },
    files: { 'worker.bin': sha256(Buffer.from('fixture executable')), 'vendor/transport.dll': sha256(Buffer.from('fixture library')) },
    hostTarget: structuredClone(target) };
  const path = join(root, 'adapter.json');
  const save = async () => writeFile(path, JSON.stringify(manifest));
  await save();
  return { root, path, manifest, save };
}

export const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
export const until = async predicate => {
  const deadline = Date.now() + 3000;
  while (!predicate()) { if (Date.now() > deadline) throw new Error('Condition did not settle'); await new Promise(resolve => setTimeout(resolve, 5)); }
};

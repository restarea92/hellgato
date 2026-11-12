import test from 'node:test';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { FrameAdapterRegistry, inspectFrameAdapter, sha256 } from '../apps/stream-dog/node/frame-adapter-registry.mjs';
import { adapterFixture } from './helpers/frame-runtime-fixture.mjs';

test('registering independent adapter packages resolves inventoried runtime files without executing them', async t => {
  const first = await adapterFixture(t), second = await adapterFixture(t, 'another-vendor');
  const registry = new FrameAdapterRegistry();
  const result = registry.register(first.path);
  registry.register(second.path);
  assert.equal(registry.list().length, 2);
  assert.equal(result.command, join(first.root, 'worker.bin'));
  assert.equal(result.args[1], join(first.root, 'vendor/transport.dll'));
  assert.deepEqual(result.fakeArgs, ['--fake']);
  assert.equal(registry.resolve('another-vendor').id, 'another-vendor');
  registry.unregister(first.manifest.id);
  assert.throws(() => registry.resolve(first.manifest.id), /registered adapter/);
});

test('changed manifests and runtime artifacts cannot silently inherit a registration', async t => {
  const fixture = await adapterFixture(t);
  const registry = new FrameAdapterRegistry(); registry.register(fixture.path);
  fixture.manifest.name = 'Changed'; await fixture.save();
  assert.throws(() => registry.resolve(fixture.manifest.id), /manifest changed/);
  assert.equal(registry.list()[0].available, false);
  registry.register(fixture.path);
  await writeFile(join(fixture.root, 'vendor/transport.dll'), 'changed runtime');
  assert.throws(() => registry.resolve(fixture.manifest.id), /digest mismatch/);
});

test('unsupported platforms, invalid host targets, inventory escapes and unlisted arguments are rejected', async t => {
  const fixture = await adapterFixture(t);
  for (const mutate of [
    value => { value.protocolVersion = 2; },
    value => { value.platforms = ['unsupported']; },
    value => { value.entry.command = 'unlisted.exe'; },
    value => { value.entry.args = ['${root}/unlisted.dll']; },
    value => { value.hostTarget.geometry.encoderCount = 5; },
    value => { value.hostTarget.geometry.rows = 3; },
    value => { value.files['../escape.bin'] = sha256(Buffer.from('escape')); },
  ]) {
    const changed = structuredClone(fixture.manifest);
    mutate(changed); await writeFile(fixture.path, JSON.stringify(changed));
    assert.throws(() => inspectFrameAdapter(fixture.path));
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, access, symlink, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDeviceBackup, listDeviceBackups, manageDeviceBackup } from '../apps/stream-dog/node/frame-device-backups.mjs';

test('device snapshots include matching profiles and shared data, preserve order and manage only owned backups', async t => {
  const temporary = await mkdtemp(join(tmpdir(), 'hellgato-backups-'));
  t.after(() => rm(temporary, { recursive: true, force: true }));
  const source = join(temporary, 'StreamDeck'), root = join(temporary, 'backups'), settings = join(temporary, 'settings.json');
  for (const [name, serial] of [['one', 'HGMOCK000003'], ['two', 'HGMOCK000004']]) {
    const folder = join(source, 'ProfilesV3', name + '.sdProfile');
    await mkdir(folder, { recursive: true });
    await writeFile(join(folder, 'manifest.json'), JSON.stringify({ Device: { UUID: `@(128)[4057/132/${serial}]` } }));
    await writeFile(join(folder, 'image.png'), 'profile asset');
  }
  for (const name of ['Plugins', 'Data', 'IconPacks', 'logs']) {
    await mkdir(join(source, name)); await writeFile(join(source, name, 'file'), name);
  }
  await writeFile(settings, JSON.stringify({ identity: 'preserved' }));
  const linked = join(temporary, 'plugin-source'); await mkdir(linked);
  await writeFile(join(linked, 'plugin.js'), 'linked plugin');
  await symlink(linked, join(source, 'Plugins/linked.sdPlugin'), process.platform === 'win32' ? 'junction' : 'dir');
  const device = { serial: 'HGMOCK000003', id: 'one', adapterId: 'fixture', modelId: 'test' };
  const params = { root, device, streamDeckRoot: source, settingsPath: settings, includeRegistry: false };
  const first = await createDeviceBackup(params), second = await createDeviceBackup(params);
  assert.equal(first.profiles, 1);
  assert.equal(await readFile(join(first.path, 'StreamDeck/Plugins/file'), 'utf8'), 'Plugins');
  assert.equal(await readFile(join(first.path, 'StreamDeck/Plugins/linked.sdPlugin/plugin.js'), 'utf8'), 'linked plugin');
  assert.equal((await lstat(join(first.path, 'StreamDeck/Plugins/linked.sdPlugin'))).isSymbolicLink(), false);
  assert.equal(await readFile(join(first.path, 'Hellgato/settings.json'), 'utf8'), await readFile(settings, 'utf8'));
  await assert.rejects(access(join(first.path, 'StreamDeck/ProfilesV3/two.sdProfile')));
  await assert.rejects(access(join(first.path, 'StreamDeck/logs')));
  const files = JSON.parse(await readFile(join(first.path, 'backup.json'), 'utf8')).files;
  assert.ok(files.every(file => /^[0-9a-f]{64}$/.test(file.sha256)));
  assert.deepEqual((await listDeviceBackups(root, device.serial)).entries.map(item => item.id), [second.id, first.id]);
  await manageDeviceBackup({ root, serial: device.serial, id: first.id, action: 'rename', name: 'Before update' });
  await manageDeviceBackup({ root, serial: device.serial, id: first.id, action: 'move', offset: -1 });
  let list = await listDeviceBackups(root, device.serial);
  assert.equal(list.entries[0].name, 'Before update'); assert.equal(list.entries[0].id, first.id);
  await assert.rejects(manageDeviceBackup({ root, serial: 'HGMOCK000004', id: first.id, action: 'delete' }), /does not belong/);
  await assert.rejects(listDeviceBackups(root, '../other'), /identity/);
  const outside = join(temporary, 'export'); await mkdir(outside);
  const exported = await createDeviceBackup({ ...params, destination: outside });
  assert.equal(exported.managed, false); assert.equal((await listDeviceBackups(root, device.serial)).entries.length, 2);
  await assert.rejects(createDeviceBackup({ ...params, destination: join(source, 'Data') }), /inside Stream Deck/);
  await manageDeviceBackup({ root, serial: device.serial, id: first.id, action: 'delete' });
  await assert.rejects(access(first.path));
  assert.equal((await listDeviceBackups(root, device.serial)).entries.length, 1);
  assert.equal(await readFile(join(source, 'Plugins/file'), 'utf8'), 'Plugins');
  await access(exported.path);
});

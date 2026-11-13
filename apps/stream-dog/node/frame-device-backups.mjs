import { mkdir, readdir, readFile, writeFile, rename, lstat, stat, realpath, rm, copyFile } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { resolve, join, relative, isAbsolute, dirname } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);
const requireValue = (value, message) => { if (!value) throw new Error(message); };
const missing = error => error.code === 'ENOENT';
async function plain(path) {
  const value = await lstat(path);
  requireValue(!value.isSymbolicLink(), 'Backup paths cannot be redirected');
  return value;
}
async function json(path) { return JSON.parse((await readFile(path, 'utf8')).replace(/^\uFEFF/, '')); }
async function writeJson(path, value) {
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
  await rename(temporary, path);
}
async function directory(root, serial) {
  requireValue(/^HGMOCK\d{6}$/.test(serial), 'Invalid backup device identity');
  const folder = join(resolve(root), serial);
  await mkdir(folder, { recursive: true });
  for (let path = folder; ; path = dirname(path)) {
    requireValue((await plain(path)).isDirectory(), 'Invalid backup directory');
    if (dirname(path) === path) break;
  }
  return folder;
}
async function index(folder) {
  const path = join(folder, 'index.json');
  let result;
  try { await plain(path); result = await json(path); } catch (error) { if (missing(error)) return []; throw error; }
  requireValue(Array.isArray(result) && result.every(item => /^[0-9a-f-]{36}$/.test(item.id)), 'Invalid backup index');
  return result;
}
export async function listDeviceBackups(root, serial) {
  const folder = await directory(root, serial);
  return { directory: folder, entries: await index(folder) };
}
async function fileHash(path) {
  const hash = createHash('sha256');
  for await (const bytes of createReadStream(path)) hash.update(bytes);
  return hash.digest('hex');
}
async function copyTree(source, target, base, inventory, ancestors = new Set()) {
  const info = await stat(source);
  if (info.isDirectory()) {
    const actual = await realpath(source), back = relative(actual, base);
    requireValue(!ancestors.has(actual) && (back.startsWith('..') || isAbsolute(back)), 'Backup source contains a directory cycle');
    const parents = new Set([...ancestors, actual]);
    await mkdir(target, { recursive: true });
    for (const name of await readdir(source)) await copyTree(join(source, name), join(target, name), base, inventory, parents);
    return;
  }
  requireValue(info.isFile(), 'Backup source contains a special file');
  await mkdir(dirname(target), { recursive: true });
  await copyFile(source, target);
  const after = await stat(source);
  requireValue(after.size === info.size && after.mtimeMs === info.mtimeMs, 'A source file changed during backup; retry after closing Stream Deck');
  inventory.push({ path: relative(base, target).replaceAll('\\', '/'), bytes: info.size, sha256: await fileHash(target) });
}

export async function createDeviceBackup({ root, device, streamDeckRoot, settingsPath, destination, includeRegistry = true }) {
  const folder = await directory(root, device.serial);
  const entries = await index(folder), id = randomUUID(), createdAt = new Date().toISOString();
  const parent = destination ? resolve(destination) : folder;
  requireValue(!destination || isAbsolute(destination), 'Use an absolute external backup directory');
  requireValue((await plain(parent)).isDirectory(), 'Choose an existing backup directory');
  const source = resolve(streamDeckRoot);
  const inside = relative(source, parent);
  requireValue(inside.startsWith('..') || isAbsolute(inside), 'Do not save backups inside Stream Deck data');
  const target = join(parent, destination ? `Hellgato-${device.serial}-${createdAt.replace(/[:.]/g, '-')}-${id.slice(0, 8)}` : id);
  const staging = target + '.partial';
  await mkdir(staging);
  const files = [];
  try {
    let profiles = 0;
    const profileRoot = join(source, 'ProfilesV3');
    await plain(source); await plain(profileRoot);
    for (const name of await readdir(profileRoot)) {
      if (!name.endsWith('.sdProfile')) continue;
      const path = join(profileRoot, name); await plain(path);
      const manifest = await json(join(path, 'manifest.json'));
      const uuid = manifest.Device?.UUID;
      if (typeof uuid === 'string' && uuid.endsWith(`/${device.serial}]`)) {
        await copyTree(path, join(staging, 'StreamDeck/ProfilesV3', name), staging, files); profiles++;
      }
    }
    requireValue(profiles > 0, 'No Stream Deck profiles belong to the selected device');
    const shared = [];
    const excluded = new Set(['ProfilesV3', 'BackupV3', 'logs', 'Dumps', 'Sentry', 'NodeJS']);
    for (const name of await readdir(source)) {
      if (excluded.has(name)) continue;
      await copyTree(join(source, name), join(staging, 'StreamDeck', name), staging, files); shared.push(name);
    }
    await copyTree(settingsPath, join(staging, 'Hellgato/settings.json'), staging, files);
    if (process.platform === 'win32' && includeRegistry) {
      const registry = join(staging, 'StreamDeck.reg');
      try {
        await run('reg.exe', ['export', 'HKCU\\Software\\Elgato Systems GmbH\\StreamDeck', registry, '/y'], { windowsHide: true, timeout: 10000 });
        files.push({ path: 'StreamDeck.reg', bytes: (await lstat(registry)).size, sha256: await fileHash(registry) });
      } catch (error) { if (error.code !== 1) throw error; }
    }
    const createdLabel = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(createdAt));
    const entry = { id, name: createdLabel, createdAt, createdLabel, device,
      profiles, shared, fileCount: files.length, bytes: files.reduce((sum, file) => sum + file.bytes, 0) };
    await writeJson(join(staging, 'backup.json'), { format: 'hellgato-device-backup', schemaVersion: 1, ...entry, files });
    await rename(staging, target);
    if (!destination) await writeJson(join(folder, 'index.json'), [entry, ...entries]);
    return { ...entry, path: target, managed: !destination };
  } catch (error) {
    await rm(staging, { recursive: true, force: true });
    throw error;
  }
}

export async function manageDeviceBackup({ root, serial, id, action, name, offset }) {
  const folder = await directory(root, serial), entries = await index(folder);
  const at = entries.findIndex(item => item.id === id);
  requireValue(at >= 0, 'Backup does not belong to this device');
  const target = join(folder, id);
  requireValue((await plain(target)).isDirectory(), 'Backup directory is missing');
  const manifest = await json(join(target, 'backup.json'));
  requireValue(manifest.format === 'hellgato-device-backup' && manifest.id === id && manifest.device?.serial === serial, 'Backup ownership does not match');
  if (action === 'rename') {
    requireValue(typeof name === 'string' && name.trim().length > 0 && name.length <= 100 && !/[\x00-\x1f]/.test(name), 'Use a backup name between 1 and 100 characters');
    entries[at].name = name.trim();
  } else if (action === 'move') {
    requireValue(offset === 1 || offset === -1, 'Invalid backup move');
    const to = at + offset;
    if (to >= 0 && to < entries.length) [entries[at], entries[to]] = [entries[to], entries[at]];
  } else if (action === 'delete') {
    const trash = join(folder, `.deleted-${id}`);
    await rename(target, trash); entries.splice(at, 1);
    try { await writeJson(join(folder, 'index.json'), entries); } catch (error) { await rename(trash, target); throw error; }
    await rm(trash, { recursive: true });
    return { directory: folder, entries };
  } else throw new Error('Unknown backup management action');
  await writeJson(join(folder, 'index.json'), entries);
  return { directory: folder, entries };
}

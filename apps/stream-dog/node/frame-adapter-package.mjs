import { inflateRawSync, deflateRawSync } from 'node:zlib';
import { readFileSync, writeFileSync, mkdirSync, renameSync, rmSync, existsSync, realpathSync, statSync } from 'node:fs';
import { resolve, dirname, relative, isAbsolute, sep } from 'node:path';
import { randomUUID } from 'node:crypto';
import { inspectFrameAdapter, sha256 } from './frame-adapter-registry.mjs';

const MAX_PACKAGE = 128 * 1024 * 1024, MAX_TOTAL = 256 * 1024 * 1024;
const requireValue = (value, message) => { if (!value) throw new Error(message); };
function safeName(name) {
  return name && name.length <= 240 && !name.includes('\\') && !name.includes('\0') && !name.includes(':') &&
    !name.startsWith('/') && name.split('/').every(part => part && part !== '.' && part !== '..' &&
      !/[. ]$/.test(part) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part));
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = crc >>> 1 ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
export function readAdapterPackage(path) {
  const file=statSync(path); requireValue(file.isFile() && file.size>=22 && file.size<=MAX_PACKAGE,'Adapter package size exceeds its limit');
  const bytes = readFileSync(path);
  requireValue(bytes.length >= 22 && bytes.length <= MAX_PACKAGE, 'Adapter package size exceeds its limit');
  let end = -1;
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65557); offset--) {
    if (bytes.readUInt32LE(offset) === 0x06054b50 && offset + 22 + bytes.readUInt16LE(offset + 20) === bytes.length) { end = offset; break; }
  }
  requireValue(end >= 0, 'Invalid .hga ZIP directory');
  const count = bytes.readUInt16LE(end + 10), central = bytes.readUInt32LE(end + 16), length = bytes.readUInt32LE(end + 12);
  requireValue(bytes.readUInt32LE(end + 4) === 0 && bytes.readUInt16LE(end + 8) === count && count > 0 && count <= 1024 && central + length === end, 'Split or ZIP64 adapter archives are unsupported');
  const files = new Map(), names = new Set(); let at = central, total = 0;
  for (let index = 0; index < count; index++) {
    requireValue(at + 46 <= end && bytes.readUInt32LE(at) === 0x02014b50, 'Invalid adapter directory entry');
    const flags = bytes.readUInt16LE(at + 8), method = bytes.readUInt16LE(at + 10), crc = bytes.readUInt32LE(at + 16), compressed = bytes.readUInt32LE(at + 20), size = bytes.readUInt32LE(at + 24);
    const nameLength = bytes.readUInt16LE(at + 28), extra = bytes.readUInt16LE(at + 30), comment = bytes.readUInt16LE(at + 32), local = bytes.readUInt32LE(at + 42), mode = bytes.readUInt32LE(at + 38) >>> 16;
    requireValue(at + 46 + nameLength + extra + comment <= end && !(flags & 1) && [0, 8].includes(method) && bytes.readUInt16LE(at + 34) === 0, 'Encrypted or unsupported adapter archive entry');
    const encodedName = bytes.subarray(at + 46, at + 46 + nameLength), name = encodedName.toString('utf8');
    at += 46 + nameLength + extra + comment;
    const directory = name.endsWith('/'), clean = directory ? name.slice(0, -1) : name;
    requireValue(safeName(clean) && !names.has(clean.toLowerCase()) && (!(mode & 0o170000) || (mode & 0o170000) === (directory ? 0o040000 : 0o100000)), 'Adapter contains an unsafe, duplicate or special file');
    names.add(clean.toLowerCase());
    requireValue(local + 30 <= central && bytes.readUInt32LE(local) === 0x04034b50 && bytes.readUInt16LE(local + 8) === method, 'Invalid local adapter entry');
    const localName = bytes.readUInt16LE(local + 26), localExtra = bytes.readUInt16LE(local + 28), start = local + 30 + localName + localExtra;
    requireValue(start + compressed <= central && bytes.subarray(local + 30, local + 30 + localName).equals(encodedName), 'Adapter entry filename or bounds mismatch');
    total += size; requireValue(size <= 64 * 1024 * 1024 && total <= MAX_TOTAL && (!directory || size === 0), 'Expanded adapter package exceeds its byte budget');
    if (directory) continue;
    const data = method === 0 ? Buffer.from(bytes.subarray(start, start + compressed)) : inflateRawSync(bytes.subarray(start, start + compressed), { maxOutputLength: Math.max(1, size) });
    requireValue(data.length === size && crc32(data) === crc, 'Adapter package entry is damaged');
    files.set(name, data);
  }
  requireValue(at === end && files.size <= 513 && files.has('adapter.json'), 'Package must contain adapter.json at its root');
  const manifestBytes = files.get('adapter.json'); requireValue(manifestBytes.length <= 131072, 'Adapter manifest exceeds its byte limit');
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  requireValue(manifest.schemaVersion === 1 && manifest.kind === 'hellgato-frame-adapter' && /^[a-z0-9][a-z0-9._-]{0,63}$/.test(manifest.id) && /^\d+\.\d+\.\d+$/.test(manifest.version), 'Invalid adapter package identity');
  requireValue(manifest.files && Object.keys(manifest.files).length === files.size - 1, 'All packaged artifacts must belong to the manifest inventory');
  for (const [name, data] of files) if (name !== 'adapter.json') requireValue(manifest.files[name] === sha256(data), `Adapter artifact digest mismatch: ${name}`);
  return { files, manifest, packageHash: sha256(bytes), dlls: [...files.keys()].filter(name => /\.dll$/i.test(name)), nativeCode: [...files.keys()].some(name => /\.(dll|exe|node)$/i.test(name)) };
}

export function inspectAdapterPackage(path) {
  const { manifest, packageHash, dlls, nativeCode } = readAdapterPackage(path);
  return { id: manifest.id, name: manifest.name, version: manifest.version, packageHash, dlls, nativeCode, source: 'user',
    supportsPlatform: manifest.platforms?.includes(process.platform) && manifest.architectures?.includes(process.arch),
    safety: 'unverified', warning: dlls.length ? '외부 어댑터에 DLL이 포함되어 있습니다. 난독화·패킹된 DLL은 분석이 제한되며 파일·해시 검사만으로 안전을 보장할 수 없습니다. 출처를 신뢰할 때만 설치하세요.' : '외부 어댑터는 실행 가능한 코드입니다. 출처를 신뢰할 때만 설치하세요.' };
}

export function installAdapterPackage(path, destination, expectedHash) {
  const parsed = readAdapterPackage(path);
  requireValue(expectedHash === parsed.packageHash, 'Review this exact adapter package before installation');
  const folder = resolve(destination, parsed.manifest.id, parsed.manifest.version + '-' + parsed.packageHash.slice(0, 16));
  const manifestPath = resolve(folder, 'adapter.json');
  if (existsSync(folder)) { inspectFrameAdapter(manifestPath); return manifestPath; }
  const temporary = resolve(destination, '.install-' + randomUUID()); mkdirSync(temporary, { recursive: true });
  try {
    for (const [name, data] of parsed.files) { const file = resolve(temporary, name); mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, data, { flag: 'wx' }); }
    inspectFrameAdapter(resolve(temporary, 'adapter.json'));
    mkdirSync(dirname(folder), { recursive: true }); renameSync(temporary, folder); return manifestPath;
  } finally {
    const within = relative(resolve(destination), temporary);
    if (within && !within.startsWith('..') && !isAbsolute(within)) rmSync(temporary, { recursive: true, force: true });
  }
}

export function packAdapter(directory, output) {
  const adapter = inspectFrameAdapter(resolve(directory, 'adapter.json'));
  const manifest = JSON.parse(readFileSync(adapter.manifestPath));
  const names = ['adapter.json', ...Object.keys(manifest.files)].sort();
  const locals = [], central = []; let offset = 0;
  for (const name of names) {
    requireValue(safeName(name), 'Nonportable adapter artifact name');
    const path = realpathSync(resolve(directory, name)), within = relative(realpathSync(directory), path);
    requireValue(within && !within.startsWith(`..${sep}`) && !isAbsolute(within), 'Adapter artifact escapes package');
    const raw = readFileSync(path), data = deflateRawSync(raw), filename = Buffer.from(name), crc = crc32(raw);
    const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50); local.writeUInt16LE(20, 4); local.writeUInt16LE(0x800, 6); local.writeUInt16LE(8, 8); local.writeUInt32LE(crc, 14); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(raw.length, 22); local.writeUInt16LE(filename.length, 26);
    const entry = Buffer.alloc(46); entry.writeUInt32LE(0x02014b50); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6); entry.writeUInt16LE(0x800, 8); entry.writeUInt16LE(8, 10); entry.writeUInt32LE(crc, 16); entry.writeUInt32LE(data.length, 20); entry.writeUInt32LE(raw.length, 24); entry.writeUInt16LE(filename.length, 28); entry.writeUInt32LE(offset, 42);
    locals.push(local, filename, data); central.push(entry, filename); offset += local.length + filename.length + data.length;
  }
  const table = Buffer.concat(central), end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(names.length, 8); end.writeUInt16LE(names.length, 10); end.writeUInt32LE(table.length, 12); end.writeUInt32LE(offset, 16);
  writeFileSync(output, Buffer.concat([...locals, table, end]), { flag: 'wx' }); return inspectAdapterPackage(output);
}

import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';

const ID = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const HASH = /^[a-f0-9]{64}$/;
const MAX_MANIFEST = 131072;
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const requireValue = (condition, message) => { if (!condition) throw new TypeError(message); };

function artifact(root, name) {
  requireValue(typeof name === 'string' && name.length <= 260 && !isAbsolute(name) && !name.includes('\\'), 'Artifact names must be relative portable paths');
  const path = realpathSync(resolve(root, name));
  const within = relative(root, path);
  requireValue(within && within !== '..' && !within.startsWith(`..${sep}`) && !isAbsolute(within), 'Adapter artifact escapes its package');
  requireValue(statSync(path).isFile(), 'Adapter artifact must be a regular file');
  return path;
}

export function inspectFrameAdapter(manifestPath, { expectedManifestHash, platform = process.platform, arch = process.arch, nodeCommand = process.execPath } = {}) {
  const path = realpathSync(manifestPath), root = dirname(path);
  const bytes = readFileSync(path);
  requireValue(bytes.length > 0 && bytes.length <= MAX_MANIFEST, 'Adapter manifest byte limit exceeded');
  const manifestHash = sha256(bytes);
  if (expectedManifestHash) requireValue(manifestHash === expectedManifestHash, 'Registered adapter manifest changed; register the reviewed package again');
  const value = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));
  requireValue(object(value) && value.schemaVersion === 1 && value.kind === 'hellgato-frame-adapter' && value.protocolVersion === 1, 'Unsupported frame adapter manifest');
  requireValue(ID.test(value.id) && typeof value.name === 'string' && value.name.length > 0 && value.name.length <= 120 &&
    /^\d+\.\d+\.\d+$/.test(value.version), 'Invalid adapter identity');
  requireValue(Array.isArray(value.platforms) && value.platforms.includes(platform) && Array.isArray(value.architectures) && value.architectures.includes(arch), 'Adapter does not support this platform');
  requireValue(object(value.files) && Object.keys(value.files).length > 0 && Object.keys(value.files).length <= 512, 'Adapter artifact inventory is required');
  const files = new Map();
  for (const [name, digest] of Object.entries(value.files)) {
    requireValue(typeof digest === 'string' && HASH.test(digest), 'Invalid adapter artifact digest');
    const file = artifact(root, name);
    requireValue(sha256(readFileSync(file)) === digest, `Adapter artifact digest mismatch: ${name}`);
    files.set(name, file);
  }
  requireValue(object(value.entry) && (files.has(value.entry.command) || value.entry.command === '@runtime/node'), 'Adapter executable must belong to its reviewed inventory');
  const argumentsFor = args => {
    requireValue(Array.isArray(args) && args.length <= 32 && args.every(item => typeof item === 'string' && item.length <= 1024 && !item.includes('\0')), 'Invalid adapter arguments');
    return args.map(item => {
      if (!item.startsWith('${root}/')) return item;
      const name = item.slice(8);
      requireValue(files.has(name), 'Adapter argument references an uninventoried file');
      return files.get(name);
    });
  };
  const target = value.hostTarget;
  requireValue(object(target) && ID.test(target.id) && object(target.geometry) && object(target.config), 'Explicit host target is required');
  const geometry = target.geometry;
  for (const name of ['rows', 'columns', 'keyCount', 'keyWidth', 'keyHeight']) {
    requireValue(Number.isSafeInteger(geometry[name]) && geometry[name] > 0 && geometry[name] <= 8192, `Invalid target ${name}`);
  }
  requireValue(['touchWidth','touchHeight'].every(name => Number.isSafeInteger(geometry[name]) && geometry[name] >= 0 && geometry[name] <= 8192) && (geometry.touchWidth === 0) === (geometry.touchHeight === 0), 'Invalid target touch dimensions');
  requireValue(geometry.keyCount <= 256 && geometry.rows * geometry.columns === geometry.keyCount &&
    Number.isSafeInteger(geometry.encoderCount) && geometry.encoderCount >= 0 && geometry.encoderCount <= 4, 'Invalid host control geometry');
  requireValue(Number.isSafeInteger(target.config.productId) && target.config.productId > 0 && target.config.productId <= 65535, 'Invalid host product ID');
  return { manifestPath: path, manifestHash, id: value.id, name: value.name, version: value.version,
    target: structuredClone(target), command: value.entry.command === '@runtime/node' ? nodeCommand : files.get(value.entry.command), args: argumentsFor(value.entry.args ?? []),
    fakeArgs: value.entry.fakeArgs ? argumentsFor(value.entry.fakeArgs) : undefined, cwd: root,
    license: typeof value.license === 'string' ? value.license : '' };
}

export class FrameAdapterRegistry {
  #records;
  constructor(records = []) { this.#records = structuredClone(records); }
  records() { return structuredClone(this.#records); }
  list() {
    return this.#records.map(record => {
      try {
        const adapter = inspectFrameAdapter(record.manifestPath, { expectedManifestHash: record.manifestHash });
        return { id: adapter.id, name: adapter.name, version: adapter.version, source: record.source ?? 'user', available: true, manifestPath: adapter.manifestPath };
      } catch (error) { return { id: record.id, name: record.name ?? record.id, source: record.source ?? 'user', available: false, error: error.message, manifestPath: record.manifestPath }; }
    });
  }
  register(path, { source = 'user' } = {}) {
    const adapter = inspectFrameAdapter(path);
    const record = { id: adapter.id, name: adapter.name, source, manifestPath: adapter.manifestPath, manifestHash: adapter.manifestHash };
    this.#records = [...this.#records.filter(item => item.id !== adapter.id), record];
    return adapter;
  }
  unregister(id) { this.#records = this.#records.filter(record => record.id !== id); }
  resolve(id) {
    const record = this.#records.find(item => item.id === id);
    if (!record) throw new Error('Select a registered adapter');
    return inspectFrameAdapter(record.manifestPath, { expectedManifestHash: record.manifestHash });
  }
}

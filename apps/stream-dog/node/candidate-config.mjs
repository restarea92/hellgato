// Product-owned, deliberately separate from the default compatibility launcher.
import { createHash } from 'node:crypto';
import { readFileSync, lstatSync, realpathSync, writeFileSync, renameSync, unlinkSync, appendFileSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';

export class CandidateError extends Error {
  constructor(code, message = code) { super(message); this.name = 'CandidateError'; this.code = code; }
}
const requireValue = (condition, code) => { if (!condition) throw new CandidateError(code); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const integer = (value, min, max) => Number.isSafeInteger(value) && value >= min && value <= max;
const pathValue = value => typeof value === 'string' && value.length <= 32768 && !value.includes('\0') && isAbsolute(value);
const argsValue = value => Array.isArray(value) && value.length <= 64 && value.every(arg => typeof arg === 'string' && arg.length <= 32768 && !arg.includes('\0'));
const SETTINGS = { brightness: [0, 100], knobBrightness: [0, 100], sleepDuration: [0, 0x7fffffff] };

export function validateSettings(value = {}) {
  requireValue(object(value), 'CANDIDATE_SETTINGS_INVALID');
  for (const [key, setting] of Object.entries(value)) {
    requireValue(key === 'followDisplayBrightness' ? typeof setting === 'boolean' :
      Object.hasOwn(SETTINGS, key) && integer(setting, ...SETTINGS[key]), 'CANDIDATE_SETTINGS_INVALID');
  }
  return { ...value };
}

export function validateCandidateConfig(value, { selfTest = false, discovery = false } = {}) {
  requireValue(object(value) && value.schemaVersion === 1, 'CANDIDATE_CONFIG_INVALID');
  requireValue(value.productMode === undefined || typeof value.productMode === 'boolean', 'CANDIDATE_CONFIG_INVALID');
  const identity = value.identity;
  requireValue(discovery && value.productMode === true && value.deviceId == null ||
    typeof value.deviceId === 'string' && value.deviceId.length > 0 && value.deviceId.length <= 128 && !/[\u0000-\u001f]/u.test(value.deviceId), 'CANDIDATE_DEVICE_REQUIRED');
  requireValue(object(identity) && ['dockSerial', 'childSerial'].every(key => typeof identity[key] === 'string' && /^[A-Za-z0-9_-]{1,32}$/.test(identity[key])) &&
    Array.isArray(identity.macAddress) && identity.macAddress.length === 6 && identity.macAddress.every(byte => integer(byte, 0, 255)), 'CANDIDATE_IDENTITY_REQUIRED');
  requireValue(object(value.runtime) && pathValue(value.runtime.command) && pathValue(value.runtime.manifestPath) && argsValue(value.runtime.args ?? []), 'CANDIDATE_RUNTIME_REQUIRED');
  requireValue(!value.runtime.args?.some(arg => /^--(?:backend|lcd-mode)(?:=|$)/.test(arg)), 'CANDIDATE_BACKEND_RESERVED');
  requireValue(pathValue(value.statePath) && pathValue(value.diagnosticsPath) && value.statePath !== value.diagnosticsPath, 'CANDIDATE_PATH_REQUIRED');
  for (const path of [value.statePath, value.diagnosticsPath]) {
    requireValue(![value.runtime.command, value.runtime.manifestPath].includes(path), 'CANDIDATE_PATH_INVALID');
    requireValue(lstatSync(dirname(path)).isDirectory() && !lstatSync(dirname(path)).isSymbolicLink(), 'CANDIDATE_STATE_DIRECTORY_REQUIRED');
  }
  const reconnect = { maxAttempts: 3, delayMs: 1000, ...value.reconnect };
  requireValue(integer(reconnect.maxAttempts, 0, 10) && integer(reconnect.delayMs, 1, 60000), 'CANDIDATE_RECONNECT_INVALID');
  if (!selfTest) requireValue(object(value.readiness) && pathValue(value.readiness.command) && argsValue(value.readiness.args ?? []), 'CANDIDATE_READINESS_REQUIRED');
  for (const name of ['port', 'childPort']) if (value[name] !== undefined) requireValue(integer(value[name], 1, 65535), 'CANDIDATE_PORT_INVALID');
  requireValue((value.port ?? 5343) !== (value.childPort ?? 5344), 'CANDIDATE_PORT_INVALID');
  requireValue(['native', 'stream'].includes(value.screensaverMode ?? 'stream'), 'CANDIDATE_SCREENSAVER_MODE');
  return structuredClone({ ...value, runtime: { ...value.runtime, args: value.runtime.args ?? [] }, identity,
    initialControls: validateSettings(value.initialControls), screensaverMode: value.screensaverMode ?? 'stream', productMode: value.productMode ?? false, reconnect });
}

export function readBoundedJson(path, maxBytes = 65536) {
  const stat = lstatSync(path);
  requireValue(stat.isFile() && !stat.isSymbolicLink() && stat.size > 0 && stat.size <= maxBytes, 'CANDIDATE_JSON_INVALID');
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch { throw new CandidateError('CANDIDATE_JSON_INVALID'); }
}

/** Resolve a portable, inventoried runtime. The SDK rehashes every file at start. */
export function loadCandidateManifest(runtime) {
  const manifest = readBoundedJson(runtime.manifestPath, 1024 * 1024);
  requireValue(object(manifest) && manifest.schemaVersion === 1 && typeof manifest.buildId === 'string' && /^[A-Za-z0-9_.-]{1,128}$/.test(manifest.buildId) &&
    /^[a-f0-9]{64}$/.test(manifest.executableSha256 ?? '') && Array.isArray(manifest.files) && manifest.files.length > 0 && manifest.files.length <= 1024,
  'CANDIDATE_MANIFEST_INVALID');
  const root = realpathSync(dirname(runtime.manifestPath));
  const containedFile = path => {
    const local = relative(root, path);
    requireValue(local && !local.startsWith(`..${sep}`) && local !== '..' && !isAbsolute(local), 'CANDIDATE_MANIFEST_PATH');
    let cursor = root;
    for (const part of local.split(sep)) { cursor = resolve(cursor, part); requireValue(!lstatSync(cursor).isSymbolicLink(), 'CANDIDATE_MANIFEST_PATH'); }
    requireValue(lstatSync(path).isFile() && realpathSync(path) === path, 'CANDIDATE_MANIFEST_PATH');
    return path;
  };
  const paths = new Set();
  const files = manifest.files.map(file => {
    requireValue(object(file) && typeof file.path === 'string' && file.path && !isAbsolute(file.path) && !file.path.includes('\\') &&
      !file.path.split('/').some(part => !part || part === '.' || part === '..') && /^[a-f0-9]{64}$/.test(file.sha256 ?? ''), 'CANDIDATE_MANIFEST_INVALID');
    const path = containedFile(resolve(root, file.path));
    requireValue(!paths.has(path), 'CANDIDATE_MANIFEST_INVALID'); paths.add(path);
    return { ...file, path };
  });
  const command = containedFile(resolve(runtime.command));
  requireValue(files.some(file => file.path === command && file.sha256 === manifest.executableSha256), 'CANDIDATE_EXECUTABLE_NOT_INVENTORIED');
  return { ...manifest, files };
}

export function candidateIdentityKey(config) {
  return createHash('sha256').update(JSON.stringify([config.deviceId, config.identity.dockSerial,
    config.identity.childSerial, config.identity.macAddress])).digest('hex');
}

export function loadCandidateSettings(config) {
  try {
    const saved = readBoundedJson(config.statePath);
    requireValue(saved.schemaVersion === 1 && saved.identityKey === candidateIdentityKey(config), 'CANDIDATE_STATE_IDENTITY');
    return { ...config.initialControls, ...validateSettings(saved.controls) };
  } catch (error) {
    if (error.code === 'ENOENT') return { ...config.initialControls };
    throw error;
  }
}

export function saveCandidateSettings(config, controls) {
  const temporary = `${config.statePath}.${process.pid}.tmp`;
  const data = JSON.stringify({ schemaVersion: 1, identityKey: candidateIdentityKey(config), controls: validateSettings(controls) }) + '\n';
  try {
    writeFileSync(temporary, data, { flag: 'wx', mode: 0o600 });
    renameSync(temporary, config.statePath);
  } finally { try { unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
}

const DIAGNOSTIC_CODES = new Set(`ABORTED ADAPTER_BUSY ADAPTER_DISCONNECTED ADAPTER_MISMATCH AMBIGUOUS_DEVICE AMBIGUOUS_MAPPING
CAPABILITY_MISMATCH CLEANUP_FAILED CLEANUP_REQUIRED DEVICE_NOT_FOUND DEVICE_REQUIRED HOST_IDENTITY_MISMATCH HOST_IDENTITY_REQUIRED
HOST_NOT_READY HOST_READINESS_REQUIRED INCOMPLETE_MAPPING INVALID_ADAPTER INVALID_BRIDGE INVALID_DISCOVERY INVALID_OPTIONS
INVALID_OUTPUT_SEQUENCE OUTPUT_OVERFLOW SESSION_FAILED STALE_INPUT STALE_OUTPUT UNMAPPED_DISPLAY UNSUPPORTED_CONTROL
UNSUPPORTED_INPUT UNSUPPORTED_MAPPING UNSUPPORTED_OUTPUT UNSUPPORTED_PLATFORM WORKER_DIAGNOSTIC EADDRINUSE EACCES EPERM ENOENT ENOSPC
ERR_ABORTED ERR_BACKPRESSURE ERR_CONTRACT ERR_CONTRACT_SCHEMA ERR_DEVICE_BUSY ERR_DEVICE_CLOSED ERR_DEVICE_CLOSE ERR_DEVICE_ID
ERR_DEVICE_NOT_FOUND ERR_DEVICE_OPEN ERR_DEVICE_OPERATION ERR_DUPLICATE_REQUEST ERR_HANDSHAKE_REQUIRED ERR_INPUT_ORDER ERR_IPC_CONFIG
ERR_IPC_PARSE ERR_IPC_PROTOCOL ERR_IPC_VERSION ERR_MESSAGE_TOO_LARGE ERR_OUTPUT_ORDER ERR_REQUEST_TIMEOUT ERR_STALE_GENERATION
ERR_UNSUPPORTED_OPERATION ERR_WORKER_BUILD_ID ERR_WORKER_DIAGNOSTICS ERR_WORKER_DIGEST ERR_WORKER_EOF ERR_WORKER_EXIT
ERR_WORKER_MANIFEST ERR_WORKER_OFFLINE ERR_WORKER_OPERATION ERR_WORKER_PIPE ERR_WORKER_START ERR_WORKER_STOPPED
CANDIDATE_ARGUMENTS CANDIDATE_BACKEND_RESERVED CANDIDATE_COMMAND_INVALID CANDIDATE_COMMAND_LIMIT CANDIDATE_COMMAND_TRUNCATED
CANDIDATE_CONFIG_INVALID CANDIDATE_CONFIG_REQUIRED CANDIDATE_CONTROL_INVALID CANDIDATE_CONTROL_LIMIT CANDIDATE_CONTROL_NOT_READY
CANDIDATE_CONTROL_TIMEOUT CANDIDATE_DEVICE_REQUIRED CANDIDATE_DIAGNOSTICS_INVALID CANDIDATE_DIAGNOSTICS_WRITE CANDIDATE_EXECUTABLE_NOT_INVENTORIED CANDIDATE_FAILED
CANDIDATE_HOST_DISCONNECTED CANDIDATE_IDENTITY_REQUIRED CANDIDATE_JSON_INVALID CANDIDATE_MANIFEST_INVALID CANDIDATE_MANIFEST_PATH
CANDIDATE_OUTPUT_RESULT CANDIDATE_OUTPUT_TIMEOUT CANDIDATE_PATH_INVALID CANDIDATE_PATH_REQUIRED CANDIDATE_PORT_INVALID CANDIDATE_READINESS_EXIT
CANDIDATE_READINESS_IDENTITY CANDIDATE_READINESS_JSON CANDIDATE_READINESS_LIMIT CANDIDATE_READINESS_PIPE CANDIDATE_READINESS_REQUIRED
CANDIDATE_READINESS_START CANDIDATE_READINESS_TIMEOUT CANDIDATE_RECONNECT_INVALID CANDIDATE_RECONNECT_STATE CANDIDATE_RESTORE_LIMIT
CANDIDATE_RUNTIME_REQUIRED CANDIDATE_SCREENSAVER_MODE CANDIDATE_SESSION_ENDED CANDIDATE_SETTINGS_INVALID CANDIDATE_START_CANCELLED
CANDIDATE_START_REQUIRED CANDIDATE_START_STATE CANDIDATE_START_TIMEOUT CANDIDATE_STATE_DIRECTORY_REQUIRED CANDIDATE_STATE_IDENTITY`.split(/\s+/));
export function diagnosticCode(error) { return DIAGNOSTIC_CODES.has(error?.code) ? error.code : 'CANDIDATE_FAILED'; }

/** Two bounded files, allowlisted scalar fields only. Never write raw worker text. */
export function createCandidateDiagnostics(path, maxBytes = 256 * 1024) {
  let bytes = 0;
  try { const stat = lstatSync(path); requireValue(stat.isFile() && !stat.isSymbolicLink(), 'CANDIDATE_DIAGNOSTICS_INVALID'); bytes = stat.size; }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  return (event, detail = {}) => {
    requireValue(/^[a-z][a-zA-Z]{0,40}$/.test(event), 'CANDIDATE_DIAGNOSTICS_INVALID');
    const safe = { time: new Date().toISOString(), event };
    if (detail.code) safe.code = diagnosticCode(detail);
    for (const key of ['generation', 'attempt', 'count']) if (integer(detail[key], 0, Number.MAX_SAFE_INTEGER)) safe[key] = detail[key];
    const line = JSON.stringify(safe) + '\n';
    if (bytes + Buffer.byteLength(line) > maxBytes) {
      try { unlinkSync(`${path}.1`); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      try { renameSync(path, `${path}.1`); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      bytes = 0;
    }
    appendFileSync(path, line, { mode: 0o600 }); bytes += Buffer.byteLength(line);
  };
}

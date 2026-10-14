import packageMetadata from '../../package.json' with { type: 'json' };
/** Bounded JSON-RPC 2.0 NDJSON transport. Importing never starts a process. */
import { spawn as spawnChild } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { access, lstat, realpath, stat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { validateAdapterDescriptor, validateDeviceDescriptor, validateInputEvent, validateDisplayFrame, validateControlCommand } from '@hellgato/core/contracts';

export const IPC_VERSION = Object.freeze({ major: 1, minor: 0 });
export const IPC_LIMITS = Object.freeze({ maxMessageBytes: 1_500_000, maxFrameBytes: 1_048_576,
  maxPendingRequests: 32, maxQueueBytes: 4_500_000, maxDeadlineMs: 60_000 });
const METHODS = new Set(['hello', 'discover', 'open', 'close', 'setOutput', 'setControl', 'getState']);
const FEATURES = Object.freeze(['deadlines', 'generation', 'base64-frames']);
const own = (value, key) => Object.hasOwn(value, key);
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export class SidecarError extends Error {
  constructor(code, message, details) { super(message); this.name = 'SidecarError'; this.code = code; if (details) this.details = details; }
}
const error = (code, message, details) => new SidecarError(code, message, details);
function integer(value, minimum, maximum, name) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) throw error('ERR_IPC_CONFIG', `Invalid ${name}`);
  return value;
}
/** Validate the language-neutral envelope without accepting batches or JSON-RPC notifications as commands. */
export function validateRpcMessage(message) {
  if (!plain(message) || message.jsonrpc !== '2.0') throw error('ERR_IPC_PROTOCOL', 'Expected JSON-RPC 2.0 object');
  if (own(message, 'method')) {
    if (typeof message.method !== 'string' || !message.method || message.method.length > 64 || !plain(message.params))
      throw error('ERR_IPC_PROTOCOL', 'Invalid RPC method or params');
    if (own(message, 'id') && (typeof message.id !== 'string' || !message.id || message.id.length > 128))
      throw error('ERR_IPC_PROTOCOL', 'Request ID must be a bounded nonempty string');
    if (own(message, 'result') || own(message, 'error')) throw error('ERR_IPC_PROTOCOL', 'Request contains response fields');
  } else {
    if ((message.id !== null && (typeof message.id !== 'string' || !message.id || message.id.length > 128)) ||
        (own(message, 'result') === own(message, 'error'))) throw error('ERR_IPC_PROTOCOL', 'Invalid RPC response');
    if (own(message, 'error') && (!plain(message.error) || !Number.isInteger(message.error.code) ||
        typeof message.error.message !== 'string' || message.error.message.length > 1024))
      throw error('ERR_IPC_PROTOCOL', 'Invalid RPC error');
  }
  return message;
}
async function digest(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}
function abortCheck(signal) { if (signal?.aborted) throw error('ERR_ABORTED', 'Sidecar operation aborted'); }

export function createSidecarAdapter(options = {}) {
  const { command, args = [], cwd, env, manifest, expectedBuildId = manifest?.buildId } = options;
  if (typeof command !== 'string' || !isAbsolute(command)) throw error('ERR_IPC_CONFIG', 'An explicit absolute worker executable is required');
  if (!Array.isArray(args) || args.length > 128 || args.some(arg => typeof arg !== 'string' || arg.includes('\0') || arg.length > 32768)) throw error('ERR_IPC_CONFIG', 'Worker args must be strings');
  if (cwd !== undefined && !isAbsolute(cwd)) throw error('ERR_IPC_CONFIG', 'Worker cwd must be absolute');
  if (env !== undefined && (!plain(env) || Object.entries(env).some(([key, value]) => !key || key.includes('=') || key.includes('\0') || typeof value !== 'string' || value.includes('\0'))))
    throw error('ERR_IPC_CONFIG', 'Worker environment must contain explicit string values');
  const inheritedNames = ['PATH', 'Path', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LANG', 'LC_ALL', 'LC_CTYPE', 'COMSPEC', 'PATHEXT'];
  const childEnvironment = { ...Object.fromEntries(inheritedNames.filter(name => globalThis.process.env[name] !== undefined).map(name => [name, globalThis.process.env[name]])), ...env };
  let launchCommand = command;
  const maxMessageBytes = integer(options.maxMessageBytes ?? IPC_LIMITS.maxMessageBytes, 1024, IPC_LIMITS.maxMessageBytes, 'maxMessageBytes');
  const maxPending = integer(options.maxPendingRequests ?? IPC_LIMITS.maxPendingRequests, 1, 128, 'maxPendingRequests');
  const maxQueueBytes = integer(options.maxQueueBytes ?? IPC_LIMITS.maxQueueBytes, maxMessageBytes, 16 * IPC_LIMITS.maxMessageBytes, 'maxQueueBytes');
  const timeoutMs = integer(options.requestTimeoutMs ?? 5000, 1, IPC_LIMITS.maxDeadlineMs, 'requestTimeoutMs');
  const stopTimeoutMs = integer(options.stopTimeoutMs ?? 2000, 1, 30_000, 'stopTimeoutMs');
  const descriptor = options.descriptor ?? { id: options.id ?? 'hellgato.sidecar', version: packageMetadata.version, modelIds: [],
    platforms: [globalThis.process.platform], architectures: [globalThis.process.arch], apiVersion: 1, protocol: IPC_VERSION };
  validateAdapterDescriptor(descriptor);
  const listeners = { input: new Set(), state: new Set(), diagnostic: new Set() };
  let process = null, status = 'idle', starting = null, stopping = null, epoch = 0, counter = 0;
  let current = null, generation = 0, lastInput = -1, hello = null, buffer = Buffer.alloc(0);
  let pending = new Map(), queue = [], queuedBytes = 0, writing = false, stopRequested = false, tearingDown = null;
  const closedProcesses = new WeakMap();
  let parentExit = null;
  function emit(kind, value) {
    for (const fn of listeners[kind]) {
      try { fn(value); } catch { /* Consumer callbacks cannot corrupt transport parsing. */ }
    }
  }
  function state(next, details = {}) { status = next; emit('state', { state: next, ...details }); }
  function rejectPending(reason) {
    for (const entry of pending.values()) { clearTimeout(entry.timer); entry.signal?.removeEventListener('abort', entry.abort); entry.reject(reason); }
    pending.clear(); queue = []; queuedBytes = 0;
  }
  function fail(reason, child = process) {
    if (child !== process || !child) return;
    rejectPending(reason);
    current = null;
    child.stdin.destroy();
    child.kill();
    if (!tearingDown) tearingDown = terminate(child).finally(() => { tearingDown = null; });
    if (!stopRequested) state('failed', { code: reason.code, message: reason.message });
  }
  async function preflight({ signal } = {}) {
    abortCheck(signal);
    launchCommand = await realpath(command);
    const info = await stat(launchCommand);
    if (!info.isFile()) throw error('ERR_WORKER_MANIFEST', 'Worker executable is not a regular file');
    await access(launchCommand);
    if (cwd !== undefined && !(await stat(cwd)).isDirectory()) throw error('ERR_IPC_CONFIG', 'Worker cwd is not a directory');
    if (manifest !== undefined) {
      if (!plain(manifest) || manifest.schemaVersion !== 1 || typeof manifest.buildId !== 'string' || !manifest.buildId ||
          manifest.buildId.length > 128 || manifest.platform !== globalThis.process.platform || manifest.arch !== globalThis.process.arch ||
          !/^[a-f0-9]{64}$/.test(manifest.executableSha256 ?? ''))
        throw error('ERR_WORKER_MANIFEST', 'Worker manifest version, platform, architecture or digest is invalid');
      if (await digest(launchCommand) !== manifest.executableSha256) throw error('ERR_WORKER_DIGEST', 'Worker executable digest mismatch');
      if (manifest.files !== undefined && (!Array.isArray(manifest.files) || manifest.files.length > 1024))
        throw error('ERR_WORKER_MANIFEST', 'Invalid worker file inventory');
      for (const file of manifest.files ?? []) {
        if (!plain(file) || typeof file.path !== 'string' || !isAbsolute(file.path) || !/^[a-f0-9]{64}$/.test(file.sha256 ?? ''))
          throw error('ERR_WORKER_MANIFEST', 'Invalid worker file inventory entry');
        if ((await lstat(file.path)).isSymbolicLink()) throw error('ERR_WORKER_MANIFEST', 'Worker inventory files must not be symlinks');
        if (!(await stat(file.path)).isFile() || await digest(file.path) !== file.sha256)
          throw error('ERR_WORKER_DIGEST', 'Worker dependency digest mismatch');
      }
    }
    abortCheck(signal);
    return { verified: manifest !== undefined, buildId: expectedBuildId ?? null };
  }
  function flush() {
    if (writing || !process || process.stdin.destroyed) return;
    while (queue.length) {
      const entry = queue.shift(); queuedBytes -= entry.bytes.length;
      if (!pending.has(entry.id)) continue;
      if (Date.now() >= entry.deadlineAt) { entry.expire(); continue; }
      writing = true; entry.sent = true;
      const child = process;
      child.stdin.write(entry.bytes, writeError => {
        writing = false;
        if (child !== process) return;
        if (writeError) fail(error('ERR_WORKER_PIPE', 'Worker input pipe closed'), child);
        else flush();
      });
      return;
    }
  }
  function request(method, parameters = {}, { signal, deadlineMs = timeoutMs } = {}) {
    if (!METHODS.has(method)) return Promise.reject(error('ERR_UNSUPPORTED_OPERATION', `Unsupported operation: ${method}`));
    if (!process || process.stdin.destroyed) return Promise.reject(error('ERR_WORKER_OFFLINE', 'Worker is not running'));
    if (signal?.aborted) return Promise.reject(error('ERR_ABORTED', 'Sidecar operation aborted'));
    if (pending.size >= maxPending) return Promise.reject(error('ERR_BACKPRESSURE', 'Worker request queue is full'));
    const id = `${epoch}:${++counter}`;
    const deadlineAt = Date.now() + integer(deadlineMs, 1, IPC_LIMITS.maxDeadlineMs, 'deadlineMs');
    const params = { ...parameters, deadlineAt };
    const bytes = Buffer.from(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    if (bytes.length > maxMessageBytes) return Promise.reject(error('ERR_MESSAGE_TOO_LARGE', 'Worker request exceeds message limit'));
    if (queuedBytes + bytes.length > maxQueueBytes) return Promise.reject(error('ERR_BACKPRESSURE', 'Worker byte queue is full'));
    return new Promise((resolve, reject) => {
      const entry = { id, method, bytes, deadlineAt, resolve, reject, signal, sent: false };
      entry.expire = () => {
        if (!pending.delete(id)) return;
        clearTimeout(entry.timer); signal?.removeEventListener('abort', entry.abort);
        reject(error('ERR_REQUEST_TIMEOUT', 'Worker request deadline exceeded', { method, outcome: entry.sent ? 'unknown' : 'not-sent' }));
      };
      entry.abort = () => {
        if (!pending.delete(id)) return;
        clearTimeout(entry.timer);
        reject(error('ERR_ABORTED', 'Sidecar operation aborted', { method, outcome: entry.sent ? 'unknown' : 'not-sent' }));
      };
      entry.timer = setTimeout(entry.expire, deadlineMs);
      signal?.addEventListener('abort', entry.abort, { once: true });
      pending.set(id, entry); queue.push(entry); queuedBytes += bytes.length; flush();
    });
  }
  function receive(message, child) {
    validateRpcMessage(message);
    if (own(message, 'method')) {
      if (own(message, 'id')) throw error('ERR_IPC_PROTOCOL', 'Worker cannot request parent operations');
      if (!['input', 'state', 'diagnostic'].includes(message.method)) throw error('ERR_IPC_PROTOCOL', 'Unknown worker notification');
      const value = message.params;
      if (message.method === 'input') {
        if (!current || value.generation !== generation || value.deviceId !== current) return;
        validateInputEvent(value);
        if (value.sequence <= lastInput) throw error('ERR_INPUT_ORDER', 'Worker input sequence is not increasing');
        lastInput = value.sequence; emit('input', value);
      } else if (message.method === 'state') {
        if (value.generation !== undefined && value.generation !== generation) return;
        emit('state', value);
      } else emit('diagnostic', { code: String(value.code ?? 'WORKER_DIAGNOSTIC').slice(0, 128), message: String(value.message ?? '').slice(0, 1024) });
      return;
    }
    const entry = pending.get(message.id);
    if (!entry) return; // Expired requests and previous process generations cannot settle new work.
    pending.delete(message.id); clearTimeout(entry.timer); entry.signal?.removeEventListener('abort', entry.abort);
    if (own(message, 'error')) entry.reject(error(message.error.data?.code ?? 'ERR_WORKER_OPERATION', message.error.message,
      { rpcCode: message.error.code, ...message.error.data }));
    else entry.resolve(message.result);
  }
  async function start({ signal } = {}) {
    if (stopping) await stopping;
    if (tearingDown) await tearingDown;
    if (status === 'ready') return hello;
    if (starting) return starting;
    stopRequested = false;
    starting = (async () => {
      await preflight({ signal });
      if (stopRequested) throw error('ERR_ABORTED', 'Worker start was stopped');
      state('starting'); epoch++; buffer = Buffer.alloc(0); writing = false;
      const child = spawnChild(launchCommand, args, { cwd, env: childEnvironment,
        stdio: ['pipe', 'pipe', 'pipe'], shell: false, windowsHide: true });
      process = child;
      const closed = new Promise(resolve => child.once('close', (code, exitSignal) => {
        if (child === process) {
          rejectPending(error('ERR_WORKER_EXIT', 'Worker exited', { code, signal: exitSignal }));
          process = null; current = null;
          if (parentExit) { globalThis.process.removeListener('exit', parentExit); parentExit = null; }
          if (!stopRequested) state('failed', { code: 'ERR_WORKER_EXIT', exitCode: code, signal: exitSignal });
        }
        resolve();
      }));
      closedProcesses.set(child, closed);
      parentExit = () => child.kill(); globalThis.process.once('exit', parentExit);
      child.once('error', cause => fail(error('ERR_WORKER_START', `Unable to start worker: ${cause.code ?? 'unknown'}`), child));
      child.stdin.on('error', () => fail(error('ERR_WORKER_PIPE', 'Worker input pipe failed'), child));
      child.stdout.on('data', chunk => {
        if (child !== process) return;
        try {
          let offset = 0;
          for (;;) {
            const newline = chunk.indexOf(10, offset);
            const part = newline < 0 ? chunk.subarray(offset) : chunk.subarray(offset, newline);
            if (buffer.length + part.length + 1 > maxMessageBytes) throw error('ERR_MESSAGE_TOO_LARGE', 'Worker message exceeds limit');
            buffer = Buffer.concat([buffer, part]);
            if (newline < 0) break;
            const line = new TextDecoder('utf-8', { fatal: true }).decode(buffer); buffer = Buffer.alloc(0);
            if (!line.trim()) throw error('ERR_IPC_PROTOCOL', 'Empty worker message');
            receive(JSON.parse(line), child); offset = newline + 1;
            if (offset === chunk.length) break;
          }
        } catch (cause) { fail(cause instanceof SidecarError ? cause : error('ERR_IPC_PROTOCOL', 'Invalid worker protocol message'), child); }
      });
      child.stdout.once('end', () => {
        if (!stopRequested && child === process) fail(error('ERR_WORKER_EOF', buffer.length ? 'Truncated worker message' : 'Worker protocol stream ended'), child);
      });
      // Never retain arbitrary native diagnostic output (which can contain serials or payloads).
      let stderrBytes = 0;
      child.stderr.on('data', chunk => { stderrBytes += chunk.length; if (stderrBytes > maxMessageBytes) fail(error('ERR_WORKER_DIAGNOSTICS', 'Worker diagnostic output exceeded limit'), child); });
      try {
        hello = await request('hello', { protocol: IPC_VERSION, requiredFeatures: FEATURES, maxMessageBytes }, { signal });
        if (!plain(hello) || hello.protocol?.major !== IPC_VERSION.major || !Number.isInteger(hello.protocol?.minor) || hello.protocol.minor < 0 ||
            typeof hello.buildId !== 'string' || !hello.buildId || typeof hello.implementationVersion !== 'string' ||
            !Array.isArray(hello.features) || FEATURES.some(feature => !hello.features.includes(feature)) ||
            !Number.isInteger(hello.maxMessageBytes) || hello.maxMessageBytes < maxMessageBytes ||
            !Number.isInteger(hello.maxFrameBytes) || hello.maxFrameBytes < 1 || hello.maxFrameBytes > IPC_LIMITS.maxFrameBytes)
          throw error('ERR_IPC_VERSION', 'Worker hello is incompatible');
        if (expectedBuildId !== undefined && hello.buildId !== expectedBuildId) throw error('ERR_WORKER_BUILD_ID', 'Worker build ID differs from verified manifest');
        if (stopRequested) throw error('ERR_ABORTED', 'Worker start was stopped');
        state('ready'); return hello;
      } catch (cause) {
        fail(cause, child);
        await (tearingDown ?? terminate(child));
        throw cause;
      }
    })().catch(cause => { if (!process && !stopRequested) state('failed', { code: cause.code ?? 'ERR_WORKER_START', message: 'Worker start failed' }); throw cause; }).finally(() => { starting = null; });
    return starting;
  }
  async function terminate(child) {
    if (!child) return;
    const ended = closedProcesses.get(child);
    if (child.exitCode !== null || child.signalCode !== null) return ended;
    if (!child.stdin.destroyed) child.stdin.end();
    let timer;
    await Promise.race([ended, new Promise(resolve => { timer = setTimeout(resolve, stopTimeoutMs); })]);
    clearTimeout(timer);
    if (child.exitCode === null && child.signalCode === null) {
      child.kill();
      await Promise.race([ended, new Promise(resolve => { timer = setTimeout(resolve, stopTimeoutMs); })]); clearTimeout(timer);
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await ended; }
    }
  }
  async function stop() {
    if (stopping) return stopping;
    stopRequested = true;
    stopping = (async () => {
      state('stopping');
      if (process && current) { try { await close(); } catch { /* EOF and process termination remain mandatory. */ } }
      if (process) await (tearingDown ?? terminate(process));
      if (starting) { try { await starting; } catch {} }
      rejectPending(error('ERR_WORKER_STOPPED', 'Worker stopped'));
      current = null; hello = null; state('stopped');
    })().finally(() => { stopping = null; });
    return stopping;
  }
  async function discover(options = {}) {
    if (status !== 'ready') throw error('ERR_WORKER_OFFLINE', 'Start the adapter before discovery');
    const devices = await request('discover', {}, options);
    if (!Array.isArray(devices) || devices.length > 64) throw error('ERR_IPC_PROTOCOL', 'Invalid discovery result');
    devices.forEach(validateDeviceDescriptor); return devices;
  }
  async function open(deviceId, { generation: nextGeneration = generation + 1, sessionId, signal } = {}) {
    if (current) throw error('ERR_DEVICE_BUSY', 'An adapter device is already open');
    if (typeof deviceId !== 'string' || !deviceId || deviceId.length > 256) throw error('ERR_DEVICE_ID', 'Explicit device ID is required');
    integer(nextGeneration, generation + 1, Number.MAX_SAFE_INTEGER, 'generation');
    generation = nextGeneration; lastInput = -1; current = deviceId;
    try { return await request('open', { deviceId, generation, ...(sessionId ? { sessionId } : {}) }, { signal }); }
    catch (cause) { current = null; throw cause; }
  }
  async function close(options = {}) {
    if (!current) return { status: 'closed' };
    const deviceId = current;
    try { return await request('close', { deviceId, generation }, options); }
    finally { current = null; }
  }
  function active(payload) {
    if (!current) throw error('ERR_DEVICE_CLOSED', 'Open a device before sending output');
    if (payload.deviceId !== undefined && payload.deviceId !== current) throw error('ERR_DEVICE_ID', 'Output device differs from open device');
    if (payload.generation !== undefined && payload.generation !== generation) throw error('ERR_STALE_GENERATION', 'Output generation is stale');
    return { ...payload, deviceId: current, generation };
  }
  return Object.freeze({
    id: descriptor.id, descriptor, preflight, start, stop, discover, open, close,
    setOutput(frame, requestOptions) { validateDisplayFrame(frame); return request('setOutput', active(frame), requestOptions); },
    setControl(control, requestOptions) { validateControlCommand(control); return request('setControl', active(control), requestOptions); },
    getState(requestOptions) { return request('getState', current ? { deviceId: current, generation } : {}, requestOptions); },
    onInput(fn) { listeners.input.add(fn); return () => listeners.input.delete(fn); },
    onState(fn) { listeners.state.add(fn); return () => listeners.state.delete(fn); },
    onDiagnostic(fn) { listeners.diagnostic.add(fn); return () => listeners.diagnostic.delete(fn); },
    get status() { return status; }, get pid() { return process?.pid ?? null; }, get generation() { return generation; },
  });
}

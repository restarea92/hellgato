import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import { TextDecoder } from 'node:util';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const has = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const failure = (code, message, details) => Object.assign(new Error(message), { code, ...(details ? { details } : {}) });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

async function completedWithin(promise, milliseconds) {
  let timer;
  try {
    return await Promise.race([promise.then(() => true), new Promise(resolve => { timer = setTimeout(() => resolve(false), milliseconds); })]);
  } finally { clearTimeout(timer); }
}

export class FrameAdapterProcess extends EventEmitter {
  #options; #child = null; #state = 'idle'; #metadata = null; #pending = new Map(); #sequence = 0;
  #start = null; #ready = null; #readyTimer = null; #shutdown = null; #exit = null;
  #ended = false; #buffer = Buffer.alloc(0); #error = null; #stderr = '';
  #decoder = new TextDecoder('utf-8', { fatal: true });

  constructor({ command, args = [], cwd, requestTimeoutMs = 10000, maxMessageBytes = 1500000, maxPending = 16 }) {
    super();
    if (typeof command !== 'string' || !command || command.includes('\0') || !Array.isArray(args) ||
        args.some(argument => typeof argument !== 'string' || argument.includes('\0')) ||
        (cwd !== undefined && (typeof cwd !== 'string' || !cwd || cwd.includes('\0'))) ||
        !Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > 60000 ||
        !Number.isSafeInteger(maxMessageBytes) || maxMessageBytes < 128 || maxMessageBytes > 16777216 ||
        !Number.isSafeInteger(maxPending) || maxPending < 1 || maxPending > 1024) {
      throw new TypeError('Invalid Rust adapter options');
    }
    this.#options = { command, args: [...args], cwd, requestTimeoutMs, maxMessageBytes, maxPending };
  }

  get metadata() { return this.#metadata === null ? null : structuredClone(this.#metadata); }
  status() {
    return { state: this.#state, pid: this.#ended ? null : this.#child?.pid ?? null, pending: this.#pending.size,
      error: this.#error?.code ?? null, stderrTail: this.#stderr };
  }

  start() {
    if (this.#start && ['starting', 'ready'].includes(this.#state)) return this.#start;
    if (this.#state !== 'idle') return Promise.reject(failure('RUST_ADAPTER_CLOSED', 'The Rust adapter cannot be started in its current state'));
    this.#state = 'starting'; this.#ready = deferred(); this.#exit = deferred();
    this.#start = this.#ready.promise;
    const { command, args, cwd, requestTimeoutMs } = this.#options;
    try {
      this.#child = spawn(command, args, { cwd, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
      const child = this.#child;
      child.stdout.on('data', bytes => this.#read(bytes));
      child.stdout.on('error', error => this.#fail(failure('RUST_WORKER_PIPE', 'The Rust adapter output pipe failed', { cause: error.message })));
      child.stdout.once('end', () => {
        if (this.#buffer.length && !this.#error) this.#fail(failure('RUST_PROTOCOL_INVALID', 'The Rust adapter ended with an incomplete message'));
      });
      child.stderr.on('data', bytes => { this.#stderr = (this.#stderr + bytes.toString('utf8')).slice(-8192); });
      child.stderr.on('error', error => { this.#stderr = (this.#stderr + `\n${error.message}`).slice(-8192); });
      child.stdin.on('error', error => {
        if (!['closing', 'closed'].includes(this.#state)) this.#fail(failure('RUST_WORKER_PIPE', 'The Rust adapter input pipe failed', { cause: error.message }));
      });
      child.once('error', error => this.#fail(failure('RUST_WORKER_START', 'The Rust adapter process failed', { cause: error.message })));
      child.once('close', (code, signal) => {
        this.#ended = true; this.#exit.resolve({ code, signal });
        if (!['closing', 'closed', 'failed'].includes(this.#state)) {
          this.#fail(failure('RUST_WORKER_EXIT', 'The Rust adapter exited before shutdown', { code, signal }));
        }
      });
      this.#readyTimer = setTimeout(() => this.#fail(failure('RUST_READY_TIMEOUT', 'The Rust adapter did not announce readiness in time')), requestTimeoutMs);
    } catch (error) {
      this.#ended = true; this.#exit.resolve();
      this.#fail(failure('RUST_WORKER_START', 'The Rust adapter process could not be started', { cause: error.message }));
    }
    return this.#start;
  }

  #read(bytes) {
    if (this.#error || this.#state === 'closed') return;
    this.#buffer = Buffer.concat([this.#buffer, bytes]);
    while (!this.#error) {
      const newline = this.#buffer.indexOf(10);
      if (newline < 0) break;
      if (newline > this.#options.maxMessageBytes) {
        this.#fail(failure('RUST_MESSAGE_TOO_LARGE', 'The Rust adapter exceeded its message byte limit')); return;
      }
      const line = this.#buffer.subarray(0, newline);
      this.#buffer = this.#buffer.subarray(newline + 1);
      try {
        const text = this.#decoder.decode(line);
        if (!text.trim()) throw new Error('Empty protocol line');
        this.#receive(JSON.parse(text));
      } catch (error) {
        this.#fail(failure('RUST_PROTOCOL_INVALID', 'The Rust adapter returned an invalid message', { cause: error.message }));
      }
    }
    if (this.#buffer.length > this.#options.maxMessageBytes) {
      this.#fail(failure('RUST_MESSAGE_TOO_LARGE', 'The Rust adapter exceeded its partial message byte limit'));
    }
  }

  #receive(value) {
    if (!object(value)) throw new Error('Expected a protocol object');
    if (has(value, 'event')) {
      if (typeof value.event !== 'string' || !value.event || value.event.length > 128 || has(value, 'id')) throw new Error('Invalid event envelope');
      if (value.event === 'ready') {
        if (this.#metadata || this.#state !== 'starting' || value.protocolVersion !== 1 ||
            typeof value.adapterId !== 'string' || !value.adapterId || typeof value.modelId !== 'string' || !value.modelId ||
            !object(value.layout)) throw new Error('Invalid readiness metadata');
        this.#metadata = structuredClone(value); this.#state = 'ready'; clearTimeout(this.#readyTimer);
        this.#ready.resolve(this.metadata);
      } else if (!this.#metadata) throw new Error('Event received before readiness');
      this.emit('event', value);
      return;
    }
    if (!Number.isSafeInteger(value.id) || value.id <= 0 || has(value, 'result') === has(value, 'error')) throw new Error('Invalid response envelope');
    const pending = this.#pending.get(value.id);
    if (!pending) throw new Error('Response does not match an active request');
    if (has(value, 'error') && (!object(value.error) || typeof value.error.message !== 'string' ||
        !['string', 'number'].includes(typeof value.error.code))) throw new Error('Invalid adapter error');
    this.#pending.delete(value.id); clearTimeout(pending.timer);
    if (has(value, 'error')) pending.reject(failure(value.error.code, value.error.message));
    else pending.resolve(value.result);
  }

  request(method, params = {}) { return this.#request(method, params); }

  #request(method, params, closing = false) {
    if (this.#state !== 'ready' && !(closing && this.#state === 'closing' && this.#metadata)) {
      return Promise.reject(this.#error ?? failure('RUST_ADAPTER_NOT_READY', 'The Rust adapter is not ready'));
    }
    if (typeof method !== 'string' || !method || method.length > 128 || !object(params)) return Promise.reject(new TypeError('Invalid adapter request'));
    if (!closing && this.#pending.size >= this.#options.maxPending) return Promise.reject(failure('RUST_PENDING_LIMIT', 'The Rust adapter request limit was reached'));
    let bytes;
    const id = ++this.#sequence;
    try {
      bytes = Buffer.from(`${JSON.stringify({ id, method, params })}\n`);
      if (bytes.length - 1 > this.#options.maxMessageBytes) throw failure('RUST_MESSAGE_TOO_LARGE', 'The Rust adapter request exceeds its message byte limit');
    } catch (error) { return Promise.reject(error); }
    const result = deferred();
    const timer = setTimeout(() => {
      const error = failure('RUST_REQUEST_TIMEOUT', 'The Rust adapter request timed out', { method });
      if (closing) {
        this.#pending.delete(id); result.reject(error);
      } else this.#fail(error);
    }, closing ? Math.min(3000, this.#options.requestTimeoutMs) : this.#options.requestTimeoutMs);
    this.#pending.set(id, { ...result, timer });
    try {
      this.#child.stdin.write(bytes, error => {
        if (error && !this.#ended) this.#fail(failure('RUST_WORKER_PIPE', 'The Rust adapter request could not be written', { cause: error.message }));
      });
    } catch (error) { this.#fail(failure('RUST_WORKER_PIPE', 'The Rust adapter request could not be written', { cause: error.message })); }
    return result.promise;
  }

  #rejectPending(error) {
    for (const item of this.#pending.values()) { clearTimeout(item.timer); item.reject(error); }
    this.#pending.clear();
  }

  #fail(error) {
    if (this.#error || this.#state === 'closed') return;
    this.#error = error; this.#state = 'failed'; clearTimeout(this.#readyTimer);
    this.#ready?.reject(error); this.#rejectPending(error);
    void this.#shutdownProcess(false).catch(cleanupError => this.emit('failure', cleanupError));
    this.emit('failure', error);
  }

  close() { return this.#shutdownProcess(true); }

  #shutdownProcess(graceful) {
    if (this.#shutdown) return this.#shutdown;
    this.#shutdown = this.#finishShutdown(graceful);
    return this.#shutdown;
  }

  async #finishShutdown(graceful) {
    clearTimeout(this.#readyTimer);
    if (!this.#child) { this.#state = this.#error ? 'failed' : 'closed'; return; }
    const wasReady = this.#state === 'ready';
    if (!this.#error) this.#state = 'closing';
    this.#ready?.reject(failure('RUST_ADAPTER_CLOSED', 'The Rust adapter was closed before readiness'));
    let closeResponse = Promise.resolve(null);
    if (graceful && wasReady) closeResponse = this.#request('close', {}, true).then(value => ({ value }), error => ({ error }));
    try { if (!this.#child.stdin.destroyed) this.#child.stdin.end(); } catch { /* Process completion below determines cleanup. */ }
    let forced = false;
    if (!this.#ended && !await completedWithin(this.#exit.promise, 3000)) {
      forced = true; this.#child.kill();
      if (!await completedWithin(this.#exit.promise, 1000)) {
        this.#child.kill('SIGKILL');
        if (!await completedWithin(this.#exit.promise, 1000)) {
          const error = failure('RUST_CLEANUP_FAILED', 'The Rust adapter process did not terminate');
          this.#error ??= error; this.#state = 'failed'; this.#rejectPending(error); throw error;
        }
      }
    }
    const exit = await this.#exit.promise;
    this.#rejectPending(this.#error ?? failure('RUST_ADAPTER_CLOSED', 'The Rust adapter closed before answering its request'));
    const reply = await closeResponse;
    if (forced || reply?.error || (graceful && wasReady && reply?.value?.status !== 'closed') ||
        (graceful && (exit?.code !== 0 || exit?.signal))) {
      const error = failure('RUST_CLEANUP_FAILED', 'The Rust adapter required forced termination or did not confirm native cleanup',
        { forced, exit, ...(reply?.error ? { cause: reply.error.code ?? reply.error.message } : {}) });
      this.#error ??= error; this.#state = 'failed'; throw error;
    }
    this.#state = this.#error ? 'failed' : 'closed'; this.#buffer = Buffer.alloc(0);
  }
}

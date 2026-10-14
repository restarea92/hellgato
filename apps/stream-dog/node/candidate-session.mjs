import { EventEmitter } from 'node:events';
import { dirname } from 'node:path';
import { performance } from 'node:perf_hooks';
import { createSession, createSidecarAdapter, createDeviceBridge } from 'hellgato';
import { createMiraboxN4ProAdapter, createN4ProMappingProfile } from 'hellgato/adapters/mirabox-n4-pro';
import { assertControlCommand } from '@hellgato/core/contracts';
import { CandidateError, validateCandidateConfig, loadCandidateManifest, loadCandidateSettings,
  saveCandidateSettings, createCandidateDiagnostics, diagnosticCode, validateSettings } from './candidate-config.mjs';
import { createCandidateReadiness } from './candidate-readiness.mjs';
import { createCandidateObservations } from './candidate-observability.mjs';

const SETTINGS = ['brightness', 'knobBrightness', 'followDisplayBrightness', 'sleepDuration'];
const RETRYABLE = new Set(['ADAPTER_DISCONNECTED', 'DEVICE_NOT_FOUND', 'HOST_NOT_READY', 'ERR_WORKER_EXIT',
  'ERR_WORKER_EOF', 'ERR_WORKER_PIPE', 'ERR_WORKER_START', 'ERR_DEVICE_NOT_FOUND', 'ERR_DEVICE_OPEN',
  'ERR_DEVICE_CLOSED', 'CANDIDATE_READINESS_TIMEOUT', 'CANDIDATE_READINESS_START', 'CANDIDATE_READINESS_EXIT']);
const clone = value => structuredClone(value);

export function createCandidateProfile(identity, { selfTest = false } = {}) {
  const profile = createN4ProMappingProfile({ virtualSerial: identity.dockSerial,
    childSerial: identity.childSerial, macAddress: identity.macAddress });
  if (selfTest) {
    profile.controls = { keys: [0, 1], dials: [0] };
    delete profile.touch;
    profile.displays = ['key:0', 'key:1'].map(id => ({ source: id, target: id }));
    profile.requirements.capabilities = ['keys', 'dials'];
  }
  return profile;
}

function box(frame) { return frame.region ?? { x: 0, y: 0, width: frame.width, height: frame.height }; }
function covers(a, b) { return a.x <= b.x && a.y <= b.y && a.x + a.width >= b.x + b.width && a.y + a.height >= b.y + b.height; }

/** Bounded ordered desired-display journal. A fresh open always starts cleared,
 * so partial updates have a deterministic baseline. Never contains input. */
export function createDisplayJournal({ maxEntries = 64, maxBytes = 8 * 1024 * 1024 } = {}) {
  let entries = [];
  let blackBackground = true; // The product clears every fresh open first.
  return {
    prepare(payload, { normalStateEffect } = {}) {
      const value = clone(payload);
      for (const key of ['deviceId', 'sessionId', 'generation', 'sequence']) delete value[key];
      if (value.type !== 'frame' && !['clear', 'fill'].includes(value.control)) return null;
      const global = value.surfaceId === 'lcd' || value.surfaceId === undefined;
      const black = value.control === 'clear' || value.control === 'fill' && value.color.every(byte => byte === 0);
      if (normalStateEffect === 'background-only' && (!global || !black)) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
      let next = normalStateEffect === 'background-only' ? [...entries] : global ? [] : entries.filter(previous => {
        if (previous.surfaceId !== value.surfaceId) return true;
        if (value.type !== 'frame' || !value.region) return false;
        return previous.type !== 'frame' || !covers(box(value), box(previous));
      });
      let nextBlackBackground = global ? black : blackBackground;
      if (normalStateEffect === 'background-only') {
        // The native wake-clear branch preserves key/strip caches, including a
        // strip originally cropped from an LCD frame. Reproduce that branch via
        // output-only sleep/clear after restoring the earlier visual history.
        // Repeated black-background clears commute with later key/strip writes.
        if (!blackBackground) next.push({ type: 'control', control: 'sleep' }, value);
        nextBlackBackground = true;
      } else next.push(value);
      if (next.length > maxEntries || next.reduce((total, entry) => total + Buffer.byteLength(JSON.stringify(entry)), 0) > maxBytes) {
        throw new CandidateError('CANDIDATE_RESTORE_LIMIT');
      }
      return () => { entries = next; blackBackground = nextBlackBackground; };
    },
    get entries() { return clone(entries); },
    get length() { return entries.length; },
  };
}

function restoringAdapter(raw, { controls, persist, count, record, journal, observations }) {
  let context, opened = false, sequence = 0, idleControl = null, inputEpoch = 0, lastInputSequence = -1;
  let inputReceivedAt = null;
  const stamp = payload => ({ ...payload, deviceId: context.deviceId, sessionId: context.sessionId,
    generation: context.generation, sequence: ++sequence });
  const write = async (payload, restoring = false) => {
    if (!opened) throw new CandidateError('ERR_DEVICE_CLOSED');
    const candidate = stamp(payload);
    const inputAtWrite = inputEpoch;
    const commit = !restoring && candidate.type === 'frame' ? journal.prepare(candidate) : null;
    let result;
    const submitStarted = performance.now();
    try { result = await raw[candidate.type === 'frame' ? 'setOutput' : 'setControl'](candidate); }
    finally { observations.submission(candidate.type, performance.now() - submitStarted); }
    if (!['applied', 'submitted'].includes(result?.status)) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
    commit?.();
    if (!restoring && candidate.type === 'control' && ['clear', 'fill'].includes(candidate.control)) {
      if (!['background-only', 'replace-all', 'surface'].includes(result.normalStateEffect)) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
      journal.prepare(candidate, result)?.();
    }
    count(restoring ? 'restoredWrites' : candidate.type === 'frame' ? 'frameWrites' : 'controlWrites');
    count(result.verification === 'simulation' ? 'simulationWrites' : 'unconfirmedWrites');
    if (!restoring && candidate.type === 'control') {
      if (![result.idle, result.sleeping, result.screensaverActive, result.logoActive].every(value => typeof value === 'boolean')) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
      if (inputEpoch === inputAtWrite) idleControl = result.logoActive ? 'showLogo' : result.idle ? result.screensaverActive ? 'screensaver' : 'sleep' : null;
      if (SETTINGS.includes(candidate.control)) {
        // Worker idle/fade policy can accept a brightness request without changing
        // the desired normal level. Read back intent, never claim hardware readback.
        const observed = await raw.getState();
        if (observed.deviceId !== context.deviceId || observed.generation !== context.generation ||
          !SETTINGS.every(key => Object.hasOwn(observed, key))) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
        Object.assign(controls, validateSettings(Object.fromEntries(SETTINGS.map(key => [key, observed[key]]))));
        if (inputEpoch === inputAtWrite) {
          if (observed.idle === true) idleControl = observed.sleeping === true ? 'sleep' : 'screensaver';
          else if (observed.idle === false) idleControl = observed.logoActive ? 'showLogo' : null;
        }
        persist(controls);
      }
    }
    if (!restoring && candidate.type === 'frame' && idleControl === 'showLogo') idleControl = null;
    return result;
  };
  return {
    descriptor: raw.descriptor,
    preflight: options => raw.preflight(options),
    start: async options => { const result = await raw.start(options); count('workerStarts'); return result; },
    discover: options => raw.discover(options),
    async open(deviceId, options) {
      context = { deviceId, ...options }; sequence = 0; lastInputSequence = -1;
      await raw.open(deviceId, options); opened = true;
      // Rebuild only acknowledged desired state. Each write receives fresh IDs
      // and a new monotonically increasing worker sequence after open.
      await write({ type: 'control', control: 'clear' }, true);
      for (const key of SETTINGS) if (Object.hasOwn(controls, key)) await write({ type: 'control', control: key, value: controls[key] }, true);
      for (const entry of journal.entries) await write(entry, true);
      if (idleControl) await write({ type: 'control', control: idleControl }, true);
      record('restored', { generation: context.generation, count: journal.length });
    },
    async close() { opened = false; await raw.close(); },
    async stop() { opened = false; await raw.stop(); },
    setOutput: payload => write(payload), setControl: payload => write(payload),
    onInput: listener => raw.onInput(event => {
      inputReceivedAt = performance.now(); count('inputObserved');
      if (event?.deviceId === context?.deviceId && event?.generation === context?.generation &&
        (event.sessionId === undefined || event.sessionId === context.sessionId) && Number.isSafeInteger(event.sequence) && event.sequence > lastInputSequence) {
        lastInputSequence = event.sequence; inputEpoch++; idleControl = null;
      }
      try { listener(event); } finally { inputReceivedAt = null; }
    }),
    inputForwarded() { if (inputReceivedAt !== null) observations.inputForward(performance.now() - inputReceivedAt); },
    onState: listener => raw.onState(listener),
    onDiagnostic: listener => raw.onDiagnostic?.(listener) ?? (() => {}),
    getState: options => raw.getState(options),
  };
}

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new CandidateError('ABORTED')); return; }
    const abort = () => { clearTimeout(timer); reject(new CandidateError('ABORTED')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, { once: true });
  });
}

/** Inert product composition. Test dependencies are never read from JSON config. */
export function createCandidateRunner(input, dependencies = {}) {
  const selfTest = dependencies.selfTest === true;
  const config = validateCandidateConfig(input, { selfTest });
  const emitter = new EventEmitter();
  const profile = createCandidateProfile(config.identity, { selfTest });
  const observations = createCandidateObservations();
  const controls = loadCandidateSettings(config);
  const writeRecord = dependencies.record ?? createCandidateDiagnostics(config.diagnosticsPath);
  let diagnosticsFailure;
  const record = (...args) => {
    if (diagnosticsFailure) return;
    try { writeRecord(...args); }
    catch {
      diagnosticsFailure = new CandidateError('CANDIDATE_DIAGNOSTICS_WRITE');
      // Filesystem failures cannot throw through an SDK event callback and strand
      // native resources. Schedule ordinary shutdown, then report a bounded code.
      queueMicrotask(() => { void runner.stop().catch(() => {}); });
    }
  };
  const counts = Object.fromEntries(['readinessChecks', 'workerStarts', 'reconnectAttempts', 'frameWrites', 'controlWrites',
    'restoredWrites', 'unconfirmedWrites', 'simulationWrites', 'inputObserved', 'inputForwarded', 'inputNotDelivered', 'diagnostics', 'cleanStops'].map(key => [key, 0]));
  const count = key => { counts[key] = Math.min(Number.MAX_SAFE_INTEGER, counts[key] + 1); };
  const manifest = dependencies.adapter ? null : loadCandidateManifest(config.runtime);
  const adapterOptions = { command: config.runtime.command, args: [...config.runtime.args, '--backend', selfTest ? 'fake' : 'mirabox'],
    cwd: dirname(config.statePath), manifest, requestTimeoutMs: 5000, stopTimeoutMs: 1000 };
  const raw = dependencies.adapter ?? (selfTest ? createSidecarAdapter({ ...adapterOptions,
    descriptor: { id: 'hellgato.fake-sidecar', version: '0.1.0', modelIds: ['buttons-dial'], platforms: ['any'], architectures: ['any'], apiVersion: 1, protocol: { major: 1, minor: 0 } } }) : createMiraboxN4ProAdapter(adapterOptions));
  const adapter = restoringAdapter(raw, { controls, persist: state => saveCandidateSettings(config, state), count, record,
    journal: createDisplayJournal(), observations });
  let activeBridge, hostApproved = false, hostEpoch = 0;
  const pendingControls = new Map();
  const rejectControls = error => {
    for (const pending of pendingControls.values()) { clearTimeout(pending.timer); pending.reject(error); }
    pendingControls.clear();
  };
  const inspect = dependencies.readiness ?? createCandidateReadiness(config.readiness);
  const readiness = async context => {
    const epoch = hostEpoch;
    count('readinessChecks'); record('readiness', { generation: context.generation });
    const report = await inspect(context);
    if (epoch === hostEpoch) hostApproved = report?.ready === true;
    return report;
  };
  const bridgeFactory = async options => {
    const bridge = await (dependencies.bridgeFactory ?? createDeviceBridge)({ ...options, sleepDuration: controls.sleepDuration ?? 0 });
    const facade = new EventEmitter();
    let sequence = 0;
    const envelope = { deviceId: options.device.id, sessionId: options.sessionId, generation: options.generation };
    const output = payload => facade.emit('output', { ...payload, ...envelope, sequence: ++sequence });
    const relayOutput = payload => { if (config.screensaverMode !== 'native' || payload.surfaceId !== 'lcd') output(payload); };
    const saver = () => { if (config.screensaverMode === 'native') output({ type: 'control', control: 'screensaver' }); };
    const relay = event => detail => {
      if (detail?.role !== 'primary') {
        hostEpoch++; hostApproved = false;
        if (event === 'clientDisconnected') rejectControls(new CandidateError('CANDIDATE_HOST_DISCONNECTED'));
      }
      facade.emit(event, detail);
    };
    const connected = relay('clientConnected'), disconnected = relay('clientDisconnected');
    const diagnostic = detail => { count('diagnostics'); record('diagnostic', { code: diagnosticCode(detail), generation: options.generation }); };
    bridge.on('output', relayOutput); bridge.on('lcdFrameStarted', saver); bridge.on('diagnostic', diagnostic);
    bridge.on('clientConnected', connected); bridge.on('clientDisconnected', disconnected);
    Object.assign(facade, { start: () => bridge.start(), sendInput: event => bridge.sendInput(event),
      async stop() {
        rejectControls(new CandidateError('CANDIDATE_SESSION_ENDED'));
        bridge.off('output', relayOutput); bridge.off('lcdFrameStarted', saver); bridge.off('diagnostic', diagnostic);
        bridge.off('clientConnected', connected); bridge.off('clientDisconnected', disconnected);
        if (activeBridge === facade) activeBridge = null;
        await bridge.stop();
      },
      control(payload, pending) {
        const key = `${options.generation}:${sequence + 1}`;
        pendingControls.set(key, pending);
        observations.pendingControls(pendingControls.size);
        output({ ...payload, type: 'control' });
      },
    });
    Object.defineProperty(facade, 'ports', { get: () => bridge.ports });
    activeBridge = facade; return facade;
  };
  const session = createSession({ adapter, deviceId: config.deviceId, profile, readiness,
    bridgeFactory, maxOutputQueue: 32,
    port: config.port, childPort: config.childPort, sleepDuration: controls.sleepDuration ?? 0 });
  const abort = new AbortController();
  let state = 'idle', stopping = false, launching = false, recovery = null, startPromise, stopPromise;
  let lastCode = null, completion;
  const completed = new Promise(resolve => { completion = resolve; });
  const snapshot = () => ({ schemaVersion: 1, engine: 'ipc-candidate', state, generation: session.generation,
    code: lastCode, counters: { ...counts, inputSuppressed: Math.max(0, counts.inputObserved - counts.inputForwarded - counts.inputNotDelivered) },
    observations: observations.snapshot(), physicalHardwareVerified: false, simulation: selfTest });
  const setState = next => { state = next; record(next, { generation: session.generation }); emitter.emit('state', snapshot()); };
  const terminal = error => { lastCode = diagnosticCode(error); record('failure', { code: lastCode }); setState('failed'); completion(snapshot()); };
  const reconnect = reason => {
    if (recovery) return recovery;
    recovery = (async () => {
      let error = reason;
      while (!stopping && counts.reconnectAttempts < config.reconnect.maxAttempts && RETRYABLE.has(diagnosticCode(error))) {
        count('reconnectAttempts'); setState('reconnecting'); record('reconnect', { attempt: counts.reconnectAttempts });
        await delay(config.reconnect.delayMs, abort.signal);
        if (stopping) return;
        try { await session.reconnect(); lastCode = null; setState('ready'); return; }
        catch (next) { error = next; }
      }
      if (!stopping) {
        // A failed final cleanup must still settle the supervisor's completion
        // promise. The Windows job remains responsible for any unreaped process.
        try { await session.stop(); }
        catch { error = new CandidateError('CLEANUP_FAILED'); }
        finally { terminal(error); }
        throw error;
      }
    })().finally(() => { recovery = null; });
    return recovery;
  };
  session.on('state', status => {
    if (['stopping', 'stopped', 'failed'].includes(status.state)) { hostApproved = false; rejectControls(new CandidateError('CANDIDATE_SESSION_ENDED')); }
    if (status.state === 'failed' && !stopping && !launching && !recovery) void reconnect(status.lastError).catch(() => {});
  });
  session.on('diagnostic', detail => { count('diagnostics'); record('diagnostic', { code: diagnosticCode(detail), generation: session.generation }); });
  session.on('input', event => { count(event.delivered ? 'inputForwarded' : 'inputNotDelivered'); if (event.delivered) adapter.inputForwarded(); });
  session.on('output', event => {
    const key = `${event.generation}:${event.sequence}`;
    const pending = pendingControls.get(key);
    if (pending) {
      pendingControls.delete(key); clearTimeout(pending.timer);
      pending.resolve({ status: event.result.status, verification: event.result.verification === 'simulation' ? 'simulation' : 'unconfirmed' });
    }
  });
  const runner = Object.assign(emitter, {
    start() {
      if (startPromise) return startPromise;
      if (stopping || state !== 'idle') return Promise.reject(new CandidateError('CANDIDATE_START_STATE'));
      launching = true; setState('starting');
      startPromise = (async () => {
        try { await session.start(); if (!stopping) setState('ready'); }
        catch (error) { if (!stopping) await reconnect(error); }
        finally { launching = false; }
        return runner;
      })();
      return startPromise;
    },
    reconnect() {
      if (stopping || state !== 'ready') return Promise.reject(new CandidateError('CANDIDATE_RECONNECT_STATE'));
      return reconnect(new CandidateError('ADAPTER_DISCONNECTED'));
    },
    async control(value) {
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !['control', 'value'].includes(key)) ||
        ![...SETTINGS, 'sleep', 'wake', 'screensaver', 'showLogo'].includes(value.control)) throw new CandidateError('CANDIDATE_CONTROL_INVALID');
      try { assertControlCommand({ ...value, type: 'control' }); } catch { throw new CandidateError('CANDIDATE_CONTROL_INVALID'); }
      if (stopping || state !== 'ready' || !activeBridge || !hostApproved) throw new CandidateError('CANDIDATE_CONTROL_NOT_READY');
      if (pendingControls.size >= 8) throw new CandidateError('CANDIDATE_CONTROL_LIMIT');
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { reject(new CandidateError('CANDIDATE_CONTROL_TIMEOUT')); void runner.stop().catch(() => {}); }, 7000);
        activeBridge.control(clone(value), { resolve, reject, timer });
      });
    },
    stop() {
      if (stopPromise) return stopPromise;
      stopping = true; abort.abort(); setState('stopping');
      stopPromise = (async () => {
        try {
          await session.stop();
          if (startPromise) await startPromise.catch(() => {});
          if (recovery) await recovery.catch(() => {});
          if (diagnosticsFailure) throw diagnosticsFailure;
          count('cleanStops'); setState('stopped'); completion(snapshot());
        } catch (error) { terminal(error); throw error; }
      })();
      return stopPromise;
    },
    getStatus: snapshot,
    // Exposed only to the local product self-test, never the console protocol.
    getWorkerState: () => adapter.getState(),
    completed,
  });
  return runner;
}

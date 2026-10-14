import { EventEmitter } from 'node:events';
import { dirname } from 'node:path';
import { performance } from 'node:perf_hooks';
import { createSession, createSidecarAdapter, createDeviceBridge } from 'hellgato';
import { createMiraboxN4ProAdapter, createN4ProMappingProfile } from 'hellgato/adapters/mirabox-n4-pro';
import { assertControlCommand, assertDeviceDescriptor } from '@hellgato/core/contracts';
import { CandidateError, validateCandidateConfig, loadCandidateManifest, loadCandidateSettings,
  saveCandidateSettings, createCandidateDiagnostics, diagnosticCode, validateSettings } from './candidate-config.mjs';
import { createCandidateReadiness } from './candidate-readiness.mjs';
import { createCandidateObservations } from './candidate-observability.mjs';
import { createCandidateOutputQueue } from './candidate-output-queue.mjs';

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
export function createDisplayJournal({ maxEntries = 64, maxBytes = 8 * 1024 * 1024, lcdMode = 'normal' } = {}) {
  if (!['normal', 'stream-screensaver'].includes(lcdMode)) throw new CandidateError('CANDIDATE_CONFIG_INVALID');
  let entries = [];
  let blackBackground = true; // The product clears every fresh open first.
  return {
    prepare(payload, { normalStateEffect } = {}) {
      if (lcdMode === 'stream-screensaver' && payload.type === 'frame' && payload.surfaceId === 'lcd') return null;
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

function restoringAdapter(raw, { controls, persist, persistControl, releaseReason, count, record, journal, observations }) {
  let context, opened = false, sequence = 0, idleControl = null, inputEpoch = 0, lastInputSequence = -1;
  let previewKnob = false;
  let inputReceivedAt = null;
  const stamp = payload => ({ ...payload, deviceId: context.deviceId, sessionId: context.sessionId,
    generation: context.generation, sequence: ++sequence });
  const write = async (payload, restoring = false) => {
    if (!opened) throw new CandidateError('ERR_DEVICE_CLOSED');
    const candidate = stamp(payload);
    const saveControl = restoring || persistControl(payload);
    const inputAtWrite = inputEpoch;
    const commit = !restoring && candidate.type === 'frame' ? journal.prepare(candidate) : null;
    let result;
    const submitStarted = performance.now();
    try { result = await raw[candidate.type === 'frame' ? 'setOutput' : 'setControl'](candidate); }
    catch (error) {
      if (candidate.type === 'frame') observations.displayFrame('failed', candidate.surfaceId);
      observations.workerDisplay(error.details?.displayObservations);
      throw error;
    }
    finally { observations.submission(candidate.type, performance.now() - submitStarted); }
    if (!['applied', 'submitted'].includes(result?.status)) {
      if (candidate.type === 'frame') observations.displayFrame('failed', candidate.surfaceId);
      throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
    }
    observations.displaySubmission(candidate, restoring, result);
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
        observations.workerDisplay(observed.displayObservations);
        if (observed.deviceId !== context.deviceId || observed.generation !== context.generation ||
          !SETTINGS.every(key => Object.hasOwn(observed, key))) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
        const observedSettings = validateSettings(Object.fromEntries(SETTINGS.map(key => [key, observed[key]])));
        if (saveControl) {
          if (previewKnob && !['knobBrightness', 'followDisplayBrightness'].includes(candidate.control)) {
            observedSettings.knobBrightness = controls.knobBrightness;
            observedSettings.followDisplayBrightness = controls.followDisplayBrightness;
          } else previewKnob = false;
          Object.assign(controls, observedSettings);
        } else if (candidate.control === 'knobBrightness') previewKnob = true;
        if (inputEpoch === inputAtWrite) {
          if (observed.idle === true) idleControl = observed.sleeping === true ? 'sleep' : 'screensaver';
          else if (observed.idle === false) idleControl = observed.logoActive ? 'showLogo' : null;
        }
        if (saveControl) persist(controls);
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
      previewKnob = false;
      observations.resetWorkerDisplay();
      await raw.open(deviceId, options); opened = true;
      // Rebuild only acknowledged desired state. Each write receives fresh IDs
      // and a new monotonically increasing worker sequence after open.
      await write({ type: 'control', control: 'clear' }, true);
      for (const key of SETTINGS) if (Object.hasOwn(controls, key)) await write({ type: 'control', control: key, value: controls[key] }, true);
      for (const entry of journal.entries) await write(entry, true);
      if (idleControl) await write({ type: 'control', control: idleControl }, true);
      record('restored', { generation: context.generation, count: journal.length });
    },
    async close() { opened = false; const reason = releaseReason(); await raw.close(reason ? { releaseReason: reason } : undefined); },
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
    async getState(options) {
      const result = await raw.getState(options);
      observations.workerDisplay(result.displayObservations);
      return result;
    },
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

function runtimeAdapter(config, { selfTest = false, adapter } = {}) {
  if (adapter) return adapter;
  const manifest = loadCandidateManifest(config.runtime);
  const options = { command: config.runtime.command, args: [...config.runtime.args, '--backend', selfTest ? 'fake' : 'mirabox',
    '--lcd-mode', config.screensaverMode === 'stream' ? 'stream-screensaver' : 'normal'],
    cwd: dirname(config.statePath), manifest, requestTimeoutMs: 5000, stopTimeoutMs: 1000 };
  return selfTest ? createSidecarAdapter({ ...options,
    descriptor: { id: 'hellgato.fake-sidecar', version: '0.1.0', modelIds: ['buttons-dial'], platforms: ['any'], architectures: ['any'], apiVersion: 1, protocol: { major: 1, minor: 0 } } }) : createMiraboxN4ProAdapter(options);
}

export async function discoverProductDevices(input, dependencies = {}) {
  const config = validateCandidateConfig(input, { selfTest: dependencies.selfTest === true, discovery: true });
  if (!config.productMode) throw new CandidateError('CANDIDATE_CONFIG_INVALID');
  const adapter = runtimeAdapter(config, dependencies);
  try {
    const options = dependencies.signal ? { signal: dependencies.signal } : {};
    await adapter.preflight?.(options);
    await adapter.start(options);
    const devices = await adapter.discover(options);
    if (!Array.isArray(devices) || devices.length > 64) throw new CandidateError('INVALID_DISCOVERY');
    for (const device of devices) assertDeviceDescriptor(device);
    return clone(devices.filter(device => device.adapterId === adapter.descriptor.id && adapter.descriptor.modelIds.includes(device.modelId)));
  } finally { await adapter.stop(); }
}

function productDevice(device) {
  const features = Object.fromEntries([...SETTINGS, 'screensaver', 'showLogo', 'sleep', 'wake'].map(name => {
    const status = device?.capabilities.features?.[name]?.status;
    return [name, { status: ['native', 'emulated', 'unavailable'].includes(status) ? status : 'unavailable' }];
  }));
  return { ...(device ? { deviceId: device.id, firmware: device.firmware ?? '' } : {}), capabilities: { features } };
}

function productWorkerState(value) {
  const settings = validateSettings(Object.fromEntries(SETTINGS.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]])));
  const state = { ...settings };
  for (const key of ['connected', 'idle', 'sleeping', 'screensaverActive', 'screensaverReady', 'logoActive']) {
    if (typeof value[key] === 'boolean') state[key] = value[key];
  }
  for (const key of ['screenBrightness', 'knobBrightnessLevel']) {
    if (value[key] === null || Number.isInteger(value[key]) && value[key] >= 0 && value[key] <= 100) state[key] = value[key];
  }
  if (['frame', 'panels'].includes(value.stripMode)) state.stripMode = value.stripMode;
  return state;
}

/** Inert product composition. Test dependencies are never read from JSON config. */
export function createCandidateRunner(input, dependencies = {}) {
  const selfTest = dependencies.selfTest === true;
  const config = validateCandidateConfig(input, { selfTest });
  const emitter = new EventEmitter();
  const product = (event, detail = {}) => {
    if (config.productMode) emitter.emit('product', { schemaVersion: 1, event, generation: session.generation, ...detail });
  };
  const profile = createCandidateProfile(config.identity, { selfTest });
  const observations = createCandidateObservations();
  const controls = { ...(config.productMode ? { brightness: 100, knobBrightness: 100, followDisplayBrightness: true, sleepDuration: 0 } : {}), ...loadCandidateSettings(config) };
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
  const raw = runtimeAdapter(config, dependencies);
  let hostSuspended = false, recoveryAbort;
  const adapter = restoringAdapter(raw, { controls, persist: settings => {
    saveCandidateSettings(config, settings); product('settingsChanged', { settings: { ...settings } });
  }, persistControl: payload => pendingOutputs.get(`${payload.generation}:${payload.sequence}`)?.persist !== false,
    releaseReason: () => hostSuspended ? 'host-exit' : undefined, count, record,
    journal: createDisplayJournal({ lcdMode: config.screensaverMode === 'stream' ? 'stream-screensaver' : 'normal' }), observations });
  let activeBridge, retainedNetwork, hostApproved = false, hostEpoch = 0, outputFailure;
  let pendingControlCount = 0;
  const pendingOutputs = new Map();
  const rejectOutputs = error => {
    for (const pending of pendingOutputs.values()) { clearTimeout(pending.timer); pending.reject(error); }
    pendingOutputs.clear();
  };
  const failOutput = error => {
    if (stopping || outputFailure) return;
    outputFailure = error; hostApproved = false;
    count('diagnostics'); record('diagnostic', { code: diagnosticCode(error), generation: session.generation });
    void session.stop().then(async () => { await closeRetainedNetwork(); terminal(error); })
      .catch(async () => { await closeRetainedNetwork().catch(() => {}); terminal(new CandidateError('CLEANUP_FAILED')); });
  };
  const closeRetainedNetwork = async () => {
    if (!retainedNetwork) return;
    const network = retainedNetwork; await network.close();
    if (retainedNetwork === network) retainedNetwork = null;
  };
  const inspect = dependencies.readiness ?? createCandidateReadiness(config.readiness);
  const readiness = async context => {
    const epoch = hostEpoch;
    count('readinessChecks'); record('readiness', { generation: context.generation });
    const report = await inspect(context);
    if (epoch === hostEpoch && context.generation === session.generation) {
      hostApproved = report?.ready === true;
    }
    return report;
  };
  const bridgeFactory = async options => {
    let network = config.productMode ? retainedNetwork : null;
    if (!network) {
      const bridge = await (dependencies.bridgeFactory ?? createDeviceBridge)({ ...options, sleepDuration: controls.sleepDuration ?? 0 });
      // Stream Deck discovers saved docks at launch; USB recovery must retain listeners and sockets.
      network = { bridge, options, current: null, inputSequence: 0, started: false };
      network.queue = createCandidateOutputQueue({ submit: payload => network.current.submit(payload), observeFrame: observations.displayFrame });
      const output = payload => {
        network.queue.enqueue(payload).catch(error => {
          if (!stopping && !['CANDIDATE_HOST_DISCONNECTED', 'CANDIDATE_SESSION_ENDED'].includes(error.code)) failOutput(error);
        });
      };
      const relayOutput = payload => { if (config.screensaverMode !== 'native' || payload.surfaceId !== 'lcd') output(payload); };
      const saver = () => { if (config.screensaverMode === 'native') output({ type: 'control', control: 'screensaver' }); };
      const relay = event => detail => {
        const role = detail?.role === 'primary' ? 'primary' : 'child';
        product(`${role}.${event}`);
        const primaryLost = config.productMode && role === 'primary' && event === 'clientDisconnected';
        const primaryReturned = config.productMode && role === 'primary' && event === 'clientConnected' && bridge.childConnected === true;
        if (role === 'child' || primaryLost || primaryReturned) {
          hostEpoch++; hostApproved = false;
          network.queue.pause(); network.queue.reset(new CandidateError('CANDIDATE_HOST_DISCONNECTED'));
          network.current?.facade.emit(event, role === 'primary' ? { role: 'child' } : detail);
        } else network.current?.facade.emit(event, detail);
      };
      const connected = relay('clientConnected'), disconnected = relay('clientDisconnected');
      const diagnostic = detail => { count('diagnostics'); record('diagnostic', { code: diagnosticCode(detail), generation: session.generation }); };
      bridge.on('output', relayOutput); bridge.on('lcdFrameStarted', saver); bridge.on('diagnostic', diagnostic);
      bridge.on('clientConnected', connected); bridge.on('clientDisconnected', disconnected);
      network.close = async () => {
        network.queue.close(new CandidateError('CANDIDATE_SESSION_ENDED'));
        bridge.off('output', relayOutput); bridge.off('lcdFrameStarted', saver); bridge.off('diagnostic', diagnostic);
        bridge.off('clientConnected', connected); bridge.off('clientDisconnected', disconnected);
        bridge.releaseInputs?.(); await bridge.stop(); network.started = false;
      };
      if (config.productMode) retainedNetwork = network;
    } else if (network.options.device.id !== options.device.id || network.options.sessionId !== options.sessionId) {
      throw new CandidateError('CANDIDATE_CONFIG_INVALID');
    }
    const bridge = network.bridge;
    const facade = new EventEmitter();
    let sequence = 0;
    const envelope = { deviceId: options.device.id, sessionId: options.sessionId, generation: options.generation };
    let closed = false;
    network.current = { facade, submit: payload => new Promise((resolve, reject) => {
      const { persist, ...value } = payload;
      const stamped = { ...value, ...envelope, sequence: ++sequence };
      const key = `${options.generation}:${stamped.sequence}`;
      const timer = setTimeout(() => {
        pendingOutputs.delete(key);
        const error = new CandidateError('CANDIDATE_OUTPUT_TIMEOUT');
        reject(error); failOutput(error);
      }, 7000);
      pendingOutputs.set(key, { resolve, reject, timer, persist });
      try { facade.emit('output', stamped); }
      catch (error) { pendingOutputs.delete(key); clearTimeout(timer); reject(error); }
    }) };
    Object.assign(facade, { generation: options.generation,
      async start() {
        if (!network.started) { await bridge.start(); network.started = true; }
        product('bridgeReady'); product('started', { ports: { primary: bridge.ports?.primary ?? null, child: bridge.ports?.child ?? null } });
      },
      sendInput: event => bridge.sendInput(config.productMode ? { ...event, generation: network.options.generation,
        sessionId: network.options.sessionId, sequence: ++network.inputSequence } : event),
      resumeOutput: () => { if (!closed && network.current?.facade === facade) network.queue.resume(); },
      async stop() {
        if (closed) return;
        if (activeBridge === facade) { hostEpoch++; hostApproved = false; }
        closed = true; network.queue.reset(new CandidateError('CANDIDATE_SESSION_ENDED'),
          { retainPending: config.productMode && !stopping && !outputFailure });
        rejectOutputs(new CandidateError('CANDIDATE_SESSION_ENDED'));
        bridge.releaseInputs?.();
        if (network.current?.facade === facade) network.current = null;
        if (activeBridge === facade) activeBridge = null;
        if (!config.productMode || stopping || outputFailure) {
          await network.close();
          if (retainedNetwork === network) retainedNetwork = null;
        }
      },
      control: payload => network.queue.enqueue({ ...payload, type: 'control' }),
    });
    Object.defineProperty(facade, 'ports', { get: () => bridge.ports });
    activeBridge = facade; return facade;
  };
  const session = createSession({ adapter, deviceId: config.deviceId, profile, readiness,
    bridgeFactory, maxOutputQueue: 32,
    port: config.port, childPort: config.childPort, sleepDuration: controls.sleepDuration ?? 0 });
  const abort = new AbortController();
  let state = 'idle', stopping = false, launching = false, recovery = null, startPromise, stopPromise;
  let hostTransition = Promise.resolve(), hostRequest = 0;
  let readyGeneration = 0, disconnectedGeneration = 0;
  let lastCode = null, completion;
  const completed = new Promise(resolve => { completion = resolve; });
  const snapshot = () => ({ schemaVersion: 1, engine: 'ipc-candidate', state, generation: session.generation,
    code: lastCode, counters: { ...counts, inputSuppressed: Math.max(0, counts.inputObserved - counts.inputForwarded - counts.inputNotDelivered) },
    observations: observations.snapshot(), physicalHardwareVerified: false, simulation: selfTest });
  const setState = next => {
    state = next; record(next, { generation: session.generation }); emitter.emit('state', snapshot());
    if (next === 'reconnecting') product('reconnecting', { attempt: counts.reconnectAttempts });
    else if (next === 'stopped') product('stopped');
  };
  const terminal = error => { lastCode = diagnosticCode(error); record('failure', { code: lastCode }); setState('failed'); completion(snapshot()); };
  const reconnect = reason => {
    if (hostSuspended) return Promise.resolve();
    if (recovery) return recovery;
    const controller = new AbortController(); recoveryAbort = controller;
    const cancel = () => controller.abort(); abort.signal.addEventListener('abort', cancel, { once: true });
    recovery = (async () => {
      let error = reason;
      while (!stopping && !hostSuspended && (config.productMode || counts.reconnectAttempts < config.reconnect.maxAttempts) &&
        (RETRYABLE.has(diagnosticCode(error)) || config.productMode && diagnosticCode(error) === 'ERR_DEVICE_OPERATION')) {
        count('reconnectAttempts'); setState('reconnecting'); record('reconnect', { attempt: counts.reconnectAttempts });
        try { await delay(config.reconnect.delayMs, controller.signal); }
        catch (failure) { if (stopping || hostSuspended) return; throw failure; }
        if (stopping || hostSuspended) return;
        try { await session.reconnect(); lastCode = null; setState('ready'); return; }
        catch (next) { error = next; }
      }
      if (!stopping && !hostSuspended) {
        // A failed final cleanup must still settle the supervisor's completion
        // promise. The Windows job remains responsible for any unreaped process.
        try { await session.stop(); await closeRetainedNetwork(); }
        catch { error = new CandidateError('CLEANUP_FAILED'); }
        finally { terminal(error); }
        throw error;
      }
    })().finally(() => { recovery = null; if (recoveryAbort === controller) recoveryAbort = null; abort.signal.removeEventListener('abort', cancel); });
    return recovery;
  };
  session.on('state', status => {
    if (['stopping', 'stopped', 'failed'].includes(status.state)) { hostApproved = false; rejectOutputs(new CandidateError('CANDIDATE_SESSION_ENDED')); }
    // Start ACK timers only after the SDK accepts readiness and completes device restoration.
    if (status.state === 'ready' && !status.lastError && hostApproved && !hostSuspended && !stopping && !outputFailure &&
      activeBridge?.generation === status.generation) {
      activeBridge.resumeOutput();
      if (readyGeneration !== status.generation) {
        const event = readyGeneration ? 'deviceReconnected' : 'deviceReady'; readyGeneration = status.generation;
        product(event, productDevice(session.device));
      }
    }
    if (status.state === 'failed' && !hostSuspended && !stopping && !launching && !recovery) void reconnect(status.lastError).catch(() => {});
  });
  session.on('adapterState', detail => {
    if (!stopping && ['disconnected', 'failed', 'crashed'].includes(detail.state ?? detail.status) && disconnectedGeneration !== session.generation) {
      retainedNetwork?.queue.pause();
      disconnectedGeneration = session.generation; product('deviceDisconnected', { code: 'ADAPTER_DISCONNECTED' });
    }
  });
  session.on('diagnostic', detail => { count('diagnostics'); record('diagnostic', { code: diagnosticCode(detail), generation: session.generation }); });
  session.on('input', event => { count(event.delivered ? 'inputForwarded' : 'inputNotDelivered'); if (event.delivered) adapter.inputForwarded(); });
  session.on('output', event => {
    const key = `${event.generation}:${event.sequence}`;
    const pending = pendingOutputs.get(key);
    if (pending) {
      pendingOutputs.delete(key); clearTimeout(pending.timer);
      pending.resolve({ status: event.result.status, verification: event.result.verification === 'simulation' ? 'simulation' : 'unconfirmed' });
    }
  });
  const runner = Object.assign(emitter, {
    start() {
      if (startPromise) return startPromise;
      if (stopping || state !== 'idle') return Promise.reject(new CandidateError('CANDIDATE_START_STATE'));
      launching = true; setState('starting');
      startPromise = (async () => {
        try { await session.start(); if (!stopping && !outputFailure) setState('ready'); }
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
    pauseDevice() {
      if (!config.productMode || stopping) return Promise.reject(new CandidateError('CANDIDATE_CONTROL_NOT_READY'));
      hostRequest++; hostSuspended = true; hostApproved = false; recoveryAbort?.abort(); retainedNetwork?.queue.pause();
      const shutdown = session.stop(); shutdown.catch(() => {});
      const pending = hostTransition.catch(() => {}).then(async () => {
        await shutdown;
        if (recovery) await recovery.catch(() => {});
        if (stopping) return { status: 'stopped' };
        disconnectedGeneration = session.generation;
        product('deviceDisconnected', { code: 'HOST_NOT_READY' }); setState('hostPaused'); product('hostPaused');
        return { status: 'paused' };
      });
      hostTransition = pending; return pending;
    },
    resumeDevice() {
      if (!config.productMode || stopping) return Promise.reject(new CandidateError('CANDIDATE_CONTROL_NOT_READY'));
      const request = ++hostRequest;
      const pending = hostTransition.catch(() => {}).then(async () => {
        if (stopping) return { status: 'stopped' };
        if (request !== hostRequest) return { status: state };
        if (!hostSuspended) return { status: state };
        hostSuspended = false; setState('starting');
        try { await session.start(); if (!stopping && !hostSuspended) setState('ready'); }
        catch (error) { if (!stopping && !hostSuspended) await reconnect(error); }
        return { status: state };
      });
      hostTransition = pending; return pending;
    },
    async control(value) {
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !['control', 'value', ...(config.productMode ? ['persist'] : [])].includes(key)) ||
        value.persist !== undefined && (typeof value.persist !== 'boolean' || value.control !== 'knobBrightness') ||
        ![...SETTINGS, 'sleep', 'wake', 'screensaver', 'showLogo'].includes(value.control)) throw new CandidateError('CANDIDATE_CONTROL_INVALID');
      const { persist, ...command } = value;
      try { assertControlCommand({ ...command, type: 'control' }); } catch { throw new CandidateError('CANDIDATE_CONTROL_INVALID'); }
      if (stopping || outputFailure || state !== 'ready' || !activeBridge || !hostApproved) throw new CandidateError('CANDIDATE_CONTROL_NOT_READY');
      if (pendingControlCount >= 8) throw new CandidateError('CANDIDATE_CONTROL_LIMIT');
      pendingControlCount++; observations.pendingControls(pendingControlCount);
      let timer;
      try {
        return await Promise.race([activeBridge.control(clone(value)), new Promise((resolve, reject) => {
          timer = setTimeout(() => { reject(new CandidateError('CANDIDATE_CONTROL_TIMEOUT')); void runner.stop().catch(() => {}); }, 7000);
        })]);
      } finally { clearTimeout(timer); pendingControlCount--; }
    },
    stop() {
      if (stopPromise) return stopPromise;
      stopping = true; abort.abort(); setState('stopping');
      stopPromise = (async () => {
        try {
          await session.stop();
          await closeRetainedNetwork();
          if (startPromise) await startPromise.catch(() => {});
          if (recovery) await recovery.catch(() => {});
          await hostTransition.catch(() => {});
          if (diagnosticsFailure) throw diagnosticsFailure;
          count('cleanStops'); setState('stopped'); completion(snapshot());
        } catch (error) { terminal(error); throw error; }
      })();
      return stopPromise;
    },
    getStatus: snapshot,
    async getProductWorkerState() {
      if (!config.productMode || stopping || state !== 'ready') throw new CandidateError('CANDIDATE_CONTROL_NOT_READY');
      const generation = session.generation;
      const observed = await adapter.getState();
      if (stopping || state !== 'ready' || generation !== session.generation || observed.generation !== generation || observed.deviceId !== config.deviceId ||
        !SETTINGS.every(key => Object.hasOwn(observed, key))) throw new CandidateError('CANDIDATE_CONTROL_NOT_READY');
      return { generation, state: productWorkerState(observed), capabilities: productDevice(session.device).capabilities };
    },
    // Exposed only to the local product self-test, never the console protocol.
    getWorkerState: () => adapter.getState(),
    completed,
  });
  return runner;
}

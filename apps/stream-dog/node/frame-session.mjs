import { mkdirSync } from 'node:fs';
import { dirname, isAbsolute } from 'node:path';
import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Worker } from 'node:worker_threads';
import { performance } from 'node:perf_hooks';
import { createDeviceBridge } from 'hellgato';
import { LatestFrameScheduler } from './full-frame-compositor.mjs';
import { FrameAdapterProcess } from './frame-adapter-process.mjs';
import { FullFrameScreensaver } from './full-frame-screensaver.mjs';
import { createTraceWriter } from './trace-log.mjs';

const executeFile = promisify(execFile);

async function inspectHost(config) {
  if (config.hostProbe?.command) {
    const { stdout } = await executeFile(config.hostProbe.command, [...(config.hostProbe.args ?? []),
      '--probe', '--exe', config.host.executable, '--sha256', config.host.executableSha256 ?? '0'.repeat(64)],
    { windowsHide: true, timeout: 5000, maxBuffer: 65536 });
    return JSON.parse(stdout.trim());
  }
  throw new Error('An independent hostProbe command is required for hardware mode');
}

export async function startFullFrameSession(config, options = {}) {
  let accept, refuse;
  const ready = new Promise((resolveReady, rejectReady) => { accept = resolveReady; refuse = rejectReady; });
  const completion = runFrameSession(config, { ...options, onReady: api => accept(api) });
  completion.catch(refuse);
  const api = await ready;
  return { ...api, completion };
}

class RenderWorker {
  #worker; #pending = new Map(); #next = 0; #bytes = 0; #closed = false;
  constructor(layout, includePreview = true) {
    this.#worker = new Worker(new URL('./full-frame-render-worker.mjs', import.meta.url), { workerData: { layout, includePreview } });
    this.#worker.on('message', value => {
      const pending = this.#pending.get(value.id);
      if (!pending) return;
      this.#pending.delete(value.id); this.#bytes -= pending.bytes; clearTimeout(pending.timer);
      value.error ? pending.reject(new Error(value.error.message)) : pending.resolve(value.result);
    });
    this.#worker.on('error', error => this.#fail(error));
    this.#worker.on('exit', code => { if (!this.#closed) this.#fail(new Error(`Renderer exited: ${code}`)); });
  }
  #fail(error) {
    this.#closed = true;
    for (const pending of this.#pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.#pending.clear(); this.#bytes = 0;
  }
  request(method, params = {}) {
    const bytes = params.data?.byteLength ?? 0;
    if (this.#closed) return Promise.reject(new Error('Renderer is closed'));
    if (this.#pending.size >= 128 || this.#bytes + bytes > 8 * 1024 * 1024) return Promise.reject(new Error('Renderer input limit exceeded'));
    const id = ++this.#next;
    return new Promise((resolveRequest, reject) => {
      const timer = setTimeout(() => this.#fail(new Error('Renderer request timed out')), 10000);
      this.#pending.set(id, { resolve: resolveRequest, reject, bytes, timer }); this.#bytes += bytes;
      this.#worker.postMessage({ id, method, params });
    });
  }
  async close() { this.#fail(new Error('Renderer stopped')); await this.#worker.terminate(); }
}

async function runFrameSession(config, { fake = false, sharedPreview: preview,
  onState = () => {}, onReady = () => {}, fakeDemo = fake, signal: abortSignal } = {}) {
  if (!isAbsolute(config.adapter?.command) || !config.target) {
    throw new TypeError('An explicit adapter command and Stream Deck target are required');
  }
  const sessionId = randomUUID(), generation = config.generation ?? 1;
  const state = { mode: fake ? 'fake' : 'hardware', modelId: null, deviceConnected: false, hostConnected: false,
    networkListening: false, stopping: false, generation, adapterPid: null, ignoredHostUpdates: 0, touchUpdates: 0, keyUpdates: 0, keySurfaces: {}, lcdUpdates: 0,
    droppedSaverFrames: 0, screensaverActive: false, screensaverEntries: 0, screensaverRestores: 0,
    framesSubmitted: 0, inputObserved: 0, inputForwarded: 0, keyInputObserved: 0, keyInputForwarded: 0,
    errors: [], warnings: [], fullFrameOnly: true, maxFps: config.maxFps ?? 60,
    brightness: config.brightness ?? 52, knobBrightness: config.knobBrightness ?? config.brightness ?? 52,
    followDisplayBrightness: config.knobBrightness == null, knobBrightnessSupported: false,
    everHostConnected: false, hostConnections: 0, touchRegions: {}, lastSubmitMs: null, timings: {} };
  const measure = (name, ms) => {
    const value = state.timings[name] ??= { count: 0, meanMs: 0, maxMs: 0, lastMs: 0 };
    value.count++; value.meanMs += (ms - value.meanMs) / value.count;
    value.maxMs = Math.max(value.maxMs, ms); value.lastMs = ms;
  };
  if (config.logPath) mkdirSync(dirname(config.logPath), { recursive: true });
  const writeLog = config.logPath ? createTraceWriter(config.logPath, 2 * 1024 * 1024) : null;
  const log = (event, detail = {}) => {
    const value = { at: new Date().toISOString(), event, ...detail };
    writeLog?.(JSON.stringify(value) + '\n');
    process.stdout.write(JSON.stringify(value) + '\n');
  };
  let renderer, scheduler, bridge, readinessTimer;
  let sourceOps = Promise.resolve(), inputOps = Promise.resolve(), sourceCount = 0, sourceBytes = 0;
  let sleeping = false, lastFrame, stoppedResolve, failure, inputSequence = 0, stopping, lastInputAt = null;
  const saver = new FullFrameScreensaver();
  let lcdEpoch = saver.epoch, lcdAssembling = false, pendingLcdFrames = 0;
  let normalBrightness = config.brightness ?? 52;
  let layout;
  const finished = new Promise(resolveFinished => { stoppedResolve = resolveFinished; });
  const adapter = new FrameAdapterProcess({ command: config.adapter.command,
    args: fake ? config.adapter.fakeArgs ?? ['--fake'] : config.adapter.args ?? [], cwd: config.adapter.cwd });
  const publish = () => {
    onState(structuredClone(state));
    preview?.update({ ...(lastFrame ? { frameJpeg: Buffer.from(lastFrame.frameJpeg) } : {}),
      status: { ...state, scheduler: scheduler?.getStatus() } });
  };
  const fail = error => {
    if (state.stopping) return;
    failure ??= error; state.errors.push(error.message);
    log('failure', { message: error.message, code: error.code, adapter: adapter.status() }); publish();
    void stop();
  };
  const abort = () => { void stop(); };
  const checkpoint = () => { if (state.stopping || abortSignal?.aborted) throw new Error('Device session cancelled'); };
  async function stop() {
    if (stopping) return stopping;
    state.stopping = true; clearInterval(readinessTimer);
    abortSignal?.removeEventListener('abort', abort);
    stopping = (async () => {
      bridge?.releaseInputs();
      await sourceOps.catch(() => {}); await inputOps.catch(() => {});
      const results = await Promise.allSettled([scheduler?.close(), bridge?.stop()]);
      const metrics = renderer ? await renderer.request('stats').catch(() => null) : null;
      await renderer?.close();
      try { await adapter.close(); } catch (error) { results.push({ status: 'rejected', reason: error }); }
      state.deviceConnected = false; state.hostConnected = false; state.networkListening = false; state.adapterPid = null;
      publish();
      const errors = results.filter(result => result.status === 'rejected').map(result => result.reason.message);
      const adapterStatus = adapter.status();
      log('stopped', { state, renderer: metrics, adapter: adapterStatus, cleanupErrors: errors });
      if (config.resultPath) {
        const { writeFileSync } = await import('node:fs');
        mkdirSync(dirname(config.resultPath), { recursive: true });
        writeFileSync(config.resultPath, JSON.stringify({ state, renderer: metrics, adapter: adapterStatus, cleanupErrors: errors }, null, 2) + '\n');
      }
      stoppedResolve();
    })().catch(error => { failure ??= error; stoppedResolve(); });
    return stopping;
  }

  async function restoreNormal(backgroundColor) {
    const result = await renderer.request('restore', { backgroundColor, receivedAt: performance.now() });
    if (result.changed) state.screensaverRestores++;
    await scheduler.barrier(async () => {
      if (sleeping) await adapter.request('control', { control: 'wake' });
      await adapter.request('control', { control: 'brightness', value: normalBrightness });
    });
    const wasSleeping = sleeping;
    sleeping = false;
    if (wasSleeping) scheduler.invalidate();
    if (result.visibleChanged) scheduler.request();
    scheduler.resume();
  }
  async function control(payload) {
    log('display-control', { control: payload.control, surface: payload.surfaceId, value: payload.value, color: payload.color });
    if (payload.control === 'knobBrightness') {
      if (!state.knobBrightnessSupported || !Number.isInteger(payload.value) || payload.value < 0 || payload.value > 100) {
        throw new Error('Knob brightness is unavailable or invalid');
      }
      await scheduler.barrier(() => adapter.request('control', { control: 'knobBrightness', value: payload.value }));
      state.knobBrightness = payload.value; state.followDisplayBrightness = false;
      return;
    }
    if (['clear', 'fill'].includes(payload.control)) {
      const color = payload.color ?? [0, 0, 0];
      if ((!payload.surfaceId || payload.surfaceId === 'lcd') && saver.consumeWakeClear(color)) {
        await restoreNormal(color); return;
      }
      if ((!payload.surfaceId || payload.surfaceId === 'lcd') && saver.active) saver.wake();
      const result = await renderer.request('clear', { surface: payload.surfaceId, color, receivedAt: performance.now() });
      if (result.visibleChanged) scheduler.request();
      return;
    }
    if ((payload.control === 'screensaver' || payload.control === 'brightness' && payload.value === 0) && saver.active) return;
    if (['sleep', 'screensaver'].includes(payload.control) || payload.control === 'brightness' && payload.value === 0) {
      sleeping = true; scheduler.pause();
      await scheduler.barrier(() => adapter.request('control', { control: 'sleep' }));
    } else if (payload.control === 'wake' || payload.control === 'brightness') {
      if (saver.active && payload.control === 'brightness' && payload.value < normalBrightness) return;
      if (saver.active || payload.control === 'wake' && sleeping) {
        saver.wake();
        await restoreNormal();
      }
      const mustWake = sleeping || payload.control === 'wake';
      await scheduler.barrier(async () => {
        if (mustWake) await adapter.request('control', { control: 'wake' });
        if (payload.control === 'brightness') await adapter.request('control', { control: 'brightness', value: payload.value });
      });
      if (payload.control === 'brightness') normalBrightness = payload.value;
      state.brightness = normalBrightness;
      if (state.followDisplayBrightness) state.knobBrightness = normalBrightness;
      sleeping = false;
      if (mustWake) scheduler.invalidate();
      scheduler.resume();
    }
  }
  function receive(payload) {
    if (payload.type === 'frame' && payload.surfaceId !== 'lcd' &&
        (payload.surfaceId === 'touch' ? !layout?.strip : !layout?.keys?.some(key => key.id === payload.surfaceId))) {
      state.ignoredHostUpdates++; return;
    }
    const receivedAt = performance.now();
    if (state.stopping) return;
    const isLcd = payload.type === 'frame' && payload.surfaceId === 'lcd';
    const frameEpoch = lcdEpoch;
    if (isLcd) { pendingLcdFrames++; lcdAssembling = false; }
    if (payload.type === 'frame' && payload.surfaceId === 'touch' && lastInputAt !== null) {
      measure('inputToNextTouchUpdate', receivedAt - lastInputAt); lastInputAt = null;
    }
    const bytes = payload.type === 'frame' ? Buffer.byteLength(payload.data ?? '', 'base64') : 0;
    if (++sourceCount > 128 || (sourceBytes += bytes) > 8 * 1024 * 1024) { fail(new Error('Source update limit exceeded')); return; }
    sourceOps = sourceOps.then(async () => {
      if (state.stopping) return;
      if (payload.type === 'frame') {
        if (isLcd) {
          state.lcdUpdates++;
          if (!saver.enter(frameEpoch)) { state.droppedSaverFrames++; return; }
          const result = await renderer.request('overlay', { data: Buffer.from(payload.data, 'base64'), region: payload.region, receivedAt });
          if (!saver.accepts(frameEpoch)) { state.droppedSaverFrames++; return; }
          if (sleeping) {
            await scheduler.barrier(async () => {
              await adapter.request('control', { control: 'wake' });
              await adapter.request('control', { control: 'brightness', value: normalBrightness });
            });
            sleeping = false; scheduler.invalidate(); scheduler.resume();
          }
          if (result.visibleChanged) scheduler.request();
          return;
        }
        if (payload.surfaceId.startsWith('key:')) {
          state.keyUpdates++;
          if (!Object.hasOwn(state.keySurfaces, payload.surfaceId) && Object.keys(state.keySurfaces).length >= 256) throw new Error('Key surface limit exceeded');
          state.keySurfaces[payload.surfaceId] = (state.keySurfaces[payload.surfaceId] ?? 0) + 1;
          const result = await renderer.request('key', { surface: payload.surfaceId, data: Buffer.from(payload.data, 'base64'), region: payload.region, receivedAt });
          if (result.visibleChanged) scheduler.request();
          return;
        }
        state.touchUpdates++;
        const rect = payload.region;
        const regionKey = rect ? `${rect.x},${rect.y},${rect.width},${rect.height}` : 'full';
        if (!state.touchRegions[regionKey] && Object.keys(state.touchRegions).length < 32) log('touch-region', { region: rect ?? null });
        if (Object.hasOwn(state.touchRegions, regionKey) || Object.keys(state.touchRegions).length < 32) {
          state.touchRegions[regionKey] = (state.touchRegions[regionKey] ?? 0) + 1;
        }
        const result = await renderer.request('touch', { data: Buffer.from(payload.data, 'base64'), region: payload.region, receivedAt });
        if (result.visibleChanged) scheduler.request();
      } else if (payload.type === 'control') await control(payload);
    }).catch(fail).finally(() => { sourceCount--; sourceBytes -= bytes; if (isLcd) pendingLcdFrames--; });
  }

  try {
    if (abortSignal?.aborted) throw new Error('Device session cancelled');
    abortSignal?.addEventListener('abort', abort, { once: true });
    const metadata = await adapter.start();
    checkpoint(); state.adapterPid = adapter.status().pid;
    state.modelId = metadata.modelId;
    if (!fake) {
      if (!config.hostProbe || !isAbsolute(config.host?.executable ?? '')) throw new TypeError('An independent current host inspection is required');
      const report = await inspectHost(config, adapter);
      if (report.running !== true && report.ready !== true) throw new Error('Stream Deck application is not running');
      if (report.tested === false) state.warnings.push({ code: 'HOST_BUILD_UNTESTED' });
      if (report.ready !== true) state.warnings.push({ code: 'HOST_LAYOUT_UNPREPARED' });
      config = { ...config, host: { ...config.host, pid: report.pid ?? config.host.pid, created: report.created, activeSha256: report.executableSha256 } };
      log('host-ready', { pid: config.host.pid });
    }
    const discovered = await adapter.request('discover');
    checkpoint();
    const devices = Array.isArray(discovered) ? discovered : discovered.devices;
    const device = config.deviceId ? devices.find(item => item.id === config.deviceId) : devices.length === 1 ? devices[0] : null;
    if (!device) throw new Error('Select exactly one discovered device');
    const opened = await adapter.request('open', { deviceId: device.id, brightness: config.brightness ?? 52, generation, sessionId });
    checkpoint();
    if (opened.firmware) device.firmware = opened.firmware;
    state.firmware = device.firmware;
    state.warnings.push(...(opened.warnings ?? []));
    state.knobBrightnessSupported = device.capabilities?.features?.knobBrightness?.status === 'native';
    if (state.knobBrightnessSupported && config.knobBrightness != null) {
      await adapter.request('control', { control: 'knobBrightness', value: config.knobBrightness });
    }
    layout = opened.layout ?? metadata.layout;
    state.deviceConnected = true;
    renderer = new RenderWorker(layout, Boolean(preview));
    await renderer.request('stats');
    checkpoint();
    checkpoint();
    state.display = { width: layout.width, height: layout.height };
    scheduler = new LatestFrameScheduler({ maxFps: state.maxFps,
      snapshot: () => renderer.request('render'),
      submit: async frame => {
        const started = performance.now();
        const submitted = await adapter.request('frame', { jpeg: Buffer.from(frame.wireJpeg).toString('base64'), width: frame.width, height: frame.height, generation });
        measure('adapterRoundTrip', performance.now() - started);
        measure('render', frame.renderMs);
        if (frame.sourceReceivedAt !== null) measure('imageUpdateToSubmitted', performance.now() - frame.sourceReceivedAt);
        if (frame.overlayActive && !state.screensaverActive) state.screensaverEntries++;
        if (frame.overlayActive !== state.screensaverActive) log('display-layer', { layer: frame.overlayActive ? 'screensaver' : 'normal' });
        state.screensaverActive = frame.overlayActive;
        state.lastSubmitMs = submitted.submitMs ?? null;
        state.framesSubmitted++; lastFrame = frame; publish();
      }, onError: fail });
    adapter.on('failure', fail);
    adapter.on('event', event => {
      if (event.event === 'state') log('adapter-state', { detail: event });
      if (event.event !== 'input' || !state.deviceConnected || state.stopping) return;
      const observedAt = performance.now();
      state.inputObserved++;
      if (event.type === 'key') state.keyInputObserved++;
      const wakeFromSaver = saver.active || sleeping || pendingLcdFrames > 0 || lcdAssembling;
      const sourceBarrier = sourceOps;
      if (wakeFromSaver) saver.wake();
      inputOps = inputOps.then(async () => {
        if (wakeFromSaver) { await sourceBarrier; await restoreNormal(); }
        if (!bridge?.childConnected) return;
        const input = { deviceId: device.id, sessionId, generation, sequence: ++inputSequence, type: event.type };
        for (const key of ['index', 'state', 'kind', 'delta', 'gesture', 'x', 'y', 'endX', 'endY']) if (event[key] !== undefined) input[key] = event[key];
        if (bridge.submitInput(input)) {
          state.inputForwarded++; lastInputAt = performance.now();
          if (event.type === 'key') state.keyInputForwarded++;
          measure('inputForward', lastInputAt - observedAt);
        }
        publish();
      }).catch(fail);
    });
    if (!fake) {
      bridge = createDeviceBridge({ device, target: config.target, sessionId, generation,
        port: config.port ?? 5343, childPort: config.childPort ?? 5344 });
      bridge.on('output', receive);
      bridge.on('lcdFrameStarted', () => { lcdEpoch = saver.epoch; lcdAssembling = true; });
      bridge.on('connection', () => {
        const connected = bridge.primaryConnected && bridge.childConnected;
        if (connected && !state.hostConnected) state.hostConnections++;
        state.hostConnected = connected; state.everHostConnected ||= connected;
        log('host-connection', { primary: bridge.primaryConnected, child: bridge.childConnected }); publish();
      });
      bridge.on('diagnostic', detail => log('host-diagnostic', { code: detail.code }));
      await bridge.start();
      if (state.stopping) { await bridge.stop(); checkpoint(); }
      state.networkListening = true;
      state.networkAddress = `127.0.0.1:${bridge.ports.primary}`;
      readinessTimer = setInterval(() => {
        inspectHost(config, adapter).then(report => {
          if (report.running !== true && report.ready !== true || report.pid !== config.host.pid ||
              config.host.created && report.created !== config.host.created ||
              config.host.activeSha256 && report.executableSha256 !== config.host.activeSha256) fail(new Error('Stream Deck process identity was lost'));
        }).catch(fail);
      }, 1500);
    }
    scheduler.request();
    publish(); log('ready', { previewUrl: preview?.url ?? null, mode: state.mode, layout, sessionId });
    onReady({ stop, getState: () => structuredClone(state), configure(settings) {
      if (state.stopping) return Promise.reject(new Error('Session is stopping'));
      const operation = sourceOps.then(async () => {
        if (settings.brightness !== undefined) await control({ control: 'brightness', value: settings.brightness });
        if (settings.knobBrightness !== undefined) await control({ control: 'knobBrightness', value: settings.knobBrightness });
        if (settings.maxFps !== undefined) { scheduler.setMaxFps(settings.maxFps); state.maxFps = settings.maxFps; }
        publish();
      });
      sourceOps = operation.catch(fail);
      return operation;
    }, control(payload) {
      if (state.stopping) return Promise.reject(new Error('Session is stopping'));
      const operation = sourceOps.then(() => control(payload));
      sourceOps = operation.catch(fail);
      return operation;
    } });
    if (fakeDemo && !state.stopping) {
      const { default: sharp } = await import('sharp');
      for (const key of layout.keys ?? []) {
        const data = await sharp({ create: { width: key.sourceWidth, height: key.sourceHeight, channels: 3,
          background: Number(key.id.slice(4)) % 2 ? '#195eaa' : '#28845e' } }).jpeg().toBuffer();
        receive({ type: 'frame', surfaceId: key.id, data: data.toString('base64') });
      }
      for (let index = 0; layout.strip && index < 4; index++) {
        const x = Math.floor(index * layout.strip.logicalWidth / 4);
        const width = Math.floor((index + 1) * layout.strip.logicalWidth / 4) - x, height = layout.strip.logicalHeight;
        if (!width) continue;
        const data = await sharp({ create: { width, height, channels: 3,
          background: ['#195eaa', '#28845e', '#a56a24', '#824ea4'][index] } }).jpeg().toBuffer();
        receive({ type: 'frame', surfaceId: 'touch', data: data.toString('base64'), region: { x, y: 0, width, height } });
      }
      await sourceOps; await scheduler.flush();
      const overlay = await sharp({ create: { width: layout.width, height: layout.height, channels: 3,
        background: '#322d78' } }).jpeg().toBuffer();
      receive({ type: 'frame', surfaceId: 'lcd', data: overlay.toString('base64') });
      await sourceOps; await scheduler.flush();
      if (layout.strip) {
        const patchWidth = Math.min(200, layout.strip.logicalWidth), patchHeight = layout.strip.logicalHeight;
        const updated = await sharp({ create: { width: patchWidth, height: patchHeight, channels: 3,
          background: '#bf6137' } }).jpeg().toBuffer();
        receive({ type: 'frame', surfaceId: 'touch', data: updated.toString('base64'), region: { x: 0, y: 0, width: patchWidth, height: patchHeight } });
      }
      await sourceOps; await scheduler.flush();
      receive({ type: 'control', control: 'wake' });
      await sourceOps; await scheduler.flush();
    }
    await finished;
    if (failure) throw failure;
    return state;
  } catch (error) {
    failure ??= error;
    if (!state.stopping) { state.errors.push(error.message); log('startup-failure', { message: error.message, code: error.code }); }
    await stop(); throw error;
  }
}

import { readFileSync, writeFileSync, renameSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { FrameAdapterRegistry } from './frame-adapter-registry.mjs';
import { FrameSessionSupervisor } from './frame-session-supervisor.mjs';
import { FrameAdapterProcess } from './frame-adapter-process.mjs';
import { Worker } from 'node:worker_threads';

function packageTask(action,data) {
  return new Promise((yes,no)=>{
    let replied=false;
    const worker=new Worker(new URL('./frame-adapter-package-worker.mjs',import.meta.url),{workerData:{action,...data},resourceLimits:{maxOldGenerationSizeMb:384}});
    const timer=setTimeout(()=>{void worker.terminate();no(new Error('Adapter package validation timed out'));},30000);
    worker.once('message',value=>{replied=true;clearTimeout(timer);value.error?no(new Error(value.error)):yes(value.result);});
    worker.once('error',error=>{clearTimeout(timer);no(error);});
    worker.once('exit',code=>{clearTimeout(timer);if(code!==0 || !replied)no(new Error('Adapter package validation worker exited'));});
  });
}

const run = promisify(execFile);
const readJson = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
export function writeRuntimeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.${randomBytes(8).toString('hex')}.tmp`;
  writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  renameSync(temporary, path);
}

function initialSettings(config) {
  let identity;
  if (config.legacyIdentityPath && existsSync(config.legacyIdentityPath)) {
    const legacy = readJson(config.legacyIdentityPath);
    if (legacy.identity && typeof legacy.identity === 'object') identity = legacy.identity;
    else {
      if (legacy.version !== 1 || !Number.isInteger(legacy.identity) || legacy.identity < 1 || legacy.identity > 999999 ||
          !Number.isInteger(legacy.dockIdentity) || legacy.dockIdentity < 1 || legacy.dockIdentity > 999999) throw new Error('Existing virtual identity is invalid; refusing to replace it');
      const dock = legacy.dockIdentity;
      identity = { dockSerial: `HGDOCK${String(dock).padStart(6, '0')}`, childSerial: `HGMOCK${String(legacy.identity).padStart(6, '0')}`,
        macAddress: [2, 0, 0, dock >>> 16 & 255, dock >>> 8 & 255, dock & 255] };
    }
  }
  identity ??= { dockSerial: `HGDOCK${String(randomBytes(3).readUIntBE(0, 3) % 999999 + 1).padStart(6, '0')}`,
    childSerial: `HGMOCK${String(randomBytes(3).readUIntBE(0, 3) % 999999 + 1).padStart(6, '0')}`,
    macAddress: [2, ...randomBytes(5)] };
  return { schemaVersion: 1, adapters: [], selectedAdapterId: null, selectedDeviceId: null,
    connectionDesired: false, identity, brightness: 52, maxFps: 60 };
}

function validateSettings(value) {
  if (value?.schemaVersion !== 1 || !Array.isArray(value.adapters) || value.adapters.length > 64 ||
      typeof value.connectionDesired !== 'boolean' ||
      !/^HGDOCK\d{6}$/.test(value.identity?.dockSerial) || !/^HGMOCK\d{6}$/.test(value.identity?.childSerial) ||
      !Array.isArray(value.identity.macAddress) || value.identity.macAddress.length !== 6 ||
      !value.identity.macAddress.every(byte => Number.isInteger(byte) && byte >= 0 && byte <= 255) ||
      !Number.isInteger(value.brightness) || value.brightness < 0 || value.brightness > 100 ||
      value.knobBrightness !== null && (!Number.isInteger(value.knobBrightness) || value.knobBrightness < 0 || value.knobBrightness > 100) ||
      !Number.isInteger(value.maxFps) || value.maxFps < 1 || value.maxFps > 120) throw new Error('Invalid saved runtime settings; identity was not regenerated');
  return value;
}

export class FrameRuntimeManager {
  #config; #preview; #settings; #registry; #supervisor; #devices = []; #adapters = [];
  #sessionState = null; #operations = Promise.resolve(); #createSession; #discover; #hostIdentity; #closing = false;
  #children = new Map();
  constructor(config, { preview, createSession, discover } = {}) {
    this.#config = config; this.#preview = preview; this.#createSession = createSession; this.#discover = discover;
    const settings = existsSync(config.settingsPath) ? readJson(config.settingsPath) : initialSettings(config);
    if (!Object.hasOwn(settings, 'knobBrightness')) {
      settings.knobBrightness = null;
      validateSettings(settings);
      const legacy = config.legacyIdentityPath && resolve(dirname(config.legacyIdentityPath), `device-controls-${settings.identity.childSerial}.json`);
      if (legacy && existsSync(legacy)) {
        const saved = readJson(legacy);
        if (saved.version !== 1 || saved.serial !== settings.identity.childSerial) throw new Error('Saved knob brightness belongs to another device');
        settings.knobBrightness = saved.knobBrightness;
      }
    }
    this.#settings = validateSettings(settings);
    this.#registry = new FrameAdapterRegistry(this.#settings.adapters);
    this.#devices = structuredClone(this.#settings.knownDevices ?? []);
    for (const path of config.builtInAdapters ?? []) {
      const inspected = new FrameAdapterRegistry();
      const entry = inspected.register(path);
      this.#registry.register(path, { source: 'official' });
    }
    this.#adapters = this.#registry.list();
    this.#supervisor = new FrameSessionSupervisor({ start: context => this.#start(context), retryMs: config.retryMs });
    this.#supervisor.on('state', () => this.#publish());
    if (!config.fleetChild) for (const record of this.#settings.linkedSessions ?? []) this.#link(record);
    this.#save();
  }
  #link(record) {
    if (!/^[a-f0-9]{16}$/.test(record.id) || !Number.isInteger(record.slot) || record.slot < 0 || record.slot > 15) throw new Error('Invalid saved device session');
    const stateDir = resolve(this.#config.stateDir, 'devices', record.id);
    const child = new FrameRuntimeManager({ ...this.#config, stateDir, settingsPath: resolve(stateDir, 'settings.json'),
      fleetChild: true, legacyIdentityPath: undefined, nativeUi: undefined, previewEnabled: false,
      port: 5350 + record.slot * 2, childPort: 5351 + record.slot * 2 }, { createSession: this.#createSession, discover: this.#discover });
    this.#children.set(record.id, child); return child;
  }
  #save() {
    this.#settings.adapters = this.#registry.records();
    writeRuntimeJson(this.#config.settingsPath, this.#settings);
  }
  #publish() {
    this.#preview?.update({ status: { ...(this.#sessionState ?? {}), manager: this.#supervisor.status(),
      ...(this.#sessionState ? {} : { deviceConnected: false, hostConnected: false }) } });
  }
  getState() {
    return { ...structuredClone(this.#sessionState ?? {}), schemaVersion: 1, runtime: this.#supervisor.status(),
      adapters: structuredClone(this.#adapters), devices: structuredClone(this.#devices), settings: structuredClone(this.#settings),
      stream: this.#preview?.getState().frame ?? null, previewUrl: this.#preview?.url ?? null, fake: this.#config.fake === true,
      sessions: [...this.#children].map(([id, manager]) => ({ id, ...manager.getState() })) };
  }
  nativeState() {
    const session = this.#sessionState;
    const available = session?.deviceConnected === true && session.knobBrightnessSupported === true && this.#supervisor.status().state === 'running';
    return { serial: this.#settings.identity.childSerial, model: session?.modelId ?? '',
      connected: session?.deviceConnected === true, hostConnected: session?.hostConnected === true,
      networkListening: session?.networkListening === true, connectionDesired: this.#supervisor.status().desired,
      available, capabilities: { knobBrightness: available },
      knobBrightness: session?.knobBrightness ?? this.#settings.knobBrightness ?? this.#settings.brightness,
      followDisplayBrightness: session?.followDisplayBrightness ?? this.#settings.knobBrightness === null };
  }
  async #attachHost() {
    if (this.#config.fake || !this.#config.nativeUi) return;
    const { host, hostProbe, nativeUi } = this.#config;
    const { stdout } = await run(hostProbe.command, [...(hostProbe.args ?? []), '--probe', '--exe', host.executable,
      '--sha256', host.executableSha256], { windowsHide: true, timeout: 5000, maxBuffer: 65536 });
    const report = JSON.parse(stdout.trim());
    if (report.ready !== true) throw new Error(report.reason ?? 'Open the prepared Stream Deck application');
    const identity = `${report.pid}:${report.created}`;
    if (identity === this.#hostIdentity) return;
    await run(hostProbe.command, [...(hostProbe.args ?? []), '--attach', '--exe', host.executable,
      '--sha256', host.executableSha256, '--dll', nativeUi.dllPath, '--config', nativeUi.configPath],
    { windowsHide: true, timeout: 15000, maxBuffer: 65536 });
    this.#hostIdentity = identity;
  }
  async #start({ signal, generation }) {
    const adapter = this.#registry.resolve(this.#settings.selectedAdapterId);
    if (!this.#settings.selectedDeviceId) throw new Error('Select a discovered device');
    await this.#attachHost();
    if (signal.aborted) throw new Error('Connection cancelled');
    const target = structuredClone(adapter.target);
    Object.assign(target.config, { serialNumber: this.#settings.identity.dockSerial,
      childSerialNumber: this.#settings.identity.childSerial, macAddress: [...this.#settings.identity.macAddress] });
    const sessionConfig = { adapter, target, deviceId: this.#settings.selectedDeviceId, generation,
      brightness: this.#settings.brightness, knobBrightness: this.#settings.knobBrightness, maxFps: this.#settings.maxFps,
      host: this.#config.host, hostProbe: this.#config.hostProbe, port: this.#config.port, childPort: this.#config.childPort,
      logPath: resolve(this.#config.stateDir, 'frame-events.jsonl'), resultPath: resolve(this.#config.stateDir, 'last-session.json') };
    const start = this.#createSession ?? (await import('./frame-session.mjs')).startFullFrameSession;
    return start(sessionConfig, { fake: this.#config.fake === true, sharedPreview: this.#preview, signal,
      onState: state => { this.#sessionState = state; } });
  }
  async #discoverAdapter(id) {
    const adapter = this.#registry.resolve(id);
    if (this.#supervisor.status().desired && id === this.#settings.selectedAdapterId) return structuredClone(this.#devices.filter(device => device.adapterId === id));
    if (this.#discover) return this.#discover(adapter);
    const process = new FrameAdapterProcess({ command: adapter.command,
      args: this.#config.fake ? adapter.fakeArgs ?? adapter.args : adapter.args, cwd: adapter.cwd });
    try {
      const metadata = await process.start();
      if (metadata.adapterId !== adapter.id) throw new Error('Adapter process identity does not match its registered manifest');
      const result = await process.request('discover');
      return Array.isArray(result) ? result : result.devices;
    } finally { await process.close(); }
  }
  command(action, params = {}) {
    const operation = this.#operations.then(() => this.#command(action, params));
    this.#operations = operation.catch(() => {});
    return operation;
  }
  async #command(action, params) {
    if (this.#closing) throw new Error('Runtime is closing');
    switch (action) {
      case 'native-control': {
        const owner = [this, ...this.#children.values()].find(manager => manager.#settings.identity.childSerial === params.serial);
        if (!owner || params.action !== 'knobBrightness' || !Number.isInteger(params.value) || params.value < 0 || params.value > 100 ||
            params.persist !== undefined && typeof params.persist !== 'boolean') throw new Error('Invalid native device control');
        if (owner !== this) return owner.command(action, params);
        if (!this.nativeState().available) throw new Error('Knob brightness is unavailable for this device');
        if (!await this.#supervisor.configure({ knobBrightness: params.value })) throw new Error('Device is not connected');
        if (params.persist !== false) { this.#settings.knobBrightness = params.value; this.#save(); }
        return { ok: true, state: this.nativeState() };
      }
      case 'device-settings': {
        const owner=[this,...this.#children.values()].find(manager=>{const settings=manager.getState().settings;return settings.selectedDeviceId===params.deviceId && settings.selectedAdapterId===params.adapterId;});
        if (!owner) throw new Error('Connect the selected device before changing its settings');
        const settings={maxFps:params.maxFps};
        if(owner===this) await this.#command('settings',settings);else await owner.command('settings',settings);
        return this.getState();
      }
      case 'connect-device': {
        if (!this.#devices.some(device => device.adapterId === params.adapterId && device.id === params.deviceId)) throw new Error('Select a discovered device');
        const owner = [this, ...this.#children.values()].find(manager => {
          const settings = manager.getState().settings; return settings.selectedAdapterId === params.adapterId && settings.selectedDeviceId === params.deviceId;
        });
        if (owner) { if (owner === this) await this.#command('connect', {}); else await owner.command('connect'); return this.getState(); }
        if (!this.#supervisor.status().desired && !this.#settings.selectedDeviceId) {
          await this.#command('select', params); await this.#command('connect', {}); return this.getState();
        }
        if (this.#config.fleetChild || this.#children.size >= 16) throw new Error('Device session limit reached');
        const used = new Set((this.#settings.linkedSessions ?? []).map(record => record.slot));
        const slot = Array.from({ length: 16 }, (_, index) => index).find(index => !used.has(index));
        const record = { id: randomBytes(8).toString('hex'), slot }, child = this.#link(record);
        try {
          child.#registry = new FrameAdapterRegistry(this.#registry.records()); child.#adapters = child.#registry.list();
          child.#devices = [structuredClone(this.#devices.find(device => device.id === params.deviceId && device.adapterId === params.adapterId))];
          await child.command('select', params); await child.command('connect');
          this.#settings.linkedSessions ??= []; this.#settings.linkedSessions.push(record); this.#save(); return this.getState();
        } catch (error) { await child.close(); this.#children.delete(record.id); throw error; }
      }
      case 'disconnect-device': {
        const owner = [this, ...this.#children.values()].find(manager => {
          const settings = manager.getState().settings; return settings.selectedAdapterId === params.adapterId && settings.selectedDeviceId === params.deviceId;
        });
        if (owner === this) { await this.#command('disconnect', {}); return this.getState(); }
        if (owner) await owner.command('disconnect'); return this.getState();
      }
      case 'inspect-package': return packageTask('inspect',{path:params.path});
      case 'install-package': {
        if (params.acknowledgeExternalCode !== true) throw new Error('Confirm the external adapter code warning before installation');
        const installed = await packageTask('install',{path:params.path,destination:resolve(this.#config.stateDir,'adapters'),packageHash:params.packageHash,reserved:this.#adapters.filter(adapter=>adapter.source==='official').map(adapter=>adapter.id)});
        const adapter = this.#registry.register(installed.manifestPath);
        this.#adapters = this.#registry.list(); this.#save(); return { adapterId: adapter.id };
      }
      case 'check-adapters': this.#adapters = this.#registry.list(); return this.getState();
      case 'register': {
        if (this.#adapters.some(adapter => adapter.source === 'official' && adapter.manifestPath === params.path)) throw new Error('Bundled adapters are managed by the app');
        const adapter = this.#registry.register(params.path);
        this.#adapters = this.#registry.list(); this.#save(); return { adapterId: adapter.id };
      }
      case 'unregister':
        if (params.adapterId === this.#settings.selectedAdapterId) {
          await this.#supervisor.disconnect(); this.#settings.connectionDesired = false;
          this.#settings.selectedAdapterId = null; this.#settings.selectedDeviceId = null;
        }
        this.#registry.unregister(params.adapterId); this.#adapters = this.#registry.list(); this.#save(); break;
      case 'discover':
        {
        const found = await this.#discoverAdapter(params.adapterId);
        if (!Array.isArray(found) || found.length > 256) throw new Error('Invalid discovery result');
        this.#devices = [...this.#devices.filter(device => device.adapterId !== params.adapterId), ...found];
        this.#settings.knownDevices = structuredClone(this.#devices); this.#save();
        return { devices: structuredClone(this.#devices) }; }
      case 'select':
        this.#registry.resolve(params.adapterId);
        if (!this.#devices.some(device => device.id === params.deviceId && device.adapterId === params.adapterId)) throw new Error('Select a device from explicit discovery');
        await this.#supervisor.disconnect();
        this.#settings.connectionDesired = false;
        this.#settings.selectedAdapterId = params.adapterId; this.#settings.selectedDeviceId = params.deviceId;
        this.#preview?.update({ frameJpeg: null }); this.#save(); break;
      case 'connect':
        this.#registry.resolve(this.#settings.selectedAdapterId);
        if (!this.#settings.selectedDeviceId) throw new Error('Select a discovered device');
        this.#settings.connectionDesired = true; this.#save(); this.#supervisor.connect(); break;
      case 'disconnect':
        this.#settings.connectionDesired = false; this.#save(); await this.#supervisor.disconnect(); break;
      case 'settings': {
        const next = { ...this.#settings };
        for (const key of ['brightness', 'maxFps']) if (Object.hasOwn(params, key)) next[key] = params[key];
        validateSettings(next);
        const reconnect = this.#settings.connectionDesired;
        if (await this.#supervisor.configure({ ...(Object.hasOwn(params, 'brightness') ? { brightness: next.brightness } : {}),
          ...(Object.hasOwn(params, 'maxFps') ? { maxFps: next.maxFps } : {}) })) {
          this.#settings = next; this.#save(); break;
        }
        await this.#supervisor.disconnect(); this.#settings = next; this.#save();
        if (reconnect) this.#supervisor.connect(); break;
      }
      default: throw new Error('Unknown management command');
    }
    this.#publish(); return this.getState();
  }
  resume() { if (this.#settings.connectionDesired) this.#supervisor.connect(); for (const child of this.#children.values()) child.resume(); }
  async close() { this.#closing = true; await this.#operations; await Promise.all([...this.#children.values()].map(child => child.close())); await this.#supervisor.disconnect(); }
}

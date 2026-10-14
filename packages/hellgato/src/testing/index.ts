import type { Adapter, OperationContext } from '../../index.js';
import type { DeviceDescriptor, MappingProfile, InputEvent, DisplayFrame, ControlCommand } from '@hellgato/core/contracts';
export interface SimulatorOptions {
  device?: DeviceDescriptor;
  deviceId?: string;
  adapterId?: string;
  modelId?: string;
  keys?: number;
  dials?: number;
  touch?: { width: number; height: number } | null;
  keyDimensions?: { width: number; height: number };
}
export type SimulatorInput =
  | { type: 'key'; index: number; state: 'down' | 'up' }
  | { type: 'dial'; kind: 'rotate'; index: number; delta: number }
  | { type: 'dial'; kind: 'press'; index: number; state: 'down' | 'up' }
  | { type: 'dial'; kind: 'pulse'; index: number }
  | { type: 'touch'; gesture: 'tap' | 'hold'; x: number; y: number }
  | { type: 'touch'; gesture: 'swipe'; x: number; y: number; endX: number; endY: number };
export interface SimulatorAdapter extends Adapter {
  readonly device: DeviceDescriptor;
  readonly outputs: DisplayFrame[];
  readonly controls: ControlCommand[];
  readonly calls: string[];
  emitInput(event: SimulatorInput): InputEvent;
  injectInput(event: unknown): void;
  disconnect(): void;
}
import type { OpenContext } from '../../index.js';
import type { AdapterDescriptor, OutputEnvelope } from '@hellgato/core/contracts';
import { EventEmitter } from 'node:events';
import packageManifest from '../../package.json' with { type: 'json' };
import { assertAdapterDescriptor, assertDeviceDescriptor, assertInputEvent, assertDisplayFrame, assertControlCommand } from '@hellgato/core/contracts';

/** In-process fixture only. Passing this adapter never establishes physical support. */
export function createSimulator(options: SimulatorOptions = {}): SimulatorAdapter {
  const keyCount = options.keys ?? 6;
  const dialCount = options.dials ?? 0;
  const touch = options.touch ?? null;
  const id = options.deviceId ?? 'simulator:buttons';
  const modelId = options.modelId ?? 'simulator-buttons';
  const adapterId = options.adapterId ?? 'hellgato-simulator';
  const dimensions = options.keyDimensions ?? { width: 72, height: 72 };
  const device: DeviceDescriptor = options.device ?? {
    id, adapterId, modelId, firmware: 'simulated',
    capabilities: {
      keys: keyCount, dials: dialCount, touch,
      displays: [
        ...Array.from({ length: keyCount }, (_, index) => ({ id: `key:${index}`, ...dimensions, format: 'jpeg' as const, partial: false })),
        ...(touch ? [{ id: 'touch', width: 800, height: 100, format: 'jpeg' as const, partial: true }] : []),
      ],
      dialPress: dialCount ? 'native' : 'unsupported', dialRelease: dialCount ? 'native' : 'unavailable',
      features: Object.fromEntries(['brightness', 'sleepDuration', 'clear', 'fill', 'sleep', 'wake', 'showLogo'].map(name => [name, { status: 'emulated' as const, limitations: ['In-memory simulation only'] }] as const)),
    },
  };
  assertDeviceDescriptor(device);
  const descriptor: AdapterDescriptor = { id: device.adapterId, version: packageManifest.version, modelIds: [device.modelId], platforms: ['any'], architectures: ['any'], apiVersion: 1 };
  const emitter = new EventEmitter();
  let started = false; let opened = false; let generation = 0; let sequence = 0; let sessionId: string | undefined;
  const outputs: DisplayFrame[] = []; const controls: ControlCommand[] = []; const calls: string[] = [];
  const subscribe = <T,>(name: string, callback: (event: T) => void) => { emitter.on(name, callback); return () => emitter.off(name, callback); };
  const ensureOpen = (value: OutputEnvelope | SimulatorInput) => {
    if (!opened) throw new Error('Simulator is not open');
    if ('deviceId' in value && value.deviceId !== undefined && value.deviceId !== device.id) throw new Error('Wrong simulator device');
    if ('generation' in value && value.generation !== undefined && value.generation !== generation) throw new Error('Stale simulator generation');
  };
  const adapter: SimulatorAdapter = {
    descriptor,
    get outputs() { return structuredClone(outputs); },
    get controls() { return structuredClone(controls); },
    get calls() { return [...calls]; },
    async preflight({ signal } = {}) { signal?.throwIfAborted(); calls.push('preflight'); },
    async start({ signal } = {}) { signal?.throwIfAborted(); if (!started) { started = true; calls.push('start'); } },
    async discover({ signal } = {}) { signal?.throwIfAborted(); calls.push('discover'); return [structuredClone(device)]; },
    async open(deviceId, context: OpenContext = { generation: 1 }) {
      context.signal?.throwIfAborted();
      if (!started) throw new Error('Simulator must be started before open');
      if (deviceId !== device.id) throw new Error('Unknown simulator device');
      if (opened) throw new Error('Simulator already owns the device');
      opened = true; generation = context.generation ?? 1; sessionId = context.sessionId; sequence = 0;
      calls.push('open'); emitter.emit('state', { deviceId, generation, state: 'connected' });
    },
    async close() { if (opened) { opened = false; calls.push('close'); } },
    async stop() { await adapter.close(); if (started) { started = false; calls.push('stop'); } },
    async getState() { return { deviceId: device.id, generation, state: opened ? 'connected' : 'disconnected' }; },
    async setOutput(frame) { ensureOpen(frame); assertDisplayFrame(frame); calls.push('setOutput'); outputs.push(structuredClone(frame)); return { status: 'applied', simulated: true }; },
    async setControl(control) { ensureOpen(control); assertControlCommand(control); calls.push('setControl'); controls.push(structuredClone(control)); return { status: 'applied', simulated: true }; },
    onInput: callback => subscribe('input', callback),
    onState: callback => subscribe('state', callback),
    onDiagnostic: callback => subscribe('diagnostic', callback),
    emitInput(event) {
      ensureOpen(event);
      const normalized = { deviceId: device.id, generation, sequence: ++sequence, ...(sessionId ? { sessionId } : {}), ...event };
      assertInputEvent(normalized); emitter.emit('input', structuredClone(normalized)); return normalized;
    },
    /** Malformed or stale fixture injection intentionally bypasses simulator validation. */
    injectInput(event) { emitter.emit('input', structuredClone(event)); },
    disconnect() { if (opened) { opened = false; emitter.emit('state', { deviceId: device.id, generation, state: 'disconnected' }); } },
    get device() { return structuredClone(device); },
  };
  return adapter;
}

export function createButtonSimulator(options: SimulatorOptions = {}): SimulatorAdapter { return createSimulator({ ...options, dials: 0, touch: null }); }
export function createNonN4Simulator(options: SimulatorOptions = {}): SimulatorAdapter {
  return createSimulator({ keys: 4, dials: 2, touch: { width: 320, height: 60 }, deviceId: 'simulator:compact', modelId: 'simulator-compact', ...options });
}

/** A fixture mapping, not a claim that a real Stream Deck installation supports this geometry. */
export function createSimulatorProfile(device: DeviceDescriptor, options: { id?: string; columns?: number; virtualSerial?: string; childSerial?: string } = {}): MappingProfile {
  assertDeviceDescriptor(device);
  const keys = device.capabilities.keys;
  const dials = device.capabilities.dials;
  const columns = options.columns ?? Math.min(keys || 1, 3);
  const keyDisplay = device.capabilities.displays.find(surface => surface.id === 'key:0');
  return {
    id: options.id ?? `${device.modelId}-profile`, version: '1.0.0',
    target: { id: 'streamdeck-simulator', geometry: {
      rows: Math.ceil((keys || 1) / columns), columns, keyCount: keys,
      keyWidth: keyDisplay?.width ?? 72, keyHeight: keyDisplay?.height ?? 72,
      encoderCount: dials, touchWidth: device.capabilities.touch ? 800 : 0, touchHeight: device.capabilities.touch ? 100 : 0,
      productName: 'Hellgato simulator',
    }, config: {
      serialNumber: options.virtualSerial ?? 'HGSIMDOCK000001', childSerialNumber: options.childSerial ?? 'HGSIMCHILD000001',
      dockFirmwareVersion: '1.0.0', childFirmwareVersion: '1.0.0', productId: 0x0084, macAddress: [2, 0, 0, 0, 0, 1],
    } },
    controls: { keys: Array.from({ length: keys }, (_, index) => index), dials: Array.from({ length: dials }, (_, index) => index) },
    ...(device.capabilities.touch ? { touch: { rotation: 0 } } : {}),
    displays: device.capabilities.displays.map(surface => ({ source: surface.id, target: surface.id })),
    requirements: { hostPreparation: [], capabilities: [] },
  };
}

/** Replay validated normalized input into a sink. Recorded generation is retained unless explicitly replaced. */
export async function replayInputs(events: InputEvent[], sink: (input: InputEvent) => unknown, options: { signal?: AbortSignal; generation?: number; sessionId?: string; deviceId?: string } = {}): Promise<{ count: number; physicalHardwareVerified: false }> {
  if (!Array.isArray(events) || events.length > 100000) throw new TypeError('Replay requires a bounded input array');
  if (typeof sink !== 'function') throw new TypeError('Replay requires an input sink');
  const fixture = events.map(event => { assertInputEvent(event); return structuredClone(event); });
  let count = 0;
  for (const event of fixture) {
    options.signal?.throwIfAborted();
    if (options.generation !== undefined) event.generation = options.generation;
    if (options.sessionId !== undefined) event.sessionId = options.sessionId;
    if (options.deviceId !== undefined) event.deviceId = options.deviceId;
    assertInputEvent(event); await sink(event); count++;
  }
  return { count, physicalHardwareVerified: false };
}

/** Checks discovery, explicit ownership, subscriptions and cleanup; never upgrades hardware support. */
export async function runAdapterConformance(adapter: Adapter, options: OperationContext & { deviceId: string; output?: DisplayFrame; control?: ControlCommand }): Promise<{ adapterId: string; checks: string[]; physicalHardwareVerified: false }> {
  assertAdapterDescriptor(adapter.descriptor);
  for (const name of ['discover', 'open', 'close', 'setOutput', 'setControl', 'onInput', 'onState'] as const) {
    if (typeof adapter[name] !== 'function') throw new TypeError(`Adapter missing ${name}`);
  }
  const report: { adapterId: string; checks: string[]; physicalHardwareVerified: false } = { adapterId: adapter.descriptor.id, checks: [], physicalHardwareVerified: false };
  const unsubscribers: Array<() => void> = [];
  let opened = false;
  try {
    await adapter.preflight?.({ signal: options.signal }); report.checks.push('preflight');
    await adapter.start?.({ signal: options.signal });
    const devices = await adapter.discover({ signal: options.signal });
    if (!Array.isArray(devices)) throw new TypeError('Discovery must return an array');
    devices.forEach(assertDeviceDescriptor); report.checks.push('discovery-contract');
    if (new Set(devices.map(device => device.id)).size !== devices.length) throw new TypeError('Discovery contains duplicate device IDs');
    const device = devices.find(item => item.id === options.deviceId);
    if (!device) throw new TypeError('Conformance requires an explicit discovered deviceId');
    for (const name of ['onInput', 'onState'] as const) {
      const unsubscribe = adapter[name](value => { if (name === 'onInput') assertInputEvent(value); });
      if (typeof unsubscribe !== 'function') throw new TypeError(`${name} must return unsubscribe`);
      unsubscribers.push(unsubscribe);
    }
    opened = true; await adapter.open(device.id, { generation: 1, sessionId: 'adapter-conformance', signal: options.signal });
    report.checks.push('explicit-open');
    if (options.output) { assertDisplayFrame(options.output); await adapter.setOutput(options.output); report.checks.push('output-contract'); }
    if (options.control) { assertControlCommand(options.control); await adapter.setControl(options.control); report.checks.push('control-contract'); }
  } finally {
    for (const unsubscribe of unsubscribers.reverse()) unsubscribe();
    try { if (opened) await adapter.close(); } finally { await adapter.stop?.(); }
  }
  report.checks.push('cleanup');
  return report;
}

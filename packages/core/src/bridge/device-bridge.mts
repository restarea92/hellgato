import type { Socket, AddressInfo } from 'node:net';
import type { Bridge, BridgeOptions, BridgeState } from '../bridge.mjs';
import type { InputEvent, OutputEvent } from '../contracts/index.mjs';
import type { PrimaryTransport, ChildTransport } from '../hosts/streamdeck/target.mjs';
type ResolvedOptions = Required<BridgeOptions>;
const messageOf = (error: unknown): string => error instanceof Error ? error.message : String(error);
const portOf = (address: AddressInfo | string | null | undefined): number | null => typeof address === 'object' && address ? address.port : null;
import { EventEmitter } from 'node:events';
import { validateDeviceDescriptor, validateInputEvent, validateDisplayFrame, validateControlCommand } from '../contracts/index.mjs';
import { createStreamDeckTransport, validateStreamDeckTarget } from '../hosts/streamdeck/target.mjs';

function integer(value: number, minimum: number, maximum: number, name: string): void {
  if (!Number.isInteger(value) || value < minimum || value > maximum) throw new RangeError(`${name} must be an integer from ${minimum} to ${maximum}`);
}

/** Construct an inert device-neutral Stream Deck bridge. No ports, files or
 * processes are touched until start(). Physical selection belongs to the caller. */
export function createDeviceBridge(options: BridgeOptions): Bridge {
  if (!options || typeof options !== 'object') throw new TypeError('device and target are required');
  validateDeviceDescriptor(options.device);
  validateStreamDeckTarget(options.target);
  const port = options.port ?? 5343;
  const childPort = options.childPort ?? 5344;
  const sleepDuration = options.sleepDuration ?? 0;
  const generation = options.generation ?? 1;
  const sessionId = options.sessionId ?? `${options.device.id.slice(0, 120)}:bridge`;
  integer(port, 0, 65535, 'port');
  integer(childPort, 0, 65535, 'childPort');
  integer(sleepDuration, 0, 0x7fffffff, 'sleepDuration');
  integer(generation, 0, Number.MAX_SAFE_INTEGER, 'generation');
  if (port !== 0 && port === childPort) throw new RangeError('Ports must be different');
  if (typeof sessionId !== 'string' || !sessionId.length || sessionId.length > 128) throw new TypeError('sessionId must have 1 to 128 characters');
  return new DeviceBridge(structuredClone({ device: options.device, target: options.target, port, childPort, sleepDuration, generation, sessionId }));
}

class DeviceBridge extends EventEmitter implements Bridge {
  #options: ResolvedOptions;
  #primary: PrimaryTransport | undefined;
  #child: ChildTransport | undefined;
  #hasStarted = false;
  #state: BridgeState['state'] = 'idle';
  #operation = Promise.resolve();
  #sockets = new Set<Socket>();
  #lastInputSequence = -1;
  #outputSequence = 0;
  #pressedKeys = new Set<number>();
  #pressedDials = new Set<number>();

  constructor(options: ResolvedOptions) { super(); this.#options = options; }
  get state() { return this.#state; }
  get deviceId() { return this.#options.device.id; }
  get sessionId() { return this.#options.sessionId; }
  get generation() { return this.#options.generation; }
  get primaryConnected() { return this.#primary?.hasClient ?? false; }
  get childConnected() { return this.#child?.hasClient ?? false; }
  get ports() { return { primary: portOf(this.#primary?.server.address()), child: portOf(this.#child?.server.address()) }; }
  getState() { return { state: this.state, deviceId: this.deviceId, sessionId: this.sessionId, generation: this.generation, primaryConnected: this.primaryConnected, childConnected: this.childConnected, ports: this.ports }; }
  #setState(state: BridgeState['state']) { this.#state = state; this.emit('state', this.getState()); }
  #enqueue(action: () => Promise<void>): Promise<void> { const result = this.#operation.then(action); this.#operation = result.catch(() => {}); return result; }

  #bind() {
    for (const [role, server] of [['primary', this.#primary], ['child', this.#child]] as const) {
      if (!server) throw new Error('Bridge transport is not initialized');
      server.server.on('connection', socket => {
        this.#sockets.add(socket);
        socket.once('close', () => this.#sockets.delete(socket));
      });
      for (const event of ['log', 'comm', 'clientConnected', 'clientDisconnected', 'operationRejected']) {
        server.on(event, (...detail: unknown[]) => {
          if (role === 'child' && event === 'clientDisconnected') this.releaseInputs();
          this.emit(event, { role, detail });
          if (event === 'clientConnected' || event === 'clientDisconnected') this.emit('connection', this.getState());
        });
      }
      server.on('serverLog', (...detail: unknown[]) => this.emit('log', { role, detail }));
    }
    // Legacy events remain byte-for-byte compatible. The new output event is a
    // normalized public payload and never leaks vendor implementation objects.
    for (const event of ['image', 'touchImage', 'lcdFrameStarted', 'lcdImage', 'displayCommand', 'sleepDuration', 'brightness']) {
      this.#child!.on(event, (detail: unknown) => {
        if (event === 'sleepDuration' && typeof detail === 'number') this.#options.sleepDuration = detail;
        this.emit(event, detail);
        if (this.listenerCount('output')) this.#normalizeOutput(event, detail);
      });
    }
  }

  #normalizeOutput(event: string, detail: unknown): void {
    const geometry = this.#options.target.geometry;
    const base = { deviceId: this.deviceId, sessionId: this.sessionId, generation: this.generation, sequence: ++this.#outputSequence };
    let validated: OutputEvent;
    try {
      let output: Record<string, unknown>;
      if (['image', 'touchImage', 'lcdImage'].includes(event)) {
        if (!detail || typeof detail !== 'object') throw new TypeError('Host frame must be an object');
        const frame = detail as Record<string, unknown>;
        const surfaceId = event === 'image' ? `key:${frame.keyIndex}` : event === 'touchImage' ? 'touch' : 'lcd';
        const configured = (this.#options.target.displays ?? []).find(surface => surface.id === surfaceId);
        const width = event === 'image' ? geometry.keyWidth : event === 'touchImage' ? geometry.touchWidth : configured?.width;
        const height = event === 'image' ? geometry.keyHeight : event === 'touchImage' ? geometry.touchHeight : configured?.height;
        if (!width || !height) { this.emit('diagnostic', { code: 'ERR_UNMAPPED_SURFACE', surfaceId }); return; }
        if (!(frame.data instanceof Uint8Array)) throw new TypeError('Host frame data must be bytes');
        output = { ...base, type: 'frame', surfaceId, format: frame.format ?? 'jpeg', width, height, data: Buffer.from(frame.data).toString('base64') };
        if (frame.region) {
          if (typeof frame.region !== 'object') throw new TypeError('Host frame region must be an object');
          const region = frame.region as Record<string, unknown>;
          output.region = { x: region.x, y: region.y, width: region.w ?? region.width, height: region.h ?? region.height };
        }
      } else if (event === 'brightness' || event === 'sleepDuration') {
        output = { ...base, type: 'control', control: event, value: detail };
      } else if (event === 'displayCommand') {
        if (!detail || typeof detail !== 'object') throw new TypeError('Host control must be an object');
        const command = detail as Record<string, unknown>;
        output = { ...base, type: 'control', control: command.type };
        if (command.color) output.color = Array.isArray(command.color) ? [...command.color] : command.color;
        if (command.keyIndex !== undefined) output.surfaceId = `key:${command.keyIndex}`;
      } else return;
      validated = output.type === 'frame' ? validateDisplayFrame(output) : validateControlCommand(output);
    } catch (error) { this.emit('diagnostic', { code: 'ERR_HOST_OUTPUT', message: messageOf(error) }); return; }
    this.emit('output', validated);
  }

  start() {
    return this.#enqueue(async () => {
      if (this.state === 'ready') return;
      if (this.#hasStarted) {
        integer(this.#options.generation + 1, 0, Number.MAX_SAFE_INTEGER, 'generation');
        this.#options.generation++;
        this.#lastInputSequence = -1;
        this.#hasStarted = false;
      }
      this.#setState('starting');
      try {
        const transport = await createStreamDeckTransport(this.#options.target, this.#options);
        this.#primary = transport.primary; this.#child = transport.child;
        this.#bind();
        await this.#child.start();
        this.#primary.childPort = this.ports.child;
        await this.#primary.start();
        this.#hasStarted = true;
        this.#setState('ready');
      } catch (error) {
        await this.#cleanup();
        this.#setState('failed');
        throw error;
      }
    });
  }

  async #cleanup() {
    this.releaseInputs();
    for (const socket of this.#sockets) socket.destroy();
    this.#sockets.clear();
    const results = await Promise.allSettled([this.#primary?.stop(), this.#child?.stop()]);
    this.#primary = undefined; this.#child = undefined;
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
    return errors;
  }
  stop() {
    return this.#enqueue(async () => {
      if (this.state === 'stopped' && !this.#primary && !this.#child) return;
      this.#setState('stopping');
      const errors = await this.#cleanup();
      this.#setState(errors.length ? 'failed' : 'stopped');
      if (errors.length) throw new AggregateError(errors, 'Bridge shutdown failed');
    });
  }

  // Release only presses that were actually forwarded. Never replay stale input
  // after disconnect; reset the vendor mask as well as the public state.
  releaseInputs() {
    if (this.#child?.hasClient) {
      for (const index of this.#pressedKeys) this.#child.sendKeyEvent(index, 'up');
      for (const index of this.#pressedDials) this.#child.sendDial({ kind: 'press', index, state: 'up' });
    }
    this.#pressedKeys.clear(); this.#pressedDials.clear();
    if (this.#child) { this.#child.keyStates.fill(0); this.#child.encoderPressMask = 0; }
  }
  sendKey(index: number, state: 'down' | 'up') {
    integer(index, 0, this.#options.target.geometry.keyCount - 1, 'key index');
    if (!['down', 'up'].includes(state)) throw new TypeError('state must be down or up');
    if (!this.#child?.hasClient) return false;
    this.#child.sendKeyEvent(index, state);
    state === 'down' ? this.#pressedKeys.add(index) : this.#pressedKeys.delete(index);
    return true;
  }
  sendDial(event: Parameters<Bridge['sendDial']>[0]) {
    integer(event.index, 0, this.#options.target.geometry.encoderCount - 1, 'dial index');
    if (event.kind === 'rotate') integer(event.delta, -128, 127, 'delta');
    else if (event.kind !== 'press' || !['down', 'up'].includes(event.state)) throw new TypeError('Use a rotate event with delta or a press event with state');
    if (!this.#child?.hasClient) return false;
    this.#child.sendDial(event);
    if (event.kind === 'press') event.state === 'down' ? this.#pressedDials.add(event.index) : this.#pressedDials.delete(event.index);
    return true;
  }
  sendTouch(event: Parameters<Bridge['sendTouch']>[0]) {
    if (!['tap', 'hold', 'swipe'].includes(event.type)) throw new TypeError('Unknown touch type');
    integer(event.x, 0, this.#options.target.geometry.touchWidth - 1, 'x');
    integer(event.y, 0, this.#options.target.geometry.touchHeight - 1, 'y');
    if (event.type === 'swipe') {
      integer(event.endX, 0, this.#options.target.geometry.touchWidth - 1, 'endX');
      integer(event.endY, 0, this.#options.target.geometry.touchHeight - 1, 'endY');
    }
    if (!this.#child?.hasClient) return false;
    this.#child.sendTouch(event); return true;
  }
  submitInput(event: InputEvent) {
    validateInputEvent(event);
    if (event.deviceId !== this.deviceId || event.generation !== this.generation || (event.sessionId !== undefined && event.sessionId !== this.sessionId) || event.sequence <= this.#lastInputSequence) return false;
    this.#lastInputSequence = event.sequence;
    if (event.type === 'key') return this.sendKey(event.index, event.state);
    if (event.type === 'touch') return this.sendTouch(event.gesture === 'swipe' ? { ...event, type: 'swipe' } : { ...event, type: event.gesture });
    if (event.kind === 'pulse') {
      if (!this.sendDial({ kind: 'press', index: event.index, state: 'down' })) return false;
      this.sendDial({ kind: 'press', index: event.index, state: 'up' }); return true;
    }
    return this.sendDial(event);
  }
  sendInput(event: InputEvent) { return this.submitInput(event); }
}

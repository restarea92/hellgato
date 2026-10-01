import { EventEmitter } from 'node:events';
import { profile } from './profiles.mjs';
import { withDisplayCommands } from './display-server.mjs';

const runtimeRoot = new URL('../../work/cora-runtime/', import.meta.url);

export function createN4ProProfile(identity = 2, dockIdentity = identity) {
  const device = profile('target', identity);
  device.config.serialNumber = profile('target', dockIdentity).config.serialNumber;
  return device;
}

function integer(value, minimum, maximum, name) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new RangeError(`${name} must be an integer from ${minimum} to ${maximum}`);
  }
}

export async function createBridge(options = {}) {
  const defaults = createN4ProProfile();
  const geometry = { ...defaults.geometry, ...options.geometry };
  const config = { ...defaults.config, ...options.config };
  config.macAddress = [...config.macAddress];
  const port = options.port ?? 5343;
  const childPort = options.childPort ?? 5344;
  integer(port, 0, 65535, 'port');
  integer(childPort, 0, 65535, 'childPort');
  if (port !== 0 && port === childPort) throw new RangeError('Ports must be different');
  for (const name of ['rows', 'columns', 'keyCount']) integer(geometry[name], 1, 255, name);
  for (const name of ['keyWidth', 'keyHeight', 'touchWidth', 'touchHeight']) {
    integer(geometry[name], 1, 65535, name);
  }
  integer(geometry.encoderCount, 0, 4, 'encoderCount');
  if (geometry.keyCount > geometry.rows * geometry.columns) throw new RangeError('Keys exceed the grid');
  for (const name of ['serialNumber', 'childSerialNumber', 'dockFirmwareVersion', 'childFirmwareVersion']) {
    if (typeof config[name] !== 'string' || !config[name].length || config[name].length > 31) {
      throw new TypeError(`${name} must be a nonempty string of at most 31 characters`);
    }
  }
  integer(config.productId, 0, 65535, 'productId');
  if (config.macAddress.length !== 6) throw new RangeError('macAddress must contain six bytes');
  for (const byte of config.macAddress) integer(byte, 0, 255, 'macAddress byte');
  const [{ ElgatoServer }, { ElgatoChildServer }] = await Promise.all([
    import(new URL('cora/primary-server.js', runtimeRoot)),
    import(new URL('cora/child-server.js', runtimeRoot)),
  ]);
  const primary = new ElgatoServer(geometry, port, true, { childPort });
  primary.setDeviceConfig(config);
  const DisplayServer = withDisplayCommands(ElgatoChildServer);
  const child = new DisplayServer(geometry, childPort, config, false);
  return new Bridge(primary, child, geometry);
}

class Bridge extends EventEmitter {
  #primary;
  #child;
  #geometry;
  #started = false;
  #operation = Promise.resolve();

  constructor(primary, child, geometry) {
    super();
    this.#primary = primary;
    this.#child = child;
    this.#geometry = geometry;
    for (const [role, server] of [['primary', primary], ['child', child]]) {
      for (const event of ['log', 'comm', 'clientConnected', 'clientDisconnected']) {
        server.on(event, (...detail) => this.emit(event, { role, detail }));
      }
    }
    for (const event of ['image', 'touchImage', 'brightness']) {
      child.on(event, detail => this.emit(event, detail));
    }
  }

  get primaryConnected() { return this.#primary.hasClient; }
  get childConnected() { return this.#child.hasClient; }
  get ports() {
    return {
      primary: this.#primary.server.address()?.port ?? null,
      child: this.#child.server.address()?.port ?? null,
    };
  }

  #enqueue(action) {
    const result = this.#operation.then(action);
    this.#operation = result.catch(() => {});
    return result;
  }

  start() {
    return this.#enqueue(async () => {
      if (this.#started) return;
      try {
        await this.#child.start();
        this.#primary.childPort = this.ports.child;
        await this.#primary.start();
        this.#started = true;
      } catch (error) {
        await Promise.allSettled([this.#primary.stop(), this.#child.stop()]);
        throw error;
      }
    });
  }

  stop() {
    return this.#enqueue(async () => {
      if (!this.#started) return;
      await Promise.all([this.#primary.stop(), this.#child.stop()]);
      this.#started = false;
    });
  }

  sendKey(index, state) {
    integer(index, 0, this.#geometry.keyCount - 1, 'key index');
    if (!['down', 'up'].includes(state)) throw new TypeError('state must be down or up');
    if (!this.childConnected) return false;
    this.#child.sendKeyEvent(index, state);
    return true;
  }

  sendDial(event) {
    integer(event.index, 0, this.#geometry.encoderCount - 1, 'dial index');
    if (event.kind === 'rotate') integer(event.delta, -128, 127, 'delta');
    else if (event.kind !== 'press' || !['down', 'up'].includes(event.state)) {
      throw new TypeError('Use a rotate event with delta or a press event with state');
    }
    if (!this.childConnected) return false;
    this.#child.sendDial(event);
    return true;
  }

  sendTouch(event) {
    if (!['tap', 'hold', 'swipe'].includes(event.type)) throw new TypeError('Unknown touch type');
    integer(event.x, 0, this.#geometry.touchWidth - 1, 'x');
    integer(event.y, 0, this.#geometry.touchHeight - 1, 'y');
    if (event.type === 'swipe') {
      integer(event.endX, 0, this.#geometry.touchWidth - 1, 'endX');
      integer(event.endY, 0, this.#geometry.touchHeight - 1, 'endY');
    }
    if (!this.childConnected) return false;
    this.#child.sendTouch(event);
    return true;
  }
}

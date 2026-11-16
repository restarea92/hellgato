import { EventEmitter } from 'node:events';
import { FrameAdapterProcess } from './frame-adapter-process.mjs';

// USB can disappear without destroying the host identity or retained canvas.
export class PersistentFrameAdapter extends EventEmitter {
  #create; #worker; #metadata; #device; #open; #frame; #controls = new Map();
  #online = false; #closed = false; #retry; #connecting; #failure;
  constructor(options, { create = value => new FrameAdapterProcess(value), retryMs = 1500 } = {}) {
    super(); this.#create = () => create(options); this.retryMs = retryMs;
  }
  status() { return { ...this.#worker?.status(), physicalConnected: this.#online, error: this.#failure ?? null }; }
  async #launch() {
    const worker = this.#create(); this.#worker = worker;
    worker.on('event', event => { if (this.#online) this.emit('event', event); });
    worker.on('failure', error => { void this.#offline(error); });
    const metadata = await worker.start();
    if (this.#metadata && JSON.stringify(metadata.layout) !== JSON.stringify(this.#metadata.layout)) throw new Error('Adapter layout changed during reconnect');
    this.#metadata = metadata; return metadata;
  }
  async start() { return this.#launch(); }
  #publish() { this.emit('physical', { connected: this.#online, error: this.#failure }); }
  async #offline(error) {
    if (this.#closed) return;
    this.#online = false; this.#failure = error?.message ?? 'USB device unavailable'; this.#publish();
    this.#schedule();
  }
  #schedule() {
    if (this.#closed || this.#online || this.#retry || this.#connecting || !this.#open) return;
    this.#retry = setTimeout(() => { this.#retry = null; void this.#reconnect(); }, this.retryMs);
    this.#retry.unref?.();
  }
  async #reconnect() {
    if (this.#closed || this.#online || this.#connecting) return;
    this.#connecting = (async () => {
      await this.#worker?.close().catch(error => { if (this.#worker.status().pid) throw error; });
      if (this.#closed) return;
      await this.#launch();
      const result = await this.#worker.request('discover');
      const devices = Array.isArray(result) ? result : result.devices;
      const device = devices.find(item => item.id === this.#device.id) ?? (this.#device.virtual && devices.length === 1 ? devices[0] : null);
      if (!device) throw new Error('Waiting for the selected USB device');
      await this.#worker.request('open', { ...this.#open, deviceId: device.id });
      for (const params of this.#controls.values()) await this.#worker.request('control', params);
      if (this.#frame) await this.#worker.request('frame', this.#frame);
      if (this.#closed) { await this.#worker.close(); return; }
      this.#online = true; this.#failure = null; this.#publish();
    })().catch(error => this.#offline(error)).finally(() => { this.#connecting = null; this.#schedule(); });
    await this.#connecting;
  }
  async request(method, params = {}) {
    if (this.#closed) throw new Error('Adapter is closed');
    if (method === 'describe') return this.#metadata;
    if (method === 'discover') {
      const result = await this.#worker.request(method, params);
      const devices = Array.isArray(result) ? result : result.devices;
      return { devices };
    }
    if (method === 'open') {
      this.#open = params; this.#device = params.virtualDevice;
      try {
        const result = await this.#worker.request(method, params);
        this.#online = true; this.#failure = null; this.#publish(); return result;
      } catch (error) {
        await this.#offline(error);
        return { layout: this.#metadata.layout, warnings: [] };
      }
    }
    if (method === 'frame') this.#frame = structuredClone(params);
    if (method === 'control') this.#controls.set(params.control, structuredClone(params));
    if (!['frame', 'control'].includes(method)) throw new Error('Unsupported virtual adapter operation');
    if (this.#online) {
      try { return await this.#worker.request(method, params); }
      catch (error) { await this.#offline(error); }
    }
    return { submitMs: 0, virtual: true };
  }
  async close() {
    this.#closed = true; clearTimeout(this.#retry); this.#retry = null;
    await this.#connecting; await this.#worker?.close().catch(() => {}); this.#online = false;
  }
}

export function virtualFrameDevice(adapterId, target, metadata = {}, id = `virtual:${adapterId}`) {
  const geometry = target.geometry;
  return { id, adapterId, modelId: metadata.modelId ?? target.id, displayName: target.geometry.productName ?? adapterId,
    virtual: true, capabilities: { keys: geometry.keyCount, dials: geometry.encoderCount, dialPress: geometry.encoderCount ? 'pulse' : 'unsupported', dialRelease: 'unavailable',
      touch: geometry.touchWidth ? { width: geometry.touchWidth, height: geometry.touchHeight } : null,
      displays: [...Array.from({ length: geometry.keyCount }, (_, index) => ({ id: `key:${index}`, width: geometry.keyWidth, height: geometry.keyHeight, format: 'jpeg', partial: false })),
        ...(geometry.touchWidth ? [{ id: 'touch', width: geometry.touchWidth, height: geometry.touchHeight, format: 'jpeg', partial: true }] : []),
        ...target.displays], features: { brightness: { status: 'native' }, sleep: { status: 'native' }, wake: { status: 'native' } } } };
}

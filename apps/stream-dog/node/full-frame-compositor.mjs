import { performance } from 'node:perf_hooks';

function dimension(value, name) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 8192) throw new RangeError(`Invalid ${name}`);
  return value;
}

function colorBytes(color) {
  if (!Array.isArray(color) || color.length !== 3 || color.some(value => !Number.isInteger(value) || value < 0 || value > 255)) {
    throw new TypeError('Color must contain three RGB bytes');
  }
  return color;
}

function fillRgb(buffer, color) {
  for (let offset = 0; offset < buffer.length; offset += 3) {
    buffer[offset] = color[0]; buffer[offset + 1] = color[1]; buffer[offset + 2] = color[2];
  }
}

function paintSurface(canvas, canvasWidth, pixels, width, height, [x, y, targetWidth, targetHeight]) {
  for (let row = 0; row < targetHeight; row++) {
    const sourceY = Math.min(height - 1, Math.floor((row + .5) * height / targetHeight));
    const target = ((y + row) * canvasWidth + x) * 3;
    if (width === targetWidth) {
      pixels.copy(canvas, target, sourceY * width * 3, (sourceY + 1) * width * 3);
      continue;
    }
    for (let column = 0; column < targetWidth; column++) {
      const sourceX = Math.min(width - 1, Math.floor((column + .5) * width / targetWidth));
      const source = (sourceY * width + sourceX) * 3;
      pixels.copy(canvas, target + column * 3, source, source + 3);
    }
  }
}

function assertDecoded(decoded, width, height) {
  if (decoded.width !== width || decoded.height !== height || decoded.channels !== 3 ||
      !Buffer.isBuffer(decoded.data) || decoded.data.length !== width * height * 3) {
    throw new TypeError('Decoded image does not match its declared region');
  }
}

export class FullFrameCompositor {
  #layout; #decode; #strip; #keys = new Map(); #background = [0, 0, 0]; #revision = 0; #overlay = null;
  constructor({ layout, decodeImage }) {
    const value = structuredClone(layout);
    dimension(value?.width, 'canvas width'); dimension(value?.height, 'canvas height');
    const strip = value.strip;
    if (strip) {
      dimension(strip.width, 'strip width'); dimension(strip.height, 'strip height');
      dimension(strip.logicalWidth, 'logical strip width'); dimension(strip.logicalHeight, 'logical strip height');
      if (![strip.x, strip.y].every(number => Number.isSafeInteger(number) && number >= 0) ||
          strip.x + strip.width > value.width || strip.y + strip.height > value.height) throw new TypeError('Invalid display layout or decoder');
    }
    if (value.width * value.height > 16_777_216 || typeof decodeImage !== 'function') throw new TypeError('Invalid display layout or decoder');
    if (value.keys !== undefined && (!Array.isArray(value.keys) || value.keys.length > 256)) throw new TypeError('Invalid key layout');
    let retainedPixels = strip ? strip.logicalWidth * strip.logicalHeight : 0;
    const rectangles = strip ? [[strip.x, strip.y, strip.width, strip.height]] : [];
    for (const key of value.keys ?? []) {
      const rect = key.rect;
      dimension(key.sourceWidth, 'key source width'); dimension(key.sourceHeight, 'key source height');
      if (!/^key:(0|[1-9][0-9]*)$/.test(key.id) || this.#keys.has(key.id) ||
          !Array.isArray(rect) || rect.length !== 4 || !rect.every(Number.isSafeInteger) ||
          rect[0] < 0 || rect[1] < 0 || rect[2] < 1 || rect[3] < 1 ||
          rect[0] + rect[2] > value.width || rect[1] + rect[3] > value.height ||
          rectangles.some(([x, y, width, height]) => rect[0] < x + width && rect[0] + rect[2] > x && rect[1] < y + height && rect[1] + rect[3] > y)) {
        throw new TypeError('Invalid or overlapping key layout');
      }
      retainedPixels += key.sourceWidth * key.sourceHeight;
      if (retainedPixels > 16_777_216) throw new RangeError('Retained display pixels exceed their limit');
      rectangles.push(rect);
      this.#keys.set(key.id, { ...key, data: Buffer.alloc(key.sourceWidth * key.sourceHeight * 3) });
    }
    if (retainedPixels > 16_777_216) throw new RangeError('Retained display pixels exceed their limit');
    this.#layout = value; this.#decode = decodeImage;
    this.#strip = Buffer.alloc(retainedPixels && strip ? strip.logicalWidth * strip.logicalHeight * 3 : 0);
  }
  get revision() { return this.#revision; }
  get layout() { return structuredClone(this.#layout); }
  get overlayActive() { return this.#overlay !== null; }
  #normalChange(changed) {
    const visibleChanged = changed && !this.overlayActive;
    if (visibleChanged) this.#revision++;
    return { changed, visibleChanged, revision: this.#revision, overlayActive: this.overlayActive };
  }
  async applyOverlay(bytes) {
    const decoded = await this.#decode(bytes);
    assertDecoded(decoded, this.#layout.width, this.#layout.height);
    const changed = this.#overlay === null || !this.#overlay.equals(decoded.data);
    if (changed) { this.#overlay = Buffer.from(decoded.data); this.#revision++; }
    return { changed, visibleChanged: changed, revision: this.#revision, overlayActive: true };
  }
  dismissOverlay(backgroundColor) {
    const background = backgroundColor !== undefined ? this.setBackground(backgroundColor) : null;
    const changed = this.overlayActive;
    if (changed) { this.#overlay = null; this.#revision++; }
    return { changed: changed || background?.changed === true, visibleChanged: changed || background?.visibleChanged === true,
      revision: this.#revision, overlayActive: false };
  }
  setBackground(color) {
    colorBytes(color);
    const changed = !color.every((value, index) => value === this.#background[index]);
    if (changed) this.#background = [...color];
    return this.#normalChange(changed);
  }
  async applyTouch(bytes, region) {
    const strip = this.#layout.strip;
    if (!strip) throw new RangeError('Adapter has no touch display');
    const rect = region ?? { x: 0, y: 0, width: strip.logicalWidth, height: strip.logicalHeight };
    if (![rect.x, rect.y].every(value => Number.isSafeInteger(value) && value >= 0) ||
        ![rect.width, rect.height].every(value => Number.isSafeInteger(value) && value > 0) ||
        rect.x + rect.width > strip.logicalWidth || rect.y + rect.height > strip.logicalHeight) {
      throw new RangeError('Touch patch is outside the logical strip');
    }
    const decoded = await this.#decode(bytes);
    assertDecoded(decoded, rect.width, rect.height);
    let changed = false;
    for (let row = 0; row < rect.height; row++) {
      const start = ((rect.y + row) * strip.logicalWidth + rect.x) * 3;
      const source = decoded.data.subarray(row * rect.width * 3, (row + 1) * rect.width * 3);
      if (!source.equals(this.#strip.subarray(start, start + source.length))) {
        source.copy(this.#strip, start); changed = true;
      }
    }
    return this.#normalChange(changed);
  }
  async applyKey(surfaceId, bytes) {
    const key = this.#keys.get(surfaceId);
    if (!key) throw new RangeError('Unknown key surface');
    const decoded = await this.#decode(bytes);
    assertDecoded(decoded, key.sourceWidth, key.sourceHeight);
    const changed = !key.data.equals(decoded.data);
    if (changed) key.data = Buffer.from(decoded.data);
    return this.#normalChange(changed);
  }
  clearKey(surfaceId, color = [0, 0, 0]) {
    colorBytes(color);
    const key = this.#keys.get(surfaceId);
    if (!key) throw new RangeError('Unknown key surface');
    const next = Buffer.alloc(key.data.length); fillRgb(next, color);
    const changed = !next.equals(key.data);
    if (changed) key.data = next;
    return this.#normalChange(changed);
  }
  clearTouch(color = [0, 0, 0]) {
    colorBytes(color);
    const next = Buffer.alloc(this.#strip.length); fillRgb(next, color);
    const changed = !next.equals(this.#strip);
    if (changed) this.#strip = next;
    return this.#normalChange(changed);
  }
  clear(color = [0, 0, 0]) {
    colorBytes(color);
    const previous = this.#revision;
    this.dismissOverlay();
    this.clearTouch(color);
    for (const surfaceId of this.#keys.keys()) this.clearKey(surfaceId, color);
    if (!color.every((value, index) => value === this.#background[index])) {
      this.#background = [...color];
      if (this.#revision === previous) this.#revision++;
    }
    return { changed: this.#revision !== previous, visibleChanged: this.#revision !== previous, revision: this.#revision, overlayActive: false };
  }
  snapshot() {
    const { width, height, strip } = this.#layout;
    const data = this.overlayActive ? Buffer.from(this.#overlay) : Buffer.alloc(width * height * 3);
    if (!this.overlayActive) {
      fillRgb(data, this.#background);
      for (const key of this.#keys.values()) paintSurface(data, width, key.data, key.sourceWidth, key.sourceHeight, key.rect);
      // The adapter declares physical placement; host pixels remain one continuous logical strip.
      if (strip) paintSurface(data, width, this.#strip, strip.logicalWidth, strip.logicalHeight, [strip.x, strip.y, strip.width, strip.height]);
    }
    return { revision: this.#revision, width, height, data, overlayActive: this.overlayActive,
      strip: strip ? { width: strip.logicalWidth, height: strip.logicalHeight, data: Buffer.from(this.#strip) } : null };
  }
}

export class LatestFrameScheduler {
  #snapshot; #submit; #onError; #interval; #dirty = false; #active = null; #timer = null;
  #paused = false; #barriers = 0; #closed = false; #error = null; #lastStart = -Infinity;
  #lastRevision = null; #waiters = []; #control = Promise.resolve();
  constructor({ snapshot, submit, maxFps = 20, onError = () => {} }) {
    if (typeof snapshot !== 'function' || typeof submit !== 'function' || typeof onError !== 'function' ||
        !Number.isFinite(maxFps) || maxFps <= 0 || maxFps > 120) throw new TypeError('Invalid frame scheduler options');
    this.#snapshot = snapshot; this.#submit = submit; this.#onError = onError; this.#interval = 1000 / maxFps;
  }
  request() {
    if (this.#closed) return;
    this.#dirty = true; this.#kick();
  }
  setMaxFps(value) {
    if (!Number.isFinite(value) || value < 1 || value > 120) throw new RangeError('Invalid maximum frame rate');
    this.#interval = 1000 / value;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null; this.#kick();
  }
  invalidate() { if (!this.#closed) { this.#lastRevision = null; this.request(); } }
  #settle() {
    if (!this.#error && (this.#active || this.#dirty || this.#timer)) return;
    const waiters = this.#waiters.splice(0);
    for (const waiter of waiters) this.#error ? waiter.reject(this.#error) : waiter.resolve();
  }
  #fail(error) {
    this.#error ??= error; this.#paused = true;
    try { this.#onError(error); } catch { /* The owner still receives the rejected flush. */ }
    this.#settle();
  }
  #kick() {
    if (this.#closed || this.#paused || this.#barriers || this.#error || this.#active || this.#timer || !this.#dirty) {
      this.#settle(); return;
    }
    const wait = Math.max(0, this.#lastStart + this.#interval - performance.now());
    if (wait > 0) { this.#timer = setTimeout(() => { this.#timer = null; this.#kick(); }, wait); return; }
    this.#dirty = false; this.#lastStart = performance.now();
    const active = Promise.resolve().then(() => this.#snapshot()).then(async frame => {
      if (frame.revision === this.#lastRevision) return;
      await this.#submit(frame); this.#lastRevision = frame.revision;
    }).catch(error => this.#fail(error)).finally(() => {
      if (this.#active === active) this.#active = null;
      this.#kick(); this.#settle();
    });
    this.#active = active;
  }
  flush() {
    if (this.#error) return Promise.reject(this.#error);
    if (!this.#active && !this.#dirty && !this.#timer) return Promise.resolve();
    return new Promise((resolve, reject) => this.#waiters.push({ resolve, reject }));
  }
  pause() {
    this.#paused = true;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
  }
  resume() { if (!this.#closed) { this.#paused = false; this.#kick(); } }
  barrier(action) {
    if (typeof action !== 'function') return Promise.reject(new TypeError('Barrier action must be a function'));
    this.#barriers++;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
    const operation = this.#control.then(async () => {
      await this.#active;
      if (this.#closed) throw new Error('Frame scheduler is closed');
      return action();
    });
    this.#control = operation.catch(error => this.#fail(error)).finally(() => {
      this.#barriers--; this.#kick();
    });
    return operation;
  }
  async close() {
    this.#closed = true; this.#dirty = false;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
    await this.#active; await this.#control; this.#settle();
  }
  getStatus() { return { dirty: this.#dirty, inFlight: Boolean(this.#active), paused: this.#paused,
    lastSubmittedRevision: this.#lastRevision, closed: this.#closed, error: this.#error?.message ?? null }; }
}

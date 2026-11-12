import { parentPort, workerData } from 'node:worker_threads';
import { performance } from 'node:perf_hooks';
import sharp from 'sharp';
import { FullFrameCompositor } from './full-frame-compositor.mjs';

sharp.cache({ memory: 16, files: 0, items: 16 });
sharp.concurrency(1);
const metrics = { patches: 0, changedPatches: 0, keyUpdates: 0, changedKeyUpdates: 0, overlayUpdates: 0, overlayRestores: 0, renders: 0, decodeMs: 0, renderMs: 0 };
let sourceReceivedAt = null;
const compositor = new FullFrameCompositor({ layout: workerData.layout, decodeImage: async bytes => {
  const started = performance.now();
  const { data, info } = await sharp(Buffer.from(bytes), { limitInputPixels: 16_777_216, failOn: 'warning' })
    .toColourspace('srgb').removeAlpha().raw().toBuffer({ resolveWithObject: true });
  metrics.decodeMs += performance.now() - started;
  return { data, width: info.width, height: info.height, channels: info.channels };
} });

function jpeg(data, width, height, rotation = 0) {
  return sharp(data, { raw: { width, height, channels: 3 } }).rotate(rotation)
    .jpeg({ quality: 90, chromaSubsampling: '4:4:4', progressive: false }).toBuffer();
}

async function handle({ id, method, params = {} }) {
  if (method === 'touch') {
    metrics.patches++;
    const result = await compositor.applyTouch(Buffer.from(params.data), params.region);
    if (result.changed) metrics.changedPatches++;
    if (result.visibleChanged) sourceReceivedAt = params.receivedAt ?? null;
    return result;
  }
  if (method === 'key') {
    if (params.region !== undefined) throw new TypeError('Key images must contain a complete logical key');
    metrics.keyUpdates++;
    const result = await compositor.applyKey(params.surface, Buffer.from(params.data));
    if (result.changed) metrics.changedKeyUpdates++;
    if (result.visibleChanged) sourceReceivedAt = params.receivedAt ?? null;
    return result;
  }
  if (method === 'overlay') {
    if (params.region !== undefined) throw new TypeError('An overlay must cover the entire canvas');
    const result = await compositor.applyOverlay(Buffer.from(params.data));
    metrics.overlayUpdates++;
    if (result.visibleChanged) sourceReceivedAt = params.receivedAt ?? null;
    return result;
  }
  if (method === 'restore') {
    const result = compositor.dismissOverlay(params.backgroundColor);
    if (result.changed) metrics.overlayRestores++;
    sourceReceivedAt = params.receivedAt ?? null;
    return result;
  }
  if (method === 'clear') {
    const result = params.surface === 'touch' ? compositor.clearTouch(params.color)
      : params.surface?.startsWith('key:') ? compositor.clearKey(params.surface, params.color) : compositor.clear(params.color);
    if (result.visibleChanged) sourceReceivedAt = params.receivedAt ?? null;
    return result;
  }
  if (method === 'stats') return { ...metrics, revision: compositor.revision, overlayActive: compositor.overlayActive };
  if (method !== 'render') throw new TypeError(`Unknown renderer method: ${method}`);
  const started = performance.now();
  const frame = compositor.snapshot();
  const [wireJpeg, frameJpeg] = await Promise.all([
    jpeg(frame.data, frame.width, frame.height, workerData.layout.rotation ?? 0),
    workerData.includePreview === false ? undefined : jpeg(frame.data, frame.width, frame.height),
  ]);
  const renderMs = performance.now() - started;
  metrics.renders++; metrics.renderMs += renderMs;
  return { revision: frame.revision, width: frame.width, height: frame.height, wireJpeg, frameJpeg, sourceReceivedAt, renderMs, overlayActive: frame.overlayActive };
}

// All patches and controls reach the canvas in arrival order, regardless of decoder completion time.
let operations = Promise.resolve();
parentPort.on('message', message => {
  operations = operations.then(async () => {
    try { parentPort.postMessage({ id: message.id, result: await handle(message) }); }
    catch (error) { parentPort.postMessage({ id: message.id, error: { message: error.message } }); }
  });
});
parentPort.postMessage({ event: 'ready' });

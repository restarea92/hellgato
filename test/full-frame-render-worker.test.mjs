import test from 'node:test';
import assert from 'node:assert/strict';
import { Worker } from 'node:worker_threads';
import sharp from 'sharp';

const layout = {
  width: 800, height: 480, rotation: 180,
  keys: Array.from({ length: 10 }, (_, index) => ({ id: `key:${index}`,
    rect: [7 + index % 5 * 169, 5 + Math.floor(index / 5) * 174, 112, 112], sourceWidth: 120, sourceHeight: 120 })),
  strip: { x: 0, y: 353, width: 800, height: 112, logicalWidth: 800, logicalHeight: 100 },
};
const black = [0, 0, 0], red = [220, 20, 10], green = [10, 210, 30], blue = [20, 30, 230];

function renderer(options = {}) {
  const worker = new Worker(new URL('../apps/stream-dog/node/full-frame-render-worker.mjs', import.meta.url), { workerData: { layout, ...options } });
  let sequence = 0, closing = false;
  const pending = new Map();
  let readyResolve, readyReject;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  const fail = error => {
    readyReject(error);
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(error); }
    pending.clear();
  };
  worker.on('error', fail);
  worker.on('exit', code => { if (!closing) fail(new Error(`Render worker exited: ${code}`)); });
  worker.on('message', value => {
    if (value.event === 'ready') { readyResolve(); return; }
    const item = pending.get(value.id);
    if (!item) { fail(new Error('Unexpected render worker response')); return; }
    pending.delete(value.id); clearTimeout(item.timer);
    if (value.error) item.reject(new Error(value.error.message));
    else item.resolve(value.result);
  });
  return {
    ready,
    request(method, params = {}) {
      const id = ++sequence;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Render worker request timed out: ${method}`)); }, 5000);
        pending.set(id, { resolve, reject, timer });
        worker.postMessage({ id, method, params });
      });
    },
    async close() { closing = true; fail(new Error('Render worker stopped')); await worker.terminate(); },
  };
}

function jpeg(width, height, color) {
  return sharp({ create: { width, height, channels: 3, background: { r: color[0], g: color[1], b: color[2] } } })
    .jpeg({ quality: 100, chromaSubsampling: '4:4:4', progressive: false }).toBuffer();
}

async function decode(bytes) {
  const { data, info } = await sharp(Buffer.from(bytes)).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  return { data, width: info.width, height: info.height, channels: info.channels };
}

function near(frame, x, y, expected) {
  const offset = (y * frame.width + x) * 3;
  const actual = [...frame.data.subarray(offset, offset + 3)];
  assert.ok(actual.every((value, index) => Math.abs(value - expected[index]) <= 8),
    `Expected RGB near ${expected} at ${x},${y}, received ${actual}`);
}

test('queued real JPEG patches render a complete preview and one correctly rotated wire frame', { timeout: 10000 }, async () => {
  const client = renderer();
  try {
    await client.ready;
    const [base, right, overlap] = await Promise.all([jpeg(800, 100, red), jpeg(400, 100, green), jpeg(200, 50, blue)]);
    const updates = [
      client.request('touch', { data: base }),
      client.request('touch', { data: right, region: { x: 400, y: 0, width: 400, height: 100 } }),
      client.request('touch', { data: overlap, region: { x: 300, y: 25, width: 200, height: 50 } }),
    ];
    const rendered = await client.request('render');
    assert.ok((await Promise.all(updates)).every(result => result.changed));
    const [preview, wire] = await Promise.all([decode(rendered.frameJpeg), decode(rendered.wireJpeg)]);
    assert.deepEqual([preview.width, preview.height, preview.channels], [800, 480, 3]);
    assert.deepEqual([wire.width, wire.height, wire.channels], [800, 480, 3]);
    assert.equal(Object.hasOwn(rendered, 'region'), false);
    for (const [x, y, color] of [[100, 409, red], [450, 409, blue], [700, 409, green], [450, 365, green], [100, 100, black]]) {
      near(preview, x, y, color);
      near(wire, 799 - x, 479 - y, color);
    }
    near(preview, 100, 353, red);
    near(preview, 100, 464, red);
    near(preview, 100, 340, black);
    near(preview, 100, 477, black);
    const stats = await client.request('stats');
    assert.equal(stats.patches, 3);
    assert.equal(stats.changedPatches, 3);
    assert.equal(stats.renders, 1);
    assert.equal(stats.revision, rendered.revision);
    assert.ok(stats.decodeMs >= 0 && stats.renderMs >= 0);
  } finally { await client.close(); }
});

test('management runtime encodes only the wire frame when preview is disabled', async () => {
  const client = renderer({ includePreview: false });
  try {
    await client.ready;
    await client.request('touch', { data: await jpeg(800, 100, red) });
    const result = await client.request('render');
    assert.equal(result.frameJpeg, undefined);
    const wire = await decode(result.wireJpeg);
    assert.deepEqual([wire.width, wire.height], [800, 480]);
    near(wire, 799 - 400, 479 - 409, red);
  } finally { await client.close(); }
});

test('bad bounds, mismatched JPEG dimensions, and decode failures leave the canvas intact and recoverable', { timeout: 10000 }, async () => {
  const client = renderer();
  try {
    await client.ready;
    const [base, patch, wrongSize] = await Promise.all([jpeg(800, 100, red), jpeg(200, 100, green), jpeg(199, 100, blue)]);
    await client.request('touch', { data: base });
    const before = await client.request('render');
    const failures = await Promise.allSettled([
      client.request('touch', { data: patch, region: { x: 700, y: 0, width: 200, height: 100 } }),
      client.request('touch', { data: wrongSize, region: { x: 0, y: 0, width: 200, height: 100 } }),
      client.request('touch', { data: Buffer.from('not an image') }),
    ]);
    assert.ok(failures.every(result => result.status === 'rejected'));
    const unchanged = await client.request('render');
    assert.equal(unchanged.revision, before.revision);
    assert.deepEqual((await decode(unchanged.frameJpeg)).data, (await decode(before.frameJpeg)).data);
    const accepted = client.request('touch', { data: patch, region: { x: 0, y: 0, width: 200, height: 100 } });
    const recovered = await client.request('render');
    assert.equal((await accepted).changed, true);
    assert.ok(recovered.revision > before.revision);
    const preview = await decode(recovered.frameJpeg);
    near(preview, 100, 409, green);
    near(preview, 300, 409, red);
    near(preview, 100, 100, black);
  } finally { await client.close(); }
});

test('duplicate JPEGs keep their revision and queued clear controls precede subsequent patches', { timeout: 10000 }, async () => {
  const client = renderer();
  try {
    await client.ready;
    const [base, greenPatch, redPatch] = await Promise.all([jpeg(800, 100, red), jpeg(200, 100, green), jpeg(100, 100, red)]);
    const first = await client.request('touch', { data: base });
    const duplicate = await client.request('touch', { data: base });
    assert.equal(duplicate.changed, false);
    assert.equal(duplicate.revision, first.revision);
    const clearAll = client.request('clear', { color: blue });
    const patchAfterClear = client.request('touch', { data: greenPatch, region: { x: 200, y: 0, width: 200, height: 100 } });
    const whole = await client.request('render');
    await Promise.all([clearAll, patchAfterClear]);
    const wholePreview = await decode(whole.frameJpeg);
    near(wholePreview, 100, 100, blue);
    near(wholePreview, 100, 409, blue);
    near(wholePreview, 300, 409, green);
    const clearStrip = client.request('clear', { surface: 'touch' });
    const lastPatch = client.request('touch', { data: redPatch, region: { x: 700, y: 0, width: 100, height: 100 } });
    const stripOnly = await client.request('render');
    await Promise.all([clearStrip, lastPatch]);
    const stripPreview = await decode(stripOnly.frameJpeg);
    near(stripPreview, 100, 100, blue);
    near(stripPreview, 300, 409, black);
    near(stripPreview, 750, 409, red);
    const stats = await client.request('stats');
    assert.equal(stats.patches, 4);
    assert.equal(stats.changedPatches, 3);
    assert.equal(stats.renders, 2);
  } finally { await client.close(); }
});

test('real key JPEGs compose with strip patches and clear in order inside full output frames', { timeout: 10000 }, async () => {
  const client = renderer();
  try {
    await client.ready;
    const [key, strip] = await Promise.all([jpeg(120, 120, red), jpeg(800, 100, green)]);
    const updates = Array.from({ length: 10 }, (_, index) => client.request('key', { surface: `key:${index}`, data: key, receivedAt: 7 }));
    updates.push(client.request('touch', { data: strip, receivedAt: 8 }));
    const first = await client.request('render');
    await Promise.all(updates);
    const [preview, wire] = await Promise.all([decode(first.frameJpeg), decode(first.wireJpeg)]);
    for (const [x, y] of [[60, 60], [740, 230]]) {
      near(preview, x, y, red); near(wire, 799 - x, 479 - y, red);
    }
    near(preview, 400, 409, green);
    near(preview, 150, 150, black);
    assert.equal(first.sourceReceivedAt, 8);
    await assert.rejects(client.request('key', { surface: 'key:0', data: key, region: { x: 0, y: 0, width: 120, height: 120 } }), /complete logical key/);
    await assert.rejects(client.request('key', { surface: 'key:10', data: key }), /Unknown key/);
    await assert.rejects(client.request('key', { surface: 'key:0', data: strip }), /declared region/);
    await client.request('clear', { surface: 'key:0', color: blue });
    const second = await decode((await client.request('render')).frameJpeg);
    near(second, 60, 60, blue); near(second, 740, 230, red); near(second, 400, 409, green);
    const stats = await client.request('stats');
    assert.equal(stats.changedKeyUpdates, 10);
  } finally { await client.close(); }
});

test('full-screen saver JPEG covers preview and wire output while hidden surface updates restore intact', { timeout: 10000 }, async () => {
  const client = renderer();
  try {
    await client.ready;
    const [key, strip, overlay, updatedKey, updatedStrip] = await Promise.all([
      jpeg(120, 120, red), jpeg(800, 100, green), jpeg(800, 480, blue),
      jpeg(120, 120, green), jpeg(200, 100, red),
    ]);
    await client.request('key', { surface: 'key:0', data: key });
    await client.request('touch', { data: strip });
    await client.request('overlay', { data: overlay, receivedAt: 10 });
    const covered = await client.request('render');
    assert.equal(covered.overlayActive, true);
    const [preview, wire] = await Promise.all([decode(covered.frameJpeg), decode(covered.wireJpeg)]);
    for (const [x, y] of [[0, 0], [60, 60], [400, 409], [799, 479]]) {
      near(preview, x, y, blue); near(wire, 799 - x, 479 - y, blue);
    }
    const keyResult = await client.request('key', { surface: 'key:0', data: updatedKey, receivedAt: 11 });
    const stripResult = await client.request('touch', { data: updatedStrip,
      region: { x: 600, y: 0, width: 200, height: 100 }, receivedAt: 12 });
    assert.equal(keyResult.visibleChanged, false);
    assert.equal(stripResult.visibleChanged, false);
    const stillCovered = await client.request('render');
    assert.equal(stillCovered.revision, covered.revision);
    assert.deepEqual(stillCovered.frameJpeg, covered.frameJpeg);
    assert.equal(stillCovered.sourceReceivedAt, 10);
    await assert.rejects(client.request('overlay', { data: key }), /declared region/);
    await client.request('restore', { backgroundColor: black, receivedAt: 13 });
    const restored = await client.request('render');
    assert.equal(restored.overlayActive, false);
    assert.equal(restored.sourceReceivedAt, 13);
    const restoredPreview = await decode(restored.frameJpeg);
    near(restoredPreview, 60, 60, green);
    near(restoredPreview, 400, 409, green);
    near(restoredPreview, 700, 409, red);
    near(restoredPreview, 150, 150, black);
    assert.deepEqual([restored.width, restored.height], [800, 480]);
    assert.equal(Object.hasOwn(restored, 'region'), false);
    const stats = await client.request('stats');
    assert.equal(stats.overlayUpdates, 1);
    assert.equal(stats.overlayRestores, 1);
  } finally { await client.close(); }
});

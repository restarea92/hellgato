import test from 'node:test';
import assert from 'node:assert/strict';
import { FullFrameCompositor, LatestFrameScheduler } from '../apps/stream-dog/node/full-frame-compositor.mjs';

const layout = {
  width: 800, height: 480, rotation: 180,
  strip: { x: 0, y: 353, width: 800, height: 112, logicalWidth: 800, logicalHeight: 100 },
};
const black = [0, 0, 0], red = [220, 20, 10], green = [10, 210, 30], blue = [20, 30, 230];

function image(width, height, color) {
  const bytes = Buffer.alloc(8 + width * height * 3);
  bytes.writeUInt32LE(width, 0);
  bytes.writeUInt32LE(height, 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      bytes.set(typeof color === 'function' ? color(x, y) : color, 8 + (y * width + x) * 3);
    }
  }
  return bytes;
}

async function decodeImage(bytes) {
  return { width: bytes.readUInt32LE(0), height: bytes.readUInt32LE(4), channels: 3, data: bytes.subarray(8) };
}

const compositor = () => new FullFrameCompositor({ layout, decodeImage });
const pixel = (frame, x, y) => [...frame.data.subarray((y * frame.width + x) * 3, (y * frame.width + x) * 3 + 3)];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const keyLayout = () => ({ ...layout, keys: Array.from({ length: 10 }, (_, index) => ({
  id: `key:${index}`, rect: [7 + index % 5 * 169, 5 + Math.floor(index / 5) * 174, 112, 112],
  sourceWidth: 120, sourceHeight: 120,
})) });

test('a key-only independent adapter needs no invented touch strip or fixed canvas dimensions', async () => {
  const display = new FullFrameCompositor({ layout: { width: 240, height: 120, keys: [
    { id: 'key:0', rect: [0, 0, 100, 100], sourceWidth: 100, sourceHeight: 100 },
    { id: 'key:1', rect: [140, 0, 100, 100], sourceWidth: 100, sourceHeight: 100 },
  ] }, decodeImage });
  await display.applyKey('key:0', image(100, 100, red));
  assert.equal(display.snapshot().strip, null);
  assert.deepEqual(pixel(display.snapshot(), 10, 10), red);
  assert.deepEqual(pixel(display.snapshot(), 110, 10), black);
  await assert.rejects(display.applyTouch(image(100, 100, blue)), /no touch display/);
  await display.applyOverlay(image(240, 120, green));
  assert.deepEqual(pixel(display.snapshot(), 110, 10), green);
  display.dismissOverlay();
  assert.deepEqual(pixel(display.snapshot(), 10, 10), red);
});

test('an opaque full-canvas overlay preserves hidden key and strip updates until restoration', async () => {
  const display = new FullFrameCompositor({ layout: keyLayout(), decodeImage });
  await display.applyKey('key:0', image(120, 120, red));
  await display.applyKey('key:9', image(120, 120, green));
  await display.applyTouch(image(800, 100, blue));
  const first = await display.applyOverlay(image(800, 480, (x, y) => x < 400 && y < 240 ? red : blue));
  assert.equal(first.overlayActive, true);
  const covered = display.snapshot();
  assert.deepEqual(pixel(covered, 10, 10), red);
  assert.deepEqual(pixel(covered, 700, 230), blue);
  assert.deepEqual(pixel(covered, 799, 479), blue);
  const hiddenKey = await display.applyKey('key:0', image(120, 120, green));
  const hiddenStrip = await display.applyTouch(image(200, 100, red), { x: 600, y: 0, width: 200, height: 100 });
  display.clearKey('key:9', red);
  assert.equal(hiddenKey.changed, true);
  assert.equal(hiddenKey.visibleChanged, false);
  assert.equal(hiddenStrip.visibleChanged, false);
  assert.equal(display.revision, first.revision);
  assert.deepEqual(display.snapshot().data, covered.data);
  const restored = display.dismissOverlay(black);
  assert.equal(restored.visibleChanged, true);
  assert.equal(restored.overlayActive, false);
  const normal = display.snapshot();
  assert.deepEqual(pixel(normal, 60, 60), green);
  assert.deepEqual(pixel(normal, 740, 230), red);
  assert.deepEqual(pixel(normal, 100, 409), blue);
  assert.deepEqual(pixel(normal, 700, 409), red);
  assert.deepEqual(pixel(normal, 150, 150), black);
  assert.deepEqual(pixel(covered, 60, 60), red);
  assert.equal(display.dismissOverlay().changed, false);
});

test('invalid overlays cannot replace normal pixels and global fills explicitly replace the whole stack', async () => {
  const display = new FullFrameCompositor({ layout: keyLayout(), decodeImage });
  await display.applyKey('key:0', image(120, 120, red));
  const normal = display.snapshot();
  await assert.rejects(display.applyOverlay(image(800, 100, blue)), /declared region/);
  assert.equal(display.overlayActive, false);
  assert.deepEqual(display.snapshot().data, normal.data);
  await display.applyOverlay(image(800, 480, blue));
  const duplicate = await display.applyOverlay(image(800, 480, blue));
  assert.equal(duplicate.visibleChanged, false);
  display.clear(green);
  assert.equal(display.overlayActive, false);
  assert.ok(display.snapshot().data.every((byte, index) => byte === green[index % 3]));
  const restore = display.dismissOverlay(black);
  assert.equal(restore.visibleChanged, true);
  assert.deepEqual(pixel(display.snapshot(), 150, 150), black);
  assert.deepEqual(pixel(display.snapshot(), 60, 60), green);
});

test('ten independent logical key images and the strip share one complete physical canvas', async () => {
  const display = new FullFrameCompositor({ layout: keyLayout(), decodeImage });
  for (let index = 0; index < 10; index++) {
    await display.applyKey(`key:${index}`, image(120, 120, index % 2 ? green : red));
  }
  await display.applyTouch(image(800, 100, blue));
  const frame = display.snapshot();
  for (let index = 0; index < 10; index++) {
    const [x, y, width, height] = keyLayout().keys[index].rect;
    const color = index % 2 ? green : red;
    assert.deepEqual(pixel(frame, x, y), color);
    assert.deepEqual(pixel(frame, x + width - 1, y + height - 1), color);
    assert.deepEqual(pixel(frame, x + width, y + 50), black);
  }
  assert.deepEqual(pixel(frame, 400, 409), blue);
  assert.deepEqual(pixel(frame, 100, 150), black);
  assert.deepEqual([frame.width, frame.height, frame.data.length], [800, 480, 800 * 480 * 3]);
  assert.equal(Object.hasOwn(frame, 'region'), false);
});

test('key scaling preserves orientation and key clears do not erase other surfaces', async () => {
  const display = new FullFrameCompositor({ layout: keyLayout(), decodeImage });
  const source = image(120, 120, (x, y) => y < 60 ? x < 60 ? red : green : blue);
  await display.applyKey('key:0', source);
  await display.applyKey('key:9', image(120, 120, green));
  await display.applyTouch(image(800, 100, red));
  const before = display.snapshot();
  assert.deepEqual(pixel(before, 10, 10), red);
  assert.deepEqual(pixel(before, 115, 10), green);
  assert.deepEqual(pixel(before, 10, 110), blue);
  assert.equal((await display.applyKey('key:0', source)).changed, false);
  source.fill(0);
  assert.deepEqual(pixel(display.snapshot(), 10, 10), red);
  await display.applyKey('key:1', image(120, 120, (x, y) => x === 119 || y === 119 ? blue : red));
  const border = display.snapshot();
  assert.deepEqual(pixel(border, 176, 5), red);
  assert.deepEqual(pixel(border, 287, 5), blue);
  assert.deepEqual(pixel(border, 176, 116), blue);
  display.clearKey('key:0');
  let after = display.snapshot();
  assert.deepEqual(pixel(after, 10, 10), black);
  assert.deepEqual(pixel(after, 700, 200), green);
  assert.deepEqual(pixel(after, 100, 409), red);
  assert.deepEqual(pixel(before, 10, 10), red);
  display.clear(blue);
  after = display.snapshot();
  assert.deepEqual(pixel(after, 10, 10), blue);
  assert.deepEqual(pixel(after, 700, 200), blue);
  assert.deepEqual(pixel(after, 100, 409), blue);
  assert.deepEqual(pixel(after, 150, 150), blue);
});

test('invalid key frames and adapter rectangles leave valid key and strip state untouched', async () => {
  const display = new FullFrameCompositor({ layout: keyLayout(), decodeImage });
  await display.applyKey('key:0', image(120, 120, red));
  const before = display.snapshot();
  await assert.rejects(display.applyKey('key:0', image(112, 112, green)), /declared region/);
  await assert.rejects(display.applyKey('key:10', image(120, 120, green)), /Unknown key/);
  assert.throws(() => display.clearKey('key:10'), /Unknown key/);
  assert.equal(display.revision, before.revision);
  assert.deepEqual(display.snapshot().data, before.data);
  for (const mutate of [
    value => { value.keys[0].rect = [-1, 0, 112, 112]; },
    value => { value.keys[0].rect = [700, 0, 112, 112]; },
    value => { value.keys[0].rect = [0, 353, 112, 112]; },
    value => { value.keys[1].rect = [...value.keys[0].rect]; },
    value => { value.keys[1].id = 'key:0'; },
  ]) {
    const invalid = keyLayout(); mutate(invalid);
    assert.throws(() => new FullFrameCompositor({ layout: invalid, decodeImage }), /key layout/);
  }
});

test('disjoint and overlapping touch patches compose into one complete retained strip', async () => {
  const display = compositor();
  await display.applyTouch(image(800, 100, black));
  await display.applyTouch(image(400, 100, red), { x: 0, y: 0, width: 400, height: 100 });
  await display.applyTouch(image(400, 100, green), { x: 400, y: 0, width: 400, height: 100 });
  await display.applyTouch(image(300, 50, blue), { x: 250, y: 25, width: 300, height: 50 });
  const frame = display.snapshot();
  assert.deepEqual(pixel(frame.strip, 100, 50), red);
  assert.deepEqual(pixel(frame.strip, 300, 50), blue);
  assert.deepEqual(pixel(frame.strip, 500, 50), blue);
  assert.deepEqual(pixel(frame.strip, 700, 50), green);
  assert.deepEqual(pixel(frame.strip, 300, 10), red);
  assert.deepEqual(pixel(frame.strip, 500, 90), green);
  assert.equal(frame.width, 800);
  assert.equal(frame.height, 480);
  assert.equal(frame.data.length, 800 * 480 * 3);
  assert.equal(Object.hasOwn(frame, 'region'), false);
});

test('the whole host strip maps to the calibrated physical strip without rotating the snapshot', async () => {
  const display = compositor();
  await display.applyTouch(image(800, 100, (x, y) => y === 0 ? red : y === 99 ? green : x < 400 ? blue : black));
  const frame = display.snapshot();
  assert.deepEqual(pixel(frame, 0, 353), red);
  assert.deepEqual(pixel(frame, 799, 353), red);
  assert.deepEqual(pixel(frame, 0, 464), green);
  assert.deepEqual(pixel(frame, 799, 464), green);
  assert.deepEqual(pixel(frame, 200, 409), blue);
  assert.deepEqual(pixel(frame, 600, 409), black);
  assert.deepEqual(pixel(frame, 200, 352), black);
  assert.deepEqual(pixel(frame, 200, 465), black);
  assert.deepEqual(pixel(frame, 0, 0), black);
});

test('snapshots cannot be changed by later patches or mutation of returned buffers', async () => {
  const display = compositor();
  await display.applyTouch(image(800, 100, red));
  const first = display.snapshot();
  const oldRevision = first.revision;
  await display.applyTouch(image(800, 100, green));
  const second = display.snapshot();
  assert.ok(second.revision > oldRevision);
  assert.deepEqual(pixel(first, 100, 400), red);
  assert.deepEqual(pixel(first.strip, 100, 50), red);
  assert.deepEqual(pixel(second, 100, 400), green);
  first.data.fill(255);
  first.strip.data.fill(255);
  second.data.fill(255);
  second.strip.data.fill(255);
  const retained = display.snapshot();
  assert.deepEqual(pixel(retained, 100, 400), green);
  assert.deepEqual(pixel(retained.strip, 100, 50), green);
});

test('invalid patch dimensions and bounds cannot corrupt the retained canvas', async () => {
  const display = compositor();
  await display.applyTouch(image(800, 100, red));
  const before = display.snapshot();
  const invalid = [
    [image(199, 100, green), { x: 0, y: 0, width: 200, height: 100 }],
    [image(200, 100, green), { x: 700, y: 0, width: 200, height: 100 }],
    [image(200, 100, green), { x: -1, y: 0, width: 200, height: 100 }],
    [image(200, 100, green), { x: 0, y: 1, width: 200, height: 100 }],
    [image(799, 100, green), undefined],
  ];
  for (const [bytes, region] of invalid) await assert.rejects(async () => display.applyTouch(bytes, region));
  const after = display.snapshot();
  assert.equal(after.revision, before.revision);
  assert.deepEqual(after.data, before.data);
  assert.deepEqual(after.strip.data, before.strip.data);
});

test('whole and strip-only clears preserve the difference between host strip and physical canvas', async () => {
  const display = compositor();
  display.clear(red);
  assert.deepEqual(pixel(display.snapshot(), 100, 100), red);
  assert.deepEqual(pixel(display.snapshot().strip, 100, 50), red);
  display.clearTouch(green);
  const stripCleared = display.snapshot();
  assert.deepEqual(pixel(stripCleared, 100, 100), red);
  assert.deepEqual(pixel(stripCleared, 100, 400), green);
  assert.deepEqual(pixel(stripCleared.strip, 100, 50), green);
  display.clear();
  const cleared = display.snapshot();
  assert.ok(cleared.data.every(value => value === 0));
  assert.ok(cleared.strip.data.every(value => value === 0));
});

test('an identical patch does not create a new frame revision', async () => {
  const display = compositor();
  const first = await display.applyTouch(image(800, 100, red));
  const repeated = await display.applyTouch(image(800, 100, red));
  assert.equal(first.changed, true);
  assert.equal(repeated.changed, false);
  assert.equal(repeated.revision, first.revision);
  assert.equal(display.snapshot().revision, first.revision);
});

test('output stays single-flight and sends the latest composed state after a blocked transfer', { timeout: 5000 }, async () => {
  const display = compositor();
  const submitted = [], gate = deferred(), started = deferred();
  let active = 0, maximumActive = 0;
  const scheduler = new LatestFrameScheduler({
    snapshot: () => display.snapshot(), maxFps: 120,
    submit: async frame => {
      active++; maximumActive = Math.max(maximumActive, active); submitted.push(frame);
      try { if (submitted.length === 1) { started.resolve(); await gate.promise; } }
      finally { active--; }
    },
  });
  try {
    await display.applyTouch(image(800, 100, black));
    scheduler.request();
    await started.promise;
    for (let x = 0; x < 100; x++) {
      await display.applyTouch(image(1, 100, red), { x, y: 0, width: 1, height: 100 });
      scheduler.request();
    }
    await display.applyTouch(image(400, 100, green), { x: 400, y: 0, width: 400, height: 100 });
    scheduler.request();
    const expected = display.snapshot();
    assert.equal(submitted.length, 1);
    assert.deepEqual(pixel(submitted[0].strip, 10, 50), black);
    gate.resolve();
    await scheduler.flush();
    assert.equal(maximumActive, 1);
    assert.equal(submitted.length, 2);
    assert.equal(submitted[1].revision, expected.revision);
    assert.deepEqual(submitted[1].data, expected.data);
    for (const frame of submitted) {
      assert.equal(frame.width, 800);
      assert.equal(frame.height, 480);
      assert.equal(frame.data.length, 800 * 480 * 3);
      assert.equal(Object.hasOwn(frame, 'region'), false);
    }
  } finally {
    gate.resolve();
    await scheduler.close();
  }
});

test('a control barrier waits for submitted pixels and resumes with retained latest pixels', { timeout: 5000 }, async () => {
  const display = compositor();
  const frameGate = deferred(), frameStarted = deferred(), controlGate = deferred(), controlStarted = deferred();
  const order = [];
  const scheduler = new LatestFrameScheduler({
    snapshot: () => display.snapshot(), maxFps: 120,
    submit: async frame => {
      order.push({ type: 'frame', frame });
      if (order.length === 1) { frameStarted.resolve(); await frameGate.promise; }
    },
  });
  try {
    await display.applyTouch(image(800, 100, red));
    scheduler.request();
    await frameStarted.promise;
    await display.applyTouch(image(800, 100, green));
    scheduler.request();
    const barrier = scheduler.barrier(async () => {
      order.push({ type: 'control' }); controlStarted.resolve(); await controlGate.promise;
    });
    await tick();
    assert.deepEqual(order.map(item => item.type), ['frame']);
    frameGate.resolve();
    await controlStarted.promise;
    await display.applyTouch(image(200, 100, blue), { x: 600, y: 0, width: 200, height: 100 });
    scheduler.request();
    await tick();
    assert.deepEqual(order.map(item => item.type), ['frame', 'control']);
    controlGate.resolve();
    await barrier;
    await scheduler.flush();
    assert.deepEqual(order.map(item => item.type), ['frame', 'control', 'frame']);
    assert.deepEqual(pixel(order[2].frame.strip, 100, 50), green);
    assert.deepEqual(pixel(order[2].frame.strip, 700, 50), blue);
  } finally {
    frameGate.resolve(); controlGate.resolve();
    await scheduler.close();
  }
});

test('pause retains updates and close waits for the active frame without sending pending work', { timeout: 5000 }, async () => {
  const display = compositor();
  const submitted = [], gate = deferred(), started = deferred();
  const scheduler = new LatestFrameScheduler({
    snapshot: () => display.snapshot(), maxFps: 120,
    submit: async frame => { submitted.push(frame); started.resolve(); await gate.promise; },
  });
  scheduler.pause();
  await display.applyTouch(image(800, 100, red));
  scheduler.request();
  await display.applyTouch(image(800, 100, green));
  scheduler.request();
  await tick();
  assert.equal(submitted.length, 0);
  scheduler.resume();
  await started.promise;
  assert.deepEqual(pixel(submitted[0].strip, 100, 50), green);
  await display.applyTouch(image(800, 100, blue));
  scheduler.request();
  let closed = false;
  const closing = scheduler.close().then(() => { closed = true; });
  await tick();
  assert.equal(closed, false);
  gate.resolve();
  await closing;
  scheduler.request();
  scheduler.resume();
  await tick();
  assert.equal(submitted.length, 1);
});

test('a failed full-frame transfer pauses output and rejects its flush', { timeout: 5000 }, async () => {
  const display = compositor();
  const failure = new Error('USB submission failed');
  const errors = [];
  let attempts = 0;
  const scheduler = new LatestFrameScheduler({
    snapshot: () => display.snapshot(), maxFps: 120,
    submit: async () => { attempts++; throw failure; }, onError: error => errors.push(error),
  });
  try {
    scheduler.request();
    await assert.rejects(scheduler.flush(), error => error === failure);
    scheduler.request();
    await tick();
    assert.equal(attempts, 1);
    assert.deepEqual(errors, [failure]);
  } finally {
    await scheduler.close().catch(() => {});
  }
});

test('wake invalidation resends an unchanged complete frame once and restores duplicate suppression', { timeout: 5000 }, async () => {
  const display = compositor();
  const submitted = [];
  const scheduler = new LatestFrameScheduler({
    snapshot: () => display.snapshot(), maxFps: 120,
    submit: async frame => { submitted.push(frame); },
  });
  try {
    await display.applyTouch(image(800, 100, green));
    scheduler.request();
    await scheduler.flush();
    assert.equal(submitted.length, 1);
    scheduler.pause();
    scheduler.invalidate();
    await tick();
    assert.equal(submitted.length, 1);
    scheduler.resume();
    await scheduler.flush();
    assert.equal(submitted.length, 2);
    assert.equal(submitted[1].revision, submitted[0].revision);
    assert.deepEqual(submitted[1].data, submitted[0].data);
    assert.deepEqual([submitted[1].width, submitted[1].height, submitted[1].data.length], [800, 480, 800 * 480 * 3]);
    assert.equal(Object.hasOwn(submitted[1], 'region'), false);
    scheduler.request();
    await scheduler.flush();
    assert.equal(submitted.length, 2);
  } finally { await scheduler.close(); }
});

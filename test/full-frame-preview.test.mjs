import test from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { Script } from 'node:vm';
import { createFullFramePreview } from '../apps/stream-dog/node/full-frame-preview.mjs';

const jpeg = (...bytes) => Buffer.from([0xff, 0xd8, ...bytes, 0xff, 0xd9]);
const rawRequest = (url, options) => new Promise((resolve, reject) => {
  const req = request(url, options, response => {
    const chunks = [];
    response.on('data', chunk => chunks.push(chunk));
    response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
  });
  req.on('error', reject); req.end();
});

test('read-only preview serves an isolated full frame and does not refresh it for status changes', async t => {
  const preview = await createFullFramePreview();
  t.after(() => preview.close());
  const empty = await (await fetch(new URL('/state', preview.url))).json();
  assert.equal(empty.frame.available, false);
  assert.equal((await fetch(new URL('/frame.jpg', preview.url))).status, 404);
  const frame = jpeg(3, 4), status = { hostConnected: true, note: 'ready' };
  const update = preview.update({ frameJpeg: frame, status });
  assert.equal(update.frameRevision, 1);
  assert.equal(update.frame.fps, 1);
  frame[2] = 9; status.note = 'mutated'; update.status.note = 'also mutated';
  const frameResponse = await fetch(new URL('/frame.jpg?revision=1', preview.url));
  assert.equal(frameResponse.headers.get('content-type'), 'image/jpeg');
  assert.equal(frameResponse.headers.get('cache-control'), 'no-store');
  assert.deepEqual(Buffer.from(await frameResponse.arrayBuffer()), jpeg(3, 4));
  assert.equal((await (await fetch(new URL('/state', preview.url))).json()).status.note, 'ready');
  const statusOnly = preview.update({ frameJpeg: jpeg(3, 4), status: { hostConnected: false } });
  assert.equal(statusOnly.revision, 2);
  assert.equal(statusOnly.frameRevision, 1);
  assert.equal(statusOnly.frame.fps, 1);
  preview.update({ frameJpeg: null });
  const cleared = await (await fetch(new URL('/state', preview.url))).json();
  assert.equal(cleared.frame.available, false);
});

test('preview rejects writes, foreign browser origins and unrelated routes', async t => {
  const preview = await createFullFramePreview();
  t.after(() => preview.close());
  assert.equal((await rawRequest(preview.url, { method: 'POST' })).status, 405);
  assert.equal((await rawRequest(preview.url, { headers: { Host: 'attacker.example' } })).status, 403);
  assert.equal((await rawRequest(preview.url, { headers: { Origin: 'https://attacker.example' } })).status, 403);
  assert.equal((await fetch(new URL('/unknown', preview.url))).status, 404);
  assert.equal((await fetch(new URL('/strip.jpg', preview.url))).status, 404);
  assert.equal((await fetch(new URL('/%2e%2e/secret', preview.url))).status, 404);
  const page = await fetch(preview.url);
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-security-policy'), /script-src 'nonce-/);
  const html = await page.text();
  new Script(html.match(/<script nonce="[^"]+">([\s\S]+)<\/script>/)[1]);
});

test('bounds reject updates atomically and preview shutdown is idempotent', async () => {
  await assert.rejects(createFullFramePreview({ host: '0.0.0.0' }), TypeError);
  const preview = await createFullFramePreview();
  preview.update({ frameJpeg: jpeg(1), status: { note: 'original' } });
  assert.throws(() => preview.update({ frameJpeg: Buffer.alloc(1_048_577) }), TypeError);
  assert.throws(() => preview.update({ frameJpeg: jpeg(2), status: { note: 'x'.repeat(32_769) } }), RangeError);
  assert.throws(() => preview.update({ frameJpeg: Buffer.alloc(0) }), TypeError);
  assert.throws(() => preview.update({ stripJpeg: jpeg(2) }), TypeError);
  assert.throws(() => preview.update({ status: [] }), TypeError);
  assert.throws(() => preview.update({ status: new Date() }), TypeError);
  const current = await (await fetch(new URL('/state', preview.url))).json();
  assert.equal(current.frameRevision, 1);
  assert.equal(current.status.note, 'original');
  assert.deepEqual(Buffer.from(await (await fetch(new URL('/frame.jpg', preview.url))).arrayBuffer()), jpeg(1));
  const firstClose = preview.close();
  assert.equal(preview.close(), firstClose);
  await firstClose;
  assert.equal(preview.update({ frameJpeg: jpeg(3) }), false);
  await assert.rejects(fetch(preview.url));
});

test('browser view shows only the full frame and fits small windows at 5:3', async t => {
  const preview = await createFullFramePreview();
  t.after(() => preview.close());
  preview.update({ frameJpeg: jpeg(2), status: { maxFps: 60,
    timings: { touchUpdateToSubmitted: { lastMs: 17.2, meanMs: 22.1 } } } });
  const state = await (await fetch(new URL('/state', preview.url))).json();
  const html = await (await fetch(preview.url)).text();
  assert.doesNotMatch(html, /<details|strip\.jpg|통합 스트립/);
  const script = new Script(html.match(/<script nonce="[^"]+">([\s\S]+)<\/script>/)[1]);
  const elements = new Map();
  let viewportSize = { width: 800, height: 600 }, resize;
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, hidden: false, listeners: {},
      addEventListener(name, listener) { this.listeners[name] = listener; },
      getBoundingClientRect: () => viewportSize,
      setAttribute() {},
    });
    return elements.get(id);
  };
  script.runInNewContext({
    document: { getElementById: element, body: { classList: { toggle: () => false } } },
    ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} },
    URLSearchParams, location: { search: '' }, setTimeout() {},
    fetch: async url => { assert.equal(url, '/state'); return { ok: true, json: async () => state }; },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('frame').src, '/stream.mjpg');
  assert.match(element('status').textContent, /스트림 1 fps · 상한 60 fps · 처리 지연 17 ms · 평균 22 ms/);
  assert.equal(element('frame-shell').style.width, '800px');
  assert.equal(element('frame-shell').style.height, '480px');
  for (const size of [{ width: 160, height: 90 }, { width: 100, height: 400 }]) {
    viewportSize = size; resize();
    const width = parseFloat(element('frame-shell').style.width), height = parseFloat(element('frame-shell').style.height);
    assert.ok(width <= size.width && height <= size.height);
    assert.ok(Math.abs(width / height - 5 / 3) < .000001);
  }
});

test('live preview pushes complete frames immediately and closes active viewers', async t => {
  const preview = await createFullFramePreview();
  t.after(() => preview.close());
  const response = await new Promise((resolve, reject) => {
    const req = request(new URL('/stream.mjpg', preview.url), resolve);
    req.on('error', reject); req.end();
  });
  assert.match(response.headers['content-type'], /multipart\/x-mixed-replace/);
  let body = Buffer.alloc(0), notify;
  response.on('data', data => { body = Buffer.concat([body, data]); notify?.(); });
  const waitFor = revision => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Frame was not pushed')), 2000);
    notify = () => {
      if (body.includes(Buffer.from('X-Frame-Revision: ' + revision + '\r\n'))) {
        clearTimeout(timer); resolve();
      }
    };
    notify();
  });
  for (let index = 1; index <= 12; index++) {
    preview.update({ frameJpeg: jpeg(index) });
    await waitFor(index);
  }
  assert.equal(body.toString('latin1').match(/Content-Type: image\/jpeg/g).length, 12);
  for (let index = 1; index <= 12; index++) assert.ok(body.includes(jpeg(index)));
  const ended = new Promise(resolve => response.once('close', resolve));
  await preview.close(); await ended;
});

test('frame rate returns to zero when the composed display stops changing', async t => {
  const preview = await createFullFramePreview();
  t.after(() => preview.close());
  preview.update({ frameJpeg: jpeg(1) });
  preview.update({ frameJpeg: jpeg(2) });
  let state = await (await fetch(new URL('/state', preview.url))).json();
  assert.equal(state.frame.fps, 2);
  await new Promise(resolve => setTimeout(resolve, 1050));
  state = await (await fetch(new URL('/state', preview.url))).json();
  assert.equal(state.frame.fps, 0);
  assert.equal(state.frame.available, true);
});

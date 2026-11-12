import test from 'node:test';
import assert from 'node:assert/strict';
import { FrameAdapterProcess } from '../apps/stream-dog/node/frame-adapter-process.mjs';

const metadata = {
  event: 'ready', protocolVersion: 1, adapterId: 'mirabox', modelId: 'n4-pro',
  layout: { width: 800, height: 480, rotation: 180 },
};

function fixture(body = '', { beforeReady = '', onEnd = 'process.exit(0)' } = {}) {
  return `
    const send = value => process.stdout.write(JSON.stringify(value) + '\\n');
    ${beforeReady}
    send(${JSON.stringify(metadata)});
    let buffered = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', text => {
      buffered += text;
      let newline;
      while ((newline = buffered.indexOf('\\n')) >= 0) {
        const request = JSON.parse(buffered.slice(0, newline));
        buffered = buffered.slice(newline + 1);
        if (request.method === 'close') { send({ id: request.id, result: { status: 'closed' } }); continue; }
        ${body || 'send({ id: request.id, result: { method: request.method, params: request.params } });'}
      }
    });
    process.stdin.on('end', () => { ${onEnd}; });
  `;
}

const client = (source, options = {}) => new FrameAdapterProcess({ command: process.execPath, args: ['-e', source], ...options });
const captured = promise => promise.then(value => ({ value }), error => ({ error }));
const errorCode = code => error => error?.code === code;
const tick = () => new Promise(resolve => setImmediate(resolve));

test('startup only reads metadata, forwards events, and correlates split responses', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`
    send({ event: 'input', type: 'key', index: 0, state: 'down' });
    const text = JSON.stringify({ id: request.id, result: request.params }) + '\\n';
    process.stdout.write(text.slice(0, 7));
    setImmediate(() => process.stdout.write(text.slice(7)));
  `));
  const events = [];
  adapter.on('event', value => events.push(value));
  try {
    const ready = await adapter.start();
    assert.deepEqual(ready, metadata);
    assert.equal(adapter.status().state, 'ready');
    assert.equal(adapter.status().pending, 0);
    assert.deepEqual(events, [metadata]);
    ready.layout.width = 1;
    const copy = adapter.metadata;
    copy.layout.height = 1;
    assert.deepEqual(adapter.metadata, metadata);
    assert.deepEqual(await adapter.request('echo', { nested: { value: 42 } }), { nested: { value: 42 } });
    assert.equal(events[1].event, 'input');
  } finally { await adapter.close(); }
  assert.equal(adapter.status().state, 'closed');
  assert.equal(adapter.status().pid, null);
  await assert.rejects(adapter.start(), errorCode('RUST_ADAPTER_CLOSED'));
});

test('concurrent responses can arrive in reverse order without mixing request results', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`
    setTimeout(() => send({ id: request.id, result: request.params.value }), request.params.delay);
  `));
  try {
    await adapter.start();
    const first = adapter.request('echo', { value: 'first', delay: 40 });
    const second = adapter.request('echo', { value: 'second', delay: 0 });
    assert.equal(await second, 'second');
    assert.equal(await first, 'first');
    assert.equal(adapter.status().pending, 0);
  } finally { await adapter.close(); }
});

test('valid adapter errors reject one request while the transport remains usable', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`
    if (request.method === 'reject') send({ id: request.id, error: { code: 'UNSUPPORTED_METHOD', message: 'No such operation' } });
    else send({ id: request.id, result: 'still ready' });
  `));
  try {
    await adapter.start();
    await assert.rejects(adapter.request('reject'), errorCode('UNSUPPORTED_METHOD'));
    assert.equal(await adapter.request('echo'), 'still ready');
    assert.equal(adapter.status().state, 'ready');
  } finally { await adapter.close(); }
});

test('an unknown response ID fails every outstanding request and closes its process', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`
    if (request.method === 'invalid') send({ id: 999, result: {} });
  `));
  await adapter.start();
  const first = captured(adapter.request('wait'));
  const second = captured(adapter.request('invalid'));
  for (const result of await Promise.all([first, second])) assert.equal(result.error.code, 'RUST_PROTOCOL_INVALID');
  await adapter.close();
  assert.equal(adapter.status().state, 'failed');
  assert.equal(adapter.status().pending, 0);
  assert.equal(adapter.status().pid, null);
});

for (const [name, output] of [
  ['malformed JSON', `process.stdout.write('{bad json}\\n');`],
  ['incomplete EOF', `process.stdout.write('{"id":', () => process.exit(0));`],
  ['invalid UTF-8', `process.stdout.write(Buffer.from([0xff, 0x0a]));`],
]) {
  test(`${name} cannot become an adapter event`, { timeout: 5000 }, async () => {
    const adapter = client(fixture(output));
    await adapter.start();
    await assert.rejects(adapter.request('invalid'), errorCode('RUST_PROTOCOL_INVALID'));
    await adapter.close();
    assert.equal(adapter.status().pid, null);
  });
}

test('oversized complete and unfinished lines are bounded independently', { timeout: 10000 }, async () => {
  for (const suffix of ['', '\\n']) {
    const adapter = client(fixture(`process.stdout.write('x'.repeat(1025) + '${suffix}');`), { maxMessageBytes: 1024 });
    await adapter.start();
    await assert.rejects(adapter.request('oversized'), errorCode('RUST_MESSAGE_TOO_LARGE'));
    await adapter.close();
    assert.equal(adapter.status().pending, 0);
    assert.equal(adapter.status().pid, null);
  }
});

test('a request timeout fails the session without retrying the command', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`send({ event: 'received', method: request.method });`), { requestTimeoutMs: 400 });
  const events = [];
  adapter.on('event', value => events.push(value));
  await adapter.start();
  await assert.rejects(adapter.request('blocked'), errorCode('RUST_REQUEST_TIMEOUT'));
  await adapter.close();
  assert.deepEqual(events.filter(value => value.event === 'received').map(value => value.method), ['blocked']);
  assert.equal(adapter.status().pending, 0);
  assert.equal(adapter.status().pid, null);
});

test('worker crashes reject pending work and keep only a bounded stderr tail', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`process.stderr.write('z'.repeat(16000) + 'crash-marker'); process.exitCode = 7; process.stdin.destroy();`));
  await adapter.start();
  await assert.rejects(adapter.request('crash'), errorCode('RUST_WORKER_EXIT'));
  await adapter.close();
  assert.equal(adapter.status().state, 'failed');
  assert.ok(adapter.status().stderrTail.length <= 8192);
  assert.ok(adapter.status().stderrTail.endsWith('crash-marker'));
});

test('pending and outbound byte limits reject excess admission without corrupting active requests', { timeout: 5000 }, async () => {
  const adapter = client(fixture(`setTimeout(() => send({ id: request.id, result: 'accepted' }), 40);`),
    { maxPending: 1, maxMessageBytes: 1024 });
  try {
    await adapter.start();
    const active = adapter.request('echo');
    await assert.rejects(adapter.request('excess'), errorCode('RUST_PENDING_LIMIT'));
    assert.equal(await active, 'accepted');
    await assert.rejects(adapter.request('large', { value: 'x'.repeat(2000) }), errorCode('RUST_MESSAGE_TOO_LARGE'));
    assert.equal(await adapter.request('echo'), 'accepted');
  } finally { await adapter.close(); }
});

test('closing before startup is inert and closing an active client is idempotent', { timeout: 5000 }, async () => {
  const neverStarted = client(fixture());
  await neverStarted.close();
  assert.equal(neverStarted.status().pid, null);
  await assert.rejects(neverStarted.start(), errorCode('RUST_ADAPTER_CLOSED'));
  const adapter = client(fixture());
  await adapter.start();
  const a = adapter.close(), b = adapter.close();
  await Promise.all([a, b]);
  assert.equal(adapter.status().state, 'closed');
});

test('unsupported readiness metadata fails startup before any request is sent', { timeout: 5000 }, async () => {
  const adapter = client(`
    process.stdout.write(JSON.stringify({ ...${JSON.stringify(metadata)}, protocolVersion: 2 }) + '\\n');
    process.stdin.resume(); process.stdin.on('end', () => process.exit(0));
  `);
  await assert.rejects(adapter.start(), errorCode('RUST_PROTOCOL_INVALID'));
  await adapter.close();
  assert.equal(adapter.status().pid, null);
});

test('shutdown reports forced termination when a worker acknowledges close but will not exit', { timeout: 8000 }, async () => {
  const adapter = client(fixture('', { onEnd: 'setInterval(() => {}, 1000)' }));
  await adapter.start();
  await assert.rejects(adapter.close(), error => error.code === 'RUST_CLEANUP_FAILED' && error.details.forced);
  await tick();
  assert.equal(adapter.status().state, 'failed');
  assert.equal(adapter.status().pid, null);
});

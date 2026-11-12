import test from 'node:test';
import assert from 'node:assert/strict';
import { createFrameRuntimeApi } from '../apps/stream-dog/node/frame-runtime-api.mjs';

test('management API authenticates writes, rejects foreign origins and exposes only explicit commands', async t => {
  const commands = [], token = 'a'.repeat(64);
  const api = await createFrameRuntimeApi({ token, port: 0, manager: {
    getState: () => ({ ready: true }), nativeState: () => ({ serial: 'HGMOCK000003' }),
    command: async (...args) => { commands.push(args); return { changed: true }; },
  } });
  t.after(() => api.close());
  const headers = { Authorization: `Bearer ${token}` };
  assert.equal((await fetch(api.url + '/api/state')).status, 403);
  assert.equal((await fetch(api.url + '/api/state', { headers: { ...headers, Origin: 'http://attacker.example' } })).status, 403);
  assert.deepEqual(await (await fetch(api.url + '/api/state', { headers })).json(), { ready: true });
  assert.equal((await fetch(api.url + '/api/command', { method: 'POST', headers, body: 'x'.repeat(65537) })).status, 413);
  assert.equal(commands.length, 0);
  const response = await fetch(api.url + '/api/command', { method: 'POST', headers,
    body: JSON.stringify({ action: 'connect', params: {} }) });
  assert.equal(response.status, 200);
  assert.deepEqual(commands, [['connect', {}]]);
  assert.deepEqual(await (await fetch(api.url + '/state', { headers })).json(), { serial: 'HGMOCK000003' });
});

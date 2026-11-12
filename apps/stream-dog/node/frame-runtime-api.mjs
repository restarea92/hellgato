import { createServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';

export async function createFrameRuntimeApi({ manager, token, port = 5345, onShutdown = () => {} }) {
  if (typeof token !== 'string' || token.length < 32 || !Number.isInteger(port) || port < 0 || port > 65535) throw new TypeError('Invalid runtime API configuration');
  let expectedHost;
  const secret = Buffer.from(`Bearer ${token}`);
  const server = createServer(async (request, response) => {
    const reply = (code, value) => {
      const body = Buffer.from(JSON.stringify(value));
      response.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
        'Content-Length': body.length, 'X-Content-Type-Options': 'nosniff' }); response.end(body);
    };
    const received = Buffer.from(request.headers.authorization ?? '');
    if (request.headers.host !== expectedHost || request.headers.origin || received.length !== secret.length || !timingSafeEqual(received, secret)) {
      reply(403, { error: 'Runtime authentication required' }); return;
    }
    if (request.method === 'GET' && request.url === '/state') { reply(200, manager.nativeState()); return; }
    if (request.method === 'GET' && request.url === '/api/state') { reply(200, manager.getState()); return; }
    if (request.method !== 'POST' || !['/api/command', '/control'].includes(request.url)) { reply(404, { error: 'Unknown runtime route' }); return; }
    try {
      let bytes = 0; const chunks = [];
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > 65536) { reply(413, { error: 'Command byte limit exceeded' }); return; }
        chunks.push(chunk);
      }
      const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.action !== 'string' ||
          value.params !== undefined && (!value.params || typeof value.params !== 'object' || Array.isArray(value.params))) throw new Error('Invalid management command');
      if (request.url === '/control') { reply(200, await manager.command('native-control', value)); return; }
      if (value.action === 'shutdown') { reply(200, { stopping: true }); setImmediate(onShutdown); return; }
      reply(200, { result: await manager.command(value.action, value.params ?? {}) });
    } catch (error) { if (!response.writableEnded) reply(400, { error: error.message }); }
  });
  server.headersTimeout = 5000; server.requestTimeout = 10000;
  await new Promise((resolveListen, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolveListen); });
  expectedHost = `127.0.0.1:${server.address().port}`;
  return { url: `http://${expectedHost}`, async close() {
    server.closeIdleConnections(); await new Promise(resolveClose => server.close(resolveClose));
  } };
}

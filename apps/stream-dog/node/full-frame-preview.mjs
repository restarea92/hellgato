import { randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { performance } from 'node:perf_hooks';

const MAX_IMAGE_BYTES = 1_048_576;
const MAX_STATUS_BYTES = 32_768;
const LOOPBACK_HOSTS = new Set(['127.0.0.1', '::1']);

function copyImage(value, name) {
  if (value === null) return null;
  if (!(value instanceof Uint8Array) || !value.byteLength || value.byteLength > MAX_IMAGE_BYTES) {
    throw new TypeError(`${name} must be a nonempty Uint8Array of at most ${MAX_IMAGE_BYTES} bytes`);
  }
  return Buffer.from(value);
}

function copyStatus(value) {
  if (value === null) return {};
  if (typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Preview status must be a JSON object');
  const encoded = JSON.stringify(value);
  if (Buffer.byteLength(encoded) > MAX_STATUS_BYTES) throw new RangeError('Preview status exceeds its byte limit');
  const copied = JSON.parse(encoded);
  if (!copied || typeof copied !== 'object' || Array.isArray(copied)) throw new TypeError('Preview status must serialize to a JSON object');
  return copied;
}

const sameImage = (left, right) => left === right || Boolean(left && right && left.equals(right));

function page(nonce) {
  return `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Hellgato 통합 프레임 스트림</title>
  <style>
    :root { color-scheme: dark; font: 13px/1.4 "Segoe UI",sans-serif; background: #111318; color: #e7e9ef; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    .app { height: 100vh; height: 100dvh; display: flex; flex-direction: column; min-width: 0; padding: 12px; gap: 10px; }
    header { padding-right: 84px; min-width: 0; }
    h1 { font-size: 14px; font-weight: 600; margin: 0; }
    .hint { margin: 3px 0 0; font-size: 11px; color: #a2a8b7; }
    button { border: 1px solid #454c5d; border-radius: 5px; color: #dce0e9; background: #242832; padding: 4px 8px; font: inherit; cursor: pointer; }
    button:focus-visible { outline: 2px solid #84b7ed; outline-offset: 3px; }
    #compact { position: fixed; top: 10px; right: 10px; z-index: 1; font-size: 11px; }
    #viewport { flex: 1; min-height: 0; min-width: 0; display: grid; place-items: center; }
    #frame-shell { position: relative; width: 100%; aspect-ratio: 5 / 3; background: #080a0e; box-shadow: 0 0 0 1px #343a49; overflow: hidden; }
    #frame { display: block; width: 100%; height: 100%; object-fit: contain; }
    #empty { position: absolute; inset: 0; display: grid; place-items: center; font-size: clamp(8px,1.8vw,13px); color: #a2a8b7; text-align: center; }
    [hidden] { display: none !important; }
    #status { font-size: 11px; color: #a2a8b7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .compact .app { padding: 3px; gap: 0; }
    .compact header, .compact #status { display: none; }
    .compact #compact { opacity: .38; top: 4px; right: 4px; }
    .compact #compact:hover, .compact #compact:focus-visible { opacity: 1; }
    @media (max-width: 340px), (max-height: 180px) { .hint { display: none; } .app { padding: 6px; gap: 5px; } h1 { font-size: 12px; } }
  </style>
</head>
<body>
  <div class="app">
    <header><h1>통합 프레임 스트림</h1><p class="hint">800 × 480 · 5:3 비율 유지 · 지연: Elgato 화면 수신 → USB 제출</p></header>
    <button id="compact" type="button" aria-pressed="false">화면만 보기</button>
    <main id="viewport" aria-label="통합 프레임 스트림 미리보기">
      <div id="frame-shell"><img id="frame" alt="기기로 전송하는 800 × 480 합성 프레임" hidden><div id="empty">전체 프레임을 기다리는 중</div></div>
    </main>
    <div id="status" role="status">미리보기에 연결하는 중</div>
  </div>
  <script nonce="${nonce}">
    const viewport = document.getElementById('viewport');
    const shell = document.getElementById('frame-shell');
    const frame = document.getElementById('frame');
    const empty = document.getElementById('empty');
    const status = document.getElementById('status');
    const compact = document.getElementById('compact');
    let latest = null;
    frame.src = '/stream.mjpg';

    function fitFrame() {
      const rect = viewport.getBoundingClientRect();
      const ratio = (latest?.frame.width ?? 800) / (latest?.frame.height ?? 480);
      const width = Math.max(0, Math.min(rect.width, rect.height * ratio));
      shell.style.width = width + 'px';
      shell.style.height = width / ratio + 'px';
    }
    new ResizeObserver(fitFrame).observe(viewport);
    compact.addEventListener('click', () => {
      const enabled = document.body.classList.toggle('compact');
      compact.textContent = enabled ? '정보 보기' : '화면만 보기';
      compact.setAttribute('aria-pressed', String(enabled));
      fitFrame();
    });
    if (new URLSearchParams(location.search).get('compact') === '1') compact.click();

    async function poll() {
      try {
        const response = await fetch('/state', {cache: 'no-store'});
        if (!response.ok) throw new Error('Preview unavailable');
        latest = await response.json();
        fitFrame();
        frame.hidden = !latest.frame.available;
        empty.hidden = latest.frame.available;
        const info = latest.status;
        const latency = info.timings?.imageUpdateToSubmitted ?? info.timings?.touchUpdateToSubmitted;
        const parts = ['스트림 ' + latest.frame.fps + ' fps'];
        if (Number.isFinite(info.maxFps)) parts.push('상한 ' + info.maxFps + ' fps');
        if (Number.isFinite(latency?.lastMs)) parts.push('처리 지연 ' + Math.round(latency.lastMs) + ' ms');
        if (Number.isFinite(latency?.meanMs)) parts.push('평균 ' + Math.round(latency.meanMs) + ' ms');
        if (typeof info.screensaverActive === 'boolean') parts.push(info.screensaverActive ? '화면보호기' : '기본 화면');
        if (typeof info.hostConnected === 'boolean') parts.push('Elgato ' + (info.hostConnected ? '연결됨' : '대기'));
        if (typeof info.deviceConnected === 'boolean') parts.push('기기 ' + (info.deviceConnected ? '연결됨' : '대기'));
        if (Number.isFinite(info.framesSubmitted)) parts.push('전체 프레임 ' + info.framesSubmitted);
        if (typeof info.note === 'string') parts.push(info.note);
        if (typeof info.error === 'string' && info.error) parts.push(info.error);
        status.textContent = parts.join(' · ');
      } catch {
        status.textContent = '미리보기 연결이 끊겼습니다. 마지막 화면을 유지합니다.';
      } finally {
        setTimeout(poll, 500);
      }
    }
    fitFrame();
    poll();
  </script>
</body>
</html>`;
}

/** Bounded read-only observations; closing the viewer never controls a device. */
export async function createFullFramePreview({ host = '127.0.0.1', port = 0 } = {}) {
  if (!LOOPBACK_HOSTS.has(host) || !Number.isInteger(port) || port < 0 || port > 65535) {
    throw new TypeError('Preview must bind to a numeric loopback address and a valid port');
  }
  let frame = null, status = {}, revision = 0, frameRevision = 0;
  let closed = false, closing, expectedHost;
  let frameTimes = [];
  const viewers = new Set();
  const sendLatest = viewer => {
    if (!frame || viewer.blocked || viewer.revision === frameRevision || viewer.response.destroyed) return;
    const header = Buffer.from('--hellgato-frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + frame.length +
      '\r\nX-Frame-Revision: ' + frameRevision + '\r\n\r\n');
    viewer.revision = frameRevision;
    viewer.blocked = !viewer.response.write(Buffer.concat([header, frame, Buffer.from('\r\n')]));
  };
  const nonce = randomBytes(18).toString('base64');
  const html = Buffer.from(page(nonce));
  const snapshot = () => {
    const now = performance.now();
    frameTimes = frameTimes.filter(time => now - time < 1000);
    return { revision, frameRevision,
      frame: { available: frame !== null, width: status.display?.width ?? 800, height: status.display?.height ?? 480, bytes: frame?.length ?? 0, fps: frameTimes.length },
      status: structuredClone(status) };
  };
  const server = createServer((request, response) => {
    const reply = (code, body, contentType = 'application/json; charset=utf-8', extra = {}) => {
      response.writeHead(code, { 'Content-Type': contentType, 'Content-Length': body.length, 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin',
        'Content-Security-Policy': `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'`,
        ...extra });
      response.end(body);
    };
    if (request.headers.host !== expectedHost || request.headers.origin && request.headers.origin !== `http://${expectedHost}`) {
      reply(403, Buffer.from('{"error":"Forbidden preview origin"}')); return;
    }
    if (request.method !== 'GET') { reply(405, Buffer.from('{"error":"Read-only preview"}'), undefined, { Allow: 'GET' }); return; }
    const path = request.url?.split('?')[0];
    if (path === '/') { reply(200, html, 'text/html; charset=utf-8'); return; }
    if (path === '/state') { reply(200, Buffer.from(JSON.stringify(snapshot()))); return; }
    if (path === '/stream.mjpg') {
      if (viewers.size >= 4) { reply(503, Buffer.from('{"error":"Preview viewer limit"}')); return; }
      response.writeHead(200, { 'Content-Type': 'multipart/x-mixed-replace; boundary=hellgato-frame',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin' });
      response.flushHeaders();
      response.socket?.setNoDelay(true);
      const viewer = { response, revision: -1, blocked: false };
      viewers.add(viewer);
      response.on('close', () => viewers.delete(viewer));
      response.on('error', () => viewers.delete(viewer));
      // Slow viewers retain only the current server frame, never an output queue.
      response.on('drain', () => { viewer.blocked = false; sendLatest(viewer); });
      sendLatest(viewer);
      return;
    }
    if (path === '/frame.jpg') {
      if (frame) { reply(200, frame, 'image/jpeg'); return; }
    }
    reply(404, Buffer.from('{"error":"Preview resource unavailable"}'));
  });
  server.headersTimeout = 5000;
  server.requestTimeout = 5000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, host, resolve); });
  const address = server.address();
  expectedHost = `${host.includes(':') ? `[${host}]` : host}:${address.port}`;
  return {
    url: `http://${expectedHost}/`,
    getState: snapshot,
    update(value) {
      if (closed) return false;
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Preview update must be an object');
      if (Object.keys(value).some(key => !['frameJpeg', 'status'].includes(key))) throw new TypeError('Unknown preview update field');
      const nextFrame = value.frameJpeg === undefined ? frame : copyImage(value.frameJpeg, 'frameJpeg');
      const nextStatus = value.status === undefined ? status : copyStatus(value.status);
      const frameChanged = !sameImage(frame, nextFrame);
      const statusChanged = JSON.stringify(status) !== JSON.stringify(nextStatus);
      frame = nextFrame; status = nextStatus;
      if (frameChanged) {
        frameRevision++;
        if (frame) {
          frameTimes.push(performance.now());
          if (frameTimes.length > 120) frameTimes.shift();
        }
        for (const viewer of viewers) sendLatest(viewer);
      }
      if (frameChanged || statusChanged) revision++;
      return snapshot();
    },
    close() {
      if (!closing) {
        closed = true;
        for (const viewer of viewers) viewer.response.destroy();
        viewers.clear();
        closing = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
        server.closeIdleConnections();
      }
      return closing;
    },
  };
}

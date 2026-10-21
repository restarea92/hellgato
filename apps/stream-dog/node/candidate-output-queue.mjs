import { CandidateError } from './candidate-config.mjs';

const positive = value => Number.isSafeInteger(value) && value > 0;
const envelope = ['deviceId', 'sessionId', 'generation', 'sequence'];

function queuedPayload(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || !['frame', 'control'].includes(payload.type)) {
    throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
  }
  const value = structuredClone(payload);
  for (const key of envelope) delete value[key];
  if (value.type === 'frame') {
    if (typeof value.surfaceId !== 'string' || !value.surfaceId || typeof value.format !== 'string' || !value.format ||
        !positive(value.width) || !positive(value.height)) throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
    if (value.region !== undefined) {
      const region = value.region;
      if (!region || typeof region !== 'object' || Array.isArray(region) ||
          !Number.isSafeInteger(region.x) || region.x < 0 || !Number.isSafeInteger(region.y) || region.y < 0 ||
          !positive(region.width) || !positive(region.height) ||
          region.x > value.width - region.width || region.y > value.height - region.height) {
        throw new CandidateError('CANDIDATE_OUTPUT_RESULT');
      }
    }
  }
  return value;
}

function supersedes(next, previous) {
  if (next.type !== 'frame' || previous.type !== 'frame' || next.surfaceId !== previous.surfaceId ||
      next.format !== previous.format || next.width !== previous.width || next.height !== previous.height) return false;
  const a = next.region ?? { x: 0, y: 0, width: next.width, height: next.height };
  const b = previous.region ?? { x: 0, y: 0, width: previous.width, height: previous.height };
  return a.x <= b.x && a.y <= b.y && a.x + a.width >= b.x + b.width && a.y + a.height >= b.y + b.height;
}

/** Product-only admission and pacing. Pending frames are not acknowledged
 * restoration history; the SDK still checks readiness and owns its output bound.
 */
export function createCandidateOutputQueue({ submit, observeFrame = () => {}, maxEntries = 32, maxBytes = 8 * 1024 * 1024 } = {}) {
  if (typeof submit !== 'function' || typeof observeFrame !== 'function' || !positive(maxEntries) || !positive(maxBytes)) {
    throw new CandidateError('CANDIDATE_CONFIG_INVALID');
  }
  let pending = [], pendingBytes = 0, active = null;
  let paused = true, closed = false, scheduled = false, epoch = 0;

  const settle = (item, method, value) => {
    if (item.settled) return;
    item.settled = true; item[method](value);
  };
  const discard = reason => {
    for (const item of pending) settle(item, 'reject', reason);
    pending = []; pendingBytes = 0;
    if (active) settle(active, 'reject', reason);
  };
  const schedule = () => {
    if (scheduled || closed || paused || active || !pending.length) return;
    scheduled = true;
    queueMicrotask(() => {
      scheduled = false;
      if (closed || paused || active || !pending.length) return;
      const item = pending.shift(); pendingBytes -= item.bytes; active = item;
      let submission;
      try { submission = submit(item.payload); }
      catch (error) { submission = Promise.reject(error); }
      Promise.resolve(submission).then(result => {
        if (item.epoch === epoch && !closed) settle(item, 'resolve', result);
      }, error => {
        if (item.epoch === epoch && !closed) queue.close(error);
      }).finally(() => {
        if (active === item) active = null;
        schedule();
      });
    });
  };
  const queue = {
    enqueue(payload) {
      if (closed) return Promise.reject(new CandidateError('CANDIDATE_SESSION_ENDED'));
      let value, bytes;
      try { value = queuedPayload(payload); bytes = Buffer.byteLength(JSON.stringify(value)); }
      catch (error) { return Promise.reject(error); }
      if (value.type === 'frame') observeFrame('received', value.surfaceId);
      let barrier = -1;
      for (let index = pending.length - 1; index >= 0; index--) {
        if (pending[index].payload.type === 'control') { barrier = index; break; }
      }
      // During readiness, key frames may interleave identical saver announcements.
      // Keep one pending announcement without crossing a wake or other control.
      const lastControl = pending[paused ? barrier : pending.length - 1]?.payload;
      if (value.type === 'control' && value.control === 'screensaver' &&
          lastControl?.control === 'screensaver' && JSON.stringify(lastControl) === JSON.stringify(value)) {
        return Promise.resolve({ status: 'superseded' });
      }
      const removed = pending.filter((item, index) => index > barrier && supersedes(value, item.payload));
      const removedSet = new Set(removed);
      const retained = pending.filter(item => !removedSet.has(item));
      const nextBytes = pendingBytes - removed.reduce((total, item) => total + item.bytes, 0) + bytes;
      if (retained.length + 1 + (active ? 1 : 0) > maxEntries || nextBytes + (active?.bytes ?? 0) > maxBytes) {
        const error = new CandidateError('OUTPUT_OVERFLOW');
        error.details = { paused, pendingEntries: pending.length, pendingBytes,
          pendingFrames: pending.filter(item => item.payload.type === 'frame').length,
          inFlight: Boolean(active), incomingType: value.type, incomingBytes: bytes,
          limit: retained.length + 1 + (active ? 1 : 0) > maxEntries ? 'entries' : 'bytes' };
        queue.close(error);
        return Promise.reject(error);
      }
      const result = new Promise((resolve, reject) => {
        pending = [...retained, { payload: value, bytes, resolve, reject, settled: false, epoch }];
        pendingBytes = nextBytes;
      });
      for (const item of removed) {
        observeFrame('coalesced', item.payload.surfaceId);
        settle(item, 'resolve', { status: 'superseded' });
      }
      schedule();
      return result;
    },
    pause() { paused = true; },
    resume() { if (!closed) { paused = false; schedule(); } },
    reset(reason = new CandidateError('CANDIDATE_HOST_DISCONNECTED'), { retainPending = false } = {}) {
      epoch++; paused = true;
      if (retainPending) {
        for (const item of pending) item.epoch = epoch;
        if (active) settle(active, 'reject', reason);
      } else discard(reason);
    },
    close(reason = new CandidateError('CANDIDATE_SESSION_ENDED')) {
      if (closed) return;
      closed = true; paused = true; epoch++; discard(reason);
    },
    getStatus() {
      return { pendingCount: pending.length, inFlight: Boolean(active), bytes: pendingBytes + (active?.bytes ?? 0),
        retainedCount: pending.length + (active ? 1 : 0), epoch, paused, closed, maxEntries, maxBytes };
    },
  };
  return queue;
}

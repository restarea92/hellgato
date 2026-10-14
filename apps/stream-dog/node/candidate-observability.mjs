import { performance } from 'node:perf_hooks';

// Fixed storage: observations never retain event IDs, payloads or sample arrays.
export const DURATION_BUCKETS_MS = Object.freeze([0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 33, 66, 125, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000]);
const rounded = value => Math.round(value * 1000) / 1000;
const SURFACES = Object.freeze([...Array.from({ length: 10 }, (_, index) => `key:${index}`), 'touch', 'lcd']);
const DISPLAY_COUNTERS = ['received', 'coalesced', 'submitted', 'failed', 'restored'];
const WRITERS = new Set(['none', 'initialize', 'key', 'lcd', 'clear', 'fill', 'restore', 'screensaver', 'logo']);
const RETURN_KINDS = ['none', 'zero', 'negative', 'positive', 'other'];
const countValue = value => Number.isSafeInteger(value) && value >= 0;
const dimension = (value, maximum) => value === null || Number.isSafeInteger(value) && value > 0 && value <= maximum;

function workerDisplay(value) {
  if (!value || !Number.isSafeInteger(value.keyCacheCount) || value.keyCacheCount < 0 || value.keyCacheCount > 10 ||
      !countValue(value.keyCacheClears) || !Array.isArray(value.keys) || value.keys.length !== 10) return null;
  const keys = [];
  for (const key of value.keys) {
    if (!key || !['attempted', 'submitted', 'failed'].every(name => countValue(key[name])) || !WRITERS.has(key.lastWriter) ||
        !dimension(key.width, 800) || !dimension(key.height, 480) ||
        !(key.meanBrightness === null || Number.isFinite(key.meanBrightness) && key.meanBrightness >= 0 && key.meanBrightness <= 255) ||
        !(key.black === null || typeof key.black === 'boolean') ||
        !(key.lastVendorReturnKind === null || RETURN_KINDS.includes(key.lastVendorReturnKind)) ||
        !key.vendorReturnKinds || !RETURN_KINDS.every(name => countValue(key.vendorReturnKinds[name]))) return null;
    keys.push({ attempted: key.attempted, submitted: key.submitted, failed: key.failed, lastWriter: key.lastWriter,
      width: key.width, height: key.height, meanBrightness: key.meanBrightness, black: key.black,
      lastVendorReturnKind: key.lastVendorReturnKind,
      vendorReturnKinds: Object.fromEntries(RETURN_KINDS.map(name => [name, key.vendorReturnKinds[name]])) });
  }
  return { keyCacheCount: value.keyCacheCount, keyCacheClears: value.keyCacheClears, keys };
}

export function createDurationHistogram() {
  const buckets = Array(DURATION_BUCKETS_MS.length + 1).fill(0);
  let count = 0, sum = 0, max = 0;
  const percentile = fraction => {
    if (!count) return null;
    const target = Math.ceil(count * fraction); let total = 0;
    for (let index = 0; index < buckets.length; index++) {
      total += buckets[index];
      if (total >= target) return DURATION_BUCKETS_MS[index] ?? rounded(max);
    }
    return rounded(max);
  };
  return {
    observe(ms) {
      if (!Number.isFinite(ms) || ms < 0 || count === Number.MAX_SAFE_INTEGER) return;
      count++; sum = Math.min(Number.MAX_SAFE_INTEGER, sum + ms); max = Math.max(max, ms);
      const bucket = DURATION_BUCKETS_MS.findIndex(upper => ms <= upper);
      buckets[bucket < 0 ? buckets.length - 1 : bucket]++;
    },
    snapshot: () => ({ count, p50: percentile(.5), p95: percentile(.95), p99: percentile(.99),
      max: count ? rounded(max) : null, mean: count ? rounded(sum / count) : null, buckets: [...buckets] }),
  };
}

/** Process-only observations, not USB end-to-end latency or hardware delivery.
 * Input timing begins at the validated adapter callback and ends after the
 * bridge's synchronous send return. Submission timing ends at the worker reply.
 * RSS peak is the maximum observed at snapshots, not continuous peak sampling.
 */
export function createCandidateObservations() {
  const started = performance.now(), cpuStarted = process.cpuUsage();
  const nodeInputForwardMs = createDurationHistogram(), frameSubmitMs = createDurationHistogram(), controlSubmitMs = createDurationHistogram();
  const surfaces = Object.fromEntries(SURFACES.map(surface => [surface, Object.fromEntries(DISPLAY_COUNTERS.map(name => [name, 0]))]));
  let worker = null;
  let peakRss = 0, peakControls = 0;
  const displayFrame = (stage, surface) => {
    if (DISPLAY_COUNTERS.includes(stage) && Object.hasOwn(surfaces, surface)) {
      surfaces[surface][stage] = Math.min(Number.MAX_SAFE_INTEGER, surfaces[surface][stage] + 1);
    }
  };
  return {
    inputForward: ms => nodeInputForwardMs.observe(ms),
    submission: (type, ms) => (type === 'frame' ? frameSubmitMs : controlSubmitMs).observe(ms),
    pendingControls: count => { peakControls = Math.max(peakControls, count); },
    displayFrame,
    displaySubmission(payload, restoring, result) {
      if (payload.type === 'frame') displayFrame(restoring ? 'restored' : 'submitted', payload.surfaceId);
      if (result?.displayObservations !== undefined) worker = workerDisplay(result.displayObservations);
    },
    resetWorkerDisplay() { worker = null; },
    workerDisplay(value) { if (value !== undefined) worker = workerDisplay(value); },
    snapshot() {
      const elapsedMs = Math.max(0, performance.now() - started), cpu = process.cpuUsage(cpuStarted), rssBytes = process.memoryUsage.rss();
      peakRss = Math.max(peakRss, rssBytes);
      return { quantilesAreUpperBounds: true, bucketUpperBoundsMs: [...DURATION_BUCKETS_MS],
        nodeInputForwardMs: nodeInputForwardMs.snapshot(), frameSubmitMs: frameSubmitMs.snapshot(), controlSubmitMs: controlSubmitMs.snapshot(),
        resources: { elapsedMs: rounded(elapsedMs), cpuUserMs: cpu.user / 1000, cpuSystemMs: cpu.system / 1000,
          cpuPercent: elapsedMs ? rounded((cpu.user + cpu.system) / (elapsedMs * 10)) : 0, rssBytes, observedPeakRssBytes: peakRss },
        queue: { configuredOutputLimit: 32, peakPendingConsoleControls: peakControls },
        display: { surfaces: structuredClone(surfaces), worker: structuredClone(worker) } };
    },
  };
}

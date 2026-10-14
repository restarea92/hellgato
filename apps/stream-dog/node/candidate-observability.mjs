import { performance } from 'node:perf_hooks';

// Fixed storage: observations never retain event IDs, payloads or sample arrays.
export const DURATION_BUCKETS_MS = Object.freeze([0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 33, 66, 125, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000]);
const rounded = value => Math.round(value * 1000) / 1000;

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
  let peakRss = 0, peakControls = 0;
  return {
    inputForward: ms => nodeInputForwardMs.observe(ms),
    submission: (type, ms) => (type === 'frame' ? frameSubmitMs : controlSubmitMs).observe(ms),
    pendingControls: count => { peakControls = Math.max(peakControls, count); },
    snapshot() {
      const elapsedMs = Math.max(0, performance.now() - started), cpu = process.cpuUsage(cpuStarted), rssBytes = process.memoryUsage.rss();
      peakRss = Math.max(peakRss, rssBytes);
      return { quantilesAreUpperBounds: true, bucketUpperBoundsMs: [...DURATION_BUCKETS_MS],
        nodeInputForwardMs: nodeInputForwardMs.snapshot(), frameSubmitMs: frameSubmitMs.snapshot(), controlSubmitMs: controlSubmitMs.snapshot(),
        resources: { elapsedMs: rounded(elapsedMs), cpuUserMs: cpu.user / 1000, cpuSystemMs: cpu.system / 1000,
          cpuPercent: elapsedMs ? rounded((cpu.user + cpu.system) / (elapsedMs * 10)) : 0, rssBytes, observedPeakRssBytes: peakRss },
        queue: { configuredOutputLimit: 32, peakPendingConsoleControls: peakControls } };
    },
  };
}

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, statSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createTraceWriter } from '../apps/stream-dog/node/trace-log.mjs';
import { createCandidateDiagnostics } from '../apps/stream-dog/node/candidate-config.mjs';

test('trace rotation bounds Unicode and drops oversized records without breaking traffic', () => {
  const root = mkdtempSync(join(tmpdir(), 'hellgato-trace-'));
  try {
    const path = join(root, 'target-1.jsonl');
    writeFileSync(path, 'x'.repeat(10000));
    const write = createTraceWriter(path, 128);
    for (let i = 0; i < 100; i++) write(JSON.stringify({ event: '한글', i }) + '\n');
    write('x'.repeat(1000));
    for (const name of readdirSync(root)) {
      assert.ok(statSync(join(root, name)).size <= 128);
      readFileSync(join(root, name), 'utf8').trim().split('\n').forEach(line => JSON.parse(line));
    }
    assert.doesNotThrow(() => createTraceWriter(join(root, 'absent/log'))('failure\n'));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('current diagnostics discard an oversized inherited file rather than retaining it as a backup', () => {
  const root = mkdtempSync(join(tmpdir(), 'hellgato-diagnostic-'));
  try {
    const path = join(root, 'ipc-diagnostics.jsonl');
    writeFileSync(path, 'x'.repeat(10000));
    createCandidateDiagnostics(path, 256)('started');
    for (const name of readdirSync(root)) assert.ok(statSync(join(root, name)).size <= 256);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

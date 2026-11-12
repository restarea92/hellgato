import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { createFullFramePreview } from './full-frame-preview.mjs';
import { FrameRuntimeManager, writeRuntimeJson } from './frame-runtime-manager.mjs';
import { createFrameRuntimeApi } from './frame-runtime-api.mjs';

export async function startFrameRuntime(config, dependencies = {}) {
  if (!config.stateDir || !config.settingsPath) throw new TypeError('Explicit runtime state paths are required');
  mkdirSync(config.stateDir, { recursive: true });
  const token = randomBytes(32).toString('hex');
  const preview = config.previewEnabled === false ? null : await createFullFramePreview({ port: config.previewPort ?? 5346 });
  let manager, api, closing;
  const close = () => closing ??= (async () => { await manager?.close(); await api?.close(); await preview?.close(); })();
  try {
    manager = new FrameRuntimeManager(config, { ...dependencies, preview });
    api = await createFrameRuntimeApi({ manager, token, port: config.apiPort ?? 5345,
      onShutdown: () => { void close().catch(error => process.stderr.write(error.message + '\n')); } });
    const descriptor = { schemaVersion: 1, pid: process.pid, endpoint: api.url, token, previewUrl: preview?.url ?? null };
    writeRuntimeJson(resolve(config.stateDir, 'control.json'), descriptor);
    if (config.nativeUi) {
      if (!api.url.endsWith(':5345')) throw new Error('Native Stream Deck extension requires port 5345');
      writeRuntimeJson(config.nativeUi.configPath, { endpoint: api.url, token,
        serial: manager.getState().settings.identity.childSerial, autoReconnect: true });
    }
    manager.resume();
    return { manager, descriptor, close, preview };
  } catch (error) { await close(); throw error; }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== '--config') throw new Error('Use --config with an explicit runtime bundle configuration');
  const config = JSON.parse(readFileSync(resolve(args[1]), 'utf8').replace(/^\uFEFF/, ''));
  const runtime = await startFrameRuntime(config);
  process.stdout.write(JSON.stringify({ event: 'service-ready', pid: process.pid,
    controlPath: resolve(config.stateDir, 'control.json'), previewUrl: runtime.preview?.url ?? null }) + '\n');
  const close = () => { void runtime.close().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; }); };
  process.once('SIGINT', close); process.once('SIGTERM', close);
  if (config.ownedStdin) { process.stdin.once('end', close); process.stdin.resume(); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => { process.stderr.write(JSON.stringify({ event: 'service-failed', message: error.message }) + '\n'); process.exitCode = 1; });
}

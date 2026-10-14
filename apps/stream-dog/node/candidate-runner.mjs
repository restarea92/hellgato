import { pathToFileURL } from 'node:url';
import { resolve, isAbsolute } from 'node:path';
import { CandidateError, readBoundedJson, diagnosticCode } from './candidate-config.mjs';

/** The supervisor sends start only after placing this process in its Windows
 * kill-on-close job. No config file, worker or host is opened before that gate. */
export async function runCandidateCli(argv = process.argv.slice(2), { loadModule } = {}) {
  let configPath, selfTest = false, supervised = false, discovery = false;
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--config' && !configPath) configPath = argv[++index];
    else if (arg === '--self-test' && !selfTest) selfTest = true;
    else if (arg === '--supervised' && !supervised) supervised = true;
    else if (arg === '--discover' && !discovery) discovery = true;
    else throw new CandidateError('CANDIDATE_ARGUMENTS');
  }
  if (!configPath || !isAbsolute(configPath)) throw new CandidateError('CANDIDATE_CONFIG_REQUIRED');
  if (discovery && selfTest) throw new CandidateError('CANDIDATE_ARGUMENTS');
  let runner, productMode = false, stopping = false, input = Buffer.alloc(0), gateResolved = !supervised, protocolError;
  let gateResolve, gateReject;
  const gate = new Promise((yes, no) => { gateResolve = yes; gateReject = no; });
  const abort = new AbortController();
  // A handled gate also allows EOF or signal to arrive while modules are loading.
  gate.catch(() => {});
  const write = detail => process.stdout.write(JSON.stringify(detail) + '\n');
  const stop = () => { stopping = true; abort.abort(); if (!gateResolved) gateReject(protocolError ?? new CandidateError('CANDIDATE_START_CANCELLED')); void runner?.stop().catch(() => {}); };
  const fail = code => { protocolError ??= new CandidateError(code); stop(); };
  const line = bytes => {
    let command;
    try { command = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { fail('CANDIDATE_COMMAND_INVALID'); return; }
    if (!command || Array.isArray(command) || typeof command.command !== 'string' ||
      Object.keys(command).some(key => !['command', ...(command.command === 'control' ? ['control'] : []),
        ...(['control', 'workerState', 'status'].includes(command.command) ? ['requestId'] : [])].includes(key)) ||
      command.requestId !== undefined && (!Number.isSafeInteger(command.requestId) || command.requestId < 1)) { fail('CANDIDATE_COMMAND_INVALID'); return; }
    if (!gateResolved) {
      if (command.command !== 'start' || stopping) { fail('CANDIDATE_START_REQUIRED'); return; }
      gateResolved = true; gateResolve(); return;
    }
    if (command.command === 'stop') stop();
    else if (command.command === 'status' && runner) write({ ...runner.getStatus(), ...(command.requestId ? { requestId: command.requestId } : {}) });
    else if (command.command === 'control' && runner) {
      const response = command.requestId ? { requestId: command.requestId } : {};
      void runner.control(command.control).then(result => write({ schemaVersion: 1, event: 'controlSubmitted', ...response, ...result }))
        .catch(error => {
          write({ schemaVersion: 1, event: 'controlFailed', ...response, code: diagnosticCode(error) });
          if (!productMode) fail(diagnosticCode(error));
        });
    }
    else if (command.command === 'workerState' && runner && productMode && command.requestId) {
      void runner.getProductWorkerState().then(result => write({ schemaVersion: 1, event: 'workerState', requestId: command.requestId, ...result }))
        .catch(error => write({ schemaVersion: 1, event: 'workerStateFailed', requestId: command.requestId, code: diagnosticCode(error) }));
    }
    else fail('CANDIDATE_COMMAND_INVALID');
  };
  const data = chunk => {
    if (stopping) return;
    for (const byte of chunk) {
      if (byte === 10) { line(input); input = Buffer.alloc(0); if (stopping) return; }
      else { if (input.length >= 1024) { fail('CANDIDATE_COMMAND_LIMIT'); return; } input = Buffer.concat([input, Buffer.from([byte])]); }
    }
  };
  const eof = () => { if (input.length) fail('CANDIDATE_COMMAND_TRUNCATED'); else stop(); };
  const interactiveInput = supervised || !selfTest;
  const signal = () => stop();
  process.once('SIGINT', signal); process.once('SIGTERM', signal);
  if (interactiveInput) { process.stdin.on('data', data); process.stdin.once('end', eof); process.stdin.on('error', eof); }
  let timeout;
  try {
    if (supervised) {
      timeout = setTimeout(() => { fail('CANDIDATE_START_TIMEOUT'); }, 10000);
      await gate; clearTimeout(timeout);
    }
    if (stopping) { if (protocolError) throw protocolError; return; }
    const config = readBoundedJson(configPath);
    productMode = config.productMode === true;
    const module = loadModule ? await loadModule({ selfTest }) : selfTest ? await import('./candidate-self-test.mjs') : await import('./candidate-session.mjs');
    if (stopping) { if (protocolError) throw protocolError; return; }
    if (discovery) {
      const devices = await module.discoverProductDevices(config, { signal: abort.signal });
      if (!stopping) write({ schemaVersion: 1, event: 'devices-discovered', devices });
      return;
    }
    const fixture = selfTest ? module.createCandidateSelfTest(config) : null;
    runner = fixture?.runner ?? module.createCandidateRunner(config);
    runner.on('state', write);
    runner.on('product', write);
    if (fixture) write(await fixture.verify());
    else { await runner.start(); await runner.completed; if (runner.getStatus().state === 'failed') throw new CandidateError(runner.getStatus().code); }
    if (protocolError) throw protocolError;
  } catch (error) {
    if (productMode) write({ schemaVersion: 1, event: 'productFailed', state: 'failed', code: diagnosticCode(error) });
    throw error;
  } finally {
    clearTimeout(timeout);
    await runner?.stop();
    process.removeListener('SIGINT', signal); process.removeListener('SIGTERM', signal);
    if (interactiveInput) { process.stdin.removeListener('data', data); process.stdin.removeListener('end', eof); process.stdin.removeListener('error', eof); process.stdin.pause(); }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  runCandidateCli().catch(error => {
    process.stderr.write(JSON.stringify({ schemaVersion: 1, engine: 'ipc-candidate', state: 'failed', code: diagnosticCode(error), physicalHardwareVerified: false }) + '\n');
    process.exitCode = 1;
  });
}

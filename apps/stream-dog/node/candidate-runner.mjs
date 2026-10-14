import { pathToFileURL } from 'node:url';
import { resolve, isAbsolute } from 'node:path';
import { CandidateError, readBoundedJson, diagnosticCode } from './candidate-config.mjs';

/** The supervisor sends start only after placing this process in its Windows
 * kill-on-close job. No config file, worker or host is opened before that gate. */
export async function runCandidateCli(argv = process.argv.slice(2)) {
  let configPath, selfTest = false, supervised = false;
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--config' && !configPath) configPath = argv[++index];
    else if (arg === '--self-test' && !selfTest) selfTest = true;
    else if (arg === '--supervised' && !supervised) supervised = true;
    else throw new CandidateError('CANDIDATE_ARGUMENTS');
  }
  if (!configPath || !isAbsolute(configPath)) throw new CandidateError('CANDIDATE_CONFIG_REQUIRED');
  let runner, stopping = false, input = Buffer.alloc(0), gateResolved = !supervised, protocolError;
  let gateResolve, gateReject;
  const gate = new Promise((yes, no) => { gateResolve = yes; gateReject = no; });
  // A handled gate also allows EOF or signal to arrive while modules are loading.
  gate.catch(() => {});
  const write = detail => process.stdout.write(JSON.stringify(detail) + '\n');
  const stop = () => { stopping = true; if (!gateResolved) gateReject(protocolError ?? new CandidateError('CANDIDATE_START_CANCELLED')); void runner?.stop().catch(() => {}); };
  const fail = code => { protocolError ??= new CandidateError(code); stop(); };
  const line = bytes => {
    let command;
    try { command = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { fail('CANDIDATE_COMMAND_INVALID'); return; }
    if (!command || Array.isArray(command) || typeof command.command !== 'string' ||
      Object.keys(command).some(key => !['command', ...(command.command === 'control' ? ['control'] : [])].includes(key))) { fail('CANDIDATE_COMMAND_INVALID'); return; }
    if (!gateResolved) {
      if (command.command !== 'start' || stopping) { fail('CANDIDATE_START_REQUIRED'); return; }
      gateResolved = true; gateResolve(); return;
    }
    if (command.command === 'stop') stop();
    else if (command.command === 'status' && runner) write(runner.getStatus());
    else if (command.command === 'control' && runner) {
      void runner.control(command.control).then(result => write({ schemaVersion: 1, event: 'controlSubmitted', ...result }))
        .catch(error => { write({ schemaVersion: 1, event: 'controlFailed', code: diagnosticCode(error) }); fail(diagnosticCode(error)); });
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
    const module = selfTest ? await import('./candidate-self-test.mjs') : await import('./candidate-session.mjs');
    if (stopping) { if (protocolError) throw protocolError; return; }
    const fixture = selfTest ? module.createCandidateSelfTest(config) : null;
    runner = fixture?.runner ?? module.createCandidateRunner(config);
    runner.on('state', write);
    if (fixture) write(await fixture.verify());
    else { await runner.start(); await runner.completed; if (runner.getStatus().state === 'failed') throw new CandidateError(runner.getStatus().code); }
    if (protocolError) throw protocolError;
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

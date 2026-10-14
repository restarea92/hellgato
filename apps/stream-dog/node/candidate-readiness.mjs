import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { CandidateError } from './candidate-config.mjs';

/** Each invocation owns a fresh read-only inspector process and a unique nonce. */
export function createCandidateReadiness(options, { timeoutMs = 30000 } = {}) {
  return async ({ profile, signal }) => {
    if (signal?.aborted) throw new CandidateError('ABORTED');
    const request = { schemaVersion: 1, requestId: randomUUID(), targetId: profile.target.id, profileId: profile.id };
    const report = await new Promise((resolve, reject) => {
      const inherited = ['PATH', 'Path', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA'];
      const child = spawn(options.command, options.args ?? [], { shell: false, windowsHide: true,
        env: Object.fromEntries(inherited.filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]])),
        stdio: ['pipe', 'pipe', 'pipe'] });
      let stdout = Buffer.alloc(0), stderrBytes = 0, failure, forceTimer;
      const fail = code => {
        if (failure) return;
        failure = new CandidateError(code);
        child.stdin.destroy(); child.kill();
        forceTimer = setTimeout(() => child.kill('SIGKILL'), 250);
      };
      const abort = () => fail('ABORTED');
      const timer = setTimeout(() => fail('CANDIDATE_READINESS_TIMEOUT'), timeoutMs);
      signal?.addEventListener('abort', abort, { once: true });
      child.once('error', () => fail('CANDIDATE_READINESS_START'));
      child.stdin.on('error', () => fail('CANDIDATE_READINESS_PIPE'));
      child.stdout.on('data', chunk => {
        if (stdout.length + chunk.length > 65536) { fail('CANDIDATE_READINESS_LIMIT'); return; }
        stdout = Buffer.concat([stdout, chunk]);
      });
      child.stderr.on('data', chunk => { stderrBytes += chunk.length; if (stderrBytes > 65536) fail('CANDIDATE_READINESS_LIMIT'); });
      child.once('close', code => {
        clearTimeout(timer); clearTimeout(forceTimer); signal?.removeEventListener('abort', abort);
        if (failure) { reject(failure); return; }
        if (code !== 0) { reject(new CandidateError('CANDIDATE_READINESS_EXIT')); return; }
        try { resolve(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(stdout))); }
        catch { reject(new CandidateError('CANDIDATE_READINESS_JSON')); }
      });
      child.stdin.end(JSON.stringify(request) + '\n');
      if (signal?.aborted) abort();
    });
    if (!report || report.schemaVersion !== 1 || report.requestId !== request.requestId ||
      report.targetId !== request.targetId || report.profileId !== request.profileId || typeof report.ready !== 'boolean') {
      throw new CandidateError('CANDIDATE_READINESS_IDENTITY');
    }
    // Only the SDK's readiness fields cross the boundary. Paths and native probe
    // messages are intentionally excluded from diagnostics and session status.
    return { ready: report.ready, targetId: report.targetId, profileId: report.profileId,
      preparation: report.preparation, host: report.host ? { pid: report.host.pid,
        creationTime: report.host.creationTime, executableDigest: report.host.executableDigest } : undefined };
  };
}

import { execFileSync } from 'node:child_process';
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { CANDIDATE_SCHEMA, CHANNEL_PLAN_SCHEMA } from './release-policy.mjs';
import { ROOT, REPOSITORY, PACKAGES, UPSTREAM, MANIFEST, NPM_VERSION, invariant, json, digest, archiveName, inspectArchive, assertSources, assertNpmVersion, runTool, selectedNpm, consumerSdkSource, sourceCiSdk, parseArgs, artifactOptions } from './npm-release.mjs';

export function packArtifacts(options = {}) {
  const root = options.root || ROOT;
  const artifactDir = resolve(options.artifactDir || 'dist/npm');
  invariant(options.sourceChannel === undefined || options.sourceChannel === 'beta', 'Source CI supports only explicit beta context');
  const sourceCi = options.sourceChannel === 'beta';
  const source = sourceCi ? sourceCiSdk(root) : null;
  const candidate = Boolean(options.candidatePlanPath);
  const consumer = Boolean(options.consumerPlanPath);
  const consumerSource = consumer ? consumerSdkSource(options.consumerPlanPath, options.releaseSha) : null;
  invariant([options.candidatePlanPath, options.consumerPlanPath, options.sourceChannel, options.planPath].filter(Boolean).length <= 1 && (!(consumer || sourceCi) || !options.approvalPath), 'Consumer packing cannot accept npm publication approval');
  invariant(!candidate || !options.planPath && !options.approvalPath, 'Candidate packing cannot accept public release approval');
  const candidatePlan = candidate ? JSON.parse(readFileSync(options.candidatePlanPath)) : null;
  const publicPlan = options.planPath ? JSON.parse(readFileSync(options.planPath)) : null;
  const channel = sourceCi ? source.channel : consumer ? consumerSource.channel : candidate ? selectedNpm(candidatePlan, { candidate: true }).channel : publicPlan?.schemaVersion === CHANNEL_PLAN_SCHEMA ? selectedNpm(publicPlan, { channel: publicPlan.channel }).channel : 'stable';
  const [core] = assertSources(root, { channel });
  const repository = options.repository || REPOSITORY;
  invariant(repository === REPOSITORY, 'Foreign repository cannot create a release artifact');
  const releaseSha = options.releaseSha || execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  invariant(/^[0-9a-f]{40}$/.test(releaseSha), 'A full release SHA is required');
  invariant(execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim() === releaseSha, 'Pack checkout differs from release SHA');
  if (candidate) {
    invariant(resolve(options.candidatePlanPath) === resolve(root, '.release/candidate-plan.json'), 'Use the checkout candidate plan');
    execFileSync(process.execPath, [resolve(root, 'scripts/release-candidate.mjs'), '--check', '--head', releaseSha], { cwd: root, stdio: 'inherit' });
  }
  if (consumer || publicPlan?.schemaVersion === CHANNEL_PLAN_SCHEMA) execFileSync(process.execPath, [resolve(root, 'scripts/release-version.mjs'), '--check-plan', '--head', releaseSha], { cwd: root, stdio: 'inherit' });
  let planSha256 = null;
  if (options.planPath || options.candidatePlanPath || options.consumerPlanPath) {
    const planBytes = readFileSync(options.planPath || options.candidatePlanPath || options.consumerPlanPath);
    invariant((consumer ? consumerSource : selectedNpm(JSON.parse(planBytes), { candidate, channel })).version === core.version, 'Plan npm version differs from source');
    planSha256 = digest(planBytes);
  }
  const pnpm = runTool('pnpm', ['--version'], { cwd: root }).trim();
  invariant(json(resolve(root, 'package.json')).packageManager === 'pnpm@11.10.0' && pnpm === '11.10.0', `Expected pinned pnpm 11.10.0, found ${pnpm}`);
  invariant(process.versions.node.startsWith('24.'), 'Build requires Node 24');
  assertNpmVersion();
  mkdirSync(artifactDir, { recursive: true });
  invariant(readdirSync(artifactDir).length === 0, 'Artifact output must be empty; do not overwrite verified tarballs');
  const packages = [];
  for (const pkg of PACKAGES) {
    // pnpm rewrites workspace:* to the exact local package version during pack.
    runTool('pnpm', ['--filter', pkg.name, 'pack', '--pack-destination', artifactDir], { cwd: root, stdio: 'inherit' });
    packages.push(inspectArchive(resolve(artifactDir, archiveName(pkg.name, core.version)), pkg.name, core.version, { channel }));
  }
  const manifest = { schemaVersion: candidate || consumer || sourceCi ? CANDIDATE_SCHEMA : 1, ...(candidate || consumer || sourceCi ? { candidateOnly: true } : {}), ...(consumer ? { consumer: 'windows' } : sourceCi ? { consumer: 'source-ci' } : {}), repository, releaseSha, planSha256, component: 'npm', version: core.version, channel, source: { runId: String(options.runId || 'local'), runAttempt: String(options.runAttempt || '1') }, tools: { node: process.versions.node, pnpm, npm: NPM_VERSION }, upstream: UPSTREAM, packages };
  writeFileSync(resolve(artifactDir, MANIFEST), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
  return manifest;
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try { const manifest = packArtifacts(artifactOptions(parseArgs(process.argv.slice(2)))); console.log(`Packed ${manifest.packages.length} immutable npm tarballs in ${manifest.version}`); }
  catch (error) { console.error(`npm pack refused: ${error.message}`); process.exitCode = 1; }
}

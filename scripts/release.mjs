import { assertNpmPublicationEnabled } from './npm-release.mjs';
import { appendFile, readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const REPOSITORY = 'restarea92/hellgato';
export const RELEASE_BRANCH = 'changeset-release/main';
const SHA = /^[0-9a-f]{40}$/;
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hash = data => createHash('sha256').update(data).digest('hex');
const invariant = (condition, message) => { if (!condition) throw new Error(message); };
const git = (root, ...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim();

export async function github(path, { method = 'GET', body, missing = false, token = process.env.GH_TOKEN, fetchImpl = fetch } = {}) {
  invariant(token, 'GH_TOKEN is required');
  invariant(!path.startsWith('/') && !path.includes('://'), 'Expected repository-relative GitHub API path');
  const response = await fetchImpl(`https://api.github.com/repos/${REPOSITORY}/${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(30000),
  });
  if (response.status === 404 && missing) return null;
  invariant(response.ok, `GitHub ${method} ${path}: HTTP ${response.status}`);
  if (response.status === 204) return null;
  return response.json();
}

export async function pages(path, api = github) {
  const result = [];
  for (let page = 1; page <= 1000; page++) {
    const entries = await api(`${path}${path.includes('?') ? '&' : '?'}per_page=100&page=${page}`);
    invariant(Array.isArray(entries), `Malformed paginated response: ${path}`);
    result.push(...entries);
    if (entries.length < 100) return result;
  }
  throw new Error(`Pagination did not terminate: ${path}`);
}

export function validateMergedPullRequest(pr, plan, releaseSha, commit) {
  invariant(pr && pr.merged === true && pr.state === 'closed', 'Release requires a merged pull request');
  invariant(pr.merge_commit_sha === releaseSha, 'PR merge commit differs from release SHA');
  invariant(pr.base?.ref === 'main' && pr.base?.repo?.full_name === REPOSITORY && pr.head?.repo?.full_name === REPOSITORY, 'Release PR must be same-repository into main');
  const owner = REPOSITORY.split('/')[0];
  invariant(pr.merged_by?.login === owner, 'The repository owner must merge the release PR to approve publication');
  if (plan.bootstrap) {
    invariant(pr.user?.login === owner && /^release\/bootstrap-[a-z0-9-]+$/.test(pr.head?.ref), 'Bootstrap must be an owner-authored release/bootstrap-* PR');
  } else {
    invariant(pr.user?.login === 'github-actions[bot]' && pr.head?.ref === RELEASE_BRANCH, 'Expected the Changesets bot release branch and author');
  }
  invariant(commit.sha === releaseSha && commit.parents?.[0]?.sha === plan.baseSha, 'Stale release plan: first parent must equal plan.baseSha');
  invariant(SHA.test(pr.head?.sha ?? ''), 'Missing exact release PR head');
  return true;
}

export async function approveRelease({ root = ROOT, releaseSha, api = github, environment = process.env, validatePlan = true } = {}) {
  invariant(environment.GITHUB_REPOSITORY === REPOSITORY && environment.GITHUB_REF === 'refs/heads/main', 'Release entry point must run on the original repository main');
  invariant(['push', 'workflow_dispatch'].includes(environment.GITHUB_EVENT_NAME), 'Unsupported publication event');
  if (environment.GITHUB_EVENT_NAME === 'workflow_dispatch') invariant(environment.RELEASE_CONFIRM === 'true', 'Recovery requires explicit confirmation');
  invariant(SHA.test(releaseSha ?? ''), 'A full immutable release SHA is required');
  invariant(git(root, 'rev-parse', 'HEAD') === releaseSha, 'Checkout must match the approved release SHA');
  const planPath = resolve(root, '.release/plan.json');
  if (!existsSync(planPath)) return null;
  const planBytes = await readFile(planPath);
  const plan = JSON.parse(planBytes);
  const commit = await api(`commits/${releaseSha}`);
  invariant(commit.parents?.length >= 1, 'Release commit must have a first parent');
  const changed = git(root, 'diff', '--name-only', commit.parents[0].sha, releaseSha).split('\n');
  if (!changed.includes('.release/plan.json')) return null;
  invariant(plan.schemaVersion === 1 && plan.repository === REPOSITORY && SHA.test(plan.baseSha ?? ''), 'Unsupported or foreign release plan');
  invariant(Array.isArray(plan.components) && plan.components.length > 0 && plan.components.length <= 2, 'Plan must explicitly select one or two components');
  invariant(new Set(plan.components.map(c => c.component)).size === plan.components.length && plan.components.every(c => ['windows', 'npm'].includes(c.component) && c.channel === 'stable'), 'Invalid selected components/channel');
  const prs = await pages(`commits/${releaseSha}/pulls`, api);
  const candidates = prs.filter(pr => pr.merge_commit_sha === releaseSha && pr.base?.ref === 'main');
  invariant(candidates.length === 1, 'Expected exactly one merged release PR for this commit');
  const pr = await api(`pulls/${candidates[0].number}`);
  validateMergedPullRequest(pr, plan, releaseSha, commit);
  const headCommit = await api(`git/commits/${pr.head.sha}`);
  invariant(headCommit.tree?.sha === commit.commit?.tree?.sha, 'Merged release tree differs from the reviewed PR tree');
  const relation = await api(`compare/${releaseSha}...main`);
  invariant(['identical', 'ahead'].includes(relation.status) && relation.merge_base_commit?.sha === releaseSha, 'Approved release SHA is not in main history');
  if (validatePlan) execFileSync(process.execPath, ['scripts/release-version.mjs', '--check-plan', '--base', plan.baseSha, ...(validatePlan === 'structural' ? ['--structural-only'] : [])], { cwd: root, stdio: 'inherit' });
  return { schemaVersion: 1, repository: REPOSITORY, releaseSha, planSha256: hash(planBytes), baseSha: plan.baseSha, bootstrap: plan.bootstrap === true, components: plan.components, pullRequest: { number: pr.number, headSha: pr.head.sha, mergedBy: pr.merged_by.login } };
}

export async function verifyArtifactSource({ artifactId, runId, runAttempt, releaseSha, component, api = github }) {
  for (const [name, value] of Object.entries({ artifactId, runId, runAttempt })) invariant(/^[1-9][0-9]*$/.test(String(value)), `Invalid ${name}`);
  invariant(['windows', 'npm'].includes(component) && SHA.test(releaseSha), 'Invalid artifact identity');
  const run = await api(`actions/runs/${runId}`);
  invariant(run.repository?.full_name === REPOSITORY && run.head_repository?.full_name === REPOSITORY && run.path === '.github/workflows/release.yml' && ['push', 'workflow_dispatch'].includes(run.event), 'Artifact must come from the trusted release workflow');
  invariant(run.head_branch === 'main', 'Artifact source run must be on main');
  const artifact = await api(`actions/artifacts/${artifactId}`);
  invariant(artifact.id === Number(artifactId) && artifact.expired === false && artifact.workflow_run?.id === Number(runId), 'Artifact is expired or belongs to another run');
  invariant(artifact.name === `${component === 'windows' ? 'windows-installer' : 'npm-packages'}-${runId}-${runAttempt}`, 'Artifact name does not bind the original run attempt');
  invariant(/^sha256:[a-f0-9]{64}$/.test(artifact.digest ?? ''), 'Artifact archive digest is required');
  // A recovery rebuild may check out an earlier approved SHA while its dispatch head is newer.
  // The source build manifest binds the exact checkout; additionally require the source attempt job to have passed.
  const jobs = await api(`actions/runs/${runId}/attempts/${runAttempt}/jobs?per_page=100`);
  invariant(Array.isArray(jobs.jobs) && jobs.total_count <= 100, 'Unexpected or incomplete build job list');
  const expected = component === 'windows' ? 'windows-build / build' : 'npm-build';
  invariant(jobs.jobs.some(job => job.name === expected && job.conclusion === 'success'), `Original ${expected} job did not succeed`);
  return { artifactId: Number(artifactId), runId: Number(runId), runAttempt: Number(runAttempt), archiveDigest: artifact.digest, releaseSha };
}

export async function exactTag(tag, releaseSha, api = github, create = false) {
  let ref = await api(`git/ref/tags/${encodeURIComponent(tag)}`, { missing: true });
  if (ref === null) {
    invariant(create, `Missing release tag ${tag}`);
    await api('git/refs', { method: 'POST', body: { ref: `refs/tags/${tag}`, sha: releaseSha } });
    ref = await api(`git/ref/tags/${encodeURIComponent(tag)}`);
  }
  let object = ref.object;
  const seen = new Set();
  while (object?.type === 'tag') {
    invariant(!seen.has(object.sha) && seen.size < 10, 'Invalid annotated tag chain');
    seen.add(object.sha);
    object = (await api(`git/tags/${object.sha}`)).object;
  }
  invariant(object?.type === 'commit' && object.sha === releaseSha, `Tag ${tag} already points to different source; never force tags`);
}

export async function finalizeNpm() {
  assertNpmPublicationEnabled();
}

// Fake-I/O-only regression surface while real npm finalization is disabled.
export async function simulateNpmFinalization({ approval, manifest, artifactIdentity, api, root = ROOT, uploadAsset, verifyAsset }) {
  invariant(typeof api === 'function' && typeof uploadAsset === 'function' && typeof verifyAsset === 'function', 'Finalization simulation requires explicit fake I/O');
  const component = approval.components.find(c => c.component === 'npm');
  invariant(component, 'npm is not selected');
  for (const pkg of component.packages) await exactTag(pkg.tag, approval.releaseSha, api, true);
  const marker = `<!-- hellgato-release:${approval.planSha256}:${approval.releaseSha}:npm -->`;
  const notes = await readFile(resolve(root, component.notes), 'utf8');
  const body = `${notes.trim()}\n\n${marker}\n`;
  const tag = component.tag;
  invariant(artifactIdentity && artifactIdentity.releaseSha === approval.releaseSha && /^sha256:[a-f0-9]{64}$/.test(artifactIdentity.archiveDigest), 'Confirmed npm archive provenance is required');
  const publicManifest = Buffer.from(JSON.stringify({ ...manifest, artifact: { id: artifactIdentity.artifactId, runId: artifactIdentity.runId, runAttempt: artifactIdentity.runAttempt, sha256: artifactIdentity.archiveDigest.slice('sha256:'.length) } }, null, 2) + '\n');
  invariant(manifest.releaseSha === approval.releaseSha && manifest.planSha256 === approval.planSha256, 'npm manifest must bind the approved source and plan');
  const assetName = 'npm-release-manifest.json';
  let release = await api(`releases/tags/${encodeURIComponent(tag)}`, { missing: true });
  if (release) {
    invariant(release.prerelease === false && release.body === body && release.tag_name === tag && release.author?.login === 'github-actions[bot]', 'Existing npm release has conflicting metadata or ownership');
    await exactTag(tag, approval.releaseSha, api);
  } else {
    release = await api('releases', { method: 'POST', body: { tag_name: tag, target_commitish: approval.releaseSha, name: `hellgato ${component.version}`, body, draft: true, prerelease: false, make_latest: 'false' } });
  }
  const assets = await pages(`releases/${release.id}/assets`, api);
  invariant(assets.every(asset => asset.name === assetName) && assets.length <= 1, 'Unexpected assets in npm release');
  if (assets.length) await verifyAsset(assets[0], publicManifest);
  else {
    invariant(release.draft === true, 'Published npm release is incomplete; never modify published assets');
    await uploadAsset(release.id, assetName, publicManifest);
  }
  if (release.draft) release = await api(`releases/${release.id}`, { method: 'PATCH', body: { draft: false, make_latest: 'false' } });
  release = await api(`releases/tags/${encodeURIComponent(tag)}`);
  invariant(release.draft === false && release.prerelease === false && release.tag_name === tag && release.body === body, 'npm GitHub finalization did not confirm');
  const finalAssets = await pages(`releases/${release.id}/assets`, api);
  invariant(finalAssets.length === 1 && finalAssets[0].name === assetName, 'Missing public npm manifest');
  await verifyAsset(finalAssets[0], publicManifest);
  return release.html_url;
}

async function verifyRemoteAsset(asset, expected) {
  invariant(asset.size === expected.length && asset.digest === `sha256:${hash(expected)}`, 'Public manifest size/digest differs');
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}/releases/assets/${asset.id}`, { headers: { Authorization: `Bearer ${process.env.GH_TOKEN}`, Accept: 'application/octet-stream' }, signal: AbortSignal.timeout(30000) });
  invariant(response.ok, `Public manifest download failed: HTTP ${response.status}`);
  invariant(hash(Buffer.from(await response.arrayBuffer())) === hash(expected), 'Public manifest download checksum differs');
}

async function uploadRemoteAsset(releaseId, name, bytes) {
  const response = await fetch(`https://uploads.github.com/repos/${REPOSITORY}/releases/${releaseId}/assets?name=${encodeURIComponent(name)}`, { method: 'POST', headers: { Authorization: `Bearer ${process.env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' }, body: bytes, signal: AbortSignal.timeout(30000) });
  invariant(response.ok, `Manifest upload failed: HTTP ${response.status}`);
  await verifyRemoteAsset(await response.json(), bytes);
}

async function output(values) {
  if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, Object.entries(values).map(([key, value]) => `${key}=${value}\n`).join(''));
  console.log(JSON.stringify(values));
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  const options = Object.fromEntries(args.reduce((pairs, arg, index) => arg.startsWith('--') ? [...pairs, [arg.slice(2), args[index + 1]]] : pairs, []));
  const sha = options.sha || process.env.RELEASE_SHA || process.env.GITHUB_SHA;
  if (command === 'approve') {
    const approval = await approveRelease({ releaseSha: sha, validatePlan: options['structural-only'] ? 'structural' : true });
    if (!approval) {
      invariant(process.env.GITHUB_EVENT_NAME !== 'workflow_dispatch', 'Requested SHA contains no new approved release plan');
      await output({ selected: false, windows: false, npm: false, release_sha: sha });
      return;
    }
    const path = resolve(ROOT, options.output || '.release/approval.json');
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, JSON.stringify(approval, null, 2) + '\n');
    await output({ selected: true, windows: approval.components.some(c => c.component === 'windows'), npm: approval.components.some(c => c.component === 'npm'), release_sha: sha, plan_sha256: approval.planSha256 });
  } else if (command === 'verify-artifact') {
    const identity = await verifyArtifactSource({ artifactId: options['artifact-id'], runId: options['run-id'], runAttempt: options['run-attempt'], releaseSha: sha, component: options.component });
    if (options.directory) execFileSync('python', ['scripts/verify-artifact.py', '--artifact-id', String(identity.artifactId), '--sha256', identity.archiveDigest.slice('sha256:'.length), '--directory', options.directory], { cwd: ROOT, stdio: 'inherit' });
    console.log(JSON.stringify(identity));
  } else if (command === 'finalize-npm') {
    assertNpmPublicationEnabled();
    invariant(process.env.RELEASE_ENABLED === 'true', 'Publication remains disabled until setup is approved');
    const approval = await approveRelease({ releaseSha: sha, validatePlan: 'structural' });
    invariant(approval, 'Missing approved plan');
    const manifest = JSON.parse(await readFile(resolve(options.artifacts || 'dist/npm', 'npm-artifact-manifest.json'), 'utf8'));
    execFileSync(process.execPath, ['scripts/npm-release.mjs', 'confirm', '--artifacts', options.artifacts || 'dist/npm', '--plan', '.release/plan.json', '--release-sha', sha, '--repository', REPOSITORY, '--run-id', process.env.NPM_BUILD_RUN_ID, '--run-attempt', process.env.NPM_BUILD_RUN_ATTEMPT], { cwd: ROOT, stdio: 'inherit' });
    const artifactIdentity = await verifyArtifactSource({ artifactId: process.env.NPM_ARTIFACT_ID, runId: process.env.NPM_BUILD_RUN_ID, runAttempt: process.env.NPM_BUILD_RUN_ATTEMPT, releaseSha: sha, component: 'npm' });
    console.log(await finalizeNpm({ approval, manifest, artifactIdentity, uploadAsset: uploadRemoteAsset, verifyAsset: verifyRemoteAsset }));
  } else throw new Error('Expected approve, verify-artifact, or finalize-npm');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch(error => { console.error(`Release refused: ${error.message}`); process.exitCode = 1; });

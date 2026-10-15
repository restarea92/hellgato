/** Prepare an immutable Windows DRAFT, never publish one.
 *
 * The reviewed version-only commit is the build source. A retry can reuse a
 * prior build attempt, but can never move a tag, replace assets, edit a public
 * release, or turn a draft public. Historical releases are read-only.
 * GitHub has no atomic "upload only if still draft" API: people must wait for
 * this workflow to succeed before manually publishing. We recheck immediately
 * before every upload and fail closed on any changed/unknown remote state.
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFile, lstat, readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const REPOSITORY = 'restarea92/hellgato';
const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const VERSION_FILE = 'packages/windows-release/package.json';
const SHA = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const MARKER = '<!-- hellgato-windows-draft:v1 ';
const GUIDANCE = '\n\nPrepared as a draft. Publish manually only after the Windows release workflow succeeds and the installer, SHA-256, and changelog assets are present.\n';
function check(ok, message) { if (!ok) throw new Error(message); }
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function keys(value, names) { return object(value) && Object.keys(value).sort().join(',') === [...names].sort().join(','); }
export function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function id(value, label) {
  check(/^[1-9]\d*$/.test(String(value)) && Number.isSafeInteger(Number(value)), `Invalid ${label}`);
  return Number(value);
}
function stable(version) {
  check(typeof version === 'string' && VERSION.test(version), 'A canonical stable Windows version is required');
  const parts = version.split('.').map(Number);
  check(parts.every(n => n <= 65535), 'Windows numeric version exceeds 65535');
  return parts;
}
export function compareVersions(a, b) {
  const aa = stable(a), bb = stable(b);
  for (let i = 0; i < 3; i++) if (aa[i] !== bb[i]) return aa[i] > bb[i] ? 1 : -1;
  return 0;
}
function git(root, ...args) { return execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trimEnd(); }
function blob(root, sha, path) {
  const entry = git(root, 'ls-tree', sha, '--', path);
  check(/^100(?:644|755) blob [0-9a-f]{40}\t/.test(entry), `Missing or unsafe source file: ${path}`);
  return execFileSync('git', ['show', `${sha}:${path}`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
}
function jsonBlob(root, sha, path) { return JSON.parse(blob(root, sha, path).toString('utf8')); }
function windowsPackage(root, sha) {
  const pkg = jsonBlob(root, sha, VERSION_FILE);
  check(pkg.name === '@hellgato/windows-release' && pkg.private === true, 'Invalid private Windows package');
  stable(pkg.version);
  return pkg;
}
function ancestor(root, base, head) {
  try { git(root, 'merge-base', '--is-ancestor', base, head); return true; }
  catch (error) { if (error.status === 1) return false; throw error; }
}
function metadata(root, sourceSha, version) {
  const runtime = jsonBlob(root, sourceSha, 'app/release.json');
  check(keys(runtime, ['version', 'channel']) && runtime.version === version && runtime.channel === 'stable', 'App release metadata does not match Windows version');
  const include = blob(root, sourceSha, 'installer/version.iss').toString('utf8');
  const definitions = [...include.matchAll(/^#define AppVersion "([^"\r\n]+)"[ \t]*$/gm)].map(m => m[1]);
  const numeric = [...include.matchAll(/^#define AppVersionNumeric "([^"\r\n]+)"[ \t]*$/gm)].map(m => m[1]);
  check(JSON.stringify(definitions) === JSON.stringify([version]) && JSON.stringify(numeric) === JSON.stringify([`${version}.65535`]), 'Installer metadata does not match Windows version');
  const source = blob(root, sourceSha, 'installer/hellgato.iss').toString('utf8');
  check([...source.matchAll(/^#include "version.iss"[ \t]*$/gm)].length === 1 && !/^#define AppVersion(?:Numeric)?\b/m.test(source), 'Installer must use generated version metadata');
  const notes = blob(root, sourceSha, `docs/releases/${version}.md`);
  const text = notes.toString('utf8');
  check(notes.length <= 60_000 && text.trim().length > 30 && /^# [^\r\n]+\n\n\S/.test(text) && text.split('\n')[0].endsWith(` ${version}`) && !text.includes(MARKER), 'Missing or invalid versioned release notes');
  const changelog = blob(root, sourceSha, 'packages/windows-release/CHANGELOG.md').toString('utf8');
  const headings = [...changelog.matchAll(/^## ([^\r\n]+)$/gm)];
  check(headings[0]?.[1] === version && headings.filter(m => m[1] === version).length === 1, 'Windows changelog must start with the selected version exactly once');
  const entry = changelog.slice(headings[0].index + headings[0][0].length, headings[1]?.index ?? changelog.length).trim();
  check(entry.length > 0 && text.includes(entry), 'Release notes must contain the selected Windows changelog entry');
  return notes;
}
function versionOnlyCommit(root, sourceSha, parent, version) {
  const changed = git(root, 'diff', '--no-renames', '--name-status', parent, sourceSha).split('\n').filter(Boolean);
  const packagePaths = new Set();
  let hasNotes = false;
  for (const line of changed) {
    const [status, path, extra] = line.split('\t');
    check(!extra && /^[AMD]$/.test(status), 'Unsupported file operation in version commit');
    if (/^\.changeset\/[a-z0-9][a-z0-9-]*\.md$/.test(path) && status === 'D') continue;
    if (path === `docs/releases/${version}.md` && status === 'A') { hasNotes = true; continue; }
    if (/^packages\/(core|hellgato|windows-release)\/(package\.json|CHANGELOG\.md)$/.test(path) && ['A', 'M'].includes(status)) {
      blob(root, sourceSha, path); // Reject symlinks, including for changelogs.
      if (path.endsWith('/package.json')) packagePaths.add(path);
      continue;
    }
    check(['app/release.json', 'installer/version.iss', 'pnpm-lock.yaml'].includes(path) && status === 'M', `Unreviewed source change in version commit: ${path}`);
    blob(root, sourceSha, path);
  }
  check(hasNotes && packagePaths.has(VERSION_FILE), 'Version commit must add its own notes and increase the Windows package version');
  for (const path of packagePaths) {
    const before = jsonBlob(root, parent, path), after = jsonBlob(root, sourceSha, path);
    check(compareVersions(after.version, before.version) >= 0, `Package version decreased: ${path}`);
    delete before.version; delete after.version;
    check(JSON.stringify(before) === JSON.stringify(after), `Version commit changes package metadata beyond version: ${path}`);
  }
}

/** Offline preflight. No token/network/dependencies and no build on no-op pushes. */
export function preflight({ root = ROOT, context }) {
  const { repository, eventName, ref, workflowSha, sourceSha = workflowSha, expectedVersion, event } = context;
  check(repository === REPOSITORY && ref === 'refs/heads/main' && ['push', 'workflow_dispatch'].includes(eventName), 'Only the main repository main branch may prepare drafts');
  check(SHA.test(workflowSha ?? '') && SHA.test(sourceSha ?? ''), 'Full source and workflow commit SHAs are required');
  check(git(root, 'rev-parse', 'HEAD') === workflowSha, 'Checkout does not match the exact workflow SHA');
  check(git(root, 'status', '--porcelain', '--untracked-files=no') === '', 'Tracked checkout files have unreviewed edits');
  check(object(event) && event.repository?.full_name === REPOSITORY, 'Unexpected event repository');
  const version = windowsPackage(root, sourceSha).version;
  if (eventName === 'push') {
    check(sourceSha === workflowSha && event.after === workflowSha && event.ref === ref && event.forced === false && event.deleted === false && SHA.test(event.before ?? '') && !/^0+$/.test(event.before), 'Invalid or forced main push');
    check(ancestor(root, event.before, sourceSha), 'Push baseline is not an ancestor of source');
    const increase = compareVersions(version, windowsPackage(root, event.before).version);
    check(increase >= 0, 'Windows version must not decrease');
    if (increase === 0) return { shouldRelease: false, sourceSha, version };
  } else {
    check(event.inputs?.source_sha === sourceSha && event.inputs?.version === expectedVersion && expectedVersion === version, 'Manual retry must select the exact full version-commit SHA and version');
  }
  const parent = git(root, 'rev-parse', `${sourceSha}^1`);
  check(compareVersions(version, windowsPackage(root, parent).version) > 0, 'Selected commit must itself increase the Windows version');
  const main = git(root, 'rev-parse', 'refs/remotes/origin/main');
  check(SHA.test(main) && ancestor(root, sourceSha, main) && ancestor(root, workflowSha, main), 'Source must be a merged main commit');
  check(windowsPackage(root, main).version === version, 'Stale source: main has a different Windows version');
  versionOnlyCommit(root, sourceSha, parent, version);
  const notes = metadata(root, sourceSha, version);
  return { shouldRelease: true, sourceSha, version, notes };
}

async function fileBytes(path) {
  const stat = await lstat(path);
  check(stat.isFile() && !stat.isSymbolicLink() && stat.size > 0, `Missing or unsafe artifact: ${path}`);
  return readFile(path);
}
function fileRecord(name, bytes) { return { name, size: bytes.length, sha256: sha256(bytes) }; }
function equal(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
export async function readBuild({ root = ROOT, selected, build }) {
  const { sourceSha, version } = selected;
  check(build.sourceSha === sourceSha && build.version === version, 'Reusable build source/version mismatch');
  id(build.runId, 'run ID'); id(build.artifactId, 'artifact ID');
  const match = /^windows-installer-([1-9]\d*)-([1-9]\d*)$/.exec(build.artifactName ?? '');
  check(match && id(match[1], 'artifact run ID') === Number(build.runId), 'Artifact name does not belong to this workflow run');
  check(id(match[2], 'build attempt') <= id(build.runAttempt, 'current run attempt'), 'Artifact is from a future run attempt');
  check(DIGEST.test(build.artifactDigest ?? ''), 'Missing original artifact SHA-256');
  const directory = resolve(root, 'dist');
  check((await lstat(directory)).isDirectory() && !(await lstat(directory)).isSymbolicLink(), 'Unsafe artifact directory');
  const installerName = `Hellgato-${version}-Setup.exe`;
  const names = [installerName, `${installerName}.sha256`, 'build-manifest.json'];
  check(equal((await readdir(directory)).sort(), names.slice().sort()), 'Artifact must contain exactly installer, checksum, and build manifest');
  const installer = await fileBytes(resolve(directory, installerName));
  const checksum = await fileBytes(resolve(directory, `${installerName}.sha256`));
  check(checksum.toString('ascii').trim() === `${sha256(installer)}  ${installerName}`, 'Installer checksum/name mismatch');
  const manifest = JSON.parse(await fileBytes(resolve(directory, 'build-manifest.json')));
  check(keys(manifest, ['schemaVersion', 'repository', 'component', 'version', 'channel', 'tag', 'releaseSha', 'planSha256', 'build', 'files', 'sources', 'toolchain']) && manifest.schemaVersion === 1 && manifest.repository === REPOSITORY && manifest.component === 'windows' && manifest.version === version && manifest.channel === 'stable' && manifest.tag === `v${version}` && manifest.releaseSha === sourceSha && manifest.planSha256 === null, 'Build manifest does not bind the selected stable source');
  check(keys(manifest.build, ['runId', 'runAttempt']) && manifest.build.runId === Number(build.runId) && manifest.build.runAttempt === Number(match[2]), 'Build manifest run/attempt mismatch');
  const records = [fileRecord(installerName, installer), fileRecord(`${installerName}.sha256`, checksum)];
  check(Array.isArray(manifest.files) && manifest.files.length === 2 && records.every(expected => manifest.files.some(actual => keys(actual, ['name', 'size', 'sha256']) && actual.name === expected.name && actual.size === expected.size && actual.sha256 === expected.sha256)), 'Build manifest file digest/size mismatch');
  check(object(manifest.sources) && object(manifest.toolchain) && Object.keys(manifest.toolchain).length > 0, 'Missing build source/toolchain provenance');
  for (const path of [VERSION_FILE, 'app/release.json', 'installer/version.iss', `docs/releases/${version}.md`]) check(path in manifest.sources, `Missing build source digest: ${path}`);
  for (const [path, digest] of Object.entries(manifest.sources)) {
    check(/^[a-zA-Z0-9_./-]+$/.test(path) && !path.startsWith('/') && !path.split('/').some(part => ['', '.', '..'].includes(part)) && DIGEST.test(digest), 'Invalid source digest path');
    check(sha256(blob(root, sourceSha, path)) === digest, `Build source digest mismatch: ${path}`);
  }
  return [
    { ...records[0], bytes: installer, type: 'application/vnd.microsoft.portable-executable' },
    { ...records[1], bytes: checksum, type: 'text/plain' },
    { ...fileRecord(`Hellgato-${version}-CHANGELOG.md`, selected.notes), bytes: selected.notes, type: 'text/markdown' },
  ];
}

/** Narrow API adapter: the only writes are create-draft and append-asset. */
export function createGitHubApi({ token, fetchImpl = fetch }) {
  check(typeof token === 'string' && token.length > 0, 'GH_TOKEN is required in the draft job');
  return {
    async request(method, path, { body, bytes, type, allowMissing = false, binary = false } = {}) {
      check(typeof path === 'string' && /^[a-zA-Z0-9_./?=&%-]+$/.test(path) && !path.split('/').includes('..') && !path.startsWith('/'), 'Unsafe GitHub API path');
      const upload = method === 'POST' && /^releases\/[1-9]\d*\/assets\?name=[a-zA-Z0-9_.%-]+$/.test(path);
      check(method === 'GET' || upload || (method === 'POST' && path === 'releases' && body?.draft === true && body?.prerelease === false && body?.make_latest === 'false' && SHA.test(body?.target_commitish ?? '')), 'Only draft creation and asset append are allowed');
      const url = `https://${upload ? 'uploads' : 'api'}.github.com/repos/${REPOSITORY}/${path}`;
      const headers = { Authorization: `Bearer ${token}`, Accept: binary ? 'application/octet-stream' : 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };
      if (body) headers['Content-Type'] = 'application/json';
      if (bytes) headers['Content-Type'] = type;
      let response = await fetchImpl(url, { method, headers, body: bytes ?? (body ? JSON.stringify(body) : undefined), redirect: 'manual', signal: AbortSignal.timeout(120_000) });
      if (binary && method === 'GET' && response.status === 302) {
        const location = new URL(response.headers.get('location'));
        check(location.protocol === 'https:' && (location.hostname.endsWith('.githubusercontent.com') || location.hostname.endsWith('.amazonaws.com')) && !location.username && !location.password, 'Unexpected asset download redirect');
        // Never forward GH_TOKEN to signed object-storage URLs.
        response = await fetchImpl(location.href, { redirect: 'error', signal: AbortSignal.timeout(120_000) });
      }
      if (allowMissing && response.status === 404) return null;
      check(response.status === (method === 'GET' ? 200 : 201), `GitHub ${method} ${path.split('?')[0]} failed (HTTP ${response.status}); no automatic write retry`);
      if (binary) return Buffer.from(await response.arrayBuffer());
      const result = await response.json();
      check(object(result) || Array.isArray(result), 'Invalid GitHub JSON response');
      return result;
    },
  };
}
async function pages(api, path) {
  const result = [], seen = new Set();
  // An empty page is the only completion signal. Errors/truncation never mean absent.
  for (let page = 1; page <= 100; page++) {
    const batch = await api.request('GET', `${path}?per_page=100&page=${page}`);
    check(Array.isArray(batch) && batch.length <= 100, 'Invalid GitHub pagination');
    if (!batch.length) return result;
    for (const item of batch) {
      const key = id(item?.id, 'paginated item ID');
      check(!seen.has(key), 'Duplicate GitHub pagination item');
      seen.add(key); result.push(item);
    }
  }
  throw new Error('GitHub pagination exceeded its safe bound');
}
async function tagCommit(api, tag) {
  const ref = await api.request('GET', `git/ref/tags/${tag}`, { allowMissing: true });
  if (ref === null) return null;
  let current = ref.object;
  const seen = new Set();
  while (current?.type === 'tag') {
    check(SHA.test(current.sha) && !seen.has(current.sha) && seen.size < 10, 'Invalid annotated tag chain');
    seen.add(current.sha);
    current = (await api.request('GET', `git/tags/${current.sha}`)).object;
  }
  check(current?.type === 'commit' && SHA.test(current.sha), 'Tag must resolve to a commit');
  return current.sha;
}
async function currentMain(api, selected) {
  const main = (await api.request('GET', 'git/ref/heads/main')).object;
  check(main?.type === 'commit' && SHA.test(main.sha), 'Missing current main commit');
  const comparison = await api.request('GET', `compare/${selected.sourceSha}...${main.sha}`);
  check(['ahead', 'identical'].includes(comparison.status) && comparison.base_commit?.sha === selected.sourceSha && comparison.merge_base_commit?.sha === selected.sourceSha, 'Selected source is no longer on main');
  const pkg = await api.request('GET', `contents/${VERSION_FILE}?ref=${main.sha}`);
  check(pkg.encoding === 'base64' && typeof pkg.content === 'string', 'Missing current main version');
  check(JSON.parse(Buffer.from(pkg.content, 'base64')).version === selected.version, 'Stale source: current main has a different Windows version');
}
async function verifyRemoteBuild(api, context, build) {
  const artifact = await api.request('GET', `actions/artifacts/${id(build.artifactId, 'artifact ID')}`);
  check(artifact.id === Number(build.artifactId) && artifact.name === build.artifactName && artifact.expired === false && artifact.digest === `sha256:${build.artifactDigest}` && artifact.workflow_run?.id === Number(build.runId) && artifact.workflow_run?.head_sha === context.workflowSha && artifact.workflow_run?.head_branch === 'main', 'Original Actions artifact identity/digest mismatch');
  const run = await api.request('GET', `actions/runs/${id(build.runId, 'run ID')}`);
  check(run.id === Number(build.runId) && run.head_sha === context.workflowSha && run.head_branch === 'main' && run.event === context.eventName && run.path === '.github/workflows/windows-release.yml' && run.repository?.full_name === REPOSITORY, 'Unexpected artifact workflow provenance');
}
function identity(selected, files) {
  return { schemaVersion: 1, sourceSha: selected.sourceSha, version: selected.version, files: files.map(({ name, size, sha256 }) => ({ name, size, sha256 })) };
}
function draftBody(selected, binding) { return selected.notes.toString('utf8').trimEnd() + GUIDANCE + `\n${MARKER}${JSON.stringify(binding)} -->\n`; }
function assertDraft(release, selected, binding) {
  check(release?.draft === true && release.prerelease === false && release.tag_name === `v${selected.version}` && release.target_commitish === selected.sourceSha && release.name === `Hellgato ${selected.version}` && release.body === draftBody(selected, binding), 'Conflicting or no-longer-draft release; nothing was overwritten');
  id(release.id, 'draft release ID');
}
async function assetsFor(api, releaseId, files) {
  const assets = await pages(api, `releases/${releaseId}/assets`), byName = new Map();
  for (const asset of assets) {
    const file = files.find(f => f.name === asset.name);
    check(file && !byName.has(asset.name) && asset.state === 'uploaded' && asset.size === file.size, 'Conflicting/partial asset; resume requires the same complete asset bytes');
    if (asset.digest != null) check(asset.digest === `sha256:${file.sha256}`, 'Existing asset digest differs; never replacing it');
    const bytes = await api.request('GET', `releases/assets/${id(asset.id, 'asset ID')}`, { binary: true });
    check(Buffer.isBuffer(bytes) && bytes.length === file.size && sha256(bytes) === file.sha256, 'Existing asset bytes differ; never replacing them');
    byName.set(asset.name, asset);
  }
  return byName;
}

export async function prepareDraft({ root = ROOT, context, build, api }) {
  const selected = preflight({ root, context });
  if (!selected.shouldRelease) return { status: 'no-version-increase' };
  const files = await readBuild({ root, selected, build });
  await currentMain(api, selected);
  await verifyRemoteBuild(api, context, build);
  const releases = await pages(api, 'releases');
  for (const release of releases) {
    check(typeof release.draft === 'boolean' && typeof release.prerelease === 'boolean' && typeof release.tag_name === 'string', 'Unknown release state');
    if (/^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(release.tag_name)) check(compareVersions(release.tag_name.slice(1), selected.version) <= 0, 'A newer Windows release already exists');
  }
  const tag = `v${selected.version}`;
  const matching = releases.filter(r => r.tag_name === tag);
  check(matching.length <= 1, 'Multiple releases use the selected tag');
  const existingTag = await tagCommit(api, tag);
  check(existingTag === null || existingTag === selected.sourceSha, 'Existing tag points to a different commit; never moving it');
  let release = matching[0];
  if (release?.draft === false) {
    check(existingTag === selected.sourceSha && release.prerelease === false, 'Published release conflicts with selected stable source');
    return { status: 'already-public', releaseId: release.id };
  }
  const binding = identity(selected, files);
  if (release) assertDraft(release, selected, binding);
  else {
    // No tag creation, tag force-update, PATCH, DELETE, public latest, or npm action.
    // A failed/uncertain create is not retried; the next run re-lists first.
    await currentMain(api, selected);
    check([null, selected.sourceSha].includes(await tagCommit(api, tag)), 'Tag changed before draft creation');
    release = await api.request('POST', 'releases', { body: { tag_name: tag, target_commitish: selected.sourceSha, name: `Hellgato ${selected.version}`, body: draftBody(selected, binding), draft: true, prerelease: false, make_latest: 'false', generate_release_notes: false } });
    assertDraft(release, selected, binding);
  }
  const releaseId = id(release.id, 'release ID');
  let existing = await assetsFor(api, releaseId, files);
  const complete = existing.size === files.length;
  for (const file of files) {
    if (existing.has(file.name)) continue;
    await currentMain(api, selected);
    check([null, selected.sourceSha].includes(await tagCommit(api, tag)), 'Tag changed before asset upload');
    assertDraft(await api.request('GET', `releases/${releaseId}`), selected, binding);
    // Re-list after checking state; a prior failed response or concurrent upload
    // can only be reused if exact. Never delete a starter or overwrite an asset.
    existing = await assetsFor(api, releaseId, files);
    if (existing.has(file.name)) continue;
    assertDraft(await api.request('GET', `releases/${releaseId}`), selected, binding);
    const uploaded = await api.request('POST', `releases/${releaseId}/assets?name=${encodeURIComponent(file.name)}`, { bytes: file.bytes, type: file.type });
    check(uploaded.name === file.name && uploaded.state === 'uploaded' && uploaded.size === file.size, 'Asset upload returned unexpected state; retry only after inspection');
  }
  assertDraft(await api.request('GET', `releases/${releaseId}`), selected, binding);
  check((await assetsFor(api, releaseId, files)).size === files.length, 'Draft is incomplete');
  return { status: complete ? 'already-complete-draft' : 'draft-ready', releaseId, version: selected.version, sourceSha: selected.sourceSha };
}

async function cli() {
  const env = process.env;
  const context = { repository: env.GITHUB_REPOSITORY, eventName: env.GITHUB_EVENT_NAME, ref: env.GITHUB_REF, workflowSha: env.GITHUB_SHA, sourceSha: env.RELEASE_SOURCE_SHA || env.GITHUB_SHA, expectedVersion: env.RELEASE_EXPECTED_VERSION, event: JSON.parse(await readFile(env.GITHUB_EVENT_PATH, 'utf8')) };
  if (process.argv[2] === 'preflight') {
    const selected = preflight({ context });
    if (env.GITHUB_OUTPUT) await appendFile(env.GITHUB_OUTPUT, `should-release=${selected.shouldRelease}\nsource-sha=${selected.sourceSha}\nversion=${selected.version}\n`);
    console.log(selected.shouldRelease ? `Version ${selected.version} at ${selected.sourceSha} is ready for a draft build.` : 'No Windows version increase; no draft build.');
  } else if (process.argv[2] === 'prepare') {
    const build = { sourceSha: env.BUILD_SOURCE_SHA, version: env.BUILD_VERSION, runId: env.GITHUB_RUN_ID, runAttempt: env.GITHUB_RUN_ATTEMPT, artifactId: env.BUILD_ARTIFACT_ID, artifactName: env.BUILD_ARTIFACT_NAME, artifactDigest: env.BUILD_ARTIFACT_DIGEST };
    console.log(JSON.stringify(await prepareDraft({ context, build, api: createGitHubApi({ token: env.GH_TOKEN }) })));
  } else throw new Error('Expected preflight or prepare');
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) cli().catch(error => { console.error(`Windows draft preparation failed: ${error.message}`); process.exitCode = 1; });

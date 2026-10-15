import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rename, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { REPOSITORY, sha256, preflight, readBuild, prepareDraft, createGitHubApi, compareVersions } from '../scripts/draft-windows-release.mjs';

const git = (root, ...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
async function put(root, path, value) { await mkdir(resolve(root, path, '..'), { recursive: true }); await writeFile(resolve(root, path), typeof value === 'string' || Buffer.isBuffer(value) ? value : JSON.stringify(value, null, 2) + '\n'); }
async function commit(root) { git(root, 'add', '.'); git(root, 'commit', '-qm', 'Fixture version'); return git(root, 'rev-parse', 'HEAD'); }
async function setVersion(root, version, { newNotes = true } = {}) {
  await put(root, 'packages/windows-release/package.json', { name: '@hellgato/windows-release', version, private: true });
  await put(root, 'app/release.json', { version, channel: 'stable' });
  await put(root, 'installer/version.iss', `; Generated metadata.\n#define AppVersion "${version}"\n#define AppVersionNumeric "${version}.65535"\n`);
  const entry = '### Patch Changes\n\n- Fix a Windows device connection and preserve diagnostic details.';
  await put(root, 'packages/windows-release/CHANGELOG.md', `# @hellgato/windows-release\n\n## ${version}\n\n${entry}\n`);
  if (newNotes) await put(root, `docs/releases/${version}.md`, `# Hellgato ${version}\n\n${entry}\n`);
}
async function fixture(t, { version = '0.1.4', sourceEdit = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-draft-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  git(root, 'init', '-q', '--initial-branch=main');
  git(root, 'config', 'user.name', 'Draft test'); git(root, 'config', 'user.email', 'draft@example.invalid');
  await put(root, '.gitignore', 'dist/\n');
  await put(root, 'installer/hellgato.iss', '#include "version.iss"\n');
  await put(root, 'app/main.py', '# reviewed source\n');
  await put(root, '.changeset/fix-windows.md', '---\n"@hellgato/windows-release": patch\n---\n\nFix Windows.\n');
  await put(root, 'pnpm-lock.yaml', 'lockfileVersion: 9.0\n');
  await setVersion(root, '0.1.3');
  const before = await commit(root);
  await setVersion(root, version);
  await rm(resolve(root, '.changeset/fix-windows.md'));
  if (sourceEdit) await put(root, 'app/main.py', '# unreviewed source in version commit\n');
  const sourceSha = await commit(root);
  git(root, 'update-ref', 'refs/remotes/origin/main', sourceSha);
  const context = { repository: REPOSITORY, eventName: 'push', ref: 'refs/heads/main', workflowSha: sourceSha, sourceSha, event: { repository: { full_name: REPOSITORY }, ref: 'refs/heads/main', before, after: sourceSha, forced: false, deleted: false } };
  const name = `Hellgato-${version}-Setup.exe`, installer = Buffer.from('MZ fake installer payload for offline unit tests');
  const checksum = Buffer.from(`${sha256(installer)}  ${name}\n`);
  const sourcePaths = ['packages/windows-release/package.json', 'app/release.json', 'installer/version.iss', `docs/releases/${version}.md`];
  const sources = Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path, sha256(await readFile(resolve(root, path)))])));
  const manifest = { schemaVersion: 1, repository: REPOSITORY, component: 'windows', version, channel: 'stable', tag: `v${version}`, releaseSha: sourceSha, planSha256: null, build: { runId: 100, runAttempt: 1 }, files: [{ name, size: installer.length, sha256: sha256(installer) }, { name: `${name}.sha256`, size: checksum.length, sha256: sha256(checksum) }], sources, toolchain: { NODE_VERSION: '24.19.0' } };
  await put(root, `dist/${name}`, installer); await put(root, `dist/${name}.sha256`, checksum); await put(root, 'dist/build-manifest.json', manifest);
  const build = { sourceSha, version, runId: '100', runAttempt: '1', artifactId: '200', artifactName: 'windows-installer-100-1', artifactDigest: 'b'.repeat(64) };
  return { root, before, sourceSha, version, context, build, manifest, name };
}
function fakeApi(fixture, { historical = true } = {}) {
  const { context, build, sourceSha, version } = fixture;
  const state = { releases: historical ? [{ id: 1, tag_name: 'v0.1.0', draft: false, prerelease: false, body: 'Never modify historical releases.' }] : [], assets: new Map(), tags: new Map(), calls: [], writes: [], mainSha: context.workflowSha, mainVersion: version, sourceOnMain: true, createCount: 0, beforeCall: null };
  const api = { async request(method, path, options = {}) {
    state.calls.push({ method, path });
    if (state.beforeCall) await state.beforeCall(method, path, options);
    if (method !== 'GET') state.writes.push({ method, path, options });
    if (path === 'git/ref/heads/main') return { object: { type: 'commit', sha: state.mainSha } };
    if (path.startsWith('compare/')) return { status: state.sourceOnMain ? state.mainSha === sourceSha ? 'identical' : 'ahead' : 'diverged', base_commit: { sha: sourceSha }, merge_base_commit: { sha: sourceSha } };
    if (path.startsWith('contents/')) return { encoding: 'base64', content: Buffer.from(JSON.stringify({ version: state.mainVersion })).toString('base64') };
    if (path === 'actions/artifacts/200') return { id: 200, name: build.artifactName, expired: false, digest: `sha256:${build.artifactDigest}`, workflow_run: { id: 100, head_sha: context.workflowSha, head_branch: 'main' }, ...state.artifactOverrides };
    if (path === 'actions/runs/100') return { id: 100, head_sha: context.workflowSha, head_branch: 'main', event: context.eventName, path: '.github/workflows/windows-release.yml', repository: { full_name: REPOSITORY }, ...state.runOverrides };
    if (path.startsWith('git/ref/tags/')) { const value = state.tags.get(path.slice('git/ref/tags/'.length)); return value ? { object: value } : null; }
    if (path.startsWith('git/tags/')) return { object: state.annotatedTags.get(path.slice('git/tags/'.length)) };
    if (method === 'GET' && /^releases\?/.test(path)) { const page = Number(new URLSearchParams(path.split('?')[1]).get('page')); return structuredClone(state.releases.slice((page - 1) * 100, page * 100)); }
    if (method === 'POST' && path === 'releases') {
      assert.equal(options.body.draft, true); assert.equal(options.body.make_latest, 'false');
      assert.equal(options.body.target_commitish, sourceSha);
      const release = { id: 500 + state.createCount++, ...options.body };
      state.releases.push(release); state.assets.set(release.id, []);
      return structuredClone(release);
    }
    const releaseMatch = /^releases\/(\d+)$/.exec(path);
    if (releaseMatch) return structuredClone(state.releases.find(r => r.id === Number(releaseMatch[1])));
    const assetsMatch = /^releases\/(\d+)\/assets\?(.+)$/.exec(path);
    if (assetsMatch) {
      const releaseId = Number(assetsMatch[1]), params = new URLSearchParams(assetsMatch[2]);
      const list = state.assets.get(releaseId) ?? [];
      if (method === 'GET') { const page = Number(params.get('page')); return structuredClone(list.slice((page - 1) * 100, page * 100).map(({ bytes, ...asset }) => asset)); }
      assert.equal(state.releases.find(r => r.id === releaseId).draft, true, 'Never append to a public release');
      assert.ok(!list.some(a => a.name === params.get('name')), 'Never overwrite or duplicate an asset');
      const asset = { id: 1000 + [...state.assets.values()].flat().length, name: params.get('name'), state: 'uploaded', size: options.bytes.length, digest: `sha256:${sha256(options.bytes)}`, bytes: Buffer.from(options.bytes) };
      list.push(asset); state.assets.set(releaseId, list);
      const { bytes, ...metadata } = asset; return metadata;
    }
    const download = /^releases\/assets\/(\d+)$/.exec(path);
    if (download) return Buffer.from([...state.assets.values()].flat().find(a => a.id === Number(download[1])).bytes);
    throw new Error(`Unexpected fake API request: ${method} ${path}`);
  } };
  return { api, state };
}
const draft = (f, api) => prepareDraft({ ...f, api });

test('numeric stable version comparison rejects prereleases and invalid Windows versions', () => {
  assert.equal(compareVersions('0.10.0', '0.9.9'), 1);
  assert.equal(compareVersions('1.0.0', '1.0.0'), 0);
  for (const bad of ['01.2.3', '1.0.0-beta.1', '65536.0.0', '1.2', '1.2.3\n']) assert.throws(() => compareVersions(bad, '1.0.0'));
});

test('valid version-only main merge passes offline preflight', async t => {
  const f = await fixture(t);
  const result = preflight(f);
  assert.equal(result.shouldRelease, true); assert.equal(result.sourceSha, f.sourceSha); assert.equal(result.version, '0.1.4');
});

test('ordinary push does no remote lookup or draft work', async t => {
  const f = await fixture(t);
  const before = f.sourceSha;
  await put(f.root, 'app/main.py', '# ordinary follow-on commit\n');
  const next = await commit(f.root); git(f.root, 'update-ref', 'refs/remotes/origin/main', next);
  f.context = { ...f.context, workflowSha: next, sourceSha: next, event: { ...f.context.event, before, after: next } };
  const result = await draft(f, { request() { assert.fail('No network for a no-version push'); } });
  assert.deepEqual(result, { status: 'no-version-increase' });
});

test('version-only guard rejects source edits, historical note edits, and dependency edits', async t => {
  const badSource = await fixture(t, { sourceEdit: true });
  assert.throws(() => preflight(badSource), /Unreviewed source change/);
  for (const change of ['history', 'dependency']) {
    const f = await fixture(t);
    if (change === 'history') await put(f.root, 'docs/releases/0.1.3.md', '# Rewritten historical notes\n');
    else { const pkg = JSON.parse(await readFile(resolve(f.root, 'packages/windows-release/package.json'))); pkg.dependencies = { unsafe: '*' }; await put(f.root, 'packages/windows-release/package.json', pkg); }
    git(f.root, 'add', '.'); git(f.root, 'commit', '--amend', '--no-edit', '-q');
    const sha = git(f.root, 'rev-parse', 'HEAD'); git(f.root, 'update-ref', 'refs/remotes/origin/main', sha);
    f.context.workflowSha = f.context.sourceSha = f.context.event.after = sha;
    assert.throws(() => preflight(f), /Unreviewed source change|beyond version/);
  }
});

test('preflight rejects forced/wrong-ref/wrong-repository/dirty/stale runs', async t => {
  const f = await fixture(t);
  for (const context of [{ ...f.context, ref: 'refs/heads/feature' }, { ...f.context, repository: 'fork/hellgato' }, { ...f.context, event: { ...f.context.event, forced: true } }, { ...f.context, workflowSha: 'a'.repeat(40) }]) assert.throws(() => preflight({ ...f, context }));
  await put(f.root, 'app/main.py', '# dirty source\n'); assert.throws(() => preflight(f), /unreviewed edits/);
  git(f.root, 'checkout', '--', 'app/main.py');
  await setVersion(f.root, '0.1.5'); const latest = await commit(f.root); git(f.root, 'update-ref', 'refs/remotes/origin/main', latest);
  git(f.root, 'checkout', '--detach', f.sourceSha);
  assert.throws(() => preflight(f), /Stale source/);
});

test('manual retry pins an earlier merged version commit, not later unversioned source', async t => {
  const f = await fixture(t);
  await put(f.root, 'app/main.py', '# later main source, intentionally not released\n');
  const latest = await commit(f.root); git(f.root, 'update-ref', 'refs/remotes/origin/main', latest);
  f.context = { ...f.context, workflowSha: latest, eventName: 'workflow_dispatch', expectedVersion: f.version, event: { repository: { full_name: REPOSITORY }, inputs: { source_sha: f.sourceSha, version: f.version } } };
  assert.equal(preflight(f).sourceSha, f.sourceSha);
  assert.throws(() => preflight({ ...f, context: { ...f.context, expectedVersion: '0.1.3' } }), /Manual retry/);
  assert.throws(() => preflight({ ...f, context: { ...f.context, sourceSha: latest, event: { ...f.context.event, inputs: { source_sha: latest, version: f.version } } } }), /itself increase/);
});

test('invalid metadata or notes fail before a build', async t => {
  for (const path of ['app/release.json', 'installer/version.iss', 'docs/releases/0.1.4.md', 'packages/windows-release/CHANGELOG.md']) {
    const f = await fixture(t);
    await put(f.root, path, path.endsWith('.json') ? { version: '0.1.3', channel: 'stable' } : '# invalid metadata\n');
    git(f.root, 'add', '.'); git(f.root, 'commit', '--amend', '--no-edit', '-q');
    const sha = git(f.root, 'rev-parse', 'HEAD'); git(f.root, 'update-ref', 'refs/remotes/origin/main', sha);
    f.context.workflowSha = f.context.sourceSha = f.context.event.after = sha;
    assert.throws(() => preflight(f), /metadata|notes|changelog/i);
  }
});

test('draft creation uploads three exact assets and leaves historical releases unchanged', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  const historical = structuredClone(state.releases[0]);
  const result = await draft(f, api);
  assert.equal(result.status, 'draft-ready'); assert.equal(state.createCount, 1);
  assert.deepEqual(state.releases[0], historical);
  const release = state.releases.at(-1);
  assert.equal(release.draft, true); assert.equal(release.target_commitish, f.sourceSha);
  assert.match(release.body, /Fix a Windows device connection/);
  assert.deepEqual(state.assets.get(release.id).map(a => a.name), [f.name, `${f.name}.sha256`, 'Hellgato-0.1.4-CHANGELOG.md']);
  assert.ok(state.writes.every(w => w.method === 'POST'));
});

test('complete draft retry is read-only and reuses the original attempt artifact', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  await draft(f, api); const writes = state.writes.length;
  f.build.runAttempt = '2'; // build outputs still refer to attempt 1.
  assert.equal((await draft(f, api)).status, 'already-complete-draft');
  assert.equal(state.writes.length, writes); assert.equal(state.createCount, 1);
});

test('a failed asset upload leaves a compatible partial draft that resumes without duplicates', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  let uploads = 0;
  state.beforeCall = (method, path) => { if (method === 'POST' && path.includes('/assets?') && ++uploads === 2) throw new Error('Simulated connection failure'); };
  await assert.rejects(draft(f, api), /Simulated connection failure/);
  assert.equal(state.createCount, 1); assert.equal(state.assets.get(500).length, 1);
  state.beforeCall = null;
  assert.equal((await draft(f, api)).status, 'draft-ready');
  assert.equal(state.createCount, 1); assert.equal(state.assets.get(500).length, 3);
});

test('uncertain create is not retried, and next invocation discovers the draft', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  const request = api.request.bind(api);
  let fail = true;
  api.request = async (method, path, options) => { const response = await request(method, path, options); if (method === 'POST' && path === 'releases' && fail) { fail = false; throw new Error('Lost create response'); } return response; };
  await assert.rejects(draft(f, api), /Lost create response/);
  assert.equal(state.createCount, 1);
  assert.equal((await draft(f, api)).status, 'draft-ready'); assert.equal(state.createCount, 1);
});

test('matching public release is a read-only skip, including historical-style body', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  state.releases.push({ id: 45, tag_name: 'v0.1.4', draft: false, prerelease: false, body: 'Hand-written public notes', target_commitish: 'main' });
  state.tags.set('v0.1.4', { type: 'commit', sha: f.sourceSha });
  assert.equal((await draft(f, api)).status, 'already-public'); assert.equal(state.writes.length, 0);
});

test('tag mismatch and unknown draft identities block all writes', async t => {
  for (const mode of ['tag', 'draft', 'duplicate']) {
    const f = await fixture(t), { api, state } = fakeApi(f);
    if (mode === 'tag') state.tags.set('v0.1.4', { type: 'commit', sha: 'a'.repeat(40) });
    else state.releases.push(...Array.from({ length: mode === 'duplicate' ? 2 : 1 }, (_, i) => ({ id: 45 + i, tag_name: 'v0.1.4', draft: true, prerelease: false, target_commitish: f.sourceSha, name: 'Unknown draft', body: 'Unowned notes' })));
    await assert.rejects(draft(f, api), /different commit|Conflicting|Multiple/); assert.equal(state.writes.length, 0);
  }
});

test('annotated tags must resolve to the exact selected source', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  state.tags.set('v0.1.4', { type: 'tag', sha: 'c'.repeat(40) });
  state.annotatedTags = new Map([['c'.repeat(40), { type: 'commit', sha: f.sourceSha }]]);
  assert.equal((await draft(f, api)).status, 'draft-ready');
});

test('conflicting complete/partial assets, unknown files, and starter uploads are never replaced', async t => {
  for (const mode of ['bytes', 'extra', 'starter', 'body']) {
    const f = await fixture(t), { api, state } = fakeApi(f);
    await draft(f, api); const writes = state.writes.length;
    const assets = state.assets.get(500);
    if (mode === 'bytes') { assets[0].bytes[0] ^= 1; delete assets[0].digest; }
    if (mode === 'extra') assets.push({ ...assets[0], id: 9999, name: 'unknown.txt' });
    if (mode === 'starter') assets[0].state = 'starter';
    if (mode === 'body') state.releases.at(-1).body += '\nManual edit';
    await assert.rejects(draft(f, api), /Conflicting|differ|no-longer-draft/);
    assert.equal(state.writes.length, writes);
  }
});

test('publication while preparation is in progress stops further uploads', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  state.beforeCall = (method, path) => { if (method === 'GET' && path === 'releases/500') state.releases.at(-1).draft = false; };
  await assert.rejects(draft(f, api), /no-longer-draft/);
  assert.equal(state.writes.length, 1); // Only the original draft creation.
});

test('remote stale main, wrong run, expired/different artifact, and API errors fail closed', async t => {
  for (const mode of ['version', 'ancestor', 'run', 'expired', 'digest', 'api']) {
    const f = await fixture(t), { api, state } = fakeApi(f);
    if (mode === 'version') state.mainVersion = '0.1.5';
    if (mode === 'ancestor') state.sourceOnMain = false;
    if (mode === 'run') state.runOverrides = { path: '.github/workflows/untrusted.yml' };
    if (mode === 'expired') state.artifactOverrides = { expired: true };
    if (mode === 'digest') state.artifactOverrides = { digest: `sha256:${'c'.repeat(64)}` };
    if (mode === 'api') state.beforeCall = (method, path) => { if (path.startsWith('releases?')) throw new Error('Simulated API 503'); };
    await assert.rejects(draft(f, api)); assert.equal(state.writes.length, 0);
  }
});

test('manifest tampering, checksum mismatch, extra/symlink files, and output drift are rejected', async t => {
  for (const mode of ['manifest', 'source', 'run', 'checksum', 'extra', 'symlink', 'output']) {
    const f = await fixture(t);
    if (mode === 'manifest') { f.manifest.releaseSha = 'a'.repeat(40); await put(f.root, 'dist/build-manifest.json', f.manifest); }
    if (mode === 'source') { f.manifest.sources['app/release.json'] = 'a'.repeat(64); await put(f.root, 'dist/build-manifest.json', f.manifest); }
    if (mode === 'run') { f.manifest.build.runAttempt = 2; await put(f.root, 'dist/build-manifest.json', f.manifest); }
    if (mode === 'checksum') await put(f.root, `dist/${f.name}.sha256`, `${'a'.repeat(64)}  ${f.name}\n`);
    if (mode === 'extra') await put(f.root, 'dist/extra.txt', 'Extra');
    if (mode === 'symlink') { await rename(resolve(f.root, 'dist'), resolve(f.root, 'real-dist')); await symlink(resolve(f.root, 'real-dist'), resolve(f.root, 'dist'), process.platform === 'win32' ? 'junction' : 'dir'); }
    if (mode === 'output') f.build.sourceSha = 'a'.repeat(40);
    await assert.rejects(readBuild({ ...f, selected: preflight(f) }));
  }
});

test('all historical pages are inspected, never pruned or overwritten', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f, { historical: false });
  state.releases = Array.from({ length: 105 }, (_, i) => ({ id: i + 1, tag_name: `hellgato@0.0.${i}`, draft: false, prerelease: false }));
  state.releases.push({ id: 106, tag_name: 'v0.1.4', draft: false, prerelease: false }); state.tags.set('v0.1.4', { type: 'commit', sha: f.sourceSha });
  assert.equal((await draft(f, api)).status, 'already-public'); assert.equal(state.writes.length, 0);
  assert.ok(state.calls.some(c => c.path === 'releases?per_page=100&page=3'));
});

test('newer releases and pagination duplicates fail closed', async t => {
  const f = await fixture(t), { api, state } = fakeApi(f);
  state.releases.push({ id: 2, tag_name: 'v0.1.5', draft: false, prerelease: false });
  await assert.rejects(draft(f, api), /newer Windows release/); assert.equal(state.writes.length, 0);
  state.releases[1] = { ...state.releases[0] };
  await assert.rejects(draft(f, api), /Duplicate GitHub pagination/); assert.equal(state.writes.length, 0);
});

test('production API adapter refuses unsafe writes and does not retry HTTP failures', async () => {
  const calls = [];
  const api = createGitHubApi({ token: 'fake-test-token', fetchImpl: async (...args) => { calls.push(args); return new Response('{}', { status: 503 }); } });
  for (const [method, path, options] of [['DELETE', 'releases/1'], ['PATCH', 'releases/1', { body: { draft: false } }], ['POST', 'git/refs', { body: {} }], ['POST', 'releases', { body: { draft: false } }], ['GET', '../escape']]) await assert.rejects(api.request(method, path, options));
  assert.equal(calls.length, 0);
  await assert.rejects(api.request('GET', 'releases'), /HTTP 503/); assert.equal(calls.length, 1);
});

test('asset redirects never forward authorization to object storage', async () => {
  const calls = [];
  const api = createGitHubApi({ token: 'fake-test-token', fetchImpl: async (url, options) => { calls.push({ url, options }); return calls.length === 1 ? new Response(null, { status: 302, headers: { location: 'https://release-assets.githubusercontent.com/file?sig=example' } }) : new Response('installer'); } });
  assert.equal((await api.request('GET', 'releases/assets/1', { binary: true })).toString(), 'installer');
  assert.equal(calls[1].options.headers, undefined); assert.equal(calls[1].options.redirect, 'error');
});

test('workflow remains draft-only and downloads immutable original build outputs', async () => {
  const workflow = await readFile(new URL('../.github/workflows/windows-release.yml', import.meta.url), 'utf8');
  assert.match(workflow, /push:\n    branches: \[main\]/);
  assert.match(workflow, /artifact-ids: \$\{\{ needs\.build\.outputs\.artifact-id \}\}/);
  assert.match(workflow, /BUILD_ARTIFACT_NAME: \$\{\{ needs\.build\.outputs\.artifact-name \}\}/);
  assert.match(workflow, /digest-mismatch: error/);
  assert.match(workflow, /source-sha: \$\{\{ needs\.preflight\.outputs\.source-sha \}\}/);
  assert.equal((workflow.match(/contents: write/g) ?? []).length, 1);
  assert.equal((workflow.match(/GH_TOKEN:/g) ?? []).length, 1);
  assert.doesNotMatch(workflow, /draft:\s*false|npm publish|git push|RELEASE_ENABLED|windows_release\.py publish|github\.run_attempt/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { github, pages, validateMergedPullRequest, verifyArtifactSource, exactTag, simulateNpmFinalization as finalizeNpm, finalizeNpm as blockedFinalizeNpm, REPOSITORY } from '../scripts/release.mjs';

const source = 'a'.repeat(40);
const base = 'b'.repeat(40);
const head = 'c'.repeat(40);
const plan = { baseSha: base, bootstrap: false };
const commit = { sha: source, parents: [{ sha: base }] };
const makePr = () => ({ number: 42, merged: true, state: 'closed', merge_commit_sha: source, user: { login: 'github-actions[bot]' }, merged_by: { login: 'restarea92' }, base: { ref: 'main', repo: { full_name: REPOSITORY } }, head: { sha: head, ref: 'changeset-release/main', repo: { full_name: REPOSITORY } } });

test('release approval binds owner merge, exact PR head, branch and first parent', () => {
  assert.equal(validateMergedPullRequest(makePr(), plan, source, commit), true);
  for (const mutate of [pr => pr.merged = false, pr => pr.merged_by.login = 'github-actions[bot]', pr => pr.user.login = 'restarea92', pr => pr.head.ref = 'feature/fake-release', pr => pr.head.repo.full_name = 'attacker/hellgato', pr => pr.merge_commit_sha = head]) {
    const pr = makePr(); mutate(pr);
    assert.throws(() => validateMergedPullRequest(pr, plan, source, commit));
  }
  assert.throws(() => validateMergedPullRequest(makePr(), plan, source, { ...commit, parents: [{ sha: head }] }), /Stale/);
});

test('bootstrap requires its own owner-authored review boundary', () => {
  const pr = makePr();
  assert.throws(() => validateMergedPullRequest(pr, { ...plan, bootstrap: true }, source, commit), /Bootstrap/);
  pr.user.login = 'restarea92'; pr.head.ref = 'release/bootstrap-windows-0-1-2';
  assert.equal(validateMergedPullRequest(pr, { ...plan, bootstrap: true }, source, commit), true);
});

test('API errors are never interpreted as absence except explicit optional 404', async () => {
  for (const status of [403, 429, 500]) await assert.rejects(github('releases/tags/v0.1.2', { token: 'test', missing: true, fetchImpl: async () => ({ status, ok: false }) }), new RegExp(`HTTP ${status}`));
  assert.equal(await github('releases/tags/v0.1.2', { token: 'test', missing: true, fetchImpl: async () => ({ status: 404, ok: false }) }), null);
  await assert.rejects(github('releases', { token: 'test', fetchImpl: async () => ({ status: 200, ok: true, json: async () => { throw new Error('Malformed JSON'); } }) }), /Malformed/);
});

test('pagination reads beyond the first 100 entries and rejects malformed continuation', async () => {
  const calls = [];
  const result = await pages('commits/source/pulls', async path => { calls.push(path); return path.endsWith('page=1') ? Array.from({ length: 100 }, (_, id) => ({ id })) : [{ id: 101 }]; });
  assert.equal(result.length, 101); assert.equal(calls.length, 2);
  await assert.rejects(pages('releases', async () => ({})), /Malformed/);
});

test('artifact ID/source attempt binds successful build, not a later failed publisher', async () => {
  const options = { artifactId: '7', runId: '8', runAttempt: '2', releaseSha: source, component: 'windows' };
  const fixtures = {
    'actions/runs/8/attempts/2': { id: 8, run_attempt: 2, head_sha: source, status: 'completed', repository: { full_name: REPOSITORY }, head_repository: { full_name: REPOSITORY }, path: '.github/workflows/release.yml', event: 'push', head_branch: 'main', conclusion: 'failure' },
    'actions/artifacts/7': { id: 7, expired: false, workflow_run: { id: 8, head_sha: source, head_branch: 'main' }, name: 'windows-installer-8-2', digest: `sha256:${'d'.repeat(64)}` },
    [`compare/${source}...${source}`]: { status: 'identical', merge_base_commit: { sha: source } },
    'actions/runs/8/attempts/2/jobs?per_page=100': { total_count: 2, jobs: [{ name: 'windows-build / build', status: 'completed', conclusion: 'success' }, { name: 'windows-publish', status: 'completed', conclusion: 'failure' }] },
  };
  const api = async path => structuredClone(fixtures[path]);
  assert.equal((await verifyArtifactSource({ ...options, api })).artifactId, 7);
  fixtures['actions/artifacts/7'].name = 'windows-installer-8-3';
  await assert.rejects(verifyArtifactSource({ ...options, api }), /attempt/);
  fixtures['actions/artifacts/7'].name = 'windows-installer-8-2';
  fixtures['actions/runs/8/attempts/2/jobs?per_page=100'].jobs[0].conclusion = 'failure';
  await assert.rejects(verifyArtifactSource({ ...options, api }), /did not succeed/);
});

test('immutable tag retry is a no-op and conflicting tag never gets updated', async () => {
  const calls = [];
  await exactTag('hellgato@0.1.0', source, async (path, options) => { calls.push(options?.method || 'GET'); return { object: { type: 'commit', sha: source } }; }, true);
  assert.deepEqual(calls, ['GET']);
  await assert.rejects(exactTag('hellgato@0.1.0', source, async () => ({ object: { type: 'commit', sha: head } }), true), /never force/);
});

test('release workflow has an all-build barrier and no event-driven publication loopholes', async () => {
  const yaml = await readFile(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
  assert.match(yaml, /group: hellgato-publication\n  cancel-in-progress: false/);
  assert.match(yaml, /needs: \[approval, windows-build, npm-build\]/);
  assert.equal((yaml.match(/needs\.verified\.result == 'success'/g) || []).length, 2);
  assert.doesNotMatch(yaml, /pull_request_target:|release:\n\s+types:|tags:\s*\[/);
  assert.match(yaml, /vars\.RELEASE_ENABLED == 'true'/);
  assert.match(yaml, /artifact-ids:/);
  assert.doesNotMatch(yaml, /NPM_TOKEN|npm publish \./);
  assert.doesNotMatch(yaml, /registry-url:/, 'setup-node registry-url injects a synthetic NODE_AUTH_TOKEN and blocks OIDC');
});

test('bot PR workflows dispatch checks pinned to exact head and all external actions are SHA-pinned', async () => {
  const yaml = await readFile(new URL('../.github/workflows/release-pr.yml', import.meta.url), 'utf8');
  assert.match(yaml, /checks.yml.*expected_sha="\$head".*base_sha="\$base"/);
  assert.match(yaml, /windows-build.yml.*expected_sha="\$head"/);
  assert.match(yaml, /if: steps\.pending\.outputs\.pending == 'true'/);
  const publish = await readFile(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
  assert.match(publish, /download-docs-checks:/);
  assert.match(publish, /verify-artifact.*--directory dist\/npm/);
  assert.match(publish, /windows_release\.py verify/);
  for (const file of ['checks.yml', 'windows-build.yml', 'release-pr.yml', 'release.yml']) {
    const content = await readFile(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
    for (const match of content.matchAll(/uses:\s+(\S+)/g)) if (!match[1].startsWith('./')) assert.match(match[1], /@[a-f0-9]{40}$/);
  }
});

test('Windows CI promotes the installed global npm prefix before checking its pin', async () => {
  const yaml = await readFile(new URL('../.github/workflows/checks.yml', import.meta.url), 'utf8');
  assert.match(yaml, /npm install --global npm@11\.9\.0 --ignore-scripts\n[^\n]*\n\s+npm prefix --global >> "\$GITHUB_PATH"/);
  assert.ok(yaml.indexOf('npm prefix --global') < yaml.indexOf('assertNpmVersion();'));
});

test('npm build does not inherit another job\'s nonexistent approval file', async () => {
  const yaml = await readFile(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
  const build = yaml.slice(yaml.indexOf('  npm-build:\n'), yaml.indexOf('  verified:\n'));
  assert.match(build, /RELEASE_APPROVAL_FILE: ''/);
  assert.match(build, /verify-npm\.mjs.*--plan \.release\/plan\.json.*--release-sha "\$RELEASE_SHA"/);
  const barrier = yaml.slice(yaml.indexOf('  verified:\n'), yaml.indexOf('  npm-publish:\n'));
  assert.match(barrier, /node scripts\/release\.mjs approve/);
  assert.match(barrier, /NPM_RESULT: \$\{\{ needs\.npm-build\.result \}\}/);
  assert.match(barrier, /if \[ \"\$NPM_SELECTED\" = true \]; then test \"\$NPM_RESULT\" = success; fi/);
  assert.doesNotMatch(barrier, /npm publication is hard-disabled/);
});

test('npm GitHub finalizer resumes its own draft and completed retry is immutable', async t => {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-finalize-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'docs/releases'), { recursive: true });
  await writeFile(join(root, 'docs/releases/npm-0.1.0.md'), '# npm 0.1.0\n\nReviewed changes.\n');
  const component = { component: 'npm', version: '0.1.0', tag: 'hellgato@0.1.0', notes: 'docs/releases/npm-0.1.0.md', packages: [{ tag: '@hellgato/core@0.1.0' }, { tag: 'hellgato@0.1.0' }] };
  const approval = { releaseSha: source, planSha256: 'e'.repeat(64), components: [component] };
  const manifest = { releaseSha: source, planSha256: approval.planSha256 };
  const artifactIdentity = { releaseSha: source, artifactId: 7, runId: 8, runAttempt: 2, archiveDigest: `sha256:${'d'.repeat(64)}` };
  const writes = [];
  let release = null;
  let bytes = null;
  const api = async (path, options = {}) => {
    if (options.method) writes.push({ path, ...options });
    if (path.startsWith('git/ref/')) return { object: { type: 'commit', sha: source } };
    if (path.startsWith('releases/tags/')) return release;
    if (path === 'releases' && options.method === 'POST') return release = { ...options.body, id: 1, html_url: 'https://github.com/restarea92/hellgato/releases/tag/hellgato%400.1.0', author: { login: 'github-actions[bot]' } };
    if (path.startsWith('releases/1/assets')) return bytes ? [{ name: 'npm-release-manifest.json', id: 2 }] : [];
    if (path === 'releases/1' && options.method === 'PATCH') return release = { ...release, ...options.body };
    throw new Error(`Unexpected path ${path}`);
  };
  const uploadAsset = async (id, name, content) => { assert.equal(id, 1); assert.equal(name, 'npm-release-manifest.json'); bytes = content; writes.push({ upload: name }); };
  const verifyAsset = async (asset, expected) => { assert.equal(asset.name, 'npm-release-manifest.json'); assert.deepEqual(bytes, expected); };
  const options = { root, approval, manifest, artifactIdentity, api, uploadAsset, verifyAsset };
  await finalizeNpm(options);
  assert.equal(release.draft, false);
  assert.equal(release.make_latest, 'false');
  assert.equal(JSON.parse(bytes).artifact.sha256, 'd'.repeat(64));
  writes.length = 0;
  await finalizeNpm(options);
  assert.deepEqual(writes, []);
  release.body = 'foreign notes';
  await assert.rejects(finalizeNpm(options), /conflicting/);
  assert.deepEqual(writes, []);
});


test('Node 24 action pins preserve immutable ZIP upload and strict extracted downloads', async () => {
  for (const file of ['checks.yml', 'windows-build.yml', 'release-pr.yml', 'release.yml']) {
    const yaml = await readFile(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
    for (const block of yaml.split(/(?=      - uses:)/)) {
      if (block.includes('uses: pnpm/action-setup@')) assert.match(block, /pnpm\/action-setup@fc06bc1257f339d1d5d8b3a19a8cae5388b55320/);
      if (block.includes('uses: actions/upload-artifact@')) {
        assert.match(block, /upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9/);
        assert.match(block, /archive: true/);
        assert.match(block, /overwrite: false/);
      }
      if (block.includes('uses: actions/download-artifact@')) {
        assert.match(block, /download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333/);
        assert.match(block, /skip-decompress: false/);
        assert.match(block, /digest-mismatch: error/);
        assert.match(block, /artifact-ids:/);
        assert.match(block, /merge-multiple: true/);
      }
    }
  }
});


test('npm hold cannot be overridden by release variables and does not disable Windows jobs', async () => {
  const yaml = await readFile(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
  const npm = yaml.slice(yaml.indexOf('  npm-publish:\n'), yaml.indexOf('  windows-publish:\n'));
  const windows = yaml.slice(yaml.indexOf('  windows-publish:\n'), yaml.indexOf('  npm-finalize:\n'));
  const finalize = yaml.slice(yaml.indexOf('  npm-finalize:\n'), yaml.indexOf('  download-docs:\n'));
  assert.match(npm, /if: \$\{\{ false &&/);
  assert.match(finalize, /if: \$\{\{ false &&/);
  assert.doesNotMatch(npm, /id-token: write/);
  assert.doesNotMatch(windows, /false &&|needs\.npm-publish/);
  assert.match(windows, /vars\.RELEASE_ENABLED == 'true'/);
  assert.match(yaml, /node scripts\/build-npm\.mjs --plan \.release\/plan\.json\n\s+pnpm run typecheck/);
});

test('Windows build pins npm before packed staging and preserves isolated native fixture evidence', async () => {
  const yaml = await readFile(new URL('../.github/workflows/windows-build.yml', import.meta.url), 'utf8');
  assert.match(yaml, /npm install --global npm@11\.9\.0 --ignore-scripts/);
  assert.match(yaml, /npm prefix --global >> \$env:GITHUB_PATH/);
  assert.match(yaml, /name: Save isolated native fixture results\n\s+if: always\(\)/);
  assert.match(yaml, /work\/build-native-startup\/fixture\/fixture-results\.json/);
  assert.match(yaml, /work\/build-native-ui-test\/fixture-results\.txt/);
});


test('npm finalization API is blocked before creating package tags or GitHub releases', async () => {
  let calls = 0;
  await assert.rejects(blockedFinalizeNpm({ api: async () => { calls++; } }), /hard-disabled/);
  assert.equal(calls, 0);
});


test('clean-checkout CI builds public core exports before facade tests and type checks', async () => {
  for (const file of ['checks.yml', 'release.yml']) {
    const yaml = await readFile(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
    const block = file === 'release.yml' ? yaml.slice(yaml.indexOf('  npm-build:\n'), yaml.indexOf('  verified:\n')) : yaml;
    const build = block.search(/(?:pnpm run build:npm|node scripts\/build-npm\.mjs (?:--plan \.release\/plan\.json|\"\$\{args\[@\]\}\"))/);
    const tests = block.indexOf('pnpm test');
    const types = block.indexOf('pnpm run typecheck');
    assert.ok(build >= 0 && tests > build && types > build, `${file} must generate packages/core/dist before resolving public package exports`);
    assert.match(block, /pnpm run prepare:cora\n\s+(?:pnpm run build:npm|node scripts\/build-npm\.mjs (?:--plan \.release\/plan\.json|\"\$\{args\[@\]\}\"))\n\s+pnpm run typecheck\n\s+pnpm test/);
  }
});

test('reviewed beta owner merge and artifact identities are confined to next', async () => {
  const betaPlan = { ...plan, schemaVersion: 3, channel: 'beta', branch: 'next', candidateOnly: false };
  const pr = makePr(); pr.base.ref = 'next'; pr.head.ref = 'changeset-release/next';
  assert.equal(validateMergedPullRequest(pr, betaPlan, source, commit), true);
  for (const mutate of [p => p.base.ref = 'main', p => p.head.ref = 'changeset-release/main', p => p.merged_by.login = 'github-actions[bot]', p => p.head.repo.full_name = 'fork/hellgato']) {
    const changed = structuredClone(pr); mutate(changed);
    assert.throws(() => validateMergedPullRequest(changed, betaPlan, source, commit));
  }
  const fixtures = {
    'actions/runs/8/attempts/2': { id: 8, run_attempt: 2, head_sha: source, status: 'completed', repository: { full_name: REPOSITORY }, head_repository: { full_name: REPOSITORY }, path: '.github/workflows/release.yml', event: 'push', head_branch: 'next' },
    'actions/artifacts/7': { id: 7, expired: false, name: 'npm-packages-8-2', digest: `sha256:${'e'.repeat(64)}`, workflow_run: { id: 8, head_sha: source, head_branch: 'next' } },
    [`compare/${source}...${source}`]: { status: 'identical', merge_base_commit: { sha: source } },
    'actions/runs/8/attempts/2/jobs?per_page=100': { total_count: 1, jobs: [{ name: 'npm-build', status: 'completed', conclusion: 'success' }] },
  };
  const options = { artifactId: 7, runId: 8, runAttempt: 2, releaseSha: source, component: 'npm', api: async path => fixtures[path] };
  await verifyArtifactSource({ ...options, channel: 'beta' });
  await assert.rejects(verifyArtifactSource(options), /source run/);
  fixtures['actions/runs/8/attempts/2'].head_branch = 'main';
  await assert.rejects(verifyArtifactSource({ ...options, channel: 'beta' }), /source run/);
});

test('beta npm finalizer creates a prerelease without Latest and immutable retry', async t => {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-finalize-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'docs/releases'), { recursive: true });
  await writeFile(join(root, 'docs/releases/npm-0.1.0-beta.0.md'), '# npm 0.1.0-beta.0\n\nReviewed changes.\n');
  const component = { component: 'npm', channel: 'beta', version: '0.1.0-beta.0', tag: 'hellgato@0.1.0-beta.0', notes: 'docs/releases/npm-0.1.0-beta.0.md', packages: [{ tag: '@hellgato/core@0.1.0-beta.0' }, { tag: 'hellgato@0.1.0-beta.0' }] };
  const approval = { releaseSha: source, planSha256: 'e'.repeat(64), components: [component] };
  const manifest = { releaseSha: source, planSha256: approval.planSha256 };
  const artifactIdentity = { releaseSha: source, artifactId: 7, runId: 8, runAttempt: 2, archiveDigest: `sha256:${'d'.repeat(64)}` };
  const writes = [];
  let release = null;
  let bytes = null;
  const api = async (path, options = {}) => {
    if (options.method) writes.push({ path, ...options });
    if (path.startsWith('git/ref/')) return { object: { type: 'commit', sha: source } };
    if (path.startsWith('releases/tags/')) return release;
    if (path === 'releases' && options.method === 'POST') return release = { ...options.body, id: 1, html_url: 'https://github.com/restarea92/hellgato/releases/tag/hellgato%400.1.0-beta.0', author: { login: 'github-actions[bot]' } };
    if (path.startsWith('releases/1/assets')) return bytes ? [{ name: 'npm-release-manifest.json', id: 2 }] : [];
    if (path === 'releases/1' && options.method === 'PATCH') return release = { ...release, ...options.body };
    throw new Error(`Unexpected path ${path}`);
  };
  const uploadAsset = async (id, name, content) => { assert.equal(id, 1); assert.equal(name, 'npm-release-manifest.json'); bytes = content; writes.push({ upload: name }); };
  const verifyAsset = async (asset, expected) => { assert.equal(asset.name, 'npm-release-manifest.json'); assert.deepEqual(bytes, expected); };
  const options = { root, approval, manifest, artifactIdentity, api, uploadAsset, verifyAsset };
  await finalizeNpm(options);
  assert.equal(release.draft, false);
  assert.equal(release.prerelease, true);
  assert.equal(release.make_latest, 'false');
  assert.equal(JSON.parse(bytes).artifact.sha256, 'd'.repeat(64));
  writes.length = 0;
  await finalizeNpm(options);
  assert.deepEqual(writes, []);
  release.body = 'foreign notes';
  await assert.rejects(finalizeNpm(options), /conflicting/);
  assert.deepEqual(writes, []);
});


test('artifact provenance rejects foreign source attempts, incomplete jobs, and unrelated release SHAs', async () => {
  const baseFixtures = {
    'actions/runs/8/attempts/2': { id: 8, run_attempt: 2, head_sha: head, status: 'completed', repository: { full_name: REPOSITORY }, head_repository: { full_name: REPOSITORY }, path: '.github/workflows/release.yml', event: 'workflow_dispatch', head_branch: 'main' },
    'actions/artifacts/7': { id: 7, expired: false, workflow_run: { id: 8, head_sha: head, head_branch: 'main' }, name: 'npm-packages-8-2', digest: `sha256:${'d'.repeat(64)}` },
    [`compare/${source}...${head}`]: { status: 'ahead', merge_base_commit: { sha: source } },
    'actions/runs/8/attempts/2/jobs?per_page=100': { total_count: 1, jobs: [{ name: 'npm-build', status: 'completed', conclusion: 'success' }] },
  };
  const options = { artifactId: '7', runId: '8', runAttempt: '2', releaseSha: source, component: 'npm' };
  await verifyArtifactSource({ ...options, api: async path => structuredClone(baseFixtures[path]) });
  for (const mutate of [
    f => { f['actions/runs/8/attempts/2'].run_attempt = 3; },
    f => { f['actions/artifacts/7'].workflow_run.head_sha = base; },
    f => { f[`compare/${source}...${head}`].merge_base_commit.sha = base; },
    f => { f[`compare/${source}...${head}`].status = 'diverged'; },
    f => { f['actions/runs/8/attempts/2/jobs?per_page=100'].total_count = 2; },
    f => { f['actions/runs/8/attempts/2/jobs?per_page=100'].jobs[0].status = 'in_progress'; },
    f => { const jobs = f['actions/runs/8/attempts/2/jobs?per_page=100']; jobs.jobs.push({ ...jobs.jobs[0] }); jobs.total_count = 2; },
  ]) {
    const fixtures = structuredClone(baseFixtures); mutate(fixtures);
    await assert.rejects(verifyArtifactSource({ ...options, api: async path => fixtures[path] }));
  }
});

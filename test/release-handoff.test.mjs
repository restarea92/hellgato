import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, cp, mkdir, readFile, symlink, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { ROOT, git, json, put, readJson, sha256 } from '../scripts/release-lib.mjs';
import { generateReleasePlan, validateReleasePlan } from '../scripts/release-version.mjs';
import { generateCandidatePlan } from '../scripts/release-candidate.mjs';
import { syncReleaseMetadata } from '../scripts/sync-release-metadata.mjs';
import { CHANNEL_HANDOFF_BRANCH, CHANNEL_HANDOFF_PATH, generateChannelHandoff, validateChannelHandoff } from '../scripts/release-handoff.mjs';

const reason = 'Carry the exact reviewed beta source snapshot onto main for separately reviewed stable versioning.';
const commit = root => { git(root, 'add', '.'); git(root, 'commit', '--quiet', '-m', 'Fixture snapshot'); return git(root, 'rev-parse', 'HEAD').trim(); };
const point = (root, name, sha) => git(root, 'update-ref', `refs/remotes/origin/${name}`, sha);
async function fixture(t, { releaseIntent = true } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-handoff-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const path of ['.gitattributes', 'package.json', 'pnpm-workspace.yaml', 'pnpm-lock.yaml', '.changeset/config.json', '.changeset/README.md', 'packages/core/package.json', 'packages/hellgato/package.json', 'packages/windows-release/package.json', '.release/components.json', 'docs/releases/0.1.2.md']) {
    await mkdir(resolve(root, path, '..'), { recursive: true });
    await cp(resolve(ROOT, path), resolve(root, path));
  }
  for (const [directory, version] of [['core', '0.1.0'], ['hellgato', '0.1.0'], ['windows-release', '0.1.2']]) {
    const path = `packages/${directory}/package.json`;
    const manifest = await readJson(resolve(root, path)); manifest.version = version;
    await put(root, path, json(manifest));
    await put(root, `packages/${directory}/CHANGELOG.md`, `# ${manifest.name}\n\n## ${version}\n\nKnown fixture baseline.\n`);
  }
  await syncReleaseMetadata({ root });
  await put(root, '.gitignore', 'node_modules\n');
  await put(root, 'app/hellgato.py', '# known product source\n');
  await symlink(resolve(ROOT, 'node_modules'), resolve(root, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  git(root, 'init', '--quiet', '--initial-branch=main');
  git(root, 'remote', 'add', 'origin', 'https://github.com/restarea92/hellgato.git');
  git(root, 'config', 'user.name', 'Handoff fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid');
  const baseSha = commit(root); point(root, 'main', baseSha);
  git(root, 'checkout', '--quiet', '-b', 'next');
  if (releaseIntent) {
    await put(root, '.release/windows-public-baseline.json', json({ schemaVersion: 1, component: 'windows', channel: 'stable', sourceVersion: '0.1.2', version: '0.1.1', tag: 'v0.1.1', releaseId: 3, releaseSha: 'a'.repeat(40), installerSha256: 'b'.repeat(64), manifestSha256: null, reason: 'Reviewed stable download observation before this isolated beta fixture.' }));
    const tag = { version: '0.0.1-beta', integrity: `sha512-${'A'.repeat(86)}==` };
    await put(root, '.release/npm-public-baseline.json', json({ schemaVersion: 1, component: 'npm', sourceVersion: '0.1.0', packages: ['@hellgato/core', 'hellgato'].map(name => ({ name, latest: tag, beta: tag })), reason: 'Reviewed public registry observation before this isolated beta fixture.' }));
  }
  await put(root, '.changeset/reviewed-beta.md', '---\n"@hellgato/core": patch\n"@hellgato/windows-release": patch\n---\n\nPrepare the reviewed beta source for isolated handoff testing.\n');
  const releaseBaseSha = commit(root);
  const plan = releaseIntent ? await generateReleasePlan({ root, baseSha: releaseBaseSha, channel: 'beta' }) : await generateCandidatePlan({ root, baseSha: releaseBaseSha, channel: 'beta' });
  const sourceSha = commit(root); point(root, 'next', sourceSha);
  git(root, 'checkout', '--quiet', '-b', CHANNEL_HANDOFF_BRANCH);
  return { root, baseSha, sourceSha, releaseBaseSha, plan };
}
const generate = f => generateChannelHandoff({ ...f, reason });
const validate = (f, options = {}) => validateChannelHandoff({ root: f.root, baseSha: f.baseSha, headSha: git(f.root, 'rev-parse', 'HEAD').trim(), targetBranch: 'main', ...options });

test('handoff carries exact next source and beta plan with source-only provenance', async t => {
  const f = await fixture(t);
  const planBytes = await readFile(resolve(f.root, '.release/plan.json'));
  const record = await generate(f);
  assert.equal(record.sourceOnly, true); assert.equal(record.targetBranch, 'main');
  assert.equal(record.sourceTreeSha, git(f.root, 'rev-parse', `${f.sourceSha}^{tree}`).trim());
  assert.equal(record.sourcePlanSha256, sha256(planBytes));
  assert.deepEqual(await validate(f, { workingTree: true }), record);
  assert.deepEqual(git(f.root, 'status', '--porcelain').trim().split('\n'), [`?? ${CHANNEL_HANDOFF_PATH}`]);
  const headSha = commit(f.root);
  assert.deepEqual(await validate(f), record);
  assert.equal(git(f.root, 'diff', '--name-only', f.sourceSha, headSha).trim(), CHANNEL_HANDOFF_PATH);
  assert.deepEqual(await readFile(resolve(f.root, '.release/plan.json')), planBytes);
  assert.deepEqual(await readJson(resolve(f.root, '.changeset/pre.json')), { mode: 'pre', tag: 'beta' });
  assert.equal('components' in record, false); assert.equal('releaseSha' in record, false);
  // Replaying the carried beta plan against main is intentionally still invalid.
  await assert.rejects(validateReleasePlan({ root: f.root, baseSha: f.baseSha }), /Stale candidate plan/);
});

test('handoff validation accepts the exact reviewed tree after merge into main', async t => {
  const f = await fixture(t); const record = await generate(f); const head = commit(f.root);
  git(f.root, 'checkout', '--quiet', 'main');
  git(f.root, 'merge', '--quiet', '--no-ff', '-m', 'Reviewed source handoff', head);
  const merged = git(f.root, 'rev-parse', 'HEAD').trim(); point(f.root, 'main', merged);
  assert.equal(git(f.root, 'rev-parse', 'HEAD^1').trim(), f.baseSha);
  assert.deepEqual(await validate(f), record);
  assert.equal(git(f.root, 'rev-parse', `${head}^{tree}`).trim(), git(f.root, 'rev-parse', 'HEAD^{tree}').trim());
});

test('generation refuses ordinary branches, dirty source, missing refs and stale source/base pins', async t => {
  const f = await fixture(t);
  git(f.root, 'checkout', '--quiet', 'next');
  await assert.rejects(generate(f), /only on release\/handoff-next-main/);
  git(f.root, 'checkout', '--quiet', CHANNEL_HANDOFF_BRANCH);
  await put(f.root, 'unreviewed.txt', 'extra input\n');
  await assert.rejects(generate(f), /clean committed/); await rm(resolve(f.root, 'unreviewed.txt'));
  await assert.rejects(generateChannelHandoff({ ...f, baseSha: f.releaseBaseSha, reason }), /origin\/main tip/);
  await assert.rejects(generateChannelHandoff({ ...f, sourceSha: 'HEAD', reason }), /exact pinned source/);
  point(f.root, 'next', f.releaseBaseSha); await assert.rejects(generate(f), /origin\/next tip/);
  git(f.root, 'update-ref', '-d', 'refs/remotes/origin/next'); await assert.rejects(generate(f));
  point(f.root, 'next', f.sourceSha);
  git(f.root, 'update-ref', '-d', 'refs/remotes/origin/main'); await assert.rejects(generate(f));
});

test('handoff rejects later next code even when it retains a valid beta plan', async t => {
  const f = await fixture(t);
  await put(f.root, 'app/hellgato.py', '# additional unreviewed product behavior\n');
  const later = commit(f.root); point(f.root, 'next', later);
  await assert.rejects(generateChannelHandoff({ ...f, sourceSha: later, reason }), /exact beta release commit/);
});

test('handoff rejects artifact-only candidates as source release intent', async t => {
  const f = await fixture(t, { releaseIntent: false });
  await assert.rejects(generate(f), /reviewed beta release plan/);
  await put(f.root, '.release/plan.json', json(f.plan));
  const sourceSha = commit(f.root); point(f.root, 'next', sourceSha);
  await assert.rejects(generateChannelHandoff({ ...f, sourceSha, reason }), /schema-3 beta release snapshot/);
});

test('handoff verifies full replay and rejects self-consistent generated note tampering', async t => {
  const f = await fixture(t);
  const plan = structuredClone(f.plan), path = plan.components[0].notes;
  await put(f.root, path, '# Forged beta notes\n\nNot derived from the reviewed Changesets inputs.\n');
  plan.generatedFiles.find(file => file.path === path).sha256 = sha256(await readFile(resolve(f.root, path)));
  await put(f.root, '.release/plan.json', json(plan));
  git(f.root, 'add', '.'); git(f.root, 'commit', '--quiet', '--amend', '--no-edit');
  const sourceSha = git(f.root, 'rev-parse', 'HEAD').trim(); point(f.root, 'next', sourceSha);
  await validateReleasePlan({ root: f.root, baseSha: f.releaseBaseSha, headSha: sourceSha, structuralOnly: true });
  await assert.rejects(generateChannelHandoff({ ...f, sourceSha, reason }), /deterministic Changesets/);
  await assert.rejects(readFile(resolve(f.root, CHANNEL_HANDOFF_PATH)), /ENOENT/);
});

test('handoff record has exact immutable fields and canonical bytes', async t => {
  const f = await fixture(t), record = await generate(f);
  const mutations = [
    r => { r.sourceOnly = false; }, r => { r.targetBranch = 'next'; }, r => { r.sourceBranch = 'main'; },
    r => { r.schemaVersion = 3; }, r => { r.repository = 'other/repository'; }, r => { r.extra = true; },
    r => { r.baseSha = '--help'; }, r => { r.sourceSha = '../next'; }, r => { r.sourceTreeSha = 'x'.repeat(40); },
    r => { r.sourcePlanSha256 = 'a'.repeat(64); }, r => { r.reason = 'skip'; }, r => { r.reason = reason + '\nunsafe'; },
    r => { delete r.reason; }, r => { r.baseSha = f.sourceSha; }, r => { r.sourceTreeSha = 'b'.repeat(40); },
  ];
  for (const mutate of mutations) {
    const changed = structuredClone(record); mutate(changed);
    await put(f.root, CHANNEL_HANDOFF_PATH, json(changed));
    await assert.rejects(validate(f, { workingTree: true }));
  }
  for (const bytes of [JSON.stringify(record), json(record).replace('"schemaVersion": 1,', '"schemaVersion": 2,\n  "schemaVersion": 1,')]) {
    await put(f.root, CHANNEL_HANDOFF_PATH, bytes); await assert.rejects(validate(f, { workingTree: true }), /canonical generated bytes/);
  }
});

test('handoff refuses wrong target, stale validation base/head and moved next ref', async t => {
  const f = await fixture(t); await generate(f); const head = commit(f.root);
  for (const targetBranch of ['next', 'feature/main', '', undefined]) await assert.rejects(validate(f, { targetBranch }), /only target main/);
  await assert.rejects(validate(f, { baseSha: f.releaseBaseSha }), /Stale handoff/);
  await assert.rejects(validate(f, { headSha: f.sourceSha }), /exact requested head/);
  await assert.rejects(validate(f, { headSha: 'HEAD' }), /exact requested head/);
  point(f.root, 'next', head); await assert.rejects(validate(f), /origin\/next tip/);
  point(f.root, 'next', f.sourceSha);
  point(f.root, 'main', f.releaseBaseSha); await assert.rejects(generate(f), /clean committed|origin\/main tip|exact pinned source/);
});

test('handoff rejects extra source edits, package versions, baselines and changesets', async t => {
  const f = await fixture(t); await generate(f); const head = commit(f.root);
  for (const [path, content] of [
    ['app/hellgato.py', '# unreviewed source change\n'], ['packages/core/package.json', '{}\n'],
    ['.release/npm-public-baseline.json', '{}\n'], ['.changeset/unreviewed.md', '---\n---\n\nUnreviewed changeset appended to source.\n'],
    ['.release/plan.json', '{}\n'], ['.hidden-untracked', 'extra\n'],
  ]) {
    await put(f.root, path, content);
    await assert.rejects(validate(f, { workingTree: true }), /pinned next source plus only/);
    await assert.rejects(validate(f), /Dirty handoff/);
    git(f.root, 'reset', '--hard', head); git(f.root, 'clean', '-fd');
  }
  await put(f.root, 'app/hellgato.py', '# committed source change\n'); commit(f.root);
  await assert.rejects(validate(f), /pinned next source plus only/);
});

test('handoff rejects symlink records and source-controlled release input aliases', async t => {
  const f = await fixture(t); const record = await generate(f);
  await rm(resolve(f.root, CHANNEL_HANDOFF_PATH));
  await put(f.root, 'outside-record.json', json(record));
  await symlink('../outside-record.json', resolve(f.root, CHANNEL_HANDOFF_PATH));
  await assert.rejects(validate(f, { workingTree: true }), /regular file/);
  await rm(resolve(f.root, CHANNEL_HANDOFF_PATH)); await rm(resolve(f.root, 'outside-record.json'));
  // A symlink committed at the fixed output path must not be followed on write.
  await symlink('plan.json', resolve(f.root, CHANNEL_HANDOFF_PATH));
  git(f.root, 'add', '.'); git(f.root, 'commit', '--quiet', '--amend', '--no-edit');
  const sourceSha = git(f.root, 'rev-parse', 'HEAD').trim(); point(f.root, 'next', sourceSha);
  await assert.rejects(generateChannelHandoff({ ...f, sourceSha, reason }), /regular nonexecutable file/);
  assert.equal((await lstat(resolve(f.root, CHANNEL_HANDOFF_PATH))).isSymbolicLink(), true);
});

test('handoff rejects unrelated main ancestry and avoids writing any artifact', async t => {
  const f = await fixture(t);
  const dependencyPath = resolve(ROOT, 'node_modules/@changesets/cli/bin.js');
  const dependencyBytes = await readFile(dependencyPath);
  git(f.root, 'checkout', '--quiet', '--orphan', 'unrelated-main'); git(f.root, 'rm', '-rf', '--quiet', '.');
  // Git traverses Windows junctions unless the orphan snapshot keeps its ignore policy.
  await put(f.root, '.gitignore', 'node_modules\n');
  await put(f.root, 'README.md', 'unrelated main history\n'); const unrelated = commit(f.root);
  assert.equal(git(f.root, 'ls-tree', '-r', '--name-only', unrelated, '--', 'node_modules').trim(), '');
  point(f.root, 'main', unrelated); git(f.root, 'checkout', '--quiet', CHANNEL_HANDOFF_BRANCH);
  assert.deepEqual(await readFile(dependencyPath), dependencyBytes);
  await assert.rejects(generateChannelHandoff({ ...f, baseSha: unrelated, reason }));
  await assert.rejects(readFile(resolve(f.root, CHANNEL_HANDOFF_PATH)), /ENOENT/);
});


test('handoff origin must be the original GitHub repository without aliases or rewrites', async t => {
  const f = await fixture(t);
  for (const url of ['https://github.com/attacker/hellgato.git', '/tmp/mirror', 'https://github.com/restarea92/hellgato.git?other', 'https://github.com/restarea92/hellgato.git/']) {
    git(f.root, 'remote', 'set-url', 'origin', url); await assert.rejects(generate(f), /original restarea92/);
  }
  git(f.root, 'remote', 'set-url', 'origin', 'https://github.com/restarea92/hellgato.git');
  git(f.root, 'config', 'url.https://elsewhere.invalid/.insteadOf', 'https://github.com/');
  await assert.rejects(generate(f), /original restarea92/);
  git(f.root, 'config', '--unset', 'url.https://elsewhere.invalid/.insteadOf');
  await generate(f); commit(f.root);
  git(f.root, 'remote', 'set-url', '--add', 'origin', 'https://github.com/attacker/hellgato.git');
  await assert.rejects(validate(f), /original restarea92/);
});


test('an advanced main requires regeneration and squash handoffs are rejected', async t => {
  const f = await fixture(t); await generate(f); const head = commit(f.root);
  git(f.root, 'checkout', '--quiet', 'main');
  await put(f.root, 'main-only.md', 'Reviewed independent main change.\n'); const advanced = commit(f.root);
  point(f.root, 'main', advanced); git(f.root, 'checkout', '--quiet', CHANNEL_HANDOFF_BRANCH);
  await assert.rejects(validate(f), /main advanced/);
  // Moving origin/main directly to the PR head would be a fast-forward, not
  // the required preserving main merge with baseSha as its first parent.
  point(f.root, 'main', head); await assert.rejects(validate(f), /main advanced/);
  point(f.root, 'main', f.baseSha); git(f.root, 'checkout', '--quiet', 'main');
  git(f.root, 'reset', '--hard', f.baseSha); git(f.root, 'merge', '--squash', '--quiet', head);
  const squash = commit(f.root); point(f.root, 'main', squash);
  await assert.rejects(validate(f), /does not descend/);
});

test('impact and release approval classify the carried beta plan as source-only handoff', async t => {
  const { checkReleaseImpact } = await import('../scripts/check-release-impact.mjs');
  const { approveRelease } = await import('../scripts/release.mjs');
  const f = await fixture(t); await generate(f);
  let impact = await checkReleaseImpact({ root: f.root, base: f.baseSha, workingTree: true, targetBranch: 'main' });
  assert.equal(impact.sourceOnly, true); assert.equal(impact.releasePlan, false);
  assert.deepEqual(impact.required, []); assert.deepEqual(impact.declared, []);
  const head = commit(f.root);
  impact = await checkReleaseImpact({ root: f.root, base: f.baseSha, head, targetBranch: 'main' });
  assert.equal(impact.releasePlan, false); assert.equal(impact.handoff.sourceSha, f.sourceSha);
  await assert.rejects(checkReleaseImpact({ root: f.root, base: f.baseSha, head, targetBranch: 'next' }), /only target main/);
  await put(f.root, 'app/hellgato.py', '# extra source must not ride on handoff\n');
  await assert.rejects(checkReleaseImpact({ root: f.root, base: f.baseSha, workingTree: true, targetBranch: 'main' }), /pinned next source plus only/);
  git(f.root, 'reset', '--hard', head);
  git(f.root, 'checkout', '--quiet', 'main'); git(f.root, 'merge', '--quiet', '--no-ff', '-m', 'Reviewed handoff', head);
  const releaseSha = git(f.root, 'rev-parse', 'HEAD').trim(); point(f.root, 'main', releaseSha);
  const calls = [];
  const api = async (path, options = {}) => {
    calls.push(path); assert.ok(options.method === undefined || options.method === 'GET');
    assert.equal(path, `commits/${releaseSha}`, 'Handoff must not request public release, PR approval, tags, or publication APIs');
    return { sha: releaseSha, parents: git(f.root, 'show', '-s', '--format=%P', releaseSha).trim().split(' ').map(sha => ({ sha })) };
  };
  const environment = { GITHUB_REPOSITORY: 'restarea92/hellgato', GITHUB_REF: 'refs/heads/main', GITHUB_EVENT_NAME: 'push' };
  assert.equal(await approveRelease({ root: f.root, releaseSha, api, environment, validatePlan: false }), null);
  assert.deepEqual(calls, [`commits/${releaseSha}`]);
  await assert.rejects(approveRelease({ root: f.root, releaseSha, api, environment: { ...environment, GITHUB_REF: 'refs/heads/next' }, validatePlan: false }), /only target main/);
  await put(f.root, 'app/hellgato.py', '# tampered source checkout must not publish\n');
  await assert.rejects(approveRelease({ root: f.root, releaseSha, api, environment, validatePlan: false }), /Dirty handoff/);
});

test('lingering handoff does not exempt later ordinary source or manual version changes', async t => {
  const { checkReleaseImpact } = await import('../scripts/check-release-impact.mjs');
  const { approveRelease } = await import('../scripts/release.mjs');
  const f = await fixture(t); await generate(f); const head = commit(f.root);
  git(f.root, 'checkout', '--quiet', 'main'); git(f.root, 'merge', '--quiet', '--no-ff', '-m', 'Reviewed handoff', head);
  const merged = git(f.root, 'rev-parse', 'HEAD').trim(); point(f.root, 'main', merged);
  await put(f.root, 'app/hellgato.py', '# ordinary independently reviewed main change\n');
  await put(f.root, '.changeset/post-handoff.md', '---\n"@hellgato/windows-release": patch\n---\n\nFix an ordinary product issue after the source handoff.\n');
  const later = commit(f.root); point(f.root, 'main', later);
  // Deliberately move next too. A lingering handoff must not revalidate its old
  // pin or require later main code to keep the earlier full source tree.
  point(f.root, 'next', later);
  const impact = await checkReleaseImpact({ root: f.root, base: merged, targetBranch: 'main' });
  assert.equal(impact.releasePlan, false); assert.equal(impact.sourceOnly, undefined); assert.deepEqual(impact.required, ['windows']);
  assert.equal(await approveRelease({ root: f.root, releaseSha: later, environment: { GITHUB_REPOSITORY: 'restarea92/hellgato', GITHUB_REF: 'refs/heads/main', GITHUB_EVENT_NAME: 'push' }, api: async path => {
    assert.equal(path, `commits/${later}`); return { parents: [{ sha: merged }] };
  } }), null);
  for (const directory of ['core', 'hellgato']) {
    const path = `packages/${directory}/package.json`, manifest = await readJson(resolve(f.root, path));
    manifest.version = '0.1.1-beta.99'; await put(f.root, path, json(manifest));
  }
  await assert.rejects(checkReleaseImpact({ root: f.root, base: later, workingTree: true, targetBranch: 'main' }), /Manual package version/);
});


test('reviewed handoff promotes through fresh main baselines and full pre-exit replay', async t => {
  const f = await fixture(t); await generate(f); const headSha = commit(f.root);
  git(f.root, 'checkout', '--quiet', 'main');
  git(f.root, 'merge', '--quiet', '--no-ff', '-m', 'Reviewed source handoff', headSha);
  const merged = git(f.root, 'rev-parse', 'HEAD').trim(); point(f.root, 'main', merged);
  await validate(f);
  // A stale record from the beta source must never implicitly approve promotion.
  await assert.rejects(generateReleasePlan({ root: f.root, channel: 'stable' }), /baseline/);
  git(f.root, 'reset', '--hard', merged); git(f.root, 'clean', '-fd');
  for (const [component, directory] of [['windows', 'windows-release'], ['npm', 'core']]) {
    const path = `.release/${component}-public-baseline.json`;
    const baseline = await readJson(resolve(f.root, path));
    baseline.sourceVersion = (await readJson(resolve(f.root, `packages/${directory}/package.json`))).version;
    baseline.reason = 'Freshly reviewed public observations after integrating the exact beta source onto main.';
    await put(f.root, path, json(baseline));
  }
  const promotionBase = commit(f.root); point(f.root, 'main', promotionBase);
  const plan = await generateReleasePlan({ root: f.root, channel: 'stable' });
  assert.equal(plan.schemaVersion, 3); assert.equal(plan.channel, 'stable'); assert.equal(plan.transition, 'exit');
  assert.equal(plan.branch, 'main'); assert.equal(plan.candidateOnly, false);
  assert.equal(plan.preAfter, null); assert.deepEqual(plan.archivedAfter, []);
  assert.deepEqual(plan.components.map(c => [c.component, c.oldVersion, c.version]), [['windows', '0.1.3-beta.0', '0.1.3'], ['npm', '0.1.1-beta.0', '0.1.1']]);
  for (const directory of ['core', 'hellgato']) assert.equal((await readJson(resolve(f.root, `packages/${directory}/package.json`))).publishConfig.tag, 'latest');
  const promotionHead = commit(f.root);
  await validateReleasePlan({ root: f.root, baseSha: promotionBase, headSha: promotionHead });
  // The old source-only handoff is carried as provenance, not treated as a new
  // exemption or reused as approval for the separate stable version plan.
  const { checkReleaseImpact } = await import('../scripts/check-release-impact.mjs');
  const impact = await checkReleaseImpact({ root: f.root, base: promotionBase, head: promotionHead, targetBranch: 'main' });
  assert.equal(impact.releasePlan, true); assert.equal(impact.sourceOnly, undefined);
});

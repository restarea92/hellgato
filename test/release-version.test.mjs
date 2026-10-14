import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, cp, mkdir, writeFile, readFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { ROOT, git, json, put, readJson, parseChangeset, pendingChangesets, parseWorkspacePackages } from '../scripts/release-lib.mjs';
import { generateReleasePlan, validateReleasePlan } from '../scripts/release-version.mjs';
import { syncReleaseMetadata } from '../scripts/sync-release-metadata.mjs';
import { checkReleaseImpact } from '../scripts/check-release-impact.mjs';

async function fixture(t, { sourceRoot = ROOT } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-release-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const path of ['.gitattributes', 'package.json', 'pnpm-workspace.yaml', 'pnpm-lock.yaml', '.changeset/config.json', '.changeset/README.md', 'packages/core/package.json', 'packages/hellgato/package.json', 'packages/windows-release/package.json', '.release/components.json', 'docs/releases/0.1.2.md']) {
    await mkdir(resolve(root, path, '..'), { recursive: true });
    await cp(resolve(sourceRoot, path), resolve(root, path), { recursive: true });
  }
  // Keep fixture inputs independent of pending developer changesets and future releases.
  for (const [directory, version] of [['core', '0.1.0'], ['hellgato', '0.1.0'], ['windows-release', '0.1.2']]) {
    const path = `packages/${directory}/package.json`;
    const manifest = await readJson(resolve(root, path));
    manifest.version = version;
    await put(root, path, json(manifest));
    await put(root, `packages/${directory}/CHANGELOG.md`, `# ${manifest.name}\n\n## ${version}\n\nKnown fixture baseline.\n`);
  }
  await syncReleaseMetadata({ root });
  await put(root, '.gitignore', 'node_modules\n');
  await put(root, 'app/cora/bridge.mjs', 'export const value = 1;\n');
  await put(root, 'app/hellgato.py', '# fixture\n');
  await put(root, 'scripts/prepare-cora.mjs', '// upstream fixture\n');
  await symlink(resolve(ROOT, 'node_modules'), resolve(root, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  git(root, 'init', '--quiet', '--initial-branch=main');
  git(root, 'config', 'user.name', 'Release fixture');
  git(root, 'config', 'user.email', 'fixture@example.invalid');
  await commit(root);
  return root;
}
async function commit(root) { git(root, 'add', '.'); git(root, 'commit', '--quiet', '-m', 'Fixture snapshot'); return git(root, 'rev-parse', 'HEAD').trim(); }
async function change(root, name, targets) { await put(root, `.changeset/${name}.md`, `---\n${targets.map(n => `"${n}": patch`).join('\n')}\n---\n\nFix a user-visible issue in ${name}.\n`); }

for (const [name, targets, selected, versions] of [
  ['windows-only', ['@hellgato/windows-release'], ['windows'], ['0.1.0', '0.1.3']],
  ['npm-only', ['@hellgato/core'], ['npm'], ['0.1.1', '0.1.2']],
  ['shared-change', ['@hellgato/core', 'hellgato', '@hellgato/windows-release'], ['windows', 'npm'], ['0.1.1', '0.1.3']],
]) test(`Changesets simulation: ${name}`, async t => {
  const root = await fixture(t);
  await change(root, name, targets);
  const baseSha = await commit(root);
  const prepared = await readFile(resolve(root, 'docs/releases/0.1.2.md'), 'utf8');
  const plan = await generateReleasePlan({ root, baseSha });
  assert.deepEqual(plan.components.map(c => c.component), selected);
  assert.equal((await readJson(resolve(root, 'packages/core/package.json'))).version, versions[0]);
  assert.equal((await readJson(resolve(root, 'packages/hellgato/package.json'))).version, versions[0]);
  assert.equal((await readJson(resolve(root, 'packages/hellgato/package.json'))).dependencies['@hellgato/core'], 'workspace:*');
  assert.equal((await readJson(resolve(root, 'app/release.json'))).version, versions[1]);
  assert.equal(await readFile(resolve(root, 'docs/releases/0.1.2.md'), 'utf8'), prepared);
  await validateReleasePlan({ root, baseSha });
  execFileSync(process.env.HELLGATO_PNPM || (process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm'), ['install', '--frozen-lockfile', '--ignore-scripts', '--lockfile-only', '--offline'], { cwd: root, stdio: 'pipe', shell: process.platform === 'win32' });
  await assert.rejects(validateReleasePlan({ root, baseSha: 'a'.repeat(40), structuralOnly: true }), /Stale/);
  const note = resolve(root, plan.components[0].notes);
  await writeFile(note, (await readFile(note, 'utf8')) + '\nUnreviewed manual edit.\n');
  await assert.rejects(validateReleasePlan({ root, baseSha, structuralOnly: true }), /digest mismatch/);
});

test('Windows bootstrap preserves 0.1.2 without a patch bump', async t => {
  const root = await fixture(t);
  const baseSha = git(root, 'rev-parse', 'HEAD').trim();
  const plan = await generateReleasePlan({ root, baseSha, bootstrap: 'windows', from: '0.1.1' });
  assert.equal(plan.components[0].version, '0.1.2');
  assert.equal(plan.components[0].oldVersion, '0.1.1');
  assert.equal(plan.bootstrap, true);
  assert.deepEqual(plan.changesets, []);
  await validateReleasePlan({ root, baseSha });
});

test('metadata check refuses stale application version', async t => {
  const root = await fixture(t);
  await put(root, 'app/release.json', json({ version: '0.1.1', channel: 'stable' }));
  await assert.rejects(syncReleaseMetadata({ root, check: true }), /Stale generated/);
  await syncReleaseMetadata({ root });
  await syncReleaseMetadata({ root, check: true });
});

test('no pending changesets creates no release intent', async t => {
  const root = await fixture(t);
  assert.equal(await generateReleasePlan({ root }), null);
  await assert.rejects(readFile(resolve(root, '.release/plan.json')), /ENOENT/);
});

test('impact shared source and upstream require Windows and npm', async t => {
  for (const path of ['app/cora/bridge.mjs', 'scripts/prepare-cora.mjs', 'scripts/cora-paths.mjs', 'packages/core/src/contracts/index.mjs', 'packages/core/schemas/contracts.schema.json', 'packages/hellgato/src/session/index.mjs', 'packages/hellgato/src/adapters/mirabox-n4-pro/index.mjs', 'schemas/adapter.json', 'vendor/deckbridge/manifest.json']) {
    const root = await fixture(t);
    const base = git(root, 'rev-parse', 'HEAD').trim();
    await put(root, path, '// changed shared implementation\n');
    await change(root, 'missing-npm', ['@hellgato/windows-release']);
    await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Missing changeset targets:.*npm/);
    await change(root, 'include-npm', ['hellgato']);
    assert.deepEqual((await checkReleaseImpact({ root, base, workingTree: true })).required, ['npm', 'windows']);
  }
});

test('impact catches manual versions, unknown files, and new workspace', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  await put(root, 'unknown-source.js', 'export {};\n');
  await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Unclassified paths/);
  await rm(resolve(root, 'unknown-source.js'));
  await put(root, 'packages/unclassified/package.json', json({ name: 'unclassified', version: '0.1.0' }));
  await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Unclassified workspace/);
  await rm(resolve(root, 'packages/unclassified'), { recursive: true });
  const path = resolve(root, 'packages/windows-release/package.json');
  const manifest = await readJson(path); manifest.version = '0.1.3'; await writeFile(path, json(manifest));
  await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Manual package version/);
});

test('documentation empty changeset is explicit and does not bump', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  await put(root, 'docs/guide.md', 'Clarify installation instructions.\n');
  await put(root, '.changeset/documentation-only.md', '---\n---\n\nClarify documentation without changing distributed behavior.\n');
  assert.deepEqual((await checkReleaseImpact({ root, base, workingTree: true })).required, []);
  assert.deepEqual(parseChangeset('---\n---\n\nExplain a documentation-only update.\n', 'empty').releases, []);
});

test('policy changes need scoped reasons and cannot bypass unknown paths', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  await put(root, 'scripts/release.mjs', '// reviewed release tooling\n');
  await change(root, 'ordinary-change', ['@hellgato/windows-release']);
  await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /scoped reviewable/);
  await put(root, '.release/no-release/policy.json', json({ schemaVersion: 1, baseSha: base, reason: 'Release-policy refactor only; inspect permissions and artifact binding during review.', paths: ['scripts/release.mjs'] }));
  await checkReleaseImpact({ root, base, workingTree: true });
});

test('full replay rejects changed generated notes even with recomputed hashes', async t => {
  const root = await fixture(t);
  await change(root, 'windows-patch', ['@hellgato/windows-release']);
  const baseSha = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha });
  const notesPath = plan.components[0].notes;
  await put(root, notesPath, '# Malicious replacement notes\n\nThis was not the consumed changeset summary.\n');
  const { sha256 } = await import('../scripts/release-lib.mjs');
  plan.generatedFiles.find(f => f.path === notesPath).sha256 = sha256(await readFile(resolve(root, notesPath)));
  await put(root, '.release/plan.json', json(plan));
  await validateReleasePlan({ root, baseSha, structuralOnly: true });
  await assert.rejects(validateReleasePlan({ root, baseSha }), /deterministic Changesets regeneration/);
});

test('release plan rejects source edits and altered consumed contents', async t => {
  const root = await fixture(t);
  await change(root, 'windows-patch', ['@hellgato/windows-release']);
  const baseSha = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha });
  await put(root, 'app/hellgato.py', '# unreviewed source change\n');
  await assert.rejects(validateReleasePlan({ root, baseSha, structuralOnly: true }), /Unexpected source change/);
  git(root, 'checkout', '--', 'app/hellgato.py');
  plan.changesets[0].summary = 'Altered consumed summary without matching the original source.';
  await put(root, '.release/plan.json', json(plan));
  await assert.rejects(validateReleasePlan({ root, baseSha, structuralOnly: true }), /Consumed changeset contents/);
});

test('workspace additions outside packages are not silently ignored', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  await put(root, 'pnpm-workspace.yaml', (await readFile(resolve(root, 'pnpm-workspace.yaml'), 'utf8')).replace('  - packages/core', '  - tools/new\n  - packages/core'));
  await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Workspace membership differs/);
});

test('npm bootstrap records the verified public prerelease baseline without a source bump', async t => {
  const root = await fixture(t);
  const baseSha = git(root, 'rev-parse', 'HEAD').trim();
  const plan = await generateReleasePlan({ root, baseSha, bootstrap: 'npm', from: '0.0.1-beta' });
  assert.equal(plan.components[0].oldVersion, '0.0.1-beta');
  assert.equal(plan.components[0].version, '0.1.0');
  assert.equal(plan.components[0].tag, 'hellgato@0.1.0');
  assert.deepEqual(plan.components[0].packages.map(p => p.oldVersion), ['0.0.1-beta', '0.0.1-beta']);
  await validateReleasePlan({ root, baseSha });
});

test('bootstrap consumes and records documentation-only changesets but refuses version bumps', async t => {
  const root = await fixture(t);
  await put(root, '.changeset/documentation-only.md', '---\n---\n\nClarify installation documentation without changing release bytes.\n');
  const baseSha = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha, bootstrap: 'windows', from: '0.1.1' });
  assert.equal(plan.components[0].version, '0.1.2');
  assert.equal(plan.changesets.length, 1);
  assert.deepEqual(plan.changesets[0].releases, []);
  await assert.rejects(readFile(resolve(root, '.changeset/documentation-only.md')), /ENOENT/);
  await validateReleasePlan({ root, baseSha });
  const other = await fixture(t);
  await change(other, 'actual-bump', ['@hellgato/windows-release']);
  const otherBase = await commit(other);
  await assert.rejects(generateReleasePlan({ root: other, baseSha: otherBase, bootstrap: 'windows', from: '0.1.1' }), /cannot consume pending version bumps/);
});

test('fixtures stay isolated from future source versions and real pending changesets', async t => {
  const sourceRoot = await fixture(t);
  for (const directory of ['core', 'hellgato', 'windows-release']) {
    const path = `packages/${directory}/package.json`;
    const manifest = await readJson(resolve(sourceRoot, path));
    manifest.version = '7.8.9';
    await put(sourceRoot, path, json(manifest));
    await put(sourceRoot, `packages/${directory}/CHANGELOG.md`, '# Future production changelog\n\n## 7.8.9\n');
  }
  await change(sourceRoot, 'actual-user-change', ['@hellgato/core', '@hellgato/windows-release']);
  const root = await fixture(t, { sourceRoot });
  assert.deepEqual(await pendingChangesets(root), []);
  for (const [directory, version] of [['core', '0.1.0'], ['hellgato', '0.1.0'], ['windows-release', '0.1.2']]) {
    const manifest = await readJson(resolve(root, `packages/${directory}/package.json`));
    assert.equal(manifest.version, version);
    assert.equal(await readFile(resolve(root, `packages/${directory}/CHANGELOG.md`), 'utf8'), `# ${manifest.name}\n\n## ${version}\n\nKnown fixture baseline.\n`);
  }
  await syncReleaseMetadata({ root, check: true });
  assert.equal((await readJson(resolve(root, 'app/release.json'))).version, '0.1.2');
  assert.equal(await readFile(resolve(root, 'docs/releases/0.1.2.md'), 'utf8'), await readFile(resolve(sourceRoot, 'docs/releases/0.1.2.md'), 'utf8'));
});

test('workspace parser accepts exact quoted members and comments without ignoring values', () => {
  assert.deepEqual(parseWorkspacePackages('packages: # explicit workspace members\n  - "packages/core" # public core\n  - \'packages/hellgato\' # facade\n  - packages/windows-release # private metadata\nallowBuilds: {} # no lifecycle scripts\n'), ['packages/core', 'packages/hellgato', 'packages/windows-release']);
});

test('workspace additions with comments, quotes, or inline YAML fail closed', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  const baseline = await readFile(resolve(root, 'pnpm-workspace.yaml'), 'utf8');
  for (const extra of ['  - docs/unclassified # new public workspace', '  - "docs/unclassified" # new public workspace', "  - 'docs/unclassified' # new public workspace"]) {
    await put(root, 'pnpm-workspace.yaml', baseline.replace('packages:\n', `packages:\n${extra}\n`));
    await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Workspace membership differs/);
  }
  for (const unsupported of [
    'packages: [packages/core, packages/hellgato, packages/windows-release, docs/unclassified]\nallowBuilds: {}\n',
    baseline.replace('packages:', 'packages: &members'),
    baseline + '\npackages: [docs/unclassified]\n',
    baseline.replace('packages/core', '"packages/core # not a comment"'),
    baseline + '\n<<: *extraWorkspaces\n',
    baseline + '\n---\npackages: [docs/unclassified]\n',
  ]) {
    await put(root, 'pnpm-workspace.yaml', unsupported);
    await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Unsupported workspace YAML/);
  }
});


test('stage-A worker, product, host, SDK inventory and staging changes affect Windows only', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  await change(root, 'windows-stage-a', ['@hellgato/windows-release']);
  for (const path of ['adapters/mirabox-python/src/hellgato_mirabox/worker.py', 'apps/stream-dog/node/session.mjs', 'host-integrations/streamdeck/windows/python/prepare.py', 'vendor/mirabox-sdk/manifest.json', 'scripts/stage-windows-sdk.mjs', 'pyproject.toml', 'uv.lock']) {
    await put(root, path, '# product runtime fixture\n');
    assert.deepEqual((await checkReleaseImpact({ root, base, workingTree: true })).required, ['windows']);
    await rm(resolve(root, path));
  }
});

test('new architecture tests are classified but unknown runtime and script paths still fail', async t => {
  const root = await fixture(t);
  const base = git(root, 'rev-parse', 'HEAD').trim();
  await put(root, '.changeset/contracts-tests.md', '---\n---\n\nAdd adapter contract fixtures without changing runtime behavior.\n');
  await put(root, 'tests/contracts/fixture.json', '{}\n');
  assert.deepEqual((await checkReleaseImpact({ root, base, workingTree: true })).required, []);
  for (const path of ['adapters/unreviewed/driver.py', 'scripts/unreviewed-build.mjs', 'packages/unreviewed/src/index.mjs']) {
    await put(root, path, '# unknown path\n');
    await assert.rejects(checkReleaseImpact({ root, base, workingTree: true }), /Unclassified paths/);
    await rm(resolve(root, path));
  }
});

test('beta metadata cannot enter the stable release pipeline', async t => {
  const root = await fixture(t);
  await put(root, '.changeset/pre.json', json({ mode: 'pre', tag: 'beta', initialVersions: {}, changesets: [] }));
  await assert.rejects(generateReleasePlan({ root }), /Prerelease mode is not supported/);
});

const publicBaseline = () => ({ schemaVersion: 1, component: 'windows', channel: 'stable', sourceVersion: '0.1.2', version: '0.1.1', tag: 'v0.1.1', releaseId: 91, releaseSha: 'd'.repeat(40), installerSha256: 'e'.repeat(64), manifestSha256: null, reason: 'Skip the unpublished prepared version while binding verified public Windows bytes.' });

test('reviewed public Windows baseline is distinct from deterministic source oldVersion', async t => {
  const root = await fixture(t);
  const baseline = publicBaseline();
  await put(root, '.release/windows-public-baseline.json', json(baseline));
  await change(root, 'architecture-transition', ['@hellgato/windows-release']);
  const baseSha = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha });
  assert.equal(plan.components[0].oldVersion, '0.1.2');
  assert.equal(plan.components[0].version, '0.1.3');
  assert.deepEqual(plan.components[0].publicBaseline, baseline);
  await validateReleasePlan({ root, baseSha });
  plan.components[0].publicBaseline.releaseSha = 'a'.repeat(40);
  await put(root, '.release/plan.json', json(plan));
  await assert.rejects(validateReleasePlan({ root, baseSha, structuralOnly: true }), /not canonical/);
});

test('public baseline cannot replace source equality, bootstrap, or exact reviewed base', async t => {
  const root = await fixture(t);
  await put(root, '.release/windows-public-baseline.json', json(publicBaseline()));
  const baseSha = await commit(root);
  await assert.rejects(generateReleasePlan({ root, baseSha, bootstrap: 'windows', from: '0.1.1' }), /cannot be combined with bootstrap/);
  await change(root, 'public-transition', ['@hellgato/windows-release']);
  const updatedBase = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha: updatedBase });
  plan.components[0].oldVersion = '0.1.1';
  await put(root, '.release/plan.json', json(plan));
  await assert.rejects(validateReleasePlan({ root, baseSha: updatedBase, structuralOnly: true }), /exact source/);
});

test('channel policy rejects ambiguous versions and preserves numeric beta upgrade order', async () => {
  const { releaseChannel, channelPolicy, windowsNumericVersion, compareReleaseVersions, validatePublicBaseline } = await import('../scripts/release-policy.mjs');
  assert.deepEqual(channelPolicy('beta'), { branch: 'next', releaseBranch: 'changeset-release/next', distTag: 'beta', prerelease: true });
  assert.equal(windowsNumericVersion('1.2.3-beta.0'), '1.2.3.1');
  assert.equal(windowsNumericVersion('1.2.3-beta.65533'), '1.2.3.65534');
  assert.equal(windowsNumericVersion('1.2.3'), '1.2.3.65535');
  for (const version of ['1.2.3-alpha.0', '1.2.3-beta', '1.2.3-beta.01', '01.2.3', '1.2.3+other', null]) assert.throws(() => releaseChannel(version));
  for (const version of ['65536.0.0', '1.2.3-beta.65534']) assert.throws(() => windowsNumericVersion(version));
  for (const [a, b] of [['1.2.3-beta.0', '1.2.3-beta.1'], ['1.2.3-beta.9', '1.2.3-beta.10'], ['1.2.3-beta.10', '1.2.3'], ['1.2.3', '1.2.4-beta.0']]) assert.equal(compareReleaseVersions(a, b), -1);
  assert.throws(() => channelPolicy('nightly'));
  for (const mutation of [b => b.sourceVersion = '0.1.1', b => b.version = '0.1.3', b => b.tag = 'v0.1.0', b => b.releaseSha = '', b => b.releaseId = true, b => b.installerSha256 = '', b => b.reason = 'skip', b => b.extra = true]) {
    const baseline = publicBaseline(); mutation(baseline);
    assert.throws(() => validatePublicBaseline(baseline, '0.1.2', '0.1.3'));
  }
});

test('real Changesets candidate beta enter, advance, and stable exit replay exactly', async t => {
  const { generateCandidatePlan, validateCandidatePlan } = await import('../scripts/release-candidate.mjs');
  const root = await fixture(t);
  git(root, 'checkout', '-b', 'next');
  await change(root, 'beta-entry', ['@hellgato/core', '@hellgato/windows-release']);
  let baseSha = await commit(root);
  let plan = await generateCandidatePlan({ root, baseSha, channel: 'beta' });
  assert.equal(plan.schemaVersion, 2);
  assert.equal(plan.candidateOnly, true);
  assert.equal(plan.transition, 'enter');
  assert.deepEqual(plan.components.map(c => c.version), ['0.1.3-beta.0', '0.1.1-beta.0']);
  assert.equal(plan.policy.distTag, 'beta');
  assert.equal(plan.preAfter.tag, 'beta');
  await validateCandidatePlan({ root, baseSha });
  await assert.rejects(generateReleasePlan({ root }), /Prerelease mode is not supported/);
  await commit(root);
  assert.equal(await generateCandidatePlan({ root, channel: 'beta' }), null);
  await change(root, 'beta-advance', ['@hellgato/core', '@hellgato/windows-release']);
  baseSha = await commit(root);
  plan = await generateCandidatePlan({ root, baseSha, channel: 'beta' });
  assert.equal(plan.transition, 'advance');
  assert.deepEqual(plan.components.map(c => c.version), ['0.1.3-beta.1', '0.1.1-beta.1']);
  await validateCandidatePlan({ root, baseSha });
  baseSha = await commit(root);
  plan = await generateCandidatePlan({ root, baseSha, channel: 'stable' });
  assert.equal(plan.transition, 'exit');
  assert.equal(plan.preAfter, null);
  assert.deepEqual(plan.components.map(c => c.version), ['0.1.3', '0.1.1']);
  assert.deepEqual(await pendingChangesets(root), []);
  await validateCandidatePlan({ root, baseSha });
});

test('candidate generation and replay reject wrong branch, fake channels, tampering and source changes', async t => {
  const { generateCandidatePlan, validateCandidatePlan } = await import('../scripts/release-candidate.mjs');
  const root = await fixture(t);
  await change(root, 'beta-candidate', ['@hellgato/core']);
  await commit(root);
  await assert.rejects(generateCandidatePlan({ root, channel: 'beta' }), /only be generated on next/);
  git(root, 'checkout', '-b', 'next');
  await assert.rejects(generateCandidatePlan({ root, channel: 'alpha' }), /explicit --channel/);
  await assert.rejects(generateCandidatePlan({ root, channel: 'stable' }), /active beta track/);
  const plan = await generateCandidatePlan({ root, channel: 'beta' });
  const content = json(plan);
  plan.policy.distTag = 'latest';
  await put(root, '.release/candidate-plan.json', json(plan));
  await assert.rejects(validateCandidatePlan({ root }), /Noncanonical channel policy/);
  await put(root, '.release/candidate-plan.json', content);
  await put(root, 'app/hellgato.py', '# unrelated source mutation\n');
  await assert.rejects(validateCandidatePlan({ root }), /Unexpected source change/);
});

test('reviewed beta plans replay exact channel intent and both public baselines', async t => {
  const root = await fixture(t);
  git(root, 'checkout', '-b', 'next');
  await put(root, '.release/windows-public-baseline.json', json(publicBaseline()));
  const tag = { version: '0.0.1-beta', integrity: `sha512-${'A'.repeat(86)}==` };
  await put(root, '.release/npm-public-baseline.json', json({ schemaVersion: 1, component: 'npm', sourceVersion: '0.1.0', packages: ['@hellgato/core', 'hellgato'].map(name => ({ name, latest: tag, beta: tag })), reason: 'Introduce the reviewed beta track while preserving the current registry latest aliases.' }));
  await change(root, 'reviewed-beta', ['@hellgato/core', '@hellgato/windows-release']);
  const baseSha = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha, channel: 'beta' });
  assert.equal(plan.schemaVersion, 3);
  assert.equal(plan.candidateOnly, false);
  assert.equal(plan.branch, 'next');
  assert.equal(plan.components[0].publicBaseline.sourceVersion, '0.1.2');
  assert.equal(plan.components[1].publicBaseline.sourceVersion, '0.1.0');
  await validateReleasePlan({ root, baseSha });
  await validateReleasePlan({ root, baseSha, structuralOnly: true });
  plan.components[1].publicBaseline.packages[0].latest = null;
  await put(root, '.release/plan.json', json(plan));
  await assert.rejects(validateReleasePlan({ root, baseSha, structuralOnly: true }), /Noncanonical channel component/);
});

test('reviewed beta release cannot silently invent a public baseline', async t => {
  const root = await fixture(t);
  git(root, 'checkout', '-b', 'next');
  await change(root, 'missing-beta-baseline', ['@hellgato/core']);
  const baseSha = await commit(root);
  await assert.rejects(generateReleasePlan({ root, baseSha, channel: 'beta' }), /require committed.*npm-public-baseline/);
});

test('Windows-only approved plans expose a private SDK consumer context, never npm intent', async t => {
  const { consumerSdkSource, selectedNpm } = await import('../scripts/npm-release.mjs');
  const root = await fixture(t);
  await change(root, 'windows-consumer', ['@hellgato/windows-release']);
  const baseSha = await commit(root);
  const plan = await generateReleasePlan({ root, baseSha });
  const releaseSha = await commit(root);
  const context = consumerSdkSource(resolve(root, '.release/plan.json'), releaseSha);
  assert.equal(context.version, '0.1.0');
  assert.equal(context.channel, 'stable');
  assert.throws(() => selectedNpm(plan), /does not select npm/);
  assert.throws(() => consumerSdkSource(resolve(root, '.release/plan.json'), 'a'.repeat(40)), /differs from artifact source/);
});

test('Changesets action version branch requires the exact target-branch base', async t => {
  const root = await fixture(t);
  git(root, 'checkout', '-b', 'next');
  await put(root, '.release/windows-public-baseline.json', json(publicBaseline()));
  await change(root, 'action-beta', ['@hellgato/windows-release']);
  const baseSha = await commit(root);
  git(root, 'checkout', '-b', 'changeset-release/next');
  const plan = await generateReleasePlan({ root, baseSha, channel: 'beta' });
  assert.equal(plan.branch, 'next');
  await validateReleasePlan({ root, baseSha });
  git(root, 'reset', '--hard', baseSha);
  git(root, 'clean', '-fd');
  await put(root, 'source-edit.txt', 'The release branch cannot silently include an unmerged source commit.\n');
  await commit(root);
  await assert.rejects(generateReleasePlan({ root, channel: 'beta' }), /exact reviewed release-PR base/);
});

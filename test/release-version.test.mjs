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

import test from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, readdir, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { ROOT } from '../scripts/release-lib.mjs';
import { currentChangelog, versionPackages, versionStatus } from '../scripts/version-packages.mjs';
import { syncReleaseMetadata } from '../scripts/sync-release-metadata.mjs';
import { preflight } from '../scripts/draft-windows-release.mjs';

const APP = '@hellgato/windows-release';
const paths = ['package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', '.changeset/config.json', '.changeset/README.md', '.release/components.json', 'installer/hellgato.iss',
  ...['core', 'hellgato', 'windows-release'].flatMap(name => [`packages/${name}/package.json`, `packages/${name}/CHANGELOG.md`])];
async function fixture(t, changesets = []) {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-versions-'));
  let linked = false;
  t.after(async () => {
    // Unlink the shared dependency junction before Windows recursive cleanup.
    if (linked) await unlink(resolve(root, 'node_modules'));
    await rm(root, { recursive: true, force: true });
  });
  for (const path of paths) {
    await mkdir(dirname(resolve(root, path)), { recursive: true });
    await cp(resolve(ROOT, path), resolve(root, path));
  }
  for (const [name, version] of [['core', '0.1.0'], ['hellgato', '0.1.0'], ['windows-release', '0.1.3']]) {
    const path = resolve(root, `packages/${name}/package.json`);
    const manifest = JSON.parse(await readFile(path, 'utf8'));
    manifest.version = version;
    await writeFile(path, JSON.stringify(manifest, null, 2) + '\n');
    await writeFile(resolve(root, `packages/${name}/CHANGELOG.md`), `# ${manifest.name}\n\n## ${version}\n\nExisting fixture version.\n`);
  }
  for (const [index, entries] of changesets.entries()) {
    await writeFile(resolve(root, `.changeset/test-change-${index}.md`), `---\n${Object.entries(entries).map(([name, bump]) => `"${name}": ${bump}\n`).join('')}---\n\nUser-facing change number ${index}.\n`);
  }
  await syncReleaseMetadata({ root });
  await writeFile(resolve(root, '.gitignore'), 'node_modules\n');
  execFileSync('git', ['init', '--quiet', '--initial-branch=main'], { cwd: root });
  execFileSync('git', ['add', '.'], { cwd: root });
  execFileSync('git', ['-c', 'user.name=Version tests', '-c', 'user.email=version-tests@example.invalid', 'commit', '--quiet', '-m', 'Fixture'], { cwd: root });
  await symlink(resolve(ROOT, 'node_modules'), resolve(root, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  linked = true;
  return root;
}
async function versions(root) {
  return Promise.all(['core', 'hellgato', 'windows-release'].map(async name => JSON.parse(await readFile(resolve(root, `packages/${name}/package.json`), 'utf8')).version));
}
async function snapshot(root) {
  const files = {};
  async function visit(directory) {
    for (const entry of await readdir(resolve(root, directory), { withFileTypes: true })) {
      if (entry.name === '.git' || entry.name === 'node_modules') continue;
      const path = directory ? `${directory}/${entry.name}` : entry.name;
      if (entry.isDirectory()) await visit(path);
      else files[path] = await readFile(resolve(root, path), 'utf8');
    }
  }
  await visit('');
  return files;
}

for (const [label, changesets, expected] of [
  ['core patch', [{ '@hellgato/core': 'patch' }], ['0.1.1', '0.1.1', '0.1.4']],
  ['facade minor', [{ hellgato: 'minor' }], ['0.2.0', '0.2.0', '0.1.4']],
  ['app only', [{ [APP]: 'patch' }], ['0.1.0', '0.1.0', '0.1.4']],
  ['accumulated app patches', [{ [APP]: 'patch' }, { [APP]: 'patch' }], ['0.1.0', '0.1.0', '0.1.4']],
  ['core plus larger app change', [{ '@hellgato/core': 'patch' }, { [APP]: 'minor' }], ['0.1.1', '0.1.1', '0.2.0']],
  ['multiple SDK changes use the maximum', [{ '@hellgato/core': 'patch' }, { hellgato: 'minor' }], ['0.2.0', '0.2.0', '0.1.4']],
]) test(`real Changesets preparation: ${label}`, async t => {
  const root = await fixture(t, changesets);
  const before = await snapshot(root);
  const result = await versionPackages({ root });
  assert.equal(result.changed, true);
  assert.deepEqual(await versions(root), expected);
  await syncReleaseMetadata({ root, check: true });
  const notes = await readFile(resolve(root, `docs/releases/${expected[2]}.md`), 'utf8');
  assert.match(notes, new RegExp(`^# Hellgato Windows ${expected[2].replaceAll('.', '\\.')}\\n`));
  if (label.includes('app') && !label.includes('core')) {
    for (const name of ['core', 'hellgato']) {
      assert.equal(await readFile(resolve(root, `packages/${name}/CHANGELOG.md`), 'utf8'), before[`packages/${name}/CHANGELOG.md`]);
    }
  } else {
    assert.match(notes, /Bundle @hellgato\/core and hellgato/);
    assert.match(notes, /Included SDK changes:/);
    assert.match(notes, /User-facing change number 0/);
  }
  assert.equal((await versionStatus({ root })).releases.length, 0);
  assert.equal(existsSync(resolve(root, '.release/plan.json')), false);
  assert.equal(existsSync(resolve(root, '.changeset/hellgato-bundled-sdk.md')), false);
  const prepared = await snapshot(root);
  assert.deepEqual(await versionPackages({ root }), { changed: false, packages: [] });
  assert.deepEqual(await snapshot(root), prepared, 'Retry must not bump or rewrite anything');
  const beforeSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  execFileSync('git', ['add', '.'], { cwd: root });
  execFileSync('git', ['-c', 'user.name=Version tests', '-c', 'user.email=version-tests@example.invalid', 'commit', '--quiet', '-m', 'Prepared versions'], { cwd: root });
  const sourceSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  execFileSync('git', ['update-ref', 'refs/remotes/origin/main', sourceSha], { cwd: root });
  const repository = 'restarea92/hellgato';
  const selected = preflight({ root, context: { repository, eventName: 'push', ref: 'refs/heads/main', workflowSha: sourceSha,
    event: { repository: { full_name: repository }, after: sourceSha, before: beforeSha, ref: 'refs/heads/main', forced: false, deleted: false } } });
  assert.equal(selected.shouldRelease, true, 'Real generated version commit must be accepted by draft preflight');
  assert.equal(selected.version, expected[2]);
});

for (const changesets of [[], [{}]]) test(`no version changes is a byte-for-byte no-op (${changesets.length} empty inputs)`, async t => {
  const root = await fixture(t, changesets);
  const before = await snapshot(root);
  assert.equal((await versionStatus({ root })).releases.length, 0);
  assert.deepEqual(await versionPackages({ root }), { changed: false, packages: [] });
  assert.deepEqual(await snapshot(root), before);
});

test('existing release notes cannot be overwritten', async t => {
  const root = await fixture(t, [{ [APP]: 'patch' }]);
  await mkdir(resolve(root, 'docs/releases'), { recursive: true });
  await writeFile(resolve(root, 'docs/releases/0.1.4.md'), 'Historical release notes.\n');
  const before = await snapshot(root);
  await assert.rejects(versionPackages({ root }), /historical release notes/);
  assert.deepEqual(await snapshot(root), before);
});

test('stale metadata and legacy plan inputs fail before versioning', async t => {
  const root = await fixture(t, [{ '@hellgato/core': 'patch' }]);
  await writeFile(resolve(root, 'app/release.json'), '{}\n');
  await assert.rejects(versionPackages({ root }), /Stale generated metadata/);
  assert.deepEqual(await versions(root), ['0.1.0', '0.1.0', '0.1.3']);
  await syncReleaseMetadata({ root });
  await writeFile(resolve(root, '.release/plan.json'), '{}\n');
  await assert.rejects(versionPackages({ root }), /Legacy release plans/);
});

test('release notes contain just the selected changelog section', () => {
  assert.equal(currentChangelog('# package\n\n## 1.2.3\n\n### Patch Changes\n\n- fixed\n\n## 1.2.2\n\n- older\n', '1.2.3'), '### Patch Changes\n\n- fixed');
  assert.throws(() => currentChangelog('# package\n', '1.2.3'), /Missing generated changelog/);
});


test('CI detached checkout binds a local base without filtering accumulated changesets', async t => {
  const root = await fixture(t, [{ '@hellgato/core': 'patch' }]);
  execFileSync('git', ['checkout', '--detach', '--quiet'], { cwd: root });
  execFileSync('git', ['branch', '-D', 'main'], { cwd: root });
  await assert.rejects(versionStatus({ root }));
  const sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  execFileSync('git', ['branch', '--force', 'main', sha], { cwd: root });
  assert.equal((await versionStatus({ root })).releases.find(release => release.name === '@hellgato/core').newVersion, '0.1.1');
  assert.match(await readFile(resolve(ROOT, '.github/workflows/versions.yml'), 'utf8'), /git branch --force main "\$GITHUB_SHA"/);
});

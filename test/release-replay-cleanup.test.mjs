import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, unlink } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { cleanupReplayWorktree, git, put } from '../scripts/release-lib.mjs';

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'hellgato-replay-cleanup-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = resolve(directory, 'repository');
  const temporary = resolve(directory, 'replay');
  await put(root, '.gitignore', 'node_modules\n');
  await put(root, 'source.txt', 'Committed replay input.\n');
  await put(root, 'node_modules/@changesets/cli/bin.js', 'Shared dependency sentinel.\n');
  git(root, 'init', '--quiet', '--initial-branch=main');
  git(root, 'config', 'user.name', 'Replay fixture');
  git(root, 'config', 'user.email', 'fixture@example.invalid');
  git(root, 'add', '.');
  git(root, 'commit', '--quiet', '-m', 'Fixture snapshot');
  git(root, 'worktree', 'add', '--quiet', '--detach', temporary, 'HEAD');
  return { root, temporary, dependencyPath: resolve(temporary, 'node_modules') };
}
const linkDependencies = ({ root, dependencyPath }) => symlink(resolve(root, 'node_modules'), dependencyPath, process.platform === 'win32' ? 'junction' : 'dir');
const assertSharedDependency = async root => assert.equal(await readFile(resolve(root, 'node_modules/@changesets/cli/bin.js'), 'utf8'), 'Shared dependency sentinel.\n');

for (const replayFails of [false, true]) test(`replay cleanup preserves shared dependencies after ${replayFails ? 'failed' : 'successful'} replay`, async t => {
  const f = await fixture(t);
  await linkDependencies(f);
  let dependencyLinkCreated = true;
  const replay = async () => {
    try {
      await put(f.temporary, 'generated.txt', 'Replay output.\n');
      if (replayFails) throw new Error('Replay validation failed');
    } finally {
      await cleanupReplayWorktree(f.root, f.temporary, { dependencyLinkCreated });
    }
  };
  if (replayFails) await assert.rejects(replay(), /Replay validation failed/);
  else await replay();
  await assertSharedDependency(f.root);
  assert.equal(existsSync(f.temporary), false);
  assert.equal(git(f.root, 'worktree', 'list', '--porcelain').split('\n').filter(line => line.startsWith('worktree ')).length, 1);
});

test('replay cleanup can remove a worktree before dependency linking', async t => {
  const f = await fixture(t);
  await cleanupReplayWorktree(f.root, f.temporary);
  await assertSharedDependency(f.root);
  assert.equal(existsSync(f.temporary), false);
});

test('replay cleanup preserves a worktree when its owned link becomes a directory', async t => {
  const f = await fixture(t);
  await linkDependencies(f);
  await unlink(f.dependencyPath);
  await put(f.temporary, 'node_modules/local.txt', 'Unexpected replacement must survive.\n');
  await assert.rejects(cleanupReplayWorktree(f.root, f.temporary, { dependencyLinkCreated: true }), /dependency path changed/);
  assert.equal(await readFile(resolve(f.dependencyPath, 'local.txt'), 'utf8'), 'Unexpected replacement must survive.\n');
  await assertSharedDependency(f.root);
  assert.equal(git(f.temporary, 'rev-parse', '--is-inside-work-tree').trim(), 'true');
});

test('replay cleanup preserves an unowned dependency link', async t => {
  const f = await fixture(t);
  await linkDependencies(f);
  await assert.rejects(cleanupReplayWorktree(f.root, f.temporary), /dependency path changed/);
  await assertSharedDependency(f.root);
  assert.equal(git(f.temporary, 'rev-parse', '--is-inside-work-tree').trim(), 'true');
});

test('replay cleanup preserves a worktree when its owned link target changes', async t => {
  const f = await fixture(t);
  await linkDependencies(f);
  await unlink(f.dependencyPath);
  const replacement = resolve(f.root, 'replacement');
  await mkdir(replacement);
  await put(replacement, 'sentinel.txt', 'Foreign target must survive.\n');
  await symlink(replacement, f.dependencyPath, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(cleanupReplayWorktree(f.root, f.temporary, { dependencyLinkCreated: true }), /dependency target changed/);
  assert.equal(await readFile(resolve(replacement, 'sentinel.txt'), 'utf8'), 'Foreign target must survive.\n');
  await assertSharedDependency(f.root);
  assert.equal(git(f.temporary, 'rev-parse', '--is-inside-work-tree').trim(), 'true');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { assertCurrentVersionBase, prepareVersionPullRequest, reportVersionBuild, versionPullRequest, versionPullRequestBody } from '../scripts/version-pr.mjs';

const sha = 'a'.repeat(40);
const repo = { full_name: 'restarea92/hellgato' };
const original = 'This PR was opened by the [Changesets release](https://github.com/changesets/action) GitHub action. Merging automatically publishes to npm.\n\n# Releases\n\nExact generated changelog.\n';
const pull = () => ({ state: 'open', base: { ref: 'main', repo }, head: { ref: 'changeset-release/main', repo, sha }, body: original });
const options = { number: 12, sha, result: 'success', runUrl: 'https://github.com/restarea92/hellgato/actions/runs/42/attempts/2' };

test('correct only the version action introduction, preserving its generated changelogs', () => {
  const body = versionPullRequestBody(original);
  assert.match(body, /npm publishing remains deferred/);
  assert.match(body, /unpublished Windows installer draft/);
  assert.equal(body.slice(body.indexOf('\n')), original.slice(original.indexOf('\n')));
  assert.equal(versionPullRequestBody(body), body);
  assert.throws(() => versionPullRequestBody('Unexpected user-written body.\n'), /Unexpected Changesets PR template/);
});

test('version PR lookup rejects another repository, branch or closed PR', () => {
  assert.equal(versionPullRequest(pull()), sha);
  for (const pr of [
    { ...pull(), state: 'closed' },
    { ...pull(), base: { ref: 'next', repo } },
    { ...pull(), head: { ref: 'changeset-release/main', repo: { full_name: 'other/repo' }, sha } },
    { ...pull(), head: { ref: 'user-branch', repo, sha } },
  ]) assert.throws(() => versionPullRequest(pr), /identity/);
});

test('preparation exposes exact head and only patches the misleading introduction', async () => {
  const calls = [];
  assert.equal(await prepareVersionPullRequest(12, async (path, write) => {
    calls.push({ path, write });
    return pull();
  }), sha);
  assert.deepEqual(calls.map(call => call.path), ['pulls/12', 'pulls/12']);
  assert.equal(calls[1].write.method, 'PATCH');
  assert.deepEqual(Object.keys(calls[1].write.body), ['body']);
});

test('build reporting is idempotent and names the exact source and workflow', async () => {
  let comment;
  let writes = 0;
  const api = async (path, write) => {
    if (path === 'pulls/12') return pull();
    if (!write) return comment ? [comment] : [];
    writes++;
    comment = { id: 99, user: { login: 'github-actions[bot]' }, body: write.body.body };
    return comment;
  };
  assert.equal(await reportVersionBuild(options, api), true);
  assert.match(comment.body, new RegExp(sha));
  assert.match(comment.body, /\*\*success\*\*/);
  assert.match(comment.body, /main trigger commit/);
  assert.equal(await reportVersionBuild(options, api), true);
  assert.equal(writes, 1);
  assert.equal(await reportVersionBuild({ ...options, result: 'failure' }, api), true);
  assert.equal(writes, 2);
  assert.match(comment.body, /\*\*failure\*\*/);
});

test('an old run does not report success on a newer or closed PR', async () => {
  for (const pr of [{ ...pull(), head: { ...pull().head, sha: 'b'.repeat(40) } }, { ...pull(), state: 'closed' }]) {
    const paths = [];
    assert.equal(await reportVersionBuild(options, async path => { paths.push(path); return pr; }), false);
    assert.deepEqual(paths, ['pulls/12']);
  }
});

test('API errors propagate without silently retrying external writes', async () => {
  let calls = 0;
  await assert.rejects(prepareVersionPullRequest(12, async () => { calls++; throw new Error('unavailable'); }), /unavailable/);
  assert.equal(calls, 1);
});


test('an old workflow cannot refresh the version PR from a stale main commit', async () => {
  await assertCurrentVersionBase(sha, async () => ({ object: { type: 'commit', sha } }));
  await assert.rejects(assertCurrentVersionBase(sha, async () => ({ object: { type: 'commit', sha: 'b'.repeat(40) } })), /Stale version workflow/);
  await assert.rejects(assertCurrentVersionBase('main', async () => { throw new Error('should not call'); }), /Exact version workflow source/);
});

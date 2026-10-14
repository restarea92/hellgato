import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { releaseBuildContext } from '../scripts/release-ci.mjs';

async function fixture(t, { pre = false, plan } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'hellgato-ci-context-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, '.changeset'));
  await mkdir(join(root, '.release'));
  if (pre) await writeFile(join(root, '.changeset/pre.json'), JSON.stringify(pre === true ? { mode: 'pre', tag: 'beta' } : pre));
  if (plan) await writeFile(join(root, '.release/plan.json'), typeof plan === 'string' ? plan : JSON.stringify(plan));
  return root;
}
const stable = { sdkMode: 'stable', metadataMode: 'stable' };
const source = { sdkMode: 'source-beta', metadataMode: 'source-beta' };
const plan = components => ({ schemaVersion: 3, repository: 'restarea92/hellgato', candidateOnly: false, channel: 'beta', branch: 'next', components: components.map(component => ({ component })) });

test('ordinary code builds ignore even malformed stale plans and retain stable default', async t => {
  const root = await fixture(t, { plan: 'deliberately stale malformed previous plan' });
  for (const environment of [{}, { GITHUB_REF_NAME: 'main' }, { PR_HEAD_REF: 'feature/source', PR_BASE_REF: 'main' }, { GITHUB_REF_NAME: 'next' }]) assert.deepEqual(await releaseBuildContext({ root, environment }), stable);
});

test('ordinary next and post-handoff main source CI require canonical beta prestate, never plan approval', async t => {
  const root = await fixture(t, { pre: true, plan: 'this old plan is not read' });
  for (const branch of ['next', 'main']) {
    assert.deepEqual(await releaseBuildContext({ root, environment: { GITHUB_REF_NAME: branch } }), source);
    assert.deepEqual(await releaseBuildContext({ root, environment: { PR_HEAD_REF: 'feature/code', PR_BASE_REF: branch } }), source);
  }
  assert.deepEqual(await releaseBuildContext({ root, environment: { GITHUB_REF_NAME: 'untrusted-topic' } }), stable);
  const invalid = await fixture(t, { pre: { mode: 'pre', tag: 'alpha' } });
  await assert.rejects(releaseBuildContext({ root: invalid, environment: { GITHUB_REF_NAME: 'next' } }), /beta prerelease state/);
});

test('release PR context selects reviewed npm or Windows-only consumer mode', async t => {
  for (const [components, sdkMode] of [[['windows', 'npm'], 'plan'], [['windows'], 'consumer']]) {
    const root = await fixture(t, { pre: true, plan: plan(components) });
    for (const environment of [{ PR_HEAD_REF: 'changeset-release/next', PR_BASE_REF: 'next' }, { GITHUB_REF_NAME: 'changeset-release/next' }]) assert.deepEqual(await releaseBuildContext({ root, environment }), { sdkMode, metadataMode: 'plan' });
    await assert.rejects(releaseBuildContext({ root, environment: { PR_HEAD_REF: 'changeset-release/next', PR_BASE_REF: 'main' } }), /differs from its plan branch/);
  }
});

test('explicit reusable release plan requires original fixed path and channel branch', async t => {
  const root = await fixture(t, { pre: true, plan: plan(['npm']) });
  assert.deepEqual(await releaseBuildContext({ root, environment: { RELEASE_BUILD_PLAN: '.release/plan.json', GITHUB_REF_NAME: 'next' } }), { sdkMode: 'plan', metadataMode: 'source-beta' });
  for (const environment of [{ RELEASE_BUILD_PLAN: '../other.json', GITHUB_REF_NAME: 'next' }, { RELEASE_BUILD_PLAN: '.release/plan.json', GITHUB_REF_NAME: 'main' }, { RELEASE_BUILD_PLAN: '.release/plan.json', GITHUB_REF_NAME: 'feature' }]) await assert.rejects(releaseBuildContext({ root, environment }), /Explicit/);
  const candidate = await fixture(t, { plan: { ...plan(['npm']), schemaVersion: 2, candidateOnly: true } });
  await assert.rejects(releaseBuildContext({ root: candidate, environment: { RELEASE_BUILD_PLAN: '.release/plan.json', GITHUB_REF_NAME: 'next' } }), /Explicit/);
});


test('npm-only reviewed version PR keeps Windows metadata in independent source context', async t => {
  const beta = await fixture(t, { pre: true, plan: plan(['npm']) });
  assert.deepEqual(await releaseBuildContext({ root: beta, environment: { PR_HEAD_REF: 'changeset-release/next', PR_BASE_REF: 'next' } }), { sdkMode: 'plan', metadataMode: 'source-beta' });
  const stableRoot = await fixture(t, { plan: { schemaVersion: 1, repository: 'restarea92/hellgato', components: [{ component: 'npm' }] } });
  assert.deepEqual(await releaseBuildContext({ root: stableRoot, environment: { PR_HEAD_REF: 'changeset-release/main', PR_BASE_REF: 'main' } }), { sdkMode: 'plan', metadataMode: 'stable' });
});

// A next -> main handoff copies an exact beta source snapshot. It never grants
// publication approval, rewrites a plan, or exits Changesets prerelease mode.
import { readFile, lstat, mkdtemp, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, SHA, invariant, sha256, json, git, gitFile, put, argumentsFor } from './release-lib.mjs';
import { CHANNEL_PLAN_SCHEMA } from './release-policy.mjs';
import { validateCandidatePlan } from './release-candidate.mjs';

export const CHANNEL_HANDOFF_PATH = '.release/channel-handoff.json';
export const CHANNEL_HANDOFF_BRANCH = 'release/handoff-next-main';
const PLAN = '.release/plan.json';
const REPOSITORY = 'restarea92/hellgato';
const FIELDS = ['schemaVersion', 'repository', 'sourceOnly', 'sourceBranch', 'targetBranch', 'baseSha', 'sourceSha', 'sourceTreeSha', 'sourcePlanSha256', 'reason'];
const assertOrigin = root => {
  const configured = git(root, 'config', '--get-all', 'remote.origin.url').trim().split('\n');
  const resolved = git(root, 'remote', 'get-url', '--all', 'origin').trim().split('\n');
  const official = /^(?:https:\/\/github\.com\/restarea92\/hellgato(?:\.git)?|git@github\.com:restarea92\/hellgato\.git)$/;
  invariant(configured.length === 1 && resolved.length === 1 && official.test(configured[0]) && resolved[0] === configured[0], 'Handoff requires the original restarea92/hellgato GitHub origin without URL rewrites');
};
const exactFields = value => value && typeof value === 'object' && !Array.isArray(value) && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...FIELDS].sort());
const commitSha = (root, ref) => {
  const sha = git(root, 'rev-parse', '--verify', `${ref}^{commit}`).trim();
  invariant(SHA.test(sha), 'Expected a full commit SHA');
  return sha;
};
const changedPaths = (root, ...refs) => git(root, 'diff', '--name-only', '-z', '--no-ext-diff', '--no-renames', ...refs, '--').split('\0').filter(Boolean);
const untrackedPaths = root => git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0').filter(Boolean);
const isAncestor = (root, ancestor, descendant) => git(root, 'merge-base', ancestor, descendant).trim() === ancestor;

function validateRecord(record) {
  invariant(exactFields(record), 'Unexpected channel handoff fields');
  invariant(record.schemaVersion === 1 && record.repository === REPOSITORY && record.sourceOnly === true && record.sourceBranch === 'next' && record.targetBranch === 'main', 'Handoff must be source-only next to main');
  invariant([record.baseSha, record.sourceSha, record.sourceTreeSha].every(value => typeof value === 'string' && SHA.test(value)) && typeof record.sourcePlanSha256 === 'string' && /^[a-f0-9]{64}$/.test(record.sourcePlanSha256), 'Handoff requires exact immutable source and plan digests');
  invariant(record.baseSha !== record.sourceSha, 'Handoff source must advance beyond main base');
  invariant(typeof record.reason === 'string' && record.reason.trim().length >= 30 && record.reason.length <= 4000 && !/[\u0000-\u001f\u007f]/.test(record.reason), 'Handoff requires a reviewable single-line reason');
}

async function safeHandoffPath(root, { missing = false } = {}) {
  const directory = await lstat(resolve(root, '.release'));
  invariant(directory.isDirectory() && !directory.isSymbolicLink(), 'Handoff directory must be a real directory');
  try {
    const file = await lstat(resolve(root, CHANNEL_HANDOFF_PATH));
    invariant(file.isFile() && !file.isSymbolicLink(), 'Handoff must be a regular file');
  } catch (error) { if (!missing || error.code !== 'ENOENT') throw error; }
}

function sourceIdentity(root, sourceSha) {
  assertOrigin(root);
  invariant(typeof sourceSha === 'string' && SHA.test(sourceSha) && commitSha(root, sourceSha) === sourceSha, 'Handoff source requires a full commit SHA');
  invariant(commitSha(root, 'refs/remotes/origin/next') === sourceSha, 'Handoff source differs from the pinned origin/next tip');
  const sourceTreeSha = git(root, 'rev-parse', `${sourceSha}^{tree}`).trim();
  const bytes = gitFile(root, sourceSha, PLAN);
  invariant(bytes !== null, 'Handoff source requires a reviewed beta release plan');
  const plan = JSON.parse(bytes);
  invariant(plan.schemaVersion === CHANNEL_PLAN_SCHEMA && plan.repository === REPOSITORY && plan.candidateOnly === false && plan.channel === 'beta' && plan.branch === 'next' && SHA.test(plan.baseSha ?? ''), 'Handoff source must contain a schema-3 beta release snapshot');
  const parents = git(root, 'show', '-s', '--format=%P', sourceSha).trim().split(' ');
  invariant(parents[0] === plan.baseSha && gitFile(root, plan.baseSha, PLAN) !== bytes, 'Handoff source must be the exact beta release commit, not later next code');
  // Before writing/replaying, reject filesystem aliases at every data boundary.
  // The replay uses only the installed, pinned Changesets CLI, never source code.
  const entries = git(root, 'ls-tree', '-r', '-z', sourceSha).split('\0').filter(Boolean);
  for (const entry of entries) {
    const separator = entry.indexOf('\t');
    const path = entry.slice(separator + 1);
    if (path === 'node_modules' || path.startsWith('node_modules/')) throw new Error('Handoff source may not contain installed dependencies');
    if (['.release', '.changeset', 'packages', 'docs', 'docs/releases', 'app', 'installer'].includes(path) || /^packages\/[^/]+$/.test(path) || path.startsWith('.release/') || path.startsWith('.changeset/') || path === 'pnpm-lock.yaml' || path === 'pnpm-workspace.yaml' || path === 'package.json' || /^packages\/[^/]+\/(?:package\.json|CHANGELOG\.md)$/.test(path) || path === 'app/release.json' || path === 'installer/version.iss' || path.startsWith('docs/releases/')) {
      invariant(/^100644 blob /.test(entry), `Handoff release input must be a regular nonexecutable file: ${path}`);
    }
  }
  return { sourceTreeSha, sourcePlanSha256: sha256(bytes), plan };
}

async function replaySource(root, sourceSha, plan) {
  const temporary = await mkdtemp(join(tmpdir(), 'hellgato-handoff-'));
  try {
    // A separate local clone prevents changes to the caller's worktree, index,
    // refs, and worktree administration while validation is running.
    git(root, 'clone', '--quiet', '--shared', '--no-checkout', '--', resolve(root), temporary);
    git(temporary, 'checkout', '--quiet', '--detach', sourceSha);
    await symlink(resolve(root, 'node_modules'), resolve(temporary, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
    await validateCandidatePlan({ root: temporary, baseSha: plan.baseSha, headSha: sourceSha, releaseIntent: true });
  } finally { await rm(temporary, { recursive: true, force: true }); }
}

// Start a dedicated branch at the freshly fetched origin/next snapshot first.
// Only the fixed handoff record is written; source and carried plan are untouched.
export async function generateChannelHandoff({ root = ROOT, baseSha, sourceSha, reason } = {}) {
  invariant(typeof baseSha === 'string' && SHA.test(baseSha), 'Handoff generation requires the exact main base SHA');
  invariant(git(root, 'branch', '--show-current').trim() === CHANNEL_HANDOFF_BRANCH, `Generate handoffs only on ${CHANNEL_HANDOFF_BRANCH}`);
  invariant(git(root, 'status', '--porcelain', '--untracked-files=all').trim() === '', 'Handoff generation requires a clean committed source checkout');
  invariant(commitSha(root, 'refs/remotes/origin/main') === baseSha, 'Handoff main base differs from the pinned origin/main tip');
  invariant(typeof sourceSha === 'string' && SHA.test(sourceSha) && commitSha(root, 'HEAD') === sourceSha, 'Handoff checkout must equal its exact pinned source');
  const source = sourceIdentity(root, sourceSha);
  invariant(isAncestor(root, baseSha, sourceSha), 'Handoff main base must be the source merge-base ancestor');
  const record = { schemaVersion: 1, repository: REPOSITORY, sourceOnly: true, sourceBranch: 'next', targetBranch: 'main', baseSha, sourceSha, sourceTreeSha: source.sourceTreeSha, sourcePlanSha256: source.sourcePlanSha256, reason };
  validateRecord(record);
  await safeHandoffPath(root, { missing: true });
  await replaySource(root, sourceSha, source.plan);
  // Ref/check-out changes during replay must not result in a stale write.
  assertOrigin(root);
  invariant(commitSha(root, 'refs/remotes/origin/main') === baseSha && commitSha(root, 'refs/remotes/origin/next') === sourceSha && commitSha(root, 'HEAD') === sourceSha && git(root, 'status', '--porcelain', '--untracked-files=all').trim() === '', 'Handoff refs or checkout changed during replay');
  await put(root, CHANNEL_HANDOFF_PATH, json(record));
  return record;
}

// Invoke only when this record changed in the current PR/push. Its persistence
// never exempts later ordinary code/version changes from the impact guard.
export async function validateChannelHandoff({ root = ROOT, baseSha, headSha, workingTree = false, targetBranch } = {}) {
  invariant(targetBranch === 'main', 'Channel handoff can only target main');
  invariant(typeof baseSha === 'string' && SHA.test(baseSha), 'Handoff validation requires the exact PR merge-base SHA');
  invariant(typeof workingTree === 'boolean', 'Invalid handoff working-tree mode');
  const checkoutSha = commitSha(root, 'HEAD');
  if (!workingTree) invariant(typeof headSha === 'string' && SHA.test(headSha) && headSha === checkoutSha, 'Handoff checkout differs from exact requested head');
  else invariant(headSha === undefined || headSha === checkoutSha, 'Handoff working-tree head differs from checkout');
  await safeHandoffPath(root);
  const bytes = await readFile(resolve(root, CHANNEL_HANDOFF_PATH), 'utf8');
  const record = JSON.parse(bytes);
  validateRecord(record);
  invariant(bytes === json(record), 'Handoff record must have canonical generated bytes');
  invariant(record.baseSha === baseSha, 'Stale handoff main base or PR merge-base');
  const mainSha = commitSha(root, 'refs/remotes/origin/main');
  // An unmerged handoff must be rebuilt if main advances. After integration,
  // only a history-preserving merge with this exact first parent is accepted;
  // squash/rebase merges discard the pinned source ancestry and are refused.
  const mergedHandoff = !workingTree && isAncestor(root, checkoutSha, mainSha) && git(root, 'rev-parse', `${checkoutSha}^1`).trim() === baseSha;
  invariant(mainSha === baseSha || mergedHandoff, 'Handoff main advanced; require a fresh base or an exact history-preserving main merge');
  invariant(isAncestor(root, baseSha, record.sourceSha) && isAncestor(root, record.sourceSha, checkoutSha), 'Handoff source/head does not descend from its main merge-base');
  const source = sourceIdentity(root, record.sourceSha);
  invariant(record.sourceTreeSha === source.sourceTreeSha && record.sourcePlanSha256 === source.sourcePlanSha256, 'Handoff source tree or plan digest mismatch');
  const changed = [...new Set([...changedPaths(root, record.sourceSha, ...(workingTree ? [] : [headSha])), ...(workingTree ? untrackedPaths(root) : [])])].sort();
  invariant(changed.length === 1 && changed[0] === CHANNEL_HANDOFF_PATH, 'Handoff tree must equal the pinned next source plus only the handoff record');
  if (!workingTree) {
    invariant(/^100644 blob /.test(git(root, 'ls-tree', headSha, '--', CHANNEL_HANDOFF_PATH)), 'Committed handoff must be a regular nonexecutable file');
    invariant(changedPaths(root, headSha).length === 0 && untrackedPaths(root).length === 0, 'Dirty handoff checkout');
  }
  invariant(gitFile(root, baseSha, CHANNEL_HANDOFF_PATH) !== bytes, 'Handoff record must be newly changed in this PR');
  await replaySource(root, record.sourceSha, source.plan);
  assertOrigin(root);
  invariant(commitSha(root, 'refs/remotes/origin/main') === mainSha && commitSha(root, 'refs/remotes/origin/next') === record.sourceSha && commitSha(root, 'HEAD') === checkoutSha && await readFile(resolve(root, CHANNEL_HANDOFF_PATH), 'utf8') === bytes, 'Handoff source ref or checkout changed during replay');
  const finalChanges = [...new Set([...changedPaths(root, record.sourceSha, ...(workingTree ? [] : [headSha])), ...(workingTree ? untrackedPaths(root) : [])])];
  invariant(finalChanges.length === 1 && finalChanges[0] === CHANNEL_HANDOFF_PATH && (workingTree || changedPaths(root, headSha).length === 0 && untrackedPaths(root).length === 0), 'Handoff tree changed during replay');
  return record;
}

async function main() {
  const args = argumentsFor(process.argv.slice(2));
  invariant(Object.keys(args).every(key => ['base', 'source', 'head', 'reason', 'target-branch', 'check', 'working-tree'].includes(key)), 'Unknown channel-handoff option');
  if (args.check) {
    invariant(!args.source && !args.reason, 'Handoff validation reads its pinned source and reason');
    await validateChannelHandoff({ baseSha: args.base, headSha: args.head, workingTree: Boolean(args['working-tree']), targetBranch: args['target-branch'] });
    console.log('Verified beta source handoff to main; no publication approval');
  } else {
    invariant(!args.head && !args['working-tree'] && !args['target-branch'], 'Validation options require --check');
    await generateChannelHandoff({ baseSha: args.base, sourceSha: args.source, reason: args.reason });
    console.log('Prepared beta source handoff to main; commit and review the handoff PR before stable versioning');
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch(error => { console.error(`Channel handoff refused: ${error.message}`); process.exitCode = 1; });

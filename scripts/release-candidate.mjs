// Offline candidate plans are deliberately incompatible with the public
// schema-v1 coordinator. This command never reads credentials or writes remotely.
import { readFile, readdir, mkdtemp, symlink } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, SHA, invariant, sha256, json, git, gitFile, readJson, put, configuration, pendingChangesets, parseChangeset, pnpm, argumentsFor, cleanupReplayWorktree } from './release-lib.mjs';
import { CANDIDATE_SCHEMA, CHANNEL_PLAN_SCHEMA, NPM_BASELINE_PATH, PUBLIC_BASELINE_PATH, validateNpmBaseline, validateChannelWindowsBaseline, channelPolicy, releaseChannel, compareReleaseVersions, windowsNumericVersion, validatePrereleaseState } from './release-policy.mjs';
import { syncReleaseMetadata } from './sync-release-metadata.mjs';

const PLAN = '.release/candidate-plan.json';
const PRE = '.changeset/pre.json';
const fileExists = (root, path) => existsSync(resolve(root, path));
const prereleaseState = async root => fileExists(root, PRE) ? readJson(resolve(root, PRE)) : null;
async function archivedChangesets(root) {
  const directory = resolve(root, '.changeset/pre');
  if (!existsSync(directory)) return [];
  const entries = await readdir(directory, { withFileTypes: true });
  invariant(entries.every(entry => entry.isFile() && /^[a-z0-9][a-z0-9-]*\.md$/.test(entry.name)), 'Unexpected archived prerelease input');
  return Promise.all(entries.map(entry => entry.name).sort().map(async name => parseChangeset(await readFile(resolve(directory, name), 'utf8'), name.slice(0, -3))));
}
const bytesRecord = async (root, path) => ({ path, sha256: sha256(await readFile(resolve(root, path))) });
const packagePaths = component => component === 'windows' ? ['packages/windows-release'] : ['packages/core', 'packages/hellgato'];
const generatedPaths = plan => [...new Set(['pnpm-lock.yaml', 'app/release.json', 'installer/version.iss', ...(plan.channel === 'beta' ? [PRE, ...plan.archivedAfter.map(c => `.changeset/pre/${c.id}.md`)] : []), ...plan.components.flatMap(c => [c.notes, ...packagePaths(c.component).flatMap(path => [`${path}/package.json`, `${path}/CHANGELOG.md`])])])].sort();

export async function generateCandidatePlan({ root = ROOT, baseSha, channel, replay = false, releaseIntent = false } = {}) {
  invariant(channel === 'beta' || channel === 'stable', 'Candidate generation requires explicit --channel beta or stable');
  const config = await configuration(root, { channel: 'beta' });
  baseSha ??= git(root, 'rev-parse', 'HEAD').trim();
  invariant(SHA.test(baseSha), 'Candidate generation requires an exact base SHA');
  if (!replay) {
    invariant(git(root, 'rev-parse', 'HEAD').trim() === baseSha && git(root, 'status', '--porcelain', '--untracked-files=all').trim() === '', 'Candidate generation requires the exact clean committed base');
    const branch = releaseIntent ? channelPolicy(channel).branch : 'next';
    const current = git(root, 'branch', '--show-current').trim();
    // changesets/action prepares its canonical version branch before invoking
    // our custom script. Accept that checkout only at the exact target base.
    const releaseBranch = releaseIntent && current === channelPolicy(channel).releaseBranch;
    invariant(current === branch || releaseBranch && git(root, 'rev-parse', `refs/heads/${branch}`).trim() === baseSha, `Candidate versions may only be generated on ${branch} or its exact reviewed release-PR base`);
  }
  invariant(releaseIntent || !fileExists(root, PUBLIC_BASELINE_PATH), 'Candidate tracks cannot use a stable public-baseline override');
  invariant(pnpm(root, ['--version'], { capture: true }).trim() === '11.10.0', 'Use the pinned pnpm 11.10.0');
  const pre = await prereleaseState(root);
  if (pre) validatePrereleaseState(pre);
  const archivedBefore = await archivedChangesets(root);
  invariant(pre || archivedBefore.length === 0 && config.packages.every(p => releaseChannel(p.version) === 'stable'), 'Orphan prerelease inputs or versions require an active beta track');
  invariant(channel === 'beta' || pre !== null, 'Stable candidate promotion requires an active beta track');
  const changesets = await pendingChangesets(root);
  const names = new Set(config.packages.map(p => p.name));
  for (const c of [...changesets, ...archivedBefore]) for (const r of c.releases) invariant(names.has(r.name), `Unclassified changeset package ${r.name}`);
  invariant(!changesets.some(c => archivedBefore.some(a => a.id === c.id)), 'Changeset IDs cannot overwrite archived beta inputs');
  const pending = changesets;
  if (channel === 'beta' && !pending.some(c => c.releases.length)) return null;
  const cli = args => execFileSync(process.execPath, [resolve(root, 'node_modules/@changesets/cli/bin.js'), ...args], { cwd: root, stdio: 'inherit' });
  const transition = channel === 'stable' ? 'exit' : pre ? 'advance' : 'enter';
  if (transition === 'enter') cli(['pre', 'enter', 'beta']);
  if (transition === 'exit') cli(['pre', 'exit']);
  cli(['version']);
  const after = await configuration(root, { channel: 'beta' });
  const preAfter = await prereleaseState(root);
  invariant(channel === 'beta' ? preAfter?.mode === 'pre' : preAfter === null, 'Changesets did not produce the expected prerelease state');
  const components = [];
  for (const declaration of config.components) {
    const before = config.packages.find(p => p.name === declaration.packages[0].name);
    const target = after.packages.find(p => p.name === before.name);
    if (target.version === before.version) continue;
    invariant(releaseChannel(target.version) === channel && compareReleaseVersions(target.version, before.version) > 0, 'Candidate target must increase in its explicit channel');
    if (declaration.component === 'windows') windowsNumericVersion(target.version);
    const component = declaration.component;
    if (component === 'npm') for (const pkg of declaration.packages) {
      const path = `${pkg.path}/package.json`;
      const metadata = await readJson(resolve(root, path));
      invariant(metadata.publishConfig && metadata.publishConfig.registry === 'https://registry.npmjs.org/', 'Candidate package requires pinned npm registry');
      metadata.publishConfig.tag = channelPolicy(channel).distTag;
      await put(root, path, json(metadata));
    }
    const notes = `docs/releases/${component === 'npm' ? 'npm-' : ''}${target.version}.md`;
    invariant(!fileExists(root, notes), `Refusing to replace existing candidate notes: ${notes}`);
    const relevant = (channel === 'stable' ? [...archivedBefore, ...changesets] : pending).filter(c => c.releases.some(r => declaration.packages.some(p => p.name === r.name)));
    await put(root, notes, `# Hellgato ${component === 'windows' ? 'Windows' : 'npm'} ${target.version}\n\n${relevant.map(c => c.summary).join('\n\n')}\n`);
    let publicBaseline;
    if (releaseIntent) {
      const path = component === 'windows' ? PUBLIC_BASELINE_PATH : NPM_BASELINE_PATH;
      const bytes = gitFile(root, baseSha, path);
      invariant(bytes !== null, `Reviewed channel releases require committed ${path}`);
      publicBaseline = component === 'windows' ? validateChannelWindowsBaseline(JSON.parse(bytes), before.version, target.version) : validateNpmBaseline(JSON.parse(bytes), before.version, target.version, channel);
    }
    components.push({ component, oldVersion: before.version, version: target.version, channel, tag: component === 'windows' ? `v${target.version}` : `hellgato@${target.version}`, notes, ...(component === 'npm' ? { packages: declaration.packages.map(p => ({ name: p.name, oldVersion: before.version, version: target.version, tag: `${p.name}@${target.version}` })) } : {}), ...(publicBaseline ? { publicBaseline } : {}) });
  }
  invariant(components.length > 0, 'Changesets did not select a candidate component');
  const windows = after.packages.find(p => p.name === '@hellgato/windows-release');
  await syncReleaseMetadata({ root, channel: releaseChannel(windows.version) });
  pnpm(root, ['install', '--lockfile-only', '--ignore-scripts', '--offline', '--frozen-lockfile=false']);
  const plan = { schemaVersion: releaseIntent ? CHANNEL_PLAN_SCHEMA : CANDIDATE_SCHEMA, repository: config.repository, candidateOnly: !releaseIntent, branch: releaseIntent ? channelPolicy(channel).branch : 'next', baseSha, channel, transition, policy: channelPolicy(channel), changesets, archivedBefore, archivedAfter: await archivedChangesets(root), preBefore: pre, preAfter, components, generatedFiles: [] };
  for (const path of generatedPaths(plan)) plan.generatedFiles.push(await bytesRecord(root, path));
  await put(root, releaseIntent ? '.release/plan.json' : PLAN, json(plan));
  return plan;
}

export async function validateCandidatePlan({ root = ROOT, baseSha, headSha, releaseIntent = false, structuralOnly = false } = {}) {
  const planPath = releaseIntent ? '.release/plan.json' : PLAN;
  const plan = await readJson(resolve(root, planPath));
  invariant(plan.schemaVersion === (releaseIntent ? CHANNEL_PLAN_SCHEMA : CANDIDATE_SCHEMA) && plan.candidateOnly === !releaseIntent && plan.repository === 'restarea92/hellgato' && plan.branch === (releaseIntent ? channelPolicy(plan.channel).branch : 'next') && SHA.test(plan.baseSha), 'Invalid artifact-only candidate plan');
  invariant(!baseSha || baseSha === plan.baseSha, 'Stale candidate plan base SHA');
  invariant(!headSha || SHA.test(headSha) && git(root, 'rev-parse', 'HEAD').trim() === headSha, 'Candidate checkout differs from requested head');
  baseSha = plan.baseSha;
  await validateChannelStructure({ root, plan, planPath, baseSha, headSha, releaseIntent });
  if (structuralOnly) return plan;
  const temporary = await mkdtemp(join(tmpdir(), 'hellgato-candidate-'));
  let dependencyLinkCreated = false;
  try {
    git(root, 'worktree', 'add', '--quiet', '--detach', temporary, baseSha);
    await symlink(resolve(root, 'node_modules'), resolve(temporary, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
    dependencyLinkCreated = true;
    const expected = await generateCandidatePlan({ root: temporary, baseSha, channel: plan.channel, replay: true, releaseIntent });
    invariant(JSON.stringify(expected) === JSON.stringify(plan), 'Candidate plan differs from deterministic Changesets pre enter/version/exit replay');
    for (const file of expected.generatedFiles) invariant(sha256(await readFile(resolve(root, file.path))) === file.sha256, `Candidate generated file digest mismatch: ${file.path}`);
    invariant(JSON.stringify(await pendingChangesets(root)) === JSON.stringify(await pendingChangesets(temporary)), 'Candidate consumed/retained changesets differ from replay');
    invariant(JSON.stringify(await archivedChangesets(root)) === JSON.stringify(expected.archivedAfter), 'Candidate archived changesets differ from replay');
    invariant(JSON.stringify(await prereleaseState(root)) === JSON.stringify(await prereleaseState(temporary)), 'Candidate prerelease state differs from replay');
    const allowed = new Set([planPath, PRE, ...expected.generatedFiles.map(f => f.path), ...expected.changesets.map(c => `.changeset/${c.id}.md`), ...expected.archivedBefore.map(c => `.changeset/pre/${c.id}.md`)]);
    const changed = [...git(root, 'diff', '--name-only', baseSha, ...(headSha ? [headSha] : [])).trim().split('\n'), ...git(root, 'ls-files', '--others', '--exclude-standard').trim().split('\n')].filter(Boolean);
    for (const path of changed) invariant(allowed.has(path), `Unexpected source change in candidate PR: ${path}`);
    if (headSha) invariant(git(root, 'diff', '--name-only', headSha).trim() === '', 'Dirty candidate checkout');
    return plan;
  } finally {
    await cleanupReplayWorktree(root, temporary, { dependencyLinkCreated });
  }
}

async function validateChannelStructure({ root, plan, planPath, baseSha, headSha, releaseIntent }) {
  const fields = ['schemaVersion', 'repository', 'candidateOnly', 'branch', 'baseSha', 'channel', 'transition', 'policy', 'changesets', 'archivedBefore', 'archivedAfter', 'preBefore', 'preAfter', 'components', 'generatedFiles'];
  invariant(JSON.stringify(Object.keys(plan).sort()) === JSON.stringify(fields.sort()), 'Unexpected channel plan fields');
  invariant(JSON.stringify(plan.policy) === JSON.stringify(channelPolicy(plan.channel)), 'Noncanonical channel policy');
  const paths = git(root, 'ls-tree', '-r', '--name-only', baseSha, '.changeset').trim().split('\n').filter(Boolean);
  const originals = prefix => paths.filter(path => path.startsWith(prefix) && /^[a-z0-9][a-z0-9-]*\.md$/.test(path.slice(prefix.length)) && path !== '.changeset/README.md').sort().map(path => parseChangeset(gitFile(root, baseSha, path), path.slice(prefix.length, -3)));
  const before = originals('.changeset/');
  const archivedBefore = originals('.changeset/pre/');
  invariant(JSON.stringify(plan.changesets) === JSON.stringify(before) && JSON.stringify(plan.archivedBefore) === JSON.stringify(archivedBefore), 'Channel changeset inputs differ from the exact base');
  const preBytes = gitFile(root, baseSha, PRE);
  const preBefore = preBytes === null ? null : validatePrereleaseState(JSON.parse(preBytes));
  invariant(JSON.stringify(plan.preBefore) === JSON.stringify(preBefore), 'Channel prerelease input differs from the exact base');
  invariant(plan.transition === (plan.channel === 'stable' ? 'exit' : preBefore ? 'advance' : 'enter') && (plan.channel !== 'stable' || preBefore), 'Invalid channel transition');
  const archivedAfter = plan.channel === 'beta' ? [...archivedBefore, ...before].sort((a, b) => a.id.localeCompare(b.id, 'en')) : [];
  invariant(JSON.stringify(plan.archivedAfter) === JSON.stringify(archivedAfter) && JSON.stringify(await archivedChangesets(root)) === JSON.stringify(archivedAfter), 'Channel archived inputs differ from consumed base changesets');
  invariant((await pendingChangesets(root)).length === 0, 'Channel versioning must consume pending changesets');
  const preAfter = plan.channel === 'beta' ? { mode: 'pre', tag: 'beta' } : null;
  invariant(JSON.stringify(plan.preAfter) === JSON.stringify(preAfter) && JSON.stringify(await prereleaseState(root)) === JSON.stringify(preAfter), 'Channel prerelease output mismatch');
  const config = await configuration(root, { channel: 'beta' });
  invariant(Array.isArray(plan.components) && plan.components.length > 0 && plan.components.length <= 2 && new Set(plan.components.map(c => c.component)).size === plan.components.length, 'Channel release must select distinct components');
  for (const declaration of config.components) {
    const name = declaration.packages[0].name;
    const source = JSON.parse(gitFile(root, baseSha, `${declaration.packages[0].path}/package.json`)).version;
    const actual = config.packages.find(p => p.name === name).version;
    const component = plan.components.find(c => c.component === declaration.component);
    if (!component) { invariant(source === actual, 'Unselected channel component version changed'); continue; }
    invariant(component.oldVersion === source && component.version === actual && releaseChannel(actual) === plan.channel && compareReleaseVersions(source, actual) < 0, 'Channel component source/version mismatch');
    let publicBaseline;
    if (releaseIntent) {
      const path = declaration.component === 'windows' ? PUBLIC_BASELINE_PATH : NPM_BASELINE_PATH;
      const bytes = gitFile(root, baseSha, path);
      invariant(bytes !== null, `Missing reviewed public baseline: ${path}`);
      publicBaseline = declaration.component === 'windows' ? validateChannelWindowsBaseline(JSON.parse(bytes), source, actual) : validateNpmBaseline(JSON.parse(bytes), source, actual, plan.channel);
    }
    const expected = { component: declaration.component, oldVersion: source, version: actual, channel: plan.channel, tag: declaration.component === 'windows' ? `v${actual}` : `hellgato@${actual}`, notes: `docs/releases/${declaration.component === 'npm' ? 'npm-' : ''}${actual}.md`, ...(declaration.component === 'npm' ? { packages: declaration.packages.map(p => ({ name: p.name, oldVersion: source, version: actual, tag: `${p.name}@${actual}` })) } : {}), ...(publicBaseline ? { publicBaseline } : {}) };
    invariant(JSON.stringify(component) === JSON.stringify(expected), 'Noncanonical channel component or public baseline');
    for (const p of declaration.packages) invariant(JSON.parse(gitFile(root, baseSha, `${p.path}/package.json`)).version === source, 'Fixed-group source versions differ');
  }
  const windows = config.packages.find(p => p.name === '@hellgato/windows-release');
  await syncReleaseMetadata({ root, check: true, channel: releaseChannel(windows.version) });
  invariant(Array.isArray(plan.generatedFiles) && JSON.stringify(plan.generatedFiles.map(f => f.path)) === JSON.stringify(generatedPaths(plan)), 'Channel generated file allowlist mismatch');
  for (const file of plan.generatedFiles) invariant(JSON.stringify(Object.keys(file).sort()) === JSON.stringify(['path', 'sha256']) && /^[a-f0-9]{64}$/.test(file.sha256) && sha256(await readFile(resolve(root, file.path))) === file.sha256, `Channel generated file digest mismatch: ${file.path}`);
  const allowed = new Set([planPath, PRE, ...plan.generatedFiles.map(f => f.path), ...before.map(c => `.changeset/${c.id}.md`), ...archivedBefore.map(c => `.changeset/pre/${c.id}.md`)]);
  const changed = [...git(root, 'diff', '--name-only', baseSha, ...(headSha ? [headSha] : [])).trim().split('\n'), ...git(root, 'ls-files', '--others', '--exclude-standard').trim().split('\n')].filter(Boolean);
  for (const path of changed) invariant(allowed.has(path), `Unexpected source change in candidate PR: ${path}`);
  if (headSha) invariant(git(root, 'diff', '--name-only', headSha).trim() === '', 'Dirty candidate checkout');
}

async function main() {
  const args = argumentsFor(process.argv.slice(2));
  invariant(Object.keys(args).every(key => ['base', 'head', 'channel', 'check'].includes(key)), 'Unknown candidate option');
  if (args.check) {
    invariant(!args.channel, 'Candidate validation reads the reviewed plan channel');
    await validateCandidatePlan({ baseSha: args.base, headSha: args.head });
    console.log('Artifact-only candidate plan replay verified; publication remains disabled');
  } else {
    invariant(!args.head, '--head requires --check');
    const plan = await generateCandidatePlan({ baseSha: args.base, channel: args.channel });
    console.log(plan ? `Prepared ${plan.channel} candidate: ${plan.components.map(c => c.version).join(', ')}; no publication approval` : 'No new candidate changesets');
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch(error => { console.error(`Candidate refused: ${error.message}`); process.exitCode = 1; });

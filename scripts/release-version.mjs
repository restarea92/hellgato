import { readFile, mkdtemp, rm, symlink } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, SHA, VERSION, invariant, sha256, json, git, gitFile, readJson, put, safePath, configuration, pendingChangesets, parseChangeset, pnpm, argumentsFor, cleanupReplayWorktree } from './release-lib.mjs';
import { syncReleaseMetadata } from './sync-release-metadata.mjs';
import { generateCandidatePlan, validateCandidatePlan } from './release-candidate.mjs';
import { CHANNEL_PLAN_SCHEMA, PUBLIC_BASELINE_PATH, validatePublicBaseline } from './release-policy.mjs';

const PLAN = '.release/plan.json';
const renderNotes = (component, version, changesets) => `# Hellgato ${component === 'windows' ? 'Windows' : 'npm'} ${version}\n\n${changesets.filter(c => c.releases.some(r => component === 'windows' ? r.name === '@hellgato/windows-release' : r.name !== '@hellgato/windows-release')).map(c => c.summary).join('\n\n')}\n`;
const expectedFiles = components => [...new Set(['pnpm-lock.yaml', ...components.flatMap(c => [c.notes, ...(c.component === 'windows' ? ['app/release.json', 'installer/version.iss', 'packages/windows-release/package.json', 'packages/windows-release/CHANGELOG.md'] : ['packages/core/package.json', 'packages/core/CHANGELOG.md', 'packages/hellgato/package.json', 'packages/hellgato/CHANGELOG.md'])])])].sort();
function componentRecord(component, oldVersion, version, packages, publicBaseline) {
  return { component, oldVersion, version, channel: 'stable', tag: component === 'windows' ? `v${version}` : `hellgato@${version}`, notes: `docs/releases/${component === 'npm' ? 'npm-' : ''}${version}.md`, ...(component === 'npm' ? { packages: packages.map(p => ({ name: p.name, oldVersion, version, tag: `${p.name}@${version}` })) } : {}), ...(publicBaseline ? { publicBaseline } : {}) };
}

export async function generateReleasePlan({ root = ROOT, baseSha, bootstrap, from, replay = false, channel } = {}) {
  if (channel) {
    invariant(!bootstrap && !from, 'Channel release plans cannot use bootstrap overrides');
    return generateCandidatePlan({ root, baseSha, channel, replay, releaseIntent: true });
  }
  const config = await configuration(root);
  baseSha ??= git(root, 'rev-parse', 'HEAD').trim();
  invariant(SHA.test(baseSha), 'Versioning requires an exact base SHA');
  if (!replay) {
    invariant(git(root, 'rev-parse', 'HEAD').trim() === baseSha, 'Versioning must start at the exact base checkout');
    invariant(git(root, 'status', '--porcelain', '--untracked-files=all').trim() === '', 'Versioning requires a clean worktree with committed changesets');
  }
  invariant(pnpm(root, ['--version'], { capture: true }).trim() === '11.10.0', 'Use the pinned pnpm 11.10.0');
  const changesets = await pendingChangesets(root);
  const names = new Set(config.packages.map(p => p.name));
  for (const c of changesets) for (const r of c.releases) invariant(names.has(r.name), `Unclassified changeset package ${r.name}`);
  const before = new Map(config.packages.map(p => [p.name, p.version]));
  const components = [];
  if (bootstrap) {
    invariant(!existsSync(resolve(root, PUBLIC_BASELINE_PATH)), 'Explicit public baseline cannot be combined with bootstrap');
    invariant(['windows', 'npm'].includes(bootstrap), 'Bootstrap requires one explicit component');
    invariant(changesets.every(c => c.releases.length === 0), 'Bootstrap cannot consume pending version bumps');
    for (const changeset of changesets) await rm(resolve(root, `.changeset/${changeset.id}.md`));
    invariant(VERSION.test(from ?? '') || bootstrap === 'npm' && from === '0.0.1-beta', 'Bootstrap requires an explicit supported previous published --from version');
    const component = config.components.find(c => c.component === bootstrap);
    const version = before.get(component.packages[0].name);
    invariant(from !== version, 'Bootstrap previous and prepared versions must differ');
    if (bootstrap === 'windows') invariant(from === '0.1.1' && version === '0.1.2', 'Only the reviewed Windows 0.1.1 -> prepared 0.1.2 bootstrap is supported');
    if (bootstrap === 'npm') invariant(['0.0.0', '0.0.1-beta'].includes(from) && version === '0.1.0', 'Only the initial npm 0.1.0 bootstrap is supported');
    const record = componentRecord(bootstrap, from, version, component.packages);
    if (bootstrap === 'windows') invariant(existsSync(resolve(root, record.notes)), 'Prepared Windows release notes must already exist');
    else await put(root, record.notes, '# Hellgato npm 0.1.0\n\nInitial release of @hellgato/core and hellgato. Source versions and the packed exact core dependency are both 0.1.0.\n');
    components.push(record);
  } else {
    if (!changesets.some(c => c.releases.length)) return null;
    execFileSync(process.execPath, [resolve(root, 'node_modules/@changesets/cli/bin.js'), 'version'], { cwd: root, stdio: 'inherit' });
    const after = await configuration(root);
    for (const component of config.components) {
      const name = component.packages[0].name;
      const version = after.packages.find(p => p.name === name).version;
      if (version === before.get(name)) continue;
      const baselineBytes = component.component === 'windows' ? gitFile(root, baseSha, PUBLIC_BASELINE_PATH) : null;
      const publicBaseline = baselineBytes === null ? undefined : validatePublicBaseline(JSON.parse(baselineBytes), before.get(name), version);
      const record = componentRecord(component.component, before.get(name), version, component.packages, publicBaseline);
      invariant(!existsSync(resolve(root, record.notes)), `Refusing to replace existing release notes: ${record.notes}`);
      await put(root, record.notes, renderNotes(component.component, version, changesets));
      components.push(record);
    }
    invariant(components.length > 0, 'Changesets did not select a release component');
    invariant((await pendingChangesets(root)).length === 0, 'Changesets left unconsumed inputs');
  }
  await syncReleaseMetadata({ root });
  pnpm(root, ['install', '--lockfile-only', '--ignore-scripts', '--offline', '--frozen-lockfile=false']);
  const generatedFiles = [];
  for (const path of expectedFiles(components)) generatedFiles.push({ path, sha256: sha256(await readFile(resolve(root, path))) });
  const plan = { schemaVersion: 1, repository: config.repository, baseSha, bootstrap: Boolean(bootstrap), changesets, components, generatedFiles };
  await put(root, PLAN, json(plan));
  if (!replay) {
    const allowed = new Set([PLAN, ...generatedFiles.map(file => file.path)]);
    for (const path of git(root, 'ls-files', '--others', '--exclude-standard').trim().split('\n').filter(Boolean)) invariant(allowed.has(path), `Unexpected generated file: ${path}`);
    await validateReleasePlan({ root, baseSha, structuralOnly: true });
  }
  return plan;
}

export async function validateReleasePlan({ root = ROOT, baseSha, headSha, structuralOnly = false } = {}) {
  const plan = await readJson(resolve(root, PLAN));
  if (plan.schemaVersion === CHANNEL_PLAN_SCHEMA) return validateCandidatePlan({ root, baseSha, headSha, releaseIntent: true, structuralOnly });
  invariant(JSON.stringify(Object.keys(plan).sort()) === JSON.stringify(['schemaVersion', 'repository', 'baseSha', 'bootstrap', 'changesets', 'components', 'generatedFiles'].sort()), 'Unexpected release plan fields');
  invariant(plan.schemaVersion === 1 && plan.repository === 'restarea92/hellgato' && typeof plan.bootstrap === 'boolean', 'Unsupported release plan');
  invariant(SHA.test(plan.baseSha) && (!baseSha || baseSha === plan.baseSha), 'Stale release plan: base SHA mismatch');
  baseSha = plan.baseSha;
  if (headSha) invariant(SHA.test(headSha) && git(root, 'rev-parse', 'HEAD').trim() === headSha, 'Plan validation checkout differs from requested head');
  const config = await configuration(root);
  await syncReleaseMetadata({ root, check: true });
  invariant(Array.isArray(plan.components) && plan.components.length > 0 && plan.components.length <= 2, 'Release must explicitly select components');
  invariant(new Set(plan.components.map(c => c.component)).size === plan.components.length, 'Duplicate component');
  const originalPaths = git(root, 'ls-tree', '-r', '--name-only', baseSha, '.changeset').trim().split('\n').filter(p => /^\.changeset\/[^/]+\.md$/.test(p) && p !== '.changeset/README.md').sort();
  const originals = originalPaths.map(path => parseChangeset(gitFile(root, baseSha, path), path.slice('.changeset/'.length, -3)));
  invariant(JSON.stringify(plan.changesets) === JSON.stringify(originals), 'Consumed changeset contents/digests differ from the exact base');
  invariant((await pendingChangesets(root)).length === 0, 'Release PR must consume all pending changesets');
  invariant(plan.bootstrap ? plan.changesets.every(c => c.releases.length === 0) && plan.components.length === 1 : plan.changesets.some(c => c.releases.length), 'Invalid bootstrap or missing Changesets inputs');
  for (const component of plan.components) {
    invariant(['windows', 'npm'].includes(component.component) && component.channel === 'stable' && (VERSION.test(component.oldVersion) || plan.bootstrap && component.component === 'npm' && component.oldVersion === '0.0.1-beta') && VERSION.test(component.version), 'Invalid stable component');
    const declaration = config.components.find(c => c.component === component.component);
    const baselineBytes = component.component === 'windows' ? gitFile(root, baseSha, PUBLIC_BASELINE_PATH) : null;
    invariant(!plan.bootstrap || baselineBytes === null, 'Explicit public baseline cannot be combined with bootstrap');
    const publicBaseline = baselineBytes === null ? undefined : validatePublicBaseline(JSON.parse(baselineBytes), component.oldVersion, component.version);
    const expected = componentRecord(component.component, component.oldVersion, component.version, declaration.packages, publicBaseline);
    invariant(JSON.stringify(component) === JSON.stringify(expected), 'Component tags/notes/packages are not canonical');
    for (const pkg of declaration.packages) {
      const original = JSON.parse(gitFile(root, baseSha, `${pkg.path}/package.json`));
      const actual = config.packages.find(p => p.name === pkg.name);
      invariant(actual.version === component.version, `Version mismatch: ${pkg.name}`);
      invariant(original.version === (plan.bootstrap ? component.version : component.oldVersion), `Manual or stale source version: ${pkg.name}`);
    }
    if (plan.bootstrap) invariant(component.component === 'windows' ? component.oldVersion === '0.1.1' && component.version === '0.1.2' : ['0.0.0', '0.0.1-beta'].includes(component.oldVersion) && component.version === '0.1.0', 'Unsupported bootstrap transition');
  }
  for (const pkg of config.packages) if (!plan.components.some(c => c.component === (pkg.name === '@hellgato/windows-release' ? 'windows' : 'npm'))) invariant(pkg.version === JSON.parse(gitFile(root, baseSha, `${pkg.path}/package.json`)).version, `Unselected package changed: ${pkg.name}`);
  const expected = expectedFiles(plan.components);
  invariant(Array.isArray(plan.generatedFiles) && JSON.stringify(plan.generatedFiles.map(f => f.path)) === JSON.stringify(expected), 'Generated file allowlist differs from selected components');
  for (const file of plan.generatedFiles) invariant(safePath(file.path) && /^[a-f0-9]{64}$/.test(file.sha256) && sha256(await readFile(resolve(root, file.path))) === file.sha256, `Generated file digest mismatch: ${file.path}`);
  const allowed = new Set([PLAN, ...expected, ...originalPaths]);
  const changed = git(root, 'diff', '--name-only', baseSha, ...(headSha ? [headSha] : [])).trim().split('\n').filter(Boolean);
  for (const path of changed) invariant(allowed.has(path), `Unexpected source change in release PR: ${path}`);
  // A version PR cannot silently leave tracked edits outside the checked head.
  if (headSha) invariant(git(root, 'diff', '--name-only', headSha).trim() === '', 'Dirty release checkout');
  if (!structuralOnly) {
    const temporary = await mkdtemp(join(tmpdir(), 'hellgato-plan-'));
    let dependencyLinkCreated = false;
    try {
      git(root, 'worktree', 'add', '--quiet', '--detach', temporary, baseSha);
      invariant(existsSync(resolve(root, 'node_modules/@changesets/cli/package.json')), 'Install frozen release dependencies before full plan replay');
      await symlink(resolve(root, 'node_modules'), resolve(temporary, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
      dependencyLinkCreated = true;
      const replay = await generateReleasePlan({ root: temporary, baseSha, replay: true, ...(plan.bootstrap ? { bootstrap: plan.components[0].component, from: plan.components[0].oldVersion } : {}) });
      invariant(JSON.stringify(replay) === JSON.stringify(plan), 'Release tree differs from deterministic Changesets regeneration');
    } finally {
      await cleanupReplayWorktree(root, temporary, { dependencyLinkCreated });
    }
  }
  return plan;
}

async function main() {
  const args = argumentsFor(process.argv.slice(2));
  invariant(Object.keys(args).every(key => ['base', 'head', 'check-plan', 'check', 'structural-only', 'bootstrap', 'from', 'channel'].includes(key)), 'Unknown release-version option');
  if (args['check-plan'] || args.check) {
    const plan = await validateReleasePlan({ baseSha: args.base, headSha: args.head, structuralOnly: Boolean(args['structural-only']) });
    console.log(`Verified ${plan.bootstrap ? 'bootstrap' : 'Changesets'} plan for ${plan.components.map(c => c.component).join(', ')}`);
  } else {
    invariant(!args['structural-only'] && !args.head, 'Validation options require --check-plan');
    const plan = await generateReleasePlan({ baseSha: args.base, bootstrap: args.bootstrap, from: args.from, channel: args.channel });
    console.log(plan ? `Prepared ${plan.components.map(c => `${c.component} ${c.oldVersion} -> ${c.version}`).join(', ')}` : 'No pending release changesets; no release intent generated');
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch(error => { console.error(`Release version refused: ${error.message}`); process.exitCode = 1; });

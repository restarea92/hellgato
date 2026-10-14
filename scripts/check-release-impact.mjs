import { CHANNEL_HANDOFF_PATH, validateChannelHandoff } from './release-handoff.mjs';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, SHA, invariant, git, gitFile, configuration, parseChangeset, readJson, globMatch, safePath, argumentsFor } from './release-lib.mjs';
import { validateReleasePlan } from './release-version.mjs';

export async function checkReleaseImpact({ root = ROOT, base, head = 'HEAD', workingTree = false, structuralOnly = false, targetBranch } = {}) {
  invariant(typeof base === 'string' && base.length > 0 && !base.startsWith('-') && !/^0+$/.test(base), 'An explicit valid diff --base is required');
  invariant(typeof head === 'string' && head.length > 0 && !head.startsWith('-'), 'Invalid head reference');
  const baseSha = git(root, 'merge-base', base, head).trim();
  invariant(SHA.test(baseSha), 'Cannot establish the full PR merge-base');
  const paths = [...new Set([
    ...git(root, 'diff', '--name-only', '--no-renames', baseSha, ...(workingTree ? [] : [head])).trim().split('\n').filter(Boolean),
    ...(workingTree ? git(root, 'ls-files', '--others', '--exclude-standard').trim().split('\n').filter(Boolean) : []),
  ])].sort();
  if (paths.includes(CHANNEL_HANDOFF_PATH)) {
    const handoff = await validateChannelHandoff({ root, baseSha, ...(workingTree ? {} : { headSha: git(root, 'rev-parse', head).trim() }), workingTree, targetBranch });
    return { baseSha, releasePlan: false, sourceOnly: true, handoff, paths, required: [], declared: [] };
  }
  if (paths.includes('.release/plan.json')) {
    invariant(existsSync(resolve(root, '.release/plan.json')), 'Release intent cannot be manually deleted');
    await validateReleasePlan({ root, baseSha, ...(workingTree ? {} : { headSha: git(root, 'rev-parse', head).trim() }), structuralOnly });
    return { baseSha, releasePlan: true, paths, required: [], declared: [] };
  }
  const config = await configuration(root, { channel: existsSync(resolve(root, '.changeset/pre.json')) ? 'beta' : 'stable' });
  const exceptions = new Map();
  let bootstrap = false;
  for (const path of paths.filter(p => /^\.release\/no-release\/[^/]+\.json$/.test(p))) {
    const entry = await readJson(resolve(root, path));
    invariant(entry.schemaVersion === 1 && entry.baseSha === baseSha && typeof entry.reason === 'string' && entry.reason.trim().length >= 30, `Invalid, stale, or unexplained no-release exception: ${path}`);
    invariant(Array.isArray(entry.paths) && entry.paths.length > 0 && new Set(entry.paths).size === entry.paths.length && entry.paths.every(safePath), `Exception requires exact paths: ${path}`);
    for (const item of entry.paths) { invariant(paths.includes(item), `No-release exception lists unchanged path: ${item}`); exceptions.set(item, entry.reason); }
    if (entry.bootstrap === true) {
      invariant(path === '.release/no-release/adopt-changesets.json' && baseSha === 'f6f664127589a6a4a8a92079a1bcfa11b73b02ca', 'Bootstrap metadata exception is restricted to the adoption base');
      bootstrap = true;
    }
  }
  for (const pkg of config.packages) {
    const original = gitFile(root, baseSha, `${pkg.path}/package.json`);
    if (original === null) invariant(bootstrap && pkg.name === '@hellgato/windows-release' && pkg.version === '0.1.2', `New workspace requires explicit release-policy adoption: ${pkg.name}`);
    else invariant(JSON.parse(original).version === pkg.version, `Manual package version change is forbidden: ${pkg.name}; use release:version`);
  }
  const changesets = [];
  for (const path of paths.filter(p => /^\.changeset\/[^/]+\.md$/.test(p) && p !== '.changeset/README.md')) {
    invariant(existsSync(resolve(root, path)), `Deleted changeset without a verified release plan: ${path}`);
    changesets.push(parseChangeset(await readFile(resolve(root, path), 'utf8'), path.slice('.changeset/'.length, -3)));
  }
  const declared = new Set();
  for (const changeset of changesets) for (const release of changeset.releases) {
    const component = config.components.find(c => c.packages.some(p => p.name === release.name));
    invariant(component, `Unclassified changeset package: ${release.name}`);
    declared.add(component.component);
  }
  const required = new Set();
  const missing = [];
  const unclassified = [];
  const review = [];
  for (const path of paths) {
    const rule = config.rules.find(rule => rule.paths.some(pattern => globMatch(path, pattern)));
    if (!rule) { unclassified.push(path); continue; }
    if (/^\.release\/no-release\/[^/]+\.json$/.test(path) || /^\.changeset\/[^/]+\.md$/.test(path) && path !== '.changeset/README.md') continue;
    if (exceptions.has(path)) continue;
    if (rule.reviewRequired) review.push(path);
    for (const component of rule.components) {
      required.add(component);
      if (!declared.has(component)) missing.push(`${path} -> ${component}`);
    }
  }
  invariant(unclassified.length === 0, `Unclassified paths (add an explicit impact mapping): ${unclassified.join(', ')}`);
  invariant(review.length === 0, `Release-policy changes require a scoped reviewable no-release reason: ${review.join(', ')}`);
  invariant(missing.length === 0, `Missing changeset targets: ${missing.join('; ')}`);
  invariant(paths.length === 0 || changesets.length > 0 || exceptions.size > 0, 'Document/test-only changes require an explanatory empty changeset or scoped no-release reason');
  return { baseSha, releasePlan: false, paths, required: [...required].sort(), declared: [...declared].sort() };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = argumentsFor(process.argv.slice(2));
  invariant(Object.keys(args).every(key => ['base', 'head', 'working-tree', 'structural-only', 'target-branch'].includes(key)), 'Unknown release-impact option');
  checkReleaseImpact({ base: args.base, head: args.head, workingTree: Boolean(args['working-tree']), structuralOnly: Boolean(args['structural-only']), targetBranch: args['target-branch'] }).then(result => console.log(JSON.stringify(result, null, 2))).catch(error => { console.error(`Release impact refused: ${error.message}`); process.exitCode = 1; });
}

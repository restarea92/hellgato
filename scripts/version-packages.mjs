// The only active versioning entry point. Changesets owns version calculation;
// this wrapper only adds the bundled-SDK app bump and synchronizes app metadata.
// No registry queries, tags, GitHub writes, or package publication happen here.
import { appendFile, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, configuration, invariant, json, put } from './release-lib.mjs';
import { runTool } from './npm-release.mjs';
import { syncReleaseMetadata } from './sync-release-metadata.mjs';

const APP = '@hellgato/windows-release';
const SDK = ['@hellgato/core', 'hellgato'];
const BUNDLED = '.changeset/hellgato-bundled-sdk.md';
const cli = root => resolve(root, 'node_modules/@changesets/cli/bin.js');

export async function versionStatus({ root = ROOT } = {}) {
  await configuration(root);
  invariant(!existsSync(resolve(root, '.release/plan.json')) && !existsSync(resolve(root, '.release/candidate-plan.json')),
    'Legacy release plans are not inputs to version management; start from main with pending changesets');
  // CLI status also checks Git diffs; after versioning that would reject the
  // deliberately consumed inputs. No pending files is an explicit no-op.
  const inputs = await readdir(resolve(root, '.changeset'));
  if (!inputs.some(name => name.endsWith('.md') && name !== 'README.md')) return { changesets: [], releases: [] };
  const temporary = await mkdtemp(join(tmpdir(), 'hellgato-version-status-'));
  try {
    const output = resolve(temporary, 'status.json');
    execFileSync(process.execPath, [cli(root), 'status', '--output', output], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
    const plan = JSON.parse(await readFile(output, 'utf8'));
    invariant(Array.isArray(plan.releases) && Array.isArray(plan.changesets), 'Unexpected Changesets status');
    const names = [APP, ...SDK];
    for (const release of plan.releases) invariant(names.includes(release.name), `Unexpected versioned package: ${release.name}`);
    return plan;
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

export function currentChangelog(changelog, version) {
  const heading = `## ${version}`;
  const lines = changelog.replaceAll('\r\n', '\n').split('\n');
  const start = lines.indexOf(heading);
  invariant(start >= 0, `Missing generated changelog for ${version}`);
  const end = lines.findIndex((line, index) => index > start && /^## /.test(line));
  const body = lines.slice(start + 1, end < 0 ? lines.length : end).join('\n').trim();
  invariant(body.length > 0, `Empty generated changelog for ${version}`);
  return body;
}

export async function versionPackages({ root = ROOT } = {}) {
  const before = await configuration(root);
  await syncReleaseMetadata({ root, check: true });
  const plan = await versionStatus({ root });
  if (!plan.releases.length) return { changed: false, packages: [] };
  invariant(runTool('pnpm', ['--version'], { cwd: root }).trim() === '11.10.0', 'Use the pinned pnpm 11.10.0');
  const sdk = plan.releases.filter(release => SDK.includes(release.name));
  invariant(sdk.length === 0 || sdk.length === 2 && sdk[0].newVersion === sdk[1].newVersion,
    'Changesets must version the complete fixed SDK pair together');
  // Even if another app change already requests a larger bump, Changesets takes
  // the maximum, not the sum. This never forces app and SDK numbers to match.
  let bundledAdded = false;
  try {
    if (sdk.length) {
      invariant(!existsSync(resolve(root, BUNDLED)), `${BUNDLED} is reserved for version preparation`);
      const sdkOnlySummaries = plan.changesets
        .filter(change => change.releases.some(release => SDK.includes(release.name)) && !change.releases.some(release => release.name === APP))
        .map(change => change.summary);
      const context = sdkOnlySummaries.length ? `\n\nIncluded SDK changes:\n\n${sdkOnlySummaries.join('\n\n')}` : '';
      await writeFile(resolve(root, BUNDLED), `---\n"${APP}": patch\n---\n\nBundle @hellgato/core and hellgato ${sdk[0].newVersion} in the Windows application.${context}\n`, { flag: 'wx' });
      bundledAdded = true;
    }
    const finalPlan = await versionStatus({ root });
    const appRelease = finalPlan.releases.find(release => release.name === APP);
    invariant(appRelease, 'Every prepared Hellgato release must include the Windows application');
    const notesPath = `docs/releases/${appRelease.newVersion}.md`;
    invariant(!existsSync(resolve(root, notesPath)), `Refusing to replace historical release notes: ${notesPath}`);
    execFileSync(process.execPath, [cli(root), 'version'], { cwd: root, stdio: 'inherit' });
    await syncReleaseMetadata({ root });
    // Workspace dependencies are local; version preparation must not resolve new
    // registry versions, run lifecycle scripts, or silently replace the lockfile.
    runTool('pnpm', ['install', '--lockfile-only', '--ignore-scripts', '--offline', '--frozen-lockfile=false'], { cwd: root, stdio: 'inherit' });
    const after = await configuration(root);
    for (const pkg of after.packages) {
      const expected = finalPlan.releases.find(release => release.name === pkg.name)?.newVersion
        ?? before.packages.find(previous => previous.name === pkg.name).version;
      invariant(pkg.version === expected, `Unexpected generated version: ${pkg.name}`);
    }
    const changelog = await readFile(resolve(root, 'packages/windows-release/CHANGELOG.md'), 'utf8');
    await put(root, notesPath, `# Hellgato Windows ${appRelease.newVersion}\n\n${currentChangelog(changelog, appRelease.newVersion)}\n`);
    await syncReleaseMetadata({ root, check: true });
    invariant((await versionStatus({ root })).releases.length === 0, 'Changesets left unconsumed version inputs');
    return { changed: true, packages: finalPlan.releases.map(({ name, oldVersion, newVersion }) => ({ name, oldVersion, newVersion })) };
  } finally {
    // Changesets normally consumes this file. A failed command may leave it;
    // remove only our generated input so the next clean run cannot double-bump.
    if (bundledAdded) await rm(resolve(root, BUNDLED), { force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  try {
    invariant(args.length === 0 || args.length === 1 && args[0] === '--status', 'Use version-packages.mjs [--status]');
    if (args.length) {
      const plan = await versionStatus();
      if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, `has_changesets=${plan.releases.length > 0}\n`);
      console.log(json(plan));
    } else {
      const result = await versionPackages();
      console.log(result.changed ? `Prepared versions only: ${result.packages.map(pkg => `${pkg.name} ${pkg.oldVersion} -> ${pkg.newVersion}`).join(', ')}` : 'No pending version changes; nothing changed');
    }
  } catch (error) {
    console.error(`Version preparation failed: ${error.message}`);
    process.exitCode = 1;
  }
}

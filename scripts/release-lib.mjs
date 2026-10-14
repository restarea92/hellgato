import { readFile, readdir, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { releaseChannel, validatePrereleaseState } from './release-policy.mjs';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const SHA = /^[a-f0-9]{40}$/;
export const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export const invariant = (condition, message) => { if (!condition) throw new Error(message); };
export const sha256 = data => createHash('sha256').update(data).digest('hex');
export const json = value => JSON.stringify(value, null, 2) + '\n';
export const git = (root, ...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 64 * 1024 * 1024 });
export const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
export async function put(root, path, content) { await mkdir(dirname(resolve(root, path)), { recursive: true }); await writeFile(resolve(root, path), content); }
export function safePath(path) { return typeof path === 'string' && /^[A-Za-z0-9@_.\/-]+$/.test(path) && !path.startsWith('/') && !path.split('/').some(p => !p || p === '.' || p === '..'); }
export function gitFile(root, ref, path) { invariant(SHA.test(ref) && safePath(path), 'Unsafe Git object path'); try { return git(root, 'show', `${ref}:${path}`); } catch (error) { if (error.stderr?.toString().includes('does not exist') || error.stderr?.toString().includes('exists on disk')) return null; throw error; } }
export function globMatch(path, pattern) { const regex = pattern.split('**').map(part => part.split('*').map(v => v.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('[^/]*')).join('.*'); return new RegExp(`^${regex}$`).test(path); }
export function pnpm(root, args, { capture = false } = {}) {
  const command = process.env.HELLGATO_PNPM || (process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm');
  return execFileSync(command, args, { cwd: root, encoding: 'utf8', stdio: capture ? 'pipe' : ['ignore', 'inherit', 'inherit'], shell: process.platform === 'win32', env: { ...process.env, CI: 'true' }, maxBuffer: 64 * 1024 * 1024 });
}
// Keep structural validation dependency-free. Deliberately accept only this
// repository's small YAML subset; aliases, inline lists, duplicate keys, escaped
// strings, and additional settings must be reviewed with a parser policy change.
export function parseWorkspacePackages(source) {
  const members = [];
  let packagesSeen = false;
  let buildsSeen = false;
  let inPackages = false;
  for (const [index, line] of source.replace(/^\uFEFF/, '').split(/\r?\n/).entries()) {
    if (/^ *(?:#.*)?$/.test(line)) continue;
    if (/^packages: *(?:#.*)?$/.test(line)) {
      invariant(!packagesSeen, 'Unsupported workspace YAML: duplicate packages key');
      packagesSeen = true;
      inPackages = true;
      continue;
    }
    if (/^allowBuilds: *\{\} *(?:#.*)?$/.test(line)) {
      invariant(!buildsSeen, 'Unsupported workspace YAML: duplicate allowBuilds key');
      buildsSeen = true;
      inPackages = false;
      continue;
    }
    // Match comments only after an entire scalar. A # inside quotes is never
    // mistaken for a comment, and unsupported YAML never disappears silently.
    const member = /^  - +(?:([A-Za-z0-9_.\/-]+)|"([A-Za-z0-9_.\/-]+)"|'([A-Za-z0-9_.\/-]+)')(?: +#.*)? *$/.exec(line);
    invariant(inPackages && member, `Unsupported workspace YAML at line ${index + 1}; use the explicit packages block list and allowBuilds: {}`);
    members.push(member[1] ?? member[2] ?? member[3]);
  }
  invariant(packagesSeen && buildsSeen, 'Unsupported workspace YAML: packages and allowBuilds: {} are required');
  invariant(new Set(members).size === members.length, 'Unsupported workspace YAML: duplicate member');
  return members.sort();
}
export async function configuration(root, { channel = 'stable' } = {}) {
  invariant(['stable', 'beta'].includes(channel), 'Unknown configuration channel');
  if (channel === 'stable') invariant(!existsSync(resolve(root, '.changeset/pre.json')), 'Prerelease mode is not supported by the stable release pipeline');
  const config = await readJson(resolve(root, '.release/components.json'));
  invariant(config.schemaVersion === 1 && config.repository === 'restarea92/hellgato', 'Unsupported component configuration');
  invariant(config.components.length === 2 && config.components[0].component === 'windows' && config.components[1].component === 'npm', 'Expected explicit Windows/npm components');
  const expected = new Map([['packages/core', '@hellgato/core'], ['packages/hellgato', 'hellgato'], ['packages/windows-release', '@hellgato/windows-release']]);
  const declared = config.components.flatMap(c => c.packages);
  invariant(declared.length === expected.size && declared.every(p => expected.get(p.path) === p.name), 'Unexpected component workspace; update the release policy explicitly');
  const entries = await readdir(resolve(root, 'packages'), { withFileTypes: true });
  for (const entry of entries) if (entry.isDirectory() && existsSync(resolve(root, 'packages', entry.name, 'package.json'))) invariant(expected.has(`packages/${entry.name}`), `Unclassified workspace: packages/${entry.name}`);
  const workspace = await readFile(resolve(root, 'pnpm-workspace.yaml'), 'utf8');
  const members = parseWorkspacePackages(workspace);
  invariant(JSON.stringify(members) === JSON.stringify([...expected.keys()].sort()), 'Workspace membership differs from the release allowlist');
  const rootPackage = await readJson(resolve(root, 'package.json'));
  invariant(rootPackage.name === 'hellgato-workspace' && rootPackage.private === true && rootPackage.workspaces === undefined && rootPackage.packageManager === 'pnpm@11.10.0' && rootPackage.devDependencies?.['@changesets/cli'] === '3.0.3', 'Release toolchain pins or private root changed');
  const packages = [];
  for (const p of declared) {
    const manifest = await readJson(resolve(root, p.path, 'package.json'));
    invariant(manifest.name === p.name && (channel === 'stable' ? VERSION.test(manifest.version) : ['stable', 'beta'].includes(releaseChannel(manifest.version))), `Invalid stable package metadata: ${p.path}`);
    invariant(p.name === '@hellgato/windows-release' ? manifest.private === true : manifest.private !== true, 'Windows metadata must remain private and npm workspaces publishable');
    packages.push({ ...p, version: manifest.version, manifest });
  }
  const core = packages.find(p => p.name === '@hellgato/core');
  const facade = packages.find(p => p.name === 'hellgato');
  invariant(core.version === facade.version && facade.manifest.dependencies?.['@hellgato/core'] === 'workspace:*', 'npm packages must keep equal source versions and workspace:* dependency');
  const changesets = await readJson(resolve(root, '.changeset/config.json'));
  invariant(JSON.stringify(changesets.fixed) === JSON.stringify([['@hellgato/core', 'hellgato']]) && changesets.privatePackages?.version === true && changesets.privatePackages?.tag === false && changesets.ignore?.length === 0 && changesets.changelog === '@changesets/cli/changelog' && changesets.commit === false && changesets.baseBranch === 'main', 'Changesets fixed/private policy changed');
  if (channel === 'beta' && existsSync(resolve(root, '.changeset/pre.json'))) validatePrereleaseState(await readJson(resolve(root, '.changeset/pre.json')));
  return { ...config, packages };
}
export function parseChangeset(content, id) {
  invariant(/^[a-z0-9][a-z0-9-]*$/.test(id), `Invalid changeset ID: ${id}`);
  const match = /^---\r?\n([\s\S]*?)^---(?:\r?\n|$)([\s\S]*)$/m.exec(content);
  invariant(match && match.index === 0, `Invalid changeset frontmatter: ${id}`);
  const releases = [];
  for (const line of match[1].split(/\r?\n/).filter(v => v.trim())) {
    const entry = /^\s*["']?(@?[a-zA-Z0-9_.\/-]+)["']?\s*:\s*(patch|minor|major)\s*$/.exec(line);
    invariant(entry && !releases.some(r => r.name === entry[1]), `Unsupported or duplicate changeset entry: ${id}: ${line}`);
    releases.push({ name: entry[1], type: entry[2] });
  }
  const summary = match[2].trim();
  invariant(summary.length >= 10, `Changeset ${id} requires a meaningful user-facing summary`);
  return { id, sha256: sha256(content), content, releases: releases.sort((a, b) => a.name.localeCompare(b.name)), summary };
}
export async function pendingChangesets(root) {
  const entries = await readdir(resolve(root, '.changeset'));
  return Promise.all(entries.filter(n => n.endsWith('.md') && n !== 'README.md').sort().map(async n => parseChangeset(await readFile(resolve(root, '.changeset', n), 'utf8'), n.slice(0, -3))));
}
export function argumentsFor(argv) { const out = {}; for (let i = 0; i < argv.length; i++) { invariant(argv[i].startsWith('--'), `Unexpected argument: ${argv[i]}`); const key = argv[i].slice(2); invariant(!(key in out), `Duplicate argument: ${key}`); out[key] = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true; } return out; }

// Static artifact verification and publication. Never builds, packs, or imports package code.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, readdirSync } from 'node:fs';
import { basename, dirname, resolve, win32 } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gunzipSync } from 'node:zlib';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const REPOSITORY = 'restarea92/hellgato';
export const REGISTRY = 'https://registry.npmjs.org/';
export const NPM_VERSION = '11.9.0'; // OIDC requires >=11.5.1. Do not use @latest in release jobs.
export const UPSTREAM = { repository: 'https://github.com/lukasMega/DeckBridge', revision: '7c00693d01ac6c47c071907835ed0724d28d8183' };
export const PACKAGES = [{ name: '@hellgato/core', directory: 'core' }, { name: 'hellgato', directory: 'hellgato' }];
export const MANIFEST = 'npm-artifact-manifest.json';
// Temporary source-level stop. No environment variable or CLI flag can enable it.
export function assertNpmPublicationEnabled() {
  throw new Error('npm publication is hard-disabled during the architecture migration; a separately approved code change is required to restore it');
}
// Reviewed public entry points and exact payload inventory. New paths require a policy change.
export const PACKAGE_LAYOUT = {
  "@hellgato/core": {
    "types": "./dist/bridge.d.mts",
    "exports": {
      ".": {
        "types": "./dist/bridge.d.mts",
        "import": "./dist/bridge.mjs"
      },
      "./contracts": {
        "types": "./dist/contracts/index.d.mts",
        "import": "./dist/contracts/index.mjs"
      },
      "./schemas/contracts.schema.json": "./schemas/contracts.schema.json"
    },
    "files": [
      "package.json",
      "README.md",
      "LICENSE",
      "THIRD-PARTY-NOTICES.txt",
      "dist/bridge.mjs",
      "dist/bridge.d.mts",
      "dist/bridge/device-bridge.mjs",
      "dist/contracts/index.mjs",
      "dist/contracts/index.d.mts",
      "dist/hosts/streamdeck/target.mjs",
      "dist/hosts/streamdeck/display-server.mjs",
      "dist/hosts/streamdeck/firmware-policy.mjs",
      "dist/legacy/bridge.mjs",
      "dist/legacy/profiles.mjs",
      "dist/profiles.mjs",
      "dist/display-server.mjs",
      "dist/firmware-policy.mjs",
      "schemas/contracts.schema.json",
      "dist/vendor/LICENSE",
      "dist/vendor/package.json",
      "dist/vendor/provenance.json"
    ]
  },
  "hellgato": {
    "types": "./index.d.ts",
    "exports": {
      ".": {
        "types": "./index.d.ts",
        "import": "./index.mjs"
      },
      "./adapters/mirabox-n4-pro": {
        "types": "./src/adapters/mirabox-n4-pro/index.d.ts",
        "import": "./src/adapters/mirabox-n4-pro/index.mjs"
      },
      "./testing": {
        "types": "./src/testing/index.d.ts",
        "import": "./src/testing/index.mjs"
      }
    },
    "files": [
      "package.json",
      "README.md",
      "LICENSE",
      "index.mjs",
      "index.d.ts",
      "src/session/index.mjs",
      "src/testing/index.mjs",
      "src/testing/index.d.ts",
      "src/adapters/mirabox-n4-pro/index.mjs",
      "src/adapters/mirabox-n4-pro/index.d.ts",
      "src/sidecar/index.mjs"
    ]
  }
};
export const VENDOR_MODULES = [
  "cora/child-payload.ts",
  "cora/child-reconnector.ts",
  "cora/child-server.ts",
  "cora/describe.ts",
  "cora/frame.ts",
  "cora/image-assembler.ts",
  "cora/plus-reports.ts",
  "cora/primary-server.ts",
  "cora/protocol.ts",
  "cora/responses.ts",
  "cora/server-base.ts",
  "cora/touch-strip-assembler.ts",
  "infra/mdns-advertiser.ts",
  "platform/tcp.ts",
  "shared/comm-format.ts",
  "shared/extra-key-config.ts",
  "shared/logger.ts",
  "shared/types.ts"
];
const SHA = /^[a-f0-9]{40}$/;
const HEX256 = /^[a-f0-9]{64}$/;
const STABLE = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export const invariant = (condition, message) => { if (!condition) throw new Error(message); };
export const digest = (bytes, algorithm = 'sha256', encoding = 'hex') => createHash(algorithm).update(bytes).digest(encoding);
export const json = path => JSON.parse(readFileSync(path, 'utf8'));
export const archiveName = (name, version) => `${name.replace(/^@/, '').replace('/', '-')}-${version}.tgz`;

// Follow Windows PATH order once. A .cmd shim from pnpm/action-setup lives in
// node_modules/.bin; a global npm/pnpm shim lives above node_modules. Never fall
// through from a broken selected install to an unrelated global executable.
export function resolveWindowsTool(tool, { locate = name => execFileSync('where.exe', [name], { encoding: 'utf8' }), exists = existsSync, nodePath = process.execPath } = {}) {
  invariant(tool === 'npm' || tool === 'pnpm', `Unsupported package tool: ${tool}`);
  const paths = locate(tool).trim().split(/\r?\n/).map(path => path.trim()).filter(Boolean);
  const selected = paths.find(path => /\.(?:cmd|exe|bat|com)$/i.test(path));
  invariant(selected, `Cannot locate the selected ${tool} executable`);
  if (/\.exe$/i.test(selected)) return { command: selected, prefix: [] };
  invariant(/\.cmd$/i.test(selected), `Unsupported selected ${tool} executable: ${selected}`);
  const base = win32.dirname(selected);
  const relative = tool === 'npm' ? ['npm/bin/npm-cli.js'] : ['pnpm/bin/pnpm.cjs', 'pnpm/bin/pnpm.mjs', 'corepack/dist/pnpm.js'];
  const packageRoot = win32.basename(base).toLowerCase() === '.bin' ? win32.resolve(base, '..') : win32.resolve(base, 'node_modules');
  const cli = relative.map(path => win32.resolve(packageRoot, path)).find(exists);
  invariant(cli, `Cannot locate the selected ${tool} JavaScript CLI beside ${selected}`);
  return { command: nodePath, prefix: [cli] };
}
// Do not pass .cmd through execFile on Windows or opt into a shell.
export function runTool(tool, args, options = {}) {
  if (process.platform !== 'win32') return execFileSync(tool, args, { encoding: 'utf8', ...options });
  const { command, prefix } = resolveWindowsTool(tool);
  return execFileSync(command, [...prefix, ...args], { encoding: 'utf8', ...options });
}
export function assertNpmVersion(run = runTool) {
  const actual = run('npm', ['--version']).trim();
  invariant(actual === NPM_VERSION, `Expected npm ${NPM_VERSION}, found ${actual}`);
  return actual;
}
export function assertSources(root = ROOT) {
  const [core, facade] = PACKAGES.map(pkg => json(resolve(root, 'packages', pkg.directory, 'package.json')));
  invariant(core.name === '@hellgato/core' && facade.name === 'hellgato', 'Unexpected npm package names');
  invariant(STABLE.test(core.version) && core.version === facade.version, 'Both npm packages must use the same stable version');
  invariant(facade.dependencies?.['@hellgato/core'] === 'workspace:*', 'Source facade must depend on @hellgato/core using workspace:*');
  return [core, facade];
}
export function selectedNpm(plan) {
  invariant(plan?.schemaVersion === 1 && plan.repository === REPOSITORY, 'Invalid release plan repository/schema');
  invariant(Array.isArray(plan.components), 'Release plan must explicitly select components');
  const selected = plan.components.filter(component => component.component === 'npm');
  invariant(selected.length === 1, 'Release plan does not select npm exactly once; Windows-only plans cannot publish npm');
  const component = selected[0];
  invariant(STABLE.test(component.version) && component.channel === 'stable' && component.tag === `hellgato@${component.version}`, 'Invalid npm release version/channel/tag');
  invariant(Array.isArray(component.packages) && component.packages.length === 2, 'The npm fixed group must select both packages');
  for (const pkg of PACKAGES) {
    const entries = component.packages.filter(entry => entry.name === pkg.name);
    invariant(entries.length === 1 && entries[0].version === component.version && entries[0].tag === `${pkg.name}@${component.version}`, `Invalid selected ${pkg.name} version/tag`);
  }
  return component;
}

// Inspect bytes in memory, never extract an untrusted archive. Reject links, duplicate
// entries, traversal, malformed headers and non-regular payloads before npm sees it.
export function readTarball(bytes) {
  invariant(bytes.length > 0 && bytes.length <= 32 * 1024 * 1024, 'Invalid tarball size');
  const tar = gunzipSync(bytes, { maxOutputLength: 64 * 1024 * 1024 });
  const files = new Map();
  let offset = 0;
  let ended = false;
  const field = (header, start, length) => header.subarray(start, start + length).toString('utf8').replace(/\0.*$/s, '');
  const octal = (value, label) => { invariant(/^\s*[0-7]+\s*$/.test(value), `Invalid tar ${label}`); return parseInt(value.trim(), 8); };
  while (offset + 512 <= tar.length) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every(byte => byte === 0)) {
      invariant(tar.length >= offset + 1024 && tar.subarray(offset).every(byte => byte === 0), 'Invalid tar trailer');
      ended = true;
      break;
    }
    const expected = octal(field(header, 148, 8), 'checksum');
    const actual = header.reduce((sum, byte, index) => sum + (index >= 148 && index < 156 ? 32 : byte), 0);
    invariant(actual === expected, 'Invalid tar header checksum');
    const prefix = field(header, 345, 155);
    const path = `${prefix ? `${prefix}/` : ''}${field(header, 0, 100)}`;
    const type = field(header, 156, 1);
    invariant(type === '' || type === '0' || type === '5', `Unsupported tar entry type ${type}: ${path}`);
    invariant(path.startsWith('package/') && !path.includes('\\') && !path.includes(':') && !/[\x00-\x1f\x7f]/.test(path), `Unsafe tar path: ${path}`);
    const name = path.slice(8).replace(/\/$/, '');
    invariant(name && name.split('/').every(part => part && part !== '.' && part !== '..'), `Unsafe tar path: ${path}`);
    const size = octal(field(header, 124, 12), 'size');
    invariant(Number.isSafeInteger(size) && size >= 0 && offset + 512 + size <= tar.length, 'Truncated tar payload');
    invariant(type !== '5' || size === 0, 'Directory has a tar payload');
    if (type !== '5') {
      invariant(!files.has(name), `Duplicate tar entry: ${name}`);
      files.set(name, tar.subarray(offset + 512, offset + 512 + size));
    }
    offset += 512 + Math.ceil(size / 512) * 512;
  }
  invariant(ended && files.size > 0 && files.size < 1000, 'Incomplete or excessive tar contents');
  return files;
}
function packageMetadata(files, expectedName, expectedVersion) {
  const layout = PACKAGE_LAYOUT[expectedName];
  invariant(layout, `Unexpected package: ${expectedName}`);
  const required = [...layout.files, ...(expectedName === '@hellgato/core' ? VENDOR_MODULES.map(source => `dist/vendor/${source.replace(/\.ts$/, '.js')}`) : [])];
  for (const path of required) invariant(files.has(path) && files.get(path).length > 0, `Missing required ${expectedName} file: ${path}`);
  for (const path of files.keys()) {
    invariant(required.includes(path) || path === 'CHANGELOG.md', `Unexpected published file: ${path}`);
  }
  const manifest = JSON.parse(files.get('package.json').toString('utf8'));
  invariant(manifest.name === expectedName && manifest.version === expectedVersion, `Tarball name/version mismatch: ${expectedName}`);
  invariant(manifest.private !== true && manifest.type === 'module' && manifest.license === 'MIT', `Invalid publish metadata: ${expectedName}`);
  invariant(manifest.repository?.url === `git+https://github.com/${REPOSITORY}.git` && manifest.repository?.directory === `packages/${expectedName === '@hellgato/core' ? 'core' : 'hellgato'}`, 'Package repository must match trusted publisher');
  invariant(manifest.engines?.node === '>=24 <25', 'Package requires Node 24');
  invariant(JSON.stringify(manifest.exports) === JSON.stringify(layout.exports), `Invalid exports: ${expectedName}`);
  invariant(manifest.types === layout.types, `Invalid type declaration entry: ${expectedName}`);
  invariant(!manifest.scripts || Object.keys(manifest.scripts).length === 0, 'Published packages must not contain lifecycle scripts');
  invariant(!manifest.bin && !manifest.bundleDependencies && !manifest.bundledDependencies, 'Unexpected executable or bundled dependencies');
  invariant(manifest.publishConfig?.access === 'public' && manifest.publishConfig?.registry === REGISTRY && manifest.publishConfig?.tag === 'latest' && manifest.publishConfig?.provenance !== false, 'Unsafe publishConfig');
  for (const section of ['dependencies', 'optionalDependencies', 'devDependencies', 'peerDependencies']) {
    for (const [name, version] of Object.entries(manifest[section] ?? {})) {
      invariant(typeof version === 'string' && !/(?:workspace:|link:|file:|^\.?\.?\/|^[A-Za-z]:[\\/]|^\/)/i.test(version), `Local dependency is forbidden: ${name}=${version}`);
      invariant(section === 'dependencies' && expectedName === 'hellgato' && name === '@hellgato/core' && version === expectedVersion, `Unexpected dependency: ${name}=${version}`);
    }
  }
  if (expectedName === 'hellgato') invariant(manifest.dependencies?.['@hellgato/core'] === expectedVersion, 'Packed facade must require the exact core version');
  invariant(files.get('LICENSE').toString('utf8').includes('MIT License'), 'Missing MIT license text');
  if (expectedName === '@hellgato/core') {
    const provenance = JSON.parse(files.get('dist/vendor/provenance.json').toString('utf8'));
    invariant(provenance.repository === UPSTREAM.repository && provenance.revision === UPSTREAM.revision, 'Unexpected upstream provenance');
    invariant(JSON.stringify(provenance.modules) === JSON.stringify(VENDOR_MODULES), 'Unexpected upstream module inventory');
    for (const source of provenance.modules) {
      invariant(typeof source === 'string' && /^(?:[\w-]+\/)*[\w-]+\.ts$/.test(source), 'Invalid upstream module path');
      invariant(files.has(`dist/vendor/${source.replace(/\.ts$/, '.js')}`), `Missing upstream module: ${source}`);
    }
    invariant(files.get('dist/vendor/LICENSE').toString('utf8').includes('MIT License'), 'Missing upstream MIT license');
    invariant(files.get('THIRD-PARTY-NOTICES.txt').toString('utf8').includes(UPSTREAM.revision), 'Missing upstream attribution/revision');
    invariant(JSON.parse(files.get('dist/vendor/package.json').toString('utf8')).type === 'module', 'Vendor ESM metadata missing');
  }
  return manifest;
}
export function inspectArchive(path, name, version) {
  invariant(STABLE.test(version), 'Expected a stable package version');
  invariant(lstatSync(path).isFile() && !lstatSync(path).isSymbolicLink(), 'Tarball must be a regular file');
  const bytes = readFileSync(path);
  const files = readTarball(bytes);
  packageMetadata(files, name, version);
  return { name, version, filename: basename(path), size: bytes.length, sha256: digest(bytes), integrity: `sha512-${digest(bytes, 'sha512', 'base64')}`, files: [...files].map(([path, bytes]) => ({ path, size: bytes.length, sha256: digest(bytes) })).sort((a, b) => a.path.localeCompare(b.path, 'en')) };
}
export function verifyArtifacts({ artifactDir = resolve(ROOT, 'dist/npm'), planPath, releaseSha, repository = REPOSITORY, runId, runAttempt, approvalPath, requireConsumerSmoke = false } = {}) {
  const manifestPath = resolve(artifactDir, MANIFEST);
  invariant(lstatSync(manifestPath).isFile() && !lstatSync(manifestPath).isSymbolicLink(), 'Artifact manifest must be a regular file');
  const manifest = json(manifestPath);
  invariant(manifest.schemaVersion === 1 && manifest.component === 'npm' && manifest.repository === repository && repository === REPOSITORY, 'Invalid artifact schema/component/repository');
  invariant(SHA.test(manifest.releaseSha) && (!releaseSha || manifest.releaseSha === releaseSha), 'Artifact release SHA mismatch');
  invariant(STABLE.test(manifest.version) && manifest.channel === 'stable', 'Invalid artifact version/channel');
  invariant(manifest.source && (runId === undefined || String(manifest.source.runId) === String(runId)) && (runAttempt === undefined || String(manifest.source.runAttempt) === String(runAttempt)), 'Artifact source run/attempt mismatch');
  invariant(manifest.tools?.npm === NPM_VERSION && /^24\./.test(manifest.tools?.node ?? '') && manifest.tools?.pnpm === '11.10.0', 'Unapproved build tool versions');
  invariant(JSON.stringify(manifest.upstream) === JSON.stringify(UPSTREAM), 'Artifact upstream mismatch');
  if (planPath) {
    const bytes = readFileSync(planPath);
    const component = selectedNpm(JSON.parse(bytes));
    invariant(manifest.planSha256 === digest(bytes) && component.version === manifest.version, 'Artifact plan digest/version mismatch');
  } else invariant(manifest.planSha256 === null || HEX256.test(manifest.planSha256), 'Invalid plan digest');
  if (approvalPath) {
    invariant(planPath && releaseSha, 'Approval verification requires a plan and exact release SHA');
    const approval = json(approvalPath);
    invariant(approval.schemaVersion === 1 && approval.repository === repository && approval.releaseSha === releaseSha && approval.planSha256 === manifest.planSha256, 'Release approval differs from artifact identity');
    invariant(JSON.stringify(selectedNpm(approval)) === JSON.stringify(selectedNpm(json(planPath))), 'Approved npm component differs from plan');
  }
  invariant(Array.isArray(manifest.packages) && manifest.packages.length === 2, 'Artifact must include exactly two npm packages');
  const actualFiles = readdirSync(artifactDir).sort();
  const expectedFiles = [MANIFEST, ...PACKAGES.map(pkg => archiveName(pkg.name, manifest.version))].sort();
  invariant(JSON.stringify(actualFiles) === JSON.stringify(expectedFiles), 'Artifact directory has missing or unexpected files');
  for (let index = 0; index < PACKAGES.length; index++) {
    const expected = manifest.packages[index];
    const pkg = PACKAGES[index];
    invariant(expected?.name === pkg.name && expected.version === manifest.version && expected.filename === archiveName(pkg.name, manifest.version), 'Artifact fixed-group order/name/version/filename mismatch');
    const actual = inspectArchive(resolve(artifactDir, expected.filename), expected.name, expected.version);
    invariant(JSON.stringify(actual) === JSON.stringify(expected), `Artifact bytes/file inventory changed: ${expected.name}`);
  }
  if (requireConsumerSmoke) invariant(manifest.verification?.consumerSmoke === 'passed' && manifest.verification?.packagesSha256 === digest(JSON.stringify(manifest.packages)), 'Missing isolated consumer verification for these tarballs');
  return manifest;
}

export async function readRegistryVersion(name, version, { fetchImpl = fetch } = {}) {
  invariant(PACKAGES.some(pkg => pkg.name === name) && STABLE.test(version), 'Unexpected registry query');
  const response = await fetchImpl(`${REGISTRY}${encodeURIComponent(name)}`, { headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' }, signal: AbortSignal.timeout(30000), redirect: 'error' });
  let data;
  try { data = await response.json(); } catch { throw new Error(`Malformed npm registry response: HTTP ${response.status}`); }
  if (response.status === 404) { invariant(data?.error === 'Not found', 'Unrecognized registry 404; refusing to infer absence'); return null; }
  invariant(response.ok, `npm registry ${name}: HTTP ${response.status}`);
  invariant(data?.name === name && data.versions && typeof data.versions === 'object' && !Array.isArray(data.versions) && data['dist-tags'] && typeof data['dist-tags'] === 'object', 'Malformed npm registry packument');
  const existing = data.versions[version];
  if (!existing) return { name, version, missing: true, registryLatest: data['dist-tags'].latest ?? null };
  invariant(existing.name === name && existing.version === version && /^sha512-[A-Za-z0-9+/]{86}==$/.test(existing.dist?.integrity ?? ''), 'Missing or invalid npm registry integrity');
  return { ...existing, registryLatest: data['dist-tags'].latest ?? null };
}
const isMissing = value => value === null || value.missing === true;
export function compareStable(left, right) {
  invariant(STABLE.test(left) && STABLE.test(right), 'Stable SemVer comparison required');
  const a = left.split('.').map(BigInt), b = right.split('.').map(BigInt);
  for (let index = 0; index < 3; index++) if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1;
  return 0;
}
function assertPublicationBaseline(plan, existing, pkg) {
  // Identical target bytes may already have been published by this approved plan.
  // A missing target must instead advance exactly the reviewed public baseline.
  if (!isMissing(existing)) return;
  const component = selectedNpm(plan);
  const entry = component.packages.find(entry => entry.name === pkg.name);
  invariant(typeof component.oldVersion === 'string' && entry.oldVersion === component.oldVersion, 'Release must record the same reviewed baseline for both npm packages');
  if (plan.bootstrap === true) {
    if (existing === null) invariant(component.oldVersion === '0.0.0', `Bootstrap baseline changed: ${pkg.name} is absent but plan expects ${component.oldVersion}`);
    else invariant(existing.registryLatest === component.oldVersion, `Bootstrap baseline changed: ${pkg.name} latest is ${existing.registryLatest}, expected ${component.oldVersion}; review a new plan`);
  } else {
    invariant(STABLE.test(component.oldVersion) && existing !== null && existing.registryLatest === component.oldVersion,
      `Normal release requires the published stable baseline ${pkg.name}@${component.oldVersion}; registry latest is ${existing?.registryLatest ?? 'absent'}. Review bootstrap or a new release plan`);
  }
}
function assertLatestCanAdvance(existing, pkg) {
  const latest = existing?.registryLatest;
  if (latest === undefined || latest === null) return;
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(latest);
  invariant(match, `Invalid registry latest SemVer: ${pkg.name}`);
  const compared = compareStable(`${match[1]}.${match[2]}.${match[3]}`, pkg.version);
  invariant(compared < 0 || (compared === 0 && match[4]), `Refusing to move ${pkg.name} latest backwards`);
}
function registryMatches(existing, pkg) {
  invariant(!isMissing(existing) && existing.dist?.integrity === pkg.integrity, `Registry integrity conflict: ${pkg.name}@${pkg.version}; never overwrite or unpublish`);
}
export async function confirmPublished(manifest, { lookup = readRegistryVersion } = {}) {
  const result = [];
  for (const pkg of manifest.packages) {
    const existing = await lookup(pkg.name, pkg.version);
    registryMatches(existing, pkg);
    invariant(STABLE.test(existing.registryLatest ?? '') && compareStable(existing.registryLatest, pkg.version) >= 0, `Registry latest does not include the confirmed stable release: ${pkg.name}`);
    result.push({ name: pkg.name, version: pkg.version, integrity: pkg.integrity, latest: existing.registryLatest, status: 'confirmed' });
  }
  return result;
}
export async function publishArtifacts() {
  assertNpmPublicationEnabled();
}

// Preserve publisher safety regression coverage without a live publication path.
// All external operations are caller-supplied fakes; there is no CLI entry point,
// credential environment, real registry client, or package-manager default here.
const missingSimulation = () => { throw new Error('Publication simulation requires explicit fake I/O'); };
export async function simulatePublication(options = {}, { environment = {}, lookup = missingSimulation, run = missingSimulation, delay = async () => {} } = {}) {
  options = { artifactDir: resolve(ROOT, 'dist/npm'), ...options };
  invariant(environment.RELEASE_ENABLED === 'true', 'Publication is disabled until setup and first release are approved');
  invariant(environment.GITHUB_ACTIONS === 'true' && environment.GITHUB_REPOSITORY === REPOSITORY && environment.GITHUB_REF === 'refs/heads/main', 'Publish only from the original repository main on GitHub Actions');
  invariant(environment.ACTIONS_ID_TOKEN_REQUEST_URL && environment.ACTIONS_ID_TOKEN_REQUEST_TOKEN, 'GitHub OIDC id-token: write is required');
  invariant(!environment.NODE_AUTH_TOKEN && !environment.NPM_TOKEN && !environment.NPM_CONFIG_TOKEN, 'Token fallback is forbidden; use the configured trusted publisher');
  invariant(options.planPath && options.approvalPath && SHA.test(options.releaseSha ?? ''), 'Publish requires the selected plan, approval, and exact release SHA');
  for (const [key, value] of Object.entries({ runId: options.runId, runAttempt: options.runAttempt, artifactId: options.artifactId })) invariant(/^[1-9]\d*$/.test(String(value)), `Publish requires the original build ${key}`);
  const manifest = verifyArtifacts({ ...options, requireConsumerSmoke: true });
  const plan = json(options.planPath);
  assertNpmVersion(run);
  const previous = [];
  // Validate both registry states before the first write, avoiding known partial failure.
  for (const pkg of manifest.packages) {
    const existing = await lookup(pkg.name, pkg.version);
    assertPublicationBaseline(plan, existing, pkg);
    if (!isMissing(existing)) registryMatches(existing, pkg);
    else assertLatestCanAdvance(existing, pkg);
    previous.push(existing);
  }
  const result = [];
  for (let index = 0; index < manifest.packages.length; index++) {
    const pkg = manifest.packages[index];
    if (!isMissing(previous[index])) { result.push({ name: pkg.name, version: pkg.version, status: 'already-published' }); continue; }
    // A manual release may have raced preflight; check again before moving latest.
    const current = await lookup(pkg.name, pkg.version);
    assertPublicationBaseline(plan, current, pkg);
    if (!isMissing(current)) { registryMatches(current, pkg); result.push({ name: pkg.name, version: pkg.version, status: 'already-published' }); continue; }
    assertLatestCanAdvance(current, pkg);
    // Rehash immediately before passing the exact tarball path to the pinned CLI.
    verifyArtifacts({ ...options, requireConsumerSmoke: true });
    // npm 11.9.0 provenance reads GITHUB_SHA. Recovery dispatch HEAD may be newer
    // than the approved checkout, so attest the reverified artifact's source SHA.
    run('npm', ['publish', resolve(options.artifactDir, pkg.filename), '--access', 'public', '--tag', 'latest', '--registry', REGISTRY, '--ignore-scripts', '--provenance'], { cwd: options.artifactDir, stdio: 'inherit', env: { ...environment, GITHUB_SHA: manifest.releaseSha } });
    let confirmed = false;
    for (let attempt = 0; attempt < 5; attempt++) {
      const existing = await lookup(pkg.name, pkg.version);
      if (!isMissing(existing)) { registryMatches(existing, pkg); confirmed = true; break; }
      if (attempt < 4) await delay(1000 * 2 ** attempt);
    }
    invariant(confirmed, `Registry did not confirm ${pkg.name}@${pkg.version}; retain these artifacts and retry after inspection`);
    result.push({ name: pkg.name, version: pkg.version, status: 'published' });
  }
  await confirmPublished(manifest, { lookup });
  return result;
}
export function parseArgs(args) {
  const options = {};
  for (let index = 0; index < args.length; index++) {
    const key = args[index];
    invariant(key.startsWith('--'), `Unexpected argument: ${key}`);
    if (['--no-smoke', '--require-smoke'].includes(key)) options[key.slice(2)] = true;
    else { invariant(args[index + 1] && !args[index + 1].startsWith('--'), `Missing value for ${key}`); options[key.slice(2)] = args[++index]; }
  }
  return options;
}
export function artifactOptions(flags, environment = process.env) {
  return { artifactDir: resolve(flags['artifact-dir'] || flags.artifacts || flags.destination || 'dist/npm'), planPath: flags.plan ? resolve(flags.plan) : undefined, approvalPath: flags.approval || environment.RELEASE_APPROVAL_FILE, releaseSha: flags['release-sha'] || environment.RELEASE_SHA, repository: flags.repository || environment.GITHUB_REPOSITORY || REPOSITORY, runId: flags['run-id'] || environment.NPM_BUILD_RUN_ID || environment.GITHUB_RUN_ID, runAttempt: flags['run-attempt'] || environment.NPM_BUILD_RUN_ATTEMPT || environment.GITHUB_RUN_ATTEMPT, artifactId: flags['artifact-id'] || environment.NPM_ARTIFACT_ID };
}
async function main() {
  const [command, ...args] = process.argv.slice(2);
  const flags = parseArgs(args);
  const options = artifactOptions(flags);
  if (command === 'publish') console.log(JSON.stringify(await publishArtifacts(options), null, 2));
  else if (command === 'verify') console.log(JSON.stringify(verifyArtifacts({ ...options, requireConsumerSmoke: true }), null, 2));
  else if (command === 'confirm') console.log(JSON.stringify(await confirmPublished(verifyArtifacts({ ...options, requireConsumerSmoke: true })), null, 2));
  else throw new Error('Expected publish, verify, or confirm');
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch(error => { console.error(`npm release refused: ${error.message}`); process.exitCode = 1; });

import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, resolve, posix, win32 } from 'node:path';
import { coraModulePath } from '../scripts/cora-paths.mjs';
import { gzipSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { MANIFEST, REPOSITORY, REGISTRY, NPM_VERSION, PACKAGES, UPSTREAM, PACKAGE_LAYOUT, VENDOR_MODULES, digest, archiveName, assertSources, selectedNpm, readTarball, inspectArchive, verifyArtifacts, readRegistryVersion, compareStable, simulatePublication as publishArtifacts, publishArtifacts as blockedPublishArtifacts, confirmPublished, resolveWindowsTool } from '../scripts/npm-release.mjs';

const SHA = 'a'.repeat(40);
const VERSION = '0.1.0';
const temporary = [];
function temporaryDirectory() { const path = mkdtempSync(resolve(tmpdir(), 'hellgato-release-test-')); temporary.push(path); return path; }
afterEach(() => { for (const path of temporary.splice(0)) rmSync(path, { recursive: true, force: true }); });
function tar(entries) {
  const blocks = [];
  for (const [name, value, type = '0'] of entries) {
    const bytes = Buffer.from(value);
    const header = Buffer.alloc(512);
    header.write(name, 0, 100);
    header.write('0000644\0', 100);
    header.write('0000000\0', 108);
    header.write('0000000\0', 116);
    header.write(bytes.length.toString(8).padStart(11, '0') + '\0', 124);
    header.write('00000000000\0', 136);
    header.fill(32, 148, 156);
    header.write(type, 156);
    header.write('ustar\0', 257);
    header.write('00', 263);
    const checksum = header.reduce((sum, byte) => sum + byte, 0);
    header.write(checksum.toString(8).padStart(6, '0') + '\0 ', 148);
    blocks.push(header, bytes, Buffer.alloc((512 - bytes.length % 512) % 512));
  }
  blocks.push(Buffer.alloc(1024));
  return gzipSync(Buffer.concat(blocks));
}
function packageFiles(name, mutate = () => {}) {
  const core = name === '@hellgato/core';
  const manifest = { name, version: VERSION, type: 'module', license: 'MIT', engines: { node: '>=24 <25' }, exports: structuredClone(PACKAGE_LAYOUT[name].exports), types: PACKAGE_LAYOUT[name].types, repository: { url: `git+https://github.com/${REPOSITORY}.git`, directory: `packages/${core ? 'core' : 'hellgato'}` }, publishConfig: { access: 'public', tag: 'latest', registry: REGISTRY }, ...(core ? {} : { dependencies: { '@hellgato/core': VERSION } }) };
  const files = { 'README.md': 'Read me', LICENSE: 'MIT License\nfixture', ...(core ? { 'THIRD-PARTY-NOTICES.txt': `DeckBridge ${UPSTREAM.revision}`, 'dist/bridge.mjs': 'export const createBridge = 1;', 'dist/profiles.mjs': 'export {};', 'dist/display-server.mjs': 'export {};', 'dist/firmware-policy.mjs': 'export {};', 'dist/vendor/LICENSE': 'MIT License\nupstream', 'dist/vendor/package.json': '{"type":"module"}', 'dist/vendor/provenance.json': JSON.stringify({ ...UPSTREAM, modules: ['cora/test.ts'] }), 'dist/vendor/cora/test.js': 'export {};' } : { 'index.mjs': "export * from '@hellgato/core';" }) };
  delete files['dist/vendor/cora/test.js'];
  for (const path of PACKAGE_LAYOUT[name].files) if (!(path in files) && path !== 'package.json') files[path] = path.endsWith('.json') ? '{}' : 'export {};';
  if (core) {
    files['dist/vendor/provenance.json'] = JSON.stringify({ ...UPSTREAM, modules: VENDOR_MODULES });
    for (const source of VENDOR_MODULES) files[`dist/vendor/${source.replace(/\.ts$/, '.js')}`] = 'export {};';
  }
  mutate(manifest, files);
  files['package.json'] = JSON.stringify(manifest);
  return Object.entries(files).map(([path, text]) => [`package/${path}`, text]);
}
function fixture({ mutate, smoke = true } = {}) {
  const root = temporaryDirectory();
  const artifactDir = resolve(root, 'npm');
  mkdirSync(artifactDir);
  const component = { component: 'npm', version: VERSION, oldVersion: '0.0.1', channel: 'stable', tag: `hellgato@${VERSION}`, notes: 'docs/releases/npm-0.1.0.md', packages: PACKAGES.map(pkg => ({ name: pkg.name, oldVersion: '0.0.1', version: VERSION, tag: `${pkg.name}@${VERSION}` })) };
  const plan = { schemaVersion: 1, repository: REPOSITORY, baseSha: 'b'.repeat(40), components: [component] };
  const planPath = resolve(root, 'plan.json');
  writeFileSync(planPath, JSON.stringify(plan));
  const manifest = { schemaVersion: 1, repository: REPOSITORY, releaseSha: SHA, planSha256: digest(readFileSync(planPath)), component: 'npm', version: VERSION, channel: 'stable', source: { runId: '12', runAttempt: '2' }, tools: { node: '24.19.0', npm: NPM_VERSION, pnpm: '11.10.0' }, upstream: UPSTREAM, packages: [] };
  for (const pkg of PACKAGES) {
    const path = resolve(artifactDir, archiveName(pkg.name, VERSION));
    writeFileSync(path, tar(packageFiles(pkg.name)));
    manifest.packages.push(inspectArchive(path, pkg.name, VERSION));
  }
  if (smoke) manifest.verification = { consumerSmoke: 'passed', packagesSha256: digest(JSON.stringify(manifest.packages)) };
  mutate?.(manifest);
  writeFileSync(resolve(artifactDir, MANIFEST), JSON.stringify(manifest));
  const approvalPath = resolve(root, 'approval.json');
  writeFileSync(approvalPath, JSON.stringify({ ...plan, releaseSha: SHA, planSha256: manifest.planSha256 }));
  return { root, manifest, plan, options: { artifactDir, planPath, approvalPath, releaseSha: SHA, repository: REPOSITORY, runId: '12', runAttempt: '2', artifactId: '123' } };
}
const environment = { RELEASE_ENABLED: 'true', GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: REPOSITORY, GITHUB_REF: 'refs/heads/main', ACTIONS_ID_TOKEN_REQUEST_URL: 'https://example.invalid', ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'test-identity-context' };
const recorded = pkg => ({ name: pkg.name, version: pkg.version, dist: { integrity: pkg.integrity }, verifiedTarball: { integrity: pkg.integrity, sha256: pkg.sha256, size: pkg.size }, registryLatest: VERSION });
// The normal fixture is a reviewed 0.0.1→0.1.0 release with 0.0.1 already public.
const predecessor = () => ({ missing: true, registryLatest: '0.0.1' });

 test('source fixed group requires equal versions and workspace:*', () => {
  const root = temporaryDirectory();
  for (const pkg of PACKAGES) { mkdirSync(resolve(root, 'packages', pkg.directory), { recursive: true }); writeFileSync(resolve(root, 'packages', pkg.directory, 'package.json'), JSON.stringify({ name: pkg.name, version: VERSION, dependencies: { '@hellgato/core': 'workspace:*' } })); }
  assert.equal(assertSources(root).length, 2);
  const path = resolve(root, 'packages/hellgato/package.json');
  writeFileSync(path, JSON.stringify({ name: 'hellgato', version: VERSION, dependencies: { '@hellgato/core': VERSION } }));
  assert.throws(() => assertSources(root), /workspace:\*/);
  writeFileSync(path, JSON.stringify({ name: 'hellgato', version: '0.2.0', dependencies: { '@hellgato/core': 'workspace:*' } }));
  assert.throws(() => assertSources(root), /same stable version/);
});
test('Windows-only plan never selects unpublished npm packages', () => {
  assert.throws(() => selectedNpm({ schemaVersion: 1, repository: REPOSITORY, components: [{ component: 'windows' }] }), /does not select npm/);
  const { plan } = fixture();
  assert.equal(selectedNpm(plan).version, VERSION);
  plan.components[0].packages.pop();
  assert.throws(() => selectedNpm(plan), /both packages/);
});
test('static verification binds identity, digests, inventory and consumer proof', () => {
  const { options, manifest } = fixture();
  assert.deepEqual(verifyArtifacts({ ...options, requireConsumerSmoke: true }), manifest);
  for (const change of [{ releaseSha: 'c'.repeat(40) }, { runId: '13' }, { runAttempt: '1' }, { repository: 'someone/fork' }]) assert.throws(() => verifyArtifacts({ ...options, ...change }), /mismatch|repository|differs/);
  writeFileSync(options.planPath, readFileSync(options.planPath, 'utf8') + ' ');
  assert.throws(() => verifyArtifacts(options), /digest/);
});
test('missing smoke proof blocks publication verification', () => {
  const { options } = fixture({ smoke: false });
  assert.throws(() => verifyArtifacts({ ...options, requireConsumerSmoke: true }), /consumer verification/);
});
test('changed bytes and unexpected artifacts are rejected', () => {
  const { options, manifest } = fixture();
  const path = resolve(options.artifactDir, manifest.packages[0].filename);
  writeFileSync(path, tar(packageFiles('@hellgato/core', (_metadata, files) => { files['README.md'] = 'Changed README'; })));
  assert.throws(() => verifyArtifacts(options), /bytes\/file inventory/);
  writeFileSync(resolve(options.artifactDir, 'extra.tgz'), 'extra');
  assert.throws(() => verifyArtifacts(options), /unexpected files/);
});
test('packed facade rejects workspace, link, file and ranged dependencies', () => {
  const directory = temporaryDirectory();
  for (const dependency of ['workspace:*', 'link:../core', 'file:../core', '../core', '^0.1.0']) {
    const path = resolve(directory, 'facade.tgz');
    writeFileSync(path, tar(packageFiles('hellgato', manifest => { manifest.dependencies['@hellgato/core'] = dependency; })));
    assert.throws(() => inspectArchive(path, 'hellgato', VERSION), /dependency/);
  }
});
test('licenses, exports, provenance and file allowlist are mandatory', () => {
  const directory = temporaryDirectory();
  for (const mutate of [(_manifest, files) => { delete files.LICENSE; }, manifest => { manifest.exports = './private.mjs'; }, (_manifest, files) => { files['.env'] = 'secret'; }, (_manifest, files) => { files['dist/vendor/provenance.json'] = '{}'; }, manifest => { manifest.scripts = { install: 'node evil.js' }; }]) {
    const path = resolve(directory, 'core.tgz');
    writeFileSync(path, tar(packageFiles('@hellgato/core', mutate)));
    assert.throws(() => inspectArchive(path, '@hellgato/core', VERSION), /Missing|required|exports|Unexpected|provenance|lifecycle/);
  }
});
test('archive traversal, symlinks, duplicate entries, and malformed gzip are rejected', () => {
  for (const entries of [[['package/../escape', 'x']], [['package/link', 'target', '2']], [['package/x', 'a'], ['package/x', 'b']], [['/absolute/file', 'x']]]) assert.throws(() => readTarball(tar(entries)), /Unsafe|Unsupported|Duplicate/);
  assert.throws(() => readTarball(Buffer.from('not gzip')));
});
test('registry reads fail closed on HTTP, malformed JSON and missing integrity', async () => {
  for (const status of [403, 429, 500]) await assert.rejects(readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => Response.json({ error: 'failed' }, { status }) }), /HTTP/);
  await assert.rejects(readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => new Response('invalid', { status: 200 }) }), /Malformed/);
  await assert.rejects(readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => Response.json({ error: 'proxy missing' }, { status: 404 }) }), /Unrecognized/);
  await assert.rejects(readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => Response.json({ name: 'hellgato', versions: { [VERSION]: { name: 'hellgato', version: VERSION } }, 'dist-tags': {} }) }), /integrity/);
  assert.equal(await readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => Response.json({ error: 'Not found' }, { status: 404 }) }), null);
  assert.equal((await readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => Response.json({ name: 'hellgato', versions: {}, 'dist-tags': { latest: '0.0.1-beta' } }) })).missing, true);
});
test('publication requires opt-in, exact approval, original source IDs and OIDC', async () => {
  const { options } = fixture();
  for (const env of [{ ...environment, RELEASE_ENABLED: 'false' }, { ...environment, ACTIONS_ID_TOKEN_REQUEST_TOKEN: '' }, { ...environment, NPM_TOKEN: 'not-allowed' }]) await assert.rejects(publishArtifacts(options, { environment: env }), /disabled|OIDC|Token/);
  await assert.rejects(publishArtifacts({ ...options, artifactId: undefined }, { environment }), /artifactId/);
  writeFileSync(options.approvalPath, '{}');
  await assert.rejects(publishArtifacts(options, { environment }), /approval/);
});
test('identical registry bytes are a no-op for both packages', async () => {
  const { options, manifest } = fixture();
  const calls = [];
  const result = await publishArtifacts(options, { environment, lookup: async name => recorded(manifest.packages.find(pkg => pkg.name === name)), run: (tool, args) => { calls.push([tool, args]); return NPM_VERSION; } });
  assert.deepEqual(result.map(pkg => pkg.status), ['already-published', 'already-published']);
  assert.deepEqual(calls, [['npm', ['--version']]]);
});
test('facade conflict is detected before publishing missing core', async () => {
  const { options, manifest } = fixture();
  let writes = 0;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async name => name === '@hellgato/core' ? predecessor() : { ...recorded(manifest.packages[1]), dist: { integrity: 'wrong' } }, run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /integrity conflict/);
  assert.equal(writes, 0);
});
test('publishes immutable core first and only then the facade, with scripts disabled', async () => {
  const { options, manifest } = fixture();
  const published = new Map();
  const order = [];
  await publishArtifacts(options, { environment, lookup: async name => published.get(name) || predecessor(), run: (_tool, args, runOptions) => {
    if (args[0] === '--version') return NPM_VERSION;
    assert.equal(args[0], 'publish');
    assert.equal(runOptions.env.GITHUB_SHA, SHA);
    assert.ok(args.includes('--ignore-scripts'));
    const pkg = manifest.packages.find(pkg => resolve(options.artifactDir, pkg.filename) === args[1]);
    order.push(pkg.name);
    published.set(pkg.name, recorded(pkg));
    return '';
  }, delay: async () => {} });
  assert.deepEqual(order, ['@hellgato/core', 'hellgato']);
});
test('partially published core is skipped; only facade is resumed', async () => {
  const { options, manifest } = fixture();
  const published = new Map([['@hellgato/core', recorded(manifest.packages[0])]]);
  const order = [];
  const result = await publishArtifacts(options, { environment, lookup: async name => published.get(name) || predecessor(), run: (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; order.push(args[1]); published.set('hellgato', recorded(manifest.packages[1])); return ''; } });
  assert.equal(order.length, 1);
  assert.equal(basename(order[0]), 'hellgato-0.1.0.tgz');
  assert.deepEqual(result.map(pkg => pkg.status), ['already-published', 'published']);
});
test('unconfirmed core stops before facade and failed publish never retries automatically', async () => {
  const { options } = fixture();
  let writes = 0;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async () => predecessor(), run: (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; writes++; return ''; }, delay: async () => {} }), /did not confirm/);
  assert.equal(writes, 1);
  writes = 0;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async () => predecessor(), run: (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; writes++; throw new Error('network failed'); } }), /network failed/);
  assert.equal(writes, 1);
});
test('stable latest rollback and wrong tool version are rejected before publication', async () => {
  const { options } = fixture();
  for (const latest of ['0.2.0', '0.2.0-beta']) {
    const bootstrap = bootstrapFixture(latest);
    await assert.rejects(publishArtifacts(bootstrap.options, { environment, lookup: async () => ({ missing: true, registryLatest: latest }), run: () => NPM_VERSION }), /backwards/);
  }
  await assert.rejects(publishArtifacts(options, { environment, run: () => '11.8.0' }), /Expected npm/);
  assert.equal(compareStable('0.1.10', '0.1.9'), 1);
});
test('confirmation verifies registry integrity and stable dist-tags without writing', async () => {
  const { manifest } = fixture();
  const result = await confirmPublished(manifest, { lookup: async name => recorded(manifest.packages.find(pkg => pkg.name === name)) });
  assert.equal(result.length, 2);
  await assert.rejects(confirmPublished(manifest, { lookup: async name => ({ ...recorded(manifest.packages.find(pkg => pkg.name === name)), registryLatest: '0.0.1-beta' }) }), /latest/);
});

test('publisher refuses a Windows-only plan before any registry write', async () => {
  const { options, plan } = fixture();
  plan.components = [{ component: 'windows', version: '0.1.2', channel: 'stable' }];
  writeFileSync(options.planPath, JSON.stringify(plan));
  await assert.rejects(publishArtifacts(options, { environment }), /does not select npm/);
});

function bootstrapFixture(oldVersion) {
  const result = fixture();
  result.plan.bootstrap = true;
  result.plan.components[0].oldVersion = oldVersion;
  for (const pkg of result.plan.components[0].packages) pkg.oldVersion = oldVersion;
  writeFileSync(result.options.planPath, JSON.stringify(result.plan));
  result.manifest.planSha256 = digest(readFileSync(result.options.planPath));
  writeFileSync(resolve(result.options.artifactDir, MANIFEST), JSON.stringify(result.manifest));
  writeFileSync(result.options.approvalPath, JSON.stringify({ ...result.plan, releaseSha: SHA, planSha256: result.manifest.planSha256 }));
  return result;
}
test('bootstrap refuses either changed baseline before any package is published', async () => {
  const { options } = bootstrapFixture('0.0.1-beta');
  let writes = 0;
  for (const changed of [null, { missing: true, registryLatest: '0.0.2-beta' }, { missing: true, registryLatest: null }]) {
    await assert.rejects(publishArtifacts(options, { environment, lookup: async name => name === '@hellgato/core' ? { missing: true, registryLatest: '0.0.1-beta' } : changed, run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /Bootstrap baseline changed/);
  }
  assert.equal(writes, 0);
});
test('bootstrap allows exact reviewed beta baseline and identical-target partial resume', async () => {
  const { options, manifest } = bootstrapFixture('0.0.1-beta');
  const published = new Map([['@hellgato/core', recorded(manifest.packages[0])]]);
  const order = [];
  await publishArtifacts(options, { environment, lookup: async name => published.get(name) || { missing: true, registryLatest: '0.0.1-beta' }, run: (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; order.push(args[1]); published.set('hellgato', recorded(manifest.packages[1])); return ''; } });
  assert.equal(order.length, 1);
  assert.equal(basename(order[0]), 'hellgato-0.1.0.tgz');
});
test('bootstrap of genuinely absent names is allowed only from 0.0.0', async () => {
  const { options, manifest } = bootstrapFixture('0.0.0');
  const published = new Map();
  await publishArtifacts(options, { environment, lookup: async name => published.get(name) || null, run: (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; const pkg = manifest.packages.find(pkg => basename(args[1]) === pkg.filename); published.set(pkg.name, recorded(pkg)); return ''; } });
  assert.equal(published.size, 2);
});

test('Windows action-setup .bin pnpm shim resolves its own pinned sibling CLI', () => {
  for (const extension of ['cjs', 'mjs']) {
    const cli = `C:\\Users\\runneradmin\\setup-pnpm\\node_modules\\pnpm\\bin\\pnpm.${extension}`;
    const located = ['C:\\Users\\runneradmin\\setup-pnpm\\node_modules\\.bin\\pnpm', 'C:\\Users\\runneradmin\\setup-pnpm\\node_modules\\.bin\\pnpm.cmd', 'C:\\global\\pnpm.exe', 'C:\\global\\pnpm.cmd'].join('\r\n');
    assert.deepEqual(resolveWindowsTool('pnpm', { locate: tool => { assert.equal(tool, 'pnpm'); return located; }, exists: path => path === cli, nodePath: 'C:\\node\\node.exe' }), { command: 'C:\\node\\node.exe', prefix: [cli] });
  }
});
test('Windows npm .bin and supported global package-manager shims resolve without shell', () => {
  const fixtures = [
    ['npm', 'C:\\tools\\node_modules\\.bin\\npm.cmd', 'C:\\tools\\node_modules\\npm\\bin\\npm-cli.js'],
    ['npm', 'C:\\Program Files\\nodejs\\npm.cmd', 'C:\\Program Files\\nodejs\\node_modules\\npm\\bin\\npm-cli.js'],
    ['pnpm', 'C:\\Users\\runneradmin\\AppData\\Roaming\\npm\\pnpm.cmd', 'C:\\Users\\runneradmin\\AppData\\Roaming\\npm\\node_modules\\pnpm\\bin\\pnpm.cjs'],
    ['pnpm', 'C:\\node\\pnpm.cmd', 'C:\\node\\node_modules\\corepack\\dist\\pnpm.js'],
  ];
  for (const [tool, shim, cli] of fixtures) assert.deepEqual(resolveWindowsTool(tool, { locate: () => shim, exists: path => path === cli, nodePath: 'C:\\node\\node.exe' }), { command: 'C:\\node\\node.exe', prefix: [cli] });
});
test('Windows selected standalone executable is honored and broken shim never selects global pnpm', () => {
  assert.deepEqual(resolveWindowsTool('pnpm', { locate: () => 'C:\\pinned\\pnpm.exe\r\nC:\\global\\pnpm.cmd' }), { command: 'C:\\pinned\\pnpm.exe', prefix: [] });
  assert.throws(() => resolveWindowsTool('pnpm', { locate: () => 'C:\\pinned\\node_modules\\.bin\\pnpm.cmd\r\nC:\\global\\pnpm.exe\r\nC:\\global\\pnpm.cmd', exists: path => path.startsWith('C:\\global') }), /Cannot locate the selected pnpm JavaScript CLI/);
});

test('normal release rejects absent packages and missing or changed stable predecessors before any write', async () => {
  const { options } = fixture();
  let writes = 0;
  for (const baseline of [null, { missing: true, registryLatest: '0.0.1-beta' }, { missing: true, registryLatest: '0.0.0' }, { missing: true, registryLatest: '0.0.2' }, { missing: true, registryLatest: null }]) {
    await assert.rejects(publishArtifacts(options, { environment, lookup: async name => name === '@hellgato/core' ? predecessor() : baseline, run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /published stable baseline/);
  }
  assert.equal(writes, 0);
});
test('normal partial recovery refuses a facade whose reviewed predecessor was never public', async () => {
  const { options, manifest } = fixture();
  let writes = 0;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async name => name === '@hellgato/core' ? recorded(manifest.packages[0]) : { missing: true, registryLatest: '0.0.1-beta' }, run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /published stable baseline/);
  assert.equal(writes, 0);
});
test('normal baseline is checked again immediately before publishing', async () => {
  const { options } = fixture();
  let reads = 0;
  let writes = 0;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async () => ++reads <= 2 ? predecessor() : { missing: true, registryLatest: '0.0.2' }, run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /published stable baseline/);
  assert.equal(writes, 0);
});

test('CORA provenance emits canonical module paths from both Windows and POSIX builds', () => {
  const expected = 'cora/primary-server.ts';
  assert.equal(coraModulePath('C:\\build\\deckbridge\\ts\\src', 'C:\\build\\deckbridge\\ts\\src\\cora\\primary-server.ts', win32.relative), expected);
  assert.equal(coraModulePath('/build/deckbridge/ts/src', '/build/deckbridge/ts/src/cora/primary-server.ts', posix.relative), expected);
  const path = resolve(temporaryDirectory(), 'core.tgz');
  writeFileSync(path, tar(packageFiles('@hellgato/core', (_manifest, files) => {
    const modules = VENDOR_MODULES.map(source => coraModulePath('D:\\src', win32.join('D:\\src', source), win32.relative));
    files['dist/vendor/provenance.json'] = JSON.stringify({ ...UPSTREAM, modules });
  })));
  assert.equal(inspectArchive(path, '@hellgato/core', VERSION).name, '@hellgato/core');
});


test('npm publication API is hard-disabled even with complete approval and fake transports', async () => {
  const { options } = fixture();
  let calls = 0;
  await assert.rejects(blockedPublishArtifacts(options, { environment, lookup: async () => { calls++; }, run: () => { calls++; } }), /hard-disabled/);
  assert.equal(calls, 0);
});

test('npm publish and finalize CLIs fail before credentials, plan or network access', () => {
  for (const [script, command] of [['npm-release.mjs', 'publish'], ['release.mjs', 'finalize-npm']]) {
    const result = spawnSync(process.execPath, [resolve('scripts', script), command], { encoding: 'utf8', env: { ...process.env, RELEASE_ENABLED: 'true', NPM_PUBLICATION_ENABLED: 'true' } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /hard-disabled/);
    assert.doesNotMatch(result.stderr, /GH_TOKEN is required|ENOENT|HTTP/);
  }
});

test('new API subpaths and declaration files require the exact reviewed archive inventory', () => {
  const directory = temporaryDirectory();
  for (const name of ['@hellgato/core', 'hellgato']) {
    for (const mutate of [
      manifest => { manifest.exports['./internal/*'] = './src/*.mjs'; },
      manifest => { manifest.types = './private.d.ts'; },
      (_manifest, files) => { delete files[PACKAGE_LAYOUT[name].types.slice(2)]; },
      (_manifest, files) => { files['src/unreviewed.mjs'] = 'export {};'; },
      (_manifest, files) => { files['dist/vendor/cora/unreviewed.js'] = 'export {};'; },
    ]) {
      const path = resolve(directory, 'package.tgz');
      writeFileSync(path, tar(packageFiles(name, mutate)));
      assert.throws(() => inspectArchive(path, name, VERSION), /exports|type declaration|Missing|Unexpected/);
    }
  }
});


test('publisher regression simulation never falls back to real npm or registry I/O', async () => {
  const { options } = fixture();
  await assert.rejects(publishArtifacts(options, { environment }), /requires explicit fake I\/O/);
});

test('beta candidates require their own explicit plan and cannot pass public approval', async () => {
  const { channelPolicy } = await import('../scripts/release-policy.mjs');
  const { options, manifest, plan } = fixture();
  const version = '0.1.1-beta.0';
  const component = plan.components[0];
  Object.assign(component, { version, channel: 'beta', tag: `hellgato@${version}` });
  component.packages = PACKAGES.map(pkg => ({ name: pkg.name, oldVersion: VERSION, version, tag: `${pkg.name}@${version}` }));
  Object.assign(plan, { schemaVersion: 2, candidateOnly: true, branch: 'next', channel: 'beta', policy: channelPolicy('beta') });
  writeFileSync(options.planPath, JSON.stringify(plan));
  for (const pkg of manifest.packages) rmSync(resolve(options.artifactDir, pkg.filename));
  Object.assign(manifest, { schemaVersion: 2, candidateOnly: true, version, channel: 'beta', planSha256: digest(readFileSync(options.planPath)), packages: [] });
  for (const pkg of PACKAGES) {
    const path = resolve(options.artifactDir, archiveName(pkg.name, version));
    writeFileSync(path, tar(packageFiles(pkg.name, metadata => {
      metadata.version = version;
      metadata.publishConfig.tag = 'beta';
      if (metadata.dependencies) metadata.dependencies['@hellgato/core'] = version;
    })));
    assert.throws(() => inspectArchive(path, pkg.name, version), /explicit artifact channel/);
    manifest.packages.push(inspectArchive(path, pkg.name, version, { channel: 'beta' }));
  }
  delete manifest.verification;
  writeFileSync(resolve(options.artifactDir, MANIFEST), JSON.stringify(manifest));
  const candidate = { ...options, candidatePlanPath: options.planPath, planPath: undefined, approvalPath: undefined };
  assert.equal(verifyArtifacts(candidate).channel, 'beta');
  assert.throws(() => verifyArtifacts(options), /schema/);
  assert.throws(() => verifyArtifacts({ ...candidate, approvalPath: options.approvalPath }), /cannot accept/);
  assert.throws(() => selectedNpm(plan), /schema/);
  assert.throws(() => selectedNpm({ ...plan, policy: channelPolicy('stable') }, { candidate: true }), /schema/);
  await assert.rejects(blockedPublishArtifacts(candidate, { environment }), /hard-disabled/);
  manifest.channel = 'stable';
  writeFileSync(resolve(options.artifactDir, MANIFEST), JSON.stringify(manifest));
  assert.throws(() => verifyArtifacts(candidate), /version\/channel/);
});

function betaReleaseFixture() {
  const result = fixture();
  const { options, manifest, plan } = result;
  const version = '0.1.1-beta.0';
  const baseline = { version: '0.0.1-beta', integrity: `sha512-${'A'.repeat(86)}==` };
  const component = plan.components[0];
  Object.assign(component, { oldVersion: VERSION, version, channel: 'beta', tag: `hellgato@${version}`, notes: `docs/releases/npm-${version}.md`, packages: PACKAGES.map(pkg => ({ name: pkg.name, oldVersion: VERSION, version, tag: `${pkg.name}@${version}` })), publicBaseline: { schemaVersion: 1, component: 'npm', sourceVersion: VERSION, packages: PACKAGES.map(pkg => ({ name: pkg.name, latest: baseline, beta: baseline })), reason: 'Start a separately reviewed beta channel while keeping latest unchanged for stable consumers.' } });
  Object.assign(plan, { schemaVersion: 3, candidateOnly: false, branch: 'next', channel: 'beta', policy: { branch: 'next', releaseBranch: 'changeset-release/next', distTag: 'beta', prerelease: true } });
  writeFileSync(options.planPath, JSON.stringify(plan));
  for (const pkg of manifest.packages) rmSync(resolve(options.artifactDir, pkg.filename));
  Object.assign(manifest, { version, channel: 'beta', planSha256: digest(readFileSync(options.planPath)), packages: [] });
  for (const pkg of PACKAGES) {
    const path = resolve(options.artifactDir, archiveName(pkg.name, version));
    writeFileSync(path, tar(packageFiles(pkg.name, metadata => {
      metadata.version = version; metadata.publishConfig.tag = 'beta';
      if (metadata.dependencies) metadata.dependencies['@hellgato/core'] = version;
    })));
    manifest.packages.push(inspectArchive(path, pkg.name, version, { channel: 'beta' }));
  }
  manifest.verification = { consumerSmoke: 'passed', packagesSha256: digest(JSON.stringify(manifest.packages)) };
  writeFileSync(resolve(options.artifactDir, MANIFEST), JSON.stringify(manifest));
  writeFileSync(options.approvalPath, JSON.stringify({ schemaVersion: 1, repository: REPOSITORY, releaseSha: SHA, planSha256: manifest.planSha256, components: plan.components }));
  const previous = pkg => ({ name: pkg.name, version, missing: true, registryLatest: baseline.version, registryLatestIntegrity: baseline.integrity, registryBeta: baseline.version, registryBetaIntegrity: baseline.integrity });
  const published = pkg => ({ ...previous(pkg), missing: false, dist: { integrity: pkg.integrity }, verifiedTarball: { integrity: pkg.integrity, sha256: pkg.sha256, size: pkg.size }, registryBeta: version, registryBetaIntegrity: pkg.integrity });
  return { ...result, previous, published, environment: { ...environment, GITHUB_REF: 'refs/heads/next' } };
}

test('beta publication engine uses only beta tag, preserves latest and resumes exact bytes', async () => {
  const { options, manifest, previous, published, environment } = betaReleaseFixture();
  const state = new Map(manifest.packages.map(pkg => [pkg.name, previous(pkg)]));
  const writes = [];
  const run = (_tool, args) => {
    if (args[0] === '--version') return NPM_VERSION;
    assert.equal(args[args.indexOf('--tag') + 1], 'beta');
    const pkg = manifest.packages.find(p => args[1].endsWith(p.filename));
    writes.push(pkg.name); state.set(pkg.name, published(pkg));
  };
  const io = { environment, lookup: async name => state.get(name), run };
  await publishArtifacts(options, io);
  assert.deepEqual(writes, ['@hellgato/core', 'hellgato']);
  writes.length = 0;
  await publishArtifacts(options, io);
  assert.deepEqual(writes, []);
  state.set('hellgato', previous(manifest.packages[1]));
  await publishArtifacts(options, io);
  assert.deepEqual(writes, ['hellgato']);
  await assert.rejects(blockedPublishArtifacts(options, io), /hard-disabled/);
});

test('beta publication rejects wrong branch, changed latest/beta baselines and post-publish latest drift', async () => {
  const { options, manifest, previous, published, environment } = betaReleaseFixture();
  let writes = 0;
  const run = (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; writes++; };
  await assert.rejects(publishArtifacts(options, { environment: { ...environment, GITHUB_REF: 'refs/heads/main' }, lookup: async () => { throw new Error('must not read registry'); }, run }), /original repository/);
  for (const mutation of [x => x.registryLatest = '0.0.2', x => x.registryLatestIntegrity = 'different', x => x.registryBeta = '0.1.2-beta.0', x => x.registryBetaIntegrity = 'different']) {
    await assert.rejects(publishArtifacts(options, { environment, lookup: async name => { const value = previous(manifest.packages.find(p => p.name === name)); mutation(value); return value; }, run }), /baseline changed/);
  }
  assert.equal(writes, 0);
  let didPublish = false;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async name => {
    const pkg = manifest.packages.find(p => p.name === name);
    return didPublish && name === '@hellgato/core' ? { ...published(pkg), registryLatest: pkg.version, registryLatestIntegrity: pkg.integrity } : previous(pkg);
  }, run: (_tool, args) => { if (args[0] === '--version') return NPM_VERSION; didPublish = true; writes++; } }), /latest baseline changed/);
  assert.equal(writes, 1);
});

test('ordinary beta source CI artifacts never inherit stale release approval', async () => {
  const { options, manifest } = fixture();
  Object.assign(manifest, { schemaVersion: 2, candidateOnly: true, consumer: 'source-ci', planSha256: null });
  writeFileSync(resolve(options.artifactDir, MANIFEST), JSON.stringify(manifest));
  const source = { ...options, planPath: undefined, approvalPath: undefined, sourceChannel: 'beta' };
  assert.equal(verifyArtifacts(source).consumer, 'source-ci');
  assert.throws(() => verifyArtifacts({ ...source, sourceChannel: undefined }), /schema/);
  assert.throws(() => verifyArtifacts({ ...source, planPath: options.planPath }), /cannot accept/);
  assert.throws(() => verifyArtifacts({ ...source, approvalPath: options.approvalPath }), /cannot accept/);
  manifest.planSha256 = digest(readFileSync(options.planPath));
  writeFileSync(resolve(options.artifactDir, MANIFEST), JSON.stringify(manifest));
  assert.throws(() => verifyArtifacts(source), /schema/);
  await assert.rejects(blockedPublishArtifacts(source), /hard-disabled/);
});

test('registry readback verifies bounded exact-version official tarball bytes', async () => {
  const bytes = Buffer.from('the exact approved package archive');
  const integrity = `sha512-${digest(bytes, 'sha512', 'base64')}`;
  const tarball = `${REGISTRY}hellgato/-/hellgato-${VERSION}.tgz`;
  const packument = { name: 'hellgato', versions: { [VERSION]: { name: 'hellgato', version: VERSION, dist: { integrity, tarball } } }, 'dist-tags': { latest: VERSION } };
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push(url); assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, undefined);
    return url === tarball ? new Response(bytes, { headers: { 'Content-Length': String(bytes.length) } }) : Response.json(packument);
  };
  const found = await readRegistryVersion('hellgato', VERSION, { fetchImpl });
  assert.deepEqual(found.verifiedTarball, { size: bytes.length, sha256: digest(bytes), integrity });
  assert.deepEqual(calls, [`${REGISTRY}hellgato`, tarball]);
  for (const url of ['http://registry.npmjs.org/hellgato/-/hellgato-0.1.0.tgz', 'https://example.invalid/package.tgz', `${tarball}?token=unexpected`, `${REGISTRY}hellgato/-/hellgato-0.0.1.tgz`, 'https://name:secret@registry.npmjs.org/hellgato/-/hellgato-0.1.0.tgz']) {
    let reads = 0;
    await assert.rejects(readRegistryVersion('hellgato', VERSION, { fetchImpl: async () => { reads++; return Response.json({ ...packument, versions: { [VERSION]: { ...packument.versions[VERSION], dist: { integrity, tarball: url } } } }); } }), /tarball URL/);
    assert.equal(reads, 1);
  }
  for (const response of [() => new Response('different'), () => new Response(bytes, { headers: { 'Content-Length': String(33 * 1024 * 1024) } }), () => new Response(bytes, { headers: { 'Content-Length': String(bytes.length + 1) } }), () => new Response(null), () => new Response('unavailable', { status: 503 }), () => new Response(new Uint8Array(32 * 1024 * 1024 + 1))]) {
    await assert.rejects(readRegistryVersion('hellgato', VERSION, { fetchImpl: async url => url === tarball ? response() : Response.json(packument) }), /integrity conflict|size|truncated|HTTP|byte limit/);
  }
});

test('matching registry metadata cannot replace actual approved-byte readback', async () => {
  const { options, manifest } = fixture();
  let writes = 0;
  for (const mutate of [x => delete x.verifiedTarball, x => x.verifiedTarball.sha256 = '0'.repeat(64), x => x.verifiedTarball.size++, x => x.verifiedTarball.integrity = 'wrong']) {
    await assert.rejects(publishArtifacts(options, { environment, lookup: async name => { const value = recorded(manifest.packages.find(p => p.name === name)); mutate(value); return value; }, run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /tarball bytes/);
  }
  assert.equal(writes, 0);
});

test('completed beta retries refuse stable values on the beta alias', async () => {
  const { options, manifest, published, environment } = betaReleaseFixture();
  let writes = 0;
  await assert.rejects(publishArtifacts(options, { environment, lookup: async name => ({ ...published(manifest.packages.find(p => p.name === name)), registryBeta: '1.0.0' }), run: (_tool, args) => { if (args[0] === 'publish') writes++; return NPM_VERSION; } }), /Confirmed beta/);
  assert.equal(writes, 0);
});

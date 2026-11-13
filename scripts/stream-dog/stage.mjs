import { cpSync, mkdirSync, readFileSync, readdirSync, realpathSync, existsSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { dirname, relative, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { verifyWindowsSdkStage } from '../stage-windows-sdk.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const json = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const writeJson = (path, value) => writeFileSync(path, JSON.stringify(value, null, 2) + '\n');
const requireValue = (value, message) => { if (!value) throw new Error(message); };

function inventory(directory, excluded = new Set()) {
  const files = {};
  const visit = path => {
    for (const entry of readdirSync(path, { withFileTypes: true })) {
      requireValue(!entry.isSymbolicLink(), 'Runtime bundles cannot contain symlinks');
      const file = resolve(path, entry.name), name = relative(directory, file).replaceAll('\\', '/');
      if (excluded.has(name)) continue;
      if (entry.isDirectory()) visit(file);
      else { requireValue(entry.isFile(), 'Runtime bundle contains a non-file'); files[name] = hash(readFileSync(file)); }
    }
  };
  visit(directory); return files;
}

function copyPackage(name, caller, destination, copied) {
  if (copied.has(name)) return;
  const metadata = caller.resolve.paths(name)?.map(base => resolve(base, name, 'package.json')).find(existsSync);
  if (!metadata) return false;
  const path = realpathSync(metadata);
  const manifest = json(path), source = dirname(path);
  if (manifest.os && !manifest.os.includes(process.platform) || manifest.cpu && !manifest.cpu.includes(process.arch)) return false;
  copied.add(name);
  cpSync(realpathSync(source), resolve(destination, name), { recursive: true, dereference: true });
  const local = createRequire(path);
  for (const dependency of Object.keys(manifest.dependencies ?? {})) requireValue(copyPackage(dependency, local, destination, copied) !== false, `Missing runtime dependency ${dependency}`);
  for (const dependency of Object.keys(manifest.optionalDependencies ?? {})) copyPackage(dependency, local, destination, copied);
  return true;
}

export function stageStreamDogRust({ destination, sdk = resolve(root, 'work/stream-dog-sdk'), withGui = true,
  vendor = resolve(root, 'work/references/mirabox/Python-SDK/src/StreamDock/Transport/TransportDLL'),
  startupDirectory = 'C:/Program Files/Elgato/StreamDeck',
  nativeUi = resolve(root, 'work/build-native-ui-package/HellgatoNativeUiV2.dll'),
  node = resolve(root, 'work/build-runtime/node.exe') } = {}) {
  requireValue(process.platform === 'win32' && process.arch === 'x64', 'The current bundle targets Windows x64');
  requireValue(destination, 'Use an explicit fresh destination for the runtime bundle');
  destination = resolve(destination);
  requireValue(!existsSync(destination), 'Bundle destination already exists; use a fresh directory');
  verifyWindowsSdkStage(sdk);
  requireValue(/^v24\./.test(execFileSync(node, ['--version'], { encoding: 'utf8' }).trim()), 'Node 24 is required');
  for (const path of ['runtime', 'node', 'native/startup', 'adapters/mirabox/vendor', 'adapters/simulator']) mkdirSync(resolve(destination, path), { recursive: true });
  cpSync(node, resolve(destination, 'node/node.exe'));
  cpSync(resolve(sdk, 'node_modules'), resolve(destination, 'runtime/node_modules'), { recursive: true, dereference: true });
  copyPackage('sharp', createRequire(resolve(root, 'package.json')), resolve(destination, 'runtime/node_modules'), new Set());
  for (const entry of readdirSync(resolve(root, 'apps/stream-dog/node'))) {
    if (/^(full-frame-|frame-).*\.mjs$/.test(entry) || entry === 'trace-log.mjs') cpSync(resolve(root, 'apps/stream-dog/node', entry), resolve(destination, 'runtime', entry));
  }
  const adapterRoot = resolve(destination, 'adapters/mirabox');
  cpSync(resolve(root, 'adapters/mirabox-rust/target/release/HellgatoMiraboxRustWorker.exe'), resolve(adapterRoot, 'HellgatoMiraboxRustWorker.exe'));
  for (const entry of readdirSync(vendor)) if (/\.dll$/i.test(entry)) cpSync(resolve(vendor, entry), resolve(adapterRoot, 'vendor', entry));
  const target = { id: 'streamdeck-plus-ten-key', displays: [{ id: 'lcd', width: 800, height: 480, format: 'jpeg', partial: false }],
    geometry: { rows: 2, columns: 5, keyCount: 10, keyWidth: 120, keyHeight: 120, encoderCount: 4, touchWidth: 800, touchHeight: 100, productName: 'Stream Deck +' },
    config: { dockFirmwareVersion: '1.01.016', childFirmwareVersion: '2.00.026', productId: 132 } };
  writeJson(resolve(adapterRoot, 'adapter.json'), { schemaVersion: 1, kind: 'hellgato-frame-adapter', protocolVersion: 1,
    id: 'mirabox', name: 'Mirabox N4 Pro', version: '0.1.0', license: 'MIT', platforms: ['win32'], architectures: ['x64'],
    entry: { command: 'HellgatoMiraboxRustWorker.exe', args: ['--dll', '${root}/vendor/transport.dll'], fakeArgs: ['--fake'] },
    files: inventory(adapterRoot), hostTarget: target });
  const compatibility = [];
  for (const model of ['n4', '293s']) {
    const folder = resolve(destination, 'adapters', 'mirabox-' + model + '-beta'); mkdirSync(folder, { recursive: true });
    cpSync(resolve(adapterRoot, 'HellgatoMiraboxRustWorker.exe'), resolve(folder, 'HellgatoMiraboxRustWorker.exe'));
    cpSync(resolve(adapterRoot, 'vendor'), resolve(folder, 'vendor'), { recursive: true });
    const hostTarget = structuredClone(target), ts = model === '293s';
    if (ts) {
      Object.assign(hostTarget.geometry, { rows:3,columns:5,keyCount:15,keyWidth:72,keyHeight:72,encoderCount:0,touchWidth:0,touchHeight:0,productName:'Stream Deck MK.2' });
      hostTarget.id = 'streamdeck-mk2-293s'; hostTarget.config.productId = 128;
      hostTarget.displays[0].width = 532; hostTarget.displays[0].height = 262;
    }
    const manifest = 'adapters/mirabox-' + model + '-beta/adapter.json'; compatibility.push(manifest);
    writeJson(resolve(destination, manifest), { schemaVersion:1,kind:'hellgato-frame-adapter',protocolVersion:1,
      id:'mirabox-'+model+'-beta',name:ts?'293S / TS115 · 호환 베타':'N4 / TS10N4 · 호환 베타',version:'0.1.0',license:'MIT',platforms:['win32'],architectures:['x64'],
      beta:true,hardwareVerified:false,entry:{command:'HellgatoMiraboxRustWorker.exe',args:['--compat-model',model,'--dll','${root}/vendor/transport.dll'],fakeArgs:['--compat-model',model,'--fake']},files:inventory(folder),hostTarget });
  }
  const simulator = resolve(destination, 'adapters/simulator');
  cpSync(resolve(root, 'adapters/simulator/worker.mjs'), resolve(simulator, 'worker.mjs'));
  const simulatorTarget = structuredClone(target);
  Object.assign(simulatorTarget.geometry, { columns: 4, keyCount: 8, keyWidth: 96, keyHeight: 96, touchWidth: 600, touchHeight: 80 });
  simulatorTarget.displays[0].width = 600; simulatorTarget.displays[0].height = 360;
  simulatorTarget.id = 'eight-key-demo';
  writeJson(resolve(simulator, 'adapter.json'), { schemaVersion: 1, kind: 'hellgato-frame-adapter', protocolVersion: 1,
    id: 'hellgato-simulator', name: '8-key simulator', version: '0.1.0', license: 'MIT', platforms: ['win32', 'linux', 'darwin'], architectures: ['x64', 'arm64'],
    entry: { command: '@runtime/node', args: ['${root}/worker.mjs'], fakeArgs: ['${root}/worker.mjs'] },
    files: inventory(simulator), hostTarget: simulatorTarget });
  cpSync(resolve(root, 'host-integrations/streamdeck/windows/rust/target/release/HellgatoHostWorker.exe'), resolve(destination, 'native/HellgatoHostWorker.exe'));
  cpSync(nativeUi, resolve(destination, 'native/HellgatoNativeUiV2.dll'));
  if (withGui) cpSync(resolve(root, 'apps/stream-dog/rust/target/release/HellgatoStreamDog.exe'), resolve(destination, 'HellgatoStreamDog.exe'));
  cpSync(resolve(root, 'LICENSE'), resolve(destination, 'LICENSE'));
  cpSync(resolve(root, 'installer/THIRD-PARTY-NOTICES.txt'), resolve(destination, 'THIRD-PARTY-NOTICES.txt'));
  mkdirSync(resolve(destination,'tools'),{recursive:true});
  for(const name of ['setup-user.ps1','setup-rust-user.ps1']) cpSync(resolve(root,'installer',name),resolve(destination,'tools',name));
  for (const name of ['docs/adapters/frame-adapter-guide.md', 'docs/compatibility/stream-dog-rust-beta.md', 'docs/compatibility/hardware-testing.md']) {
    const output = resolve(destination, name);
    mkdirSync(dirname(output), { recursive: true });
    cpSync(resolve(root, name), output);
  }
  const executable = 'C:/Program Files/Elgato/StreamDeck/StreamDeck.exe';
  const startup = json(resolve(startupDirectory, 'HellgatoStartup.json'));
  requireValue(startup.owner === 'Hellgato.Startup.VERSION/1' && /^[a-f0-9]{64}$/.test(startup.executableSha256), 'Reviewed startup support metadata is invalid');
  for (const name of ['version.dll', 'HellgatoStartup.plan']) {
    const source = resolve(startupDirectory, name);
    requireValue(startup.files[name] === hash(readFileSync(source)), 'Startup support file changed outside its owner');
    cpSync(source, resolve(destination, 'native/startup', name));
  }
  writeJson(resolve(destination, 'runtime.json'), { schemaVersion: 1, node: 'node/node.exe', entry: 'runtime/frame-runtime.mjs',
    adapters: ['adapters/mirabox/adapter.json', ...compatibility, 'adapters/simulator/adapter.json'], hostWorker: 'native/HellgatoHostWorker.exe', nativeUi: 'native/HellgatoNativeUiV2.dll',
    startupProxy: 'native/startup/version.dll', startupPlan: 'native/startup/HellgatoStartup.plan',
    service: { apiPort: 5345, previewEnabled: false,
      host: { executable, executableSha256: startup.executableSha256 } } });
  const files = inventory(destination, new Set(['bundle-manifest.json']));
  requireValue(!Object.keys(files).some(name => /(?:python|\.py[cd]?$|\.venv|pyside)/i.test(name)), 'Python files cannot enter the new runtime bundle');
  writeJson(resolve(destination, 'bundle-manifest.json'), { schemaVersion: 1, kind: 'hellgato-stream-dog-rust', files });
  return { destination, files: Object.keys(files).length, bytes: Object.keys(files).reduce((total, name) => total + readFileSync(resolve(destination, name)).length, 0) };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2); const destination = args[args.indexOf('--destination') + 1];
  requireValue(args.includes('--destination'), '--destination is required');
  const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : undefined;
  console.log(JSON.stringify(stageStreamDogRust({ destination, withGui: !args.includes('--without-gui'),
    ...(option('--startup-directory') ? { startupDirectory: option('--startup-directory') } : {}),
    ...(option('--vendor') ? { vendor: option('--vendor') } : {}) })));
}

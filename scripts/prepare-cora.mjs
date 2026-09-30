import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, existsSync, copyFileSync } from 'node:fs';
import { resolve, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripTypeScriptTypes } from 'node:module';

const root = fileURLToPath(new URL('../', import.meta.url));
const checkout = resolve(root, 'work/references/deckbridge');
const revision = '7c00693d01ac6c47c071907835ed0724d28d8183';
if (!existsSync(checkout)) {
  execFileSync('git', ['clone', '--no-checkout', 'https://github.com/lukasMega/DeckBridge.git', checkout], { stdio: 'inherit' });
  execFileSync('git', ['-C', checkout, 'checkout', '--detach', revision], { stdio: 'inherit' });
}
const actual = execFileSync('git', ['-C', checkout, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
if (actual !== revision || execFileSync('git', ['-C', checkout, 'status', '--porcelain'], { encoding: 'utf8' }).trim()) {
  throw new Error(`Reference must be clean at ${revision}; found ${actual}. No checkout was changed.`);
}
const sourceRoot = resolve(checkout, 'ts/src');
const outputRoot = resolve(root, 'work/cora-runtime');
const visited = new Set();
function compile(file) {
  if (visited.has(file)) return;
  visited.add(file);
  const path = relative(sourceRoot, file).replaceAll('\\', '/');
  let source;
  if (path === 'platform/tcp.ts') {
    source = "export * from 'node:net';\n";
  } else if (path === 'infra/mdns-advertiser.ts') {
    source = "export class MdnsAdvertiser { constructor() { throw new Error('Hellgato uses manual pairing; mDNS is disabled'); } }\n";
  } else {
    source = stripTypeScriptTypes(readFileSync(file, 'utf8'), { mode: 'transform' });
    source = source.replaceAll('__LOG_LEVEL__', '1');
    if (path === 'shared/types.ts') {
      source = source.replace("return (typeof tjs !== 'undefined' ? tjs.env['DECKBRIDGE_BIND'] : undefined) ?? '0.0.0.0';", "return '127.0.0.1';");
      if (!source.includes("return '127.0.0.1';")) throw new Error('Loopback binding adaptation failed');
    }
    source = source.replace(/(from\s+['"])(\.[^'"]+)(['"])/g, (_, prefix, specifier, suffix) => {
      const dependency = resolve(dirname(file), specifier.replace(/\.js$/, '.ts'));
      compile(dependency);
      return prefix + specifier.replace(/\.ts$/, '.js') + suffix;
    });
  }
  const output = resolve(outputRoot, path.replace(/\.ts$/, '.js'));
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, source);
}
for (const file of ['cora/primary-server.ts', 'cora/child-server.ts', 'cora/responses.ts', 'cora/plus-reports.ts', 'cora/frame.ts']) {
  compile(resolve(sourceRoot, file));
}
copyFileSync(resolve(checkout, 'LICENSE'), resolve(outputRoot, 'LICENSE'));
writeFileSync(resolve(outputRoot, 'package.json'), JSON.stringify({ type: 'module' }));
writeFileSync(resolve(outputRoot, 'provenance.json'), JSON.stringify({ repository: 'https://github.com/lukasMega/DeckBridge', revision, modules: [...visited].map(p => relative(sourceRoot, p)).sort(), adaptations: ['Node TCP transport', 'loopback binding only', 'manual pairing only; mDNS disabled', 'log level info', 'TypeScript transformed by Node'] }, null, 2));
console.log(`Prepared ${visited.size} modules from ${revision}`);

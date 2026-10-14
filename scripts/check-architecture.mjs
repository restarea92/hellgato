import { readFileSync, readdirSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { invariant } from './npm-release.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    if (['vendor', 'node_modules', '__pycache__'].includes(entry.name)) return [];
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? files(path) : [path];
  });
}
export function checkArchitecture() {
  for (const path of files(resolve(root, 'packages/core/src'))) {
    if (!/\.(?:mjs|ts|mts)$/.test(path)) continue;
    const source = readFileSync(path, 'utf8');
    const name = relative(root, path);
    invariant(!/(?:from\s*|import\s*\()\s*['"][^'"]*(?:app\/|apps\/|adapters\/|work\/|host-integrations\/|hellgato\/)/.test(source), `Forbidden reverse import in ${name}`);
    invariant(!/\b(?:node:child_process|node:fs)\b/.test(source), `Core must not own processes or persistence: ${name}`);
  }
  const build = readFileSync(resolve(root, 'scripts/build-npm.mjs'), 'utf8');
  invariant(!build.includes('app/cora/'), 'npm build must not copy or rewrite app sources');
  for (const path of files(resolve(root, 'apps/stream-dog/node'))) {
    if (!path.endsWith('.mjs')) continue;
    const source = readFileSync(path, 'utf8');
    invariant(!/(?:packages\/core|packages\/hellgato|@hellgato\/core\/dist|hellgato\/src)/.test(source), 'Product must use public SDK exports');
  }
  const spec = readFileSync(resolve(root, 'installer/hellgato.spec'), 'utf8');
  invariant(!spec.includes("include_tree('app/cora'") && !spec.includes("include_tree('work/cora-runtime'"), 'Installer cannot bundle copied core sources');
  invariant(spec.includes('work/stream-dog-sdk'), 'Installer must consume staged SDK artifacts');
  invariant(!spec.includes('build-native-ui-test') && !spec.includes('/fixture/'), 'Native test executables cannot ship in the product');
  return true;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  checkArchitecture();
  console.log('Architecture boundaries verified');
}

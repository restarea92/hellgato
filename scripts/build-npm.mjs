import { cpSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { assertSources } from './npm-release.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const core = resolve(root, 'packages/core');
const facade = resolve(root, 'packages/hellgato');
const output = resolve(core, 'dist');
const [coreManifest] = assertSources(root);
// Remove only generated build output so stale modules cannot enter a new pack.
rmSync(output, { recursive: true, force: true });
rmSync(resolve(root, 'work/cora-runtime'), { recursive: true, force: true });
await import('./prepare-cora.mjs');
mkdirSync(output, { recursive: true });
const source = readFileSync(resolve(root, 'app/cora/bridge.mjs'), 'utf8');
if (!source.includes("'../../work/cora-runtime/'")) throw new Error('Runtime import location changed');
writeFileSync(resolve(output, 'bridge.mjs'), source.replace("'../../work/cora-runtime/'", "'./vendor/'"));
cpSync(resolve(root, 'app/cora/profiles.mjs'), resolve(output, 'profiles.mjs'));
cpSync(resolve(root, 'app/cora/display-server.mjs'), resolve(output, 'display-server.mjs'));
cpSync(resolve(root, 'app/cora/firmware-policy.mjs'), resolve(output, 'firmware-policy.mjs'));
cpSync(resolve(root, 'work/cora-runtime'), resolve(output, 'vendor'), { recursive: true });
for (const directory of [core, facade]) cpSync(resolve(root, 'LICENSE'), resolve(directory, 'LICENSE'));
writeFileSync(resolve(core, 'THIRD-PARTY-NOTICES.txt'),
  'Includes DeckBridge (MIT), copyright 2026 Lukas.\n' +
  'Source: https://github.com/lukasMega/DeckBridge\n' +
  'Revision: 7c00693d01ac6c47c071907835ed0724d28d8183\n' +
  'License: dist/vendor/LICENSE\n' +
  'Adaptations and included modules: dist/vendor/provenance.json\n');
console.log(`Built hellgato and @hellgato/core ${coreManifest.version}`);

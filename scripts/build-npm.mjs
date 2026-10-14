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
// One canonical source tree; relative imports are identical in source and pack.
cpSync(resolve(core, 'src'), output, { recursive: true });
for (const directory of [core, facade]) cpSync(resolve(root, 'LICENSE'), resolve(directory, 'LICENSE'));
writeFileSync(resolve(core, 'THIRD-PARTY-NOTICES.txt'),
  'Includes DeckBridge (MIT), copyright 2026 Lukas.\n' +
  'Source: https://github.com/lukasMega/DeckBridge\n' +
  'Revision: 7c00693d01ac6c47c071907835ed0724d28d8183\n' +
  'License: dist/vendor/LICENSE\n' +
  'Adaptations and included modules: dist/vendor/provenance.json\n');
console.log(`Built hellgato and @hellgato/core ${coreManifest.version}`);

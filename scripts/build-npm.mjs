import { cpSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { assertSources, selectedNpm, consumerSdkSource, sourceCiSdk, invariant } from './npm-release.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const core = resolve(root, 'packages/core');
const facade = resolve(root, 'packages/hellgato');
const output = resolve(core, 'dist');
const args = process.argv.slice(2);
invariant(args.length === 0 || args.length === 2 && args[0] === '--source-channel' && args[1] === 'beta' || args.length === 2 && ['--candidate-plan', '--plan', '--consumer-plan'].includes(args[0]) && resolve(args[1]) === resolve(root, args[0] !== '--candidate-plan' ? '.release/plan.json' : '.release/candidate-plan.json'), 'Expected no build options, --plan .release/plan.json, or --candidate-plan .release/candidate-plan.json');
let channel = 'stable';
if (args.length) {
  const headSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  if (args[0] === '--source-channel') channel = sourceCiSdk(root).channel;
  else if (args[0] !== '--candidate-plan') {
    const { validateReleasePlan } = await import('./release-version.mjs');
    const plan = await validateReleasePlan({ root, headSha });
    channel = args[0] === '--consumer-plan' ? consumerSdkSource(resolve(args[1]), headSha).channel : selectedNpm(plan, { channel: plan.channel || 'stable' }).channel;
  } else {
    const { validateCandidatePlan } = await import('./release-candidate.mjs');
    const plan = await validateCandidatePlan({ root, headSha });
    channel = selectedNpm(plan, { candidate: true }).channel;
  }
}
const [coreManifest] = assertSources(root, { channel });
const { buildTypeScript, SDK_OUTPUTS } = await import('./build-typescript.mjs');
buildTypeScript();
// Remove only generated build output so stale modules cannot enter a new pack.
rmSync(output, { recursive: true, force: true });
rmSync(resolve(root, 'work/cora-runtime'), { recursive: true, force: true });
await import('./prepare-cora.mjs');
mkdirSync(output, { recursive: true });
// One canonical source tree; relative imports are identical in source and pack.
const publicDeclarations = new Set(SDK_OUTPUTS.map(([, path]) => resolve(root, path)).filter(path => /\.d\.mts$/.test(path)));
cpSync(resolve(core, 'src'), output, { recursive: true, filter: path => !/\.(?:ts|mts)$/.test(path) || publicDeclarations.has(path) });
for (const directory of [core, facade]) cpSync(resolve(root, 'LICENSE'), resolve(directory, 'LICENSE'));
writeFileSync(resolve(core, 'THIRD-PARTY-NOTICES.txt'),
  'Includes DeckBridge (MIT), copyright 2026 Lukas.\n' +
  'Source: https://github.com/lukasMega/DeckBridge\n' +
  'Revision: 7c00693d01ac6c47c071907835ed0724d28d8183\n' +
  'License: dist/vendor/LICENSE\n' +
  'Adaptations and included modules: dist/vendor/provenance.json\n');
console.log(`Built hellgato and @hellgato/core ${coreManifest.version}`);

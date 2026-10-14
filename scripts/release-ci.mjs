// Read-only build context. A lingering plan on an ordinary source PR never
// becomes release intent. Public approval remains solely in release.mjs.
import { existsSync, readFileSync } from 'node:fs';
import { appendFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, invariant } from './release-lib.mjs';
import { channelPolicy, validatePrereleaseState } from './release-policy.mjs';

function planBuildContext(plan, root) {
  const windows = plan.components.some(component => component.component === 'windows');
  const betaSource = existsSync(resolve(root, '.changeset/pre.json'));
  // An npm-only version PR still builds Windows as a consumer. The unrelated
  // product metadata must not be interpreted as a selected Windows release.
  if (!windows && betaSource) validatePrereleaseState(JSON.parse(readFileSync(resolve(root, '.changeset/pre.json'), 'utf8')));
  return { sdkMode: plan.components.some(component => component.component === 'npm') ? 'plan' : 'consumer', metadataMode: windows ? 'plan' : betaSource ? 'source-beta' : 'stable' };
}

export async function releaseBuildContext({ root = ROOT, environment = process.env } = {}) {
  const head = environment.PR_HEAD_REF || environment.GITHUB_REF_NAME;
  const base = environment.PR_BASE_REF || (head === 'changeset-release/next' ? 'next' : head === 'changeset-release/main' ? 'main' : head);
  if (environment.RELEASE_BUILD_PLAN) {
    invariant(environment.RELEASE_BUILD_PLAN === '.release/plan.json', 'Explicit build plan must be the checkout release plan');
    const plan = JSON.parse(await readFile(resolve(root, '.release/plan.json'), 'utf8'));
    const channel = plan.schemaVersion === 3 ? plan.channel : 'stable';
    invariant([1, 3].includes(plan.schemaVersion) && plan.repository === 'restarea92/hellgato' &&
      Array.isArray(plan.components) && plan.components.length > 0 &&
      [channelPolicy(channel).branch, channelPolicy(channel).releaseBranch].includes(head), 'Explicit release build branch differs from its plan');
    return planBuildContext(plan, root);
  }
  if (['changeset-release/main', 'changeset-release/next'].includes(head)) {
    const plan = JSON.parse(await readFile(resolve(root, '.release/plan.json'), 'utf8'));
    const channel = plan.schemaVersion === 3 ? plan.channel : 'stable';
    invariant([1, 3].includes(plan.schemaVersion) && plan.repository === 'restarea92/hellgato' && head === channelPolicy(channel).releaseBranch && base === channelPolicy(channel).branch, 'Release PR build context differs from its plan branch');
    invariant(Array.isArray(plan.components) && plan.components.length > 0, 'Missing release build component selection');
    return planBuildContext(plan, root);
  }
  // Main may temporarily carry the verified beta handoff before pre-exit.
  // This grants only read-only builds; impact/release validators own approval.
  if (['next', 'main'].includes(base) && existsSync(resolve(root, '.changeset/pre.json'))) {
    validatePrereleaseState(JSON.parse(await readFile(resolve(root, '.changeset/pre.json'), 'utf8')));
    return { sdkMode: 'source-beta', metadataMode: 'source-beta' };
  }
  return { sdkMode: 'stable', metadataMode: 'stable' };
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const context = await releaseBuildContext();
  if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, `sdk_mode=${context.sdkMode}\nmetadata_mode=${context.metadataMode}\n`);
  else console.log(JSON.stringify(context));
}

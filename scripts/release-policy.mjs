// Code-owned channel policy. Candidate support does not enable publication.
export const STABLE_VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export const BETA_VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.(0|[1-9]\d*)$/;
export const CANDIDATE_SCHEMA = 2;
export const CHANNEL_PLAN_SCHEMA = 3;
export const NPM_BASELINE_PATH = '.release/npm-public-baseline.json';
export const PUBLIC_BASELINE_PATH = '.release/windows-public-baseline.json';
const require = (condition, message) => { if (!condition) throw new Error(message); };
const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());

export function releaseChannel(version) {
  if (typeof version === 'string' && STABLE_VERSION.test(version)) return 'stable';
  if (typeof version === 'string' && BETA_VERSION.test(version)) return 'beta';
  throw new Error('Expected canonical stable or beta.N SemVer');
}
export function compareReleaseVersions(left, right) {
  releaseChannel(left); releaseChannel(right);
  const parts = value => value.split(/[.-]/).map((part, index) => index === 3 ? part : BigInt(part));
  const a = parts(left), b = parts(right);
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  if (a.length === 3 || b.length === 3) return a.length === b.length ? 0 : a.length === 3 ? 1 : -1;
  return a[4] === b[4] ? 0 : a[4] < b[4] ? -1 : 1;
}
export function channelPolicy(channel) {
  require(channel === 'stable' || channel === 'beta', 'Only stable and beta channels are supported');
  return channel === 'stable'
    ? { branch: 'main', releaseBranch: 'changeset-release/main', distTag: 'latest', prerelease: false }
    : { branch: 'next', releaseBranch: 'changeset-release/next', distTag: 'beta', prerelease: true };
}
export function windowsNumericVersion(version) {
  const channel = releaseChannel(version);
  const match = (channel === 'stable' ? STABLE_VERSION : BETA_VERSION).exec(version);
  const core = match.slice(1, 4).map(BigInt);
  require(core.every(part => part <= 65535n), 'Windows version fields must fit unsigned 16-bit integers');
  const revision = channel === 'stable' ? 65535n : BigInt(match[4]) + 1n;
  require(revision < 65535n || channel === 'stable', 'Windows beta sequence must be between 0 and 65533');
  return [...core, revision].join('.');
}
export function validatePrereleaseState(state) {
  // Pinned Changesets 3.0.3 stores consumed inputs in .changeset/pre/*.md.
  // Its pre.json no longer contains v2 initialVersions/changesets fields.
  require(keys(state, ['mode', 'tag']) && state.mode === 'pre' && state.tag === 'beta', 'Only active Changesets 3 beta prerelease state is supported');
  return state;
}
export function validatePublicBaseline(record, sourceVersion, targetVersion) {
  require(keys(record, ['schemaVersion', 'component', 'channel', 'sourceVersion', 'version', 'tag', 'releaseId', 'releaseSha', 'installerSha256', 'manifestSha256', 'reason']), 'Unexpected Windows public baseline fields');
  require(record.schemaVersion === 1 && record.component === 'windows' && record.channel === 'stable', 'Expected a stable Windows public baseline');
  require([record.sourceVersion, record.version, sourceVersion, targetVersion].every(version => typeof version === 'string' && STABLE_VERSION.test(version)), 'Public baseline/source/target must be stable versions');
  require(record.sourceVersion === sourceVersion && compareReleaseVersions(record.version, sourceVersion) <= 0 && compareReleaseVersions(sourceVersion, targetVersion) < 0, 'Public baseline must bind the exact source and a strictly increasing Changesets target');
  require(record.tag === `v${record.version}` && Number.isSafeInteger(record.releaseId) && record.releaseId > 0 && /^[a-f0-9]{40}$/.test(record.releaseSha), 'Public baseline requires exact release/tag/source identity');
  require(/^[a-f0-9]{64}$/.test(record.installerSha256) && (/^[a-f0-9]{64}$/.test(record.manifestSha256) || record.manifestSha256 === null && ['0.1.0', '0.1.1'].includes(record.version)), 'Public baseline requires exact artifact digests; only legacy releases may omit a manifest');
  require(typeof record.reason === 'string' && record.reason.trim().length >= 30, 'Public baseline requires a reviewable reason');
  return record;
}

// A release plan records public dist-tags independently from prepared source
// versions. These are read-only observations until an owner merges the exact PR.
export function validateNpmBaseline(record, sourceVersion, targetVersion, channel) {
  require(keys(record, ['schemaVersion', 'component', 'sourceVersion', 'packages', 'reason']) && record.schemaVersion === 1 && record.component === 'npm' && record.sourceVersion === sourceVersion, 'Invalid npm public baseline identity/source');
  require(releaseChannel(targetVersion) === channel && compareReleaseVersions(sourceVersion, targetVersion) < 0, 'npm candidate must increase from its exact source');
  require(typeof record.reason === 'string' && record.reason.trim().length >= 30, 'npm baseline requires a reviewable reason');
  require(Array.isArray(record.packages) && record.packages.length === 2, 'npm baseline must name the fixed pair');
  for (const [index, name] of ['@hellgato/core', 'hellgato'].entries()) {
    const entry = record.packages[index];
    require(keys(entry, ['name', 'latest', 'beta']) && entry.name === name, 'npm baseline fixed package order mismatch');
    for (const tag of ['latest', 'beta']) if (entry[tag] !== null) {
      const value = entry[tag];
      require(keys(value, ['version', 'integrity']) && (value.version === '0.0.1-beta' || (() => { try { releaseChannel(value.version); return true; } catch { return false; } })()) && /^sha512-[A-Za-z0-9+/]{86}==$/.test(value.integrity), 'npm public tag requires exact version and integrity');
      require(compareReleaseVersions(value.version === '0.0.1-beta' ? '0.0.1-beta.0' : value.version, targetVersion) < 0, 'npm release must exceed both reviewed public tags');
    }
  }
  return record;
}
export function validateChannelWindowsBaseline(record, sourceVersion, targetVersion) {
  require(keys(record, ['schemaVersion', 'component', 'channel', 'sourceVersion', 'version', 'tag', 'releaseId', 'releaseSha', 'installerSha256', 'manifestSha256', 'reason']), 'Unexpected Windows public baseline fields');
  require(record.schemaVersion === 1 && record.component === 'windows' && record.sourceVersion === sourceVersion && releaseChannel(record.version) === record.channel, 'Invalid Windows baseline identity/source/channel');
  require(compareReleaseVersions(record.version, sourceVersion) <= 0 && compareReleaseVersions(sourceVersion, targetVersion) < 0, 'Windows baseline must not exceed source and target must increase');
  require(record.tag === `v${record.version}` && Number.isSafeInteger(record.releaseId) && record.releaseId > 0 && /^[a-f0-9]{40}$/.test(record.releaseSha) && /^[a-f0-9]{64}$/.test(record.installerSha256), 'Windows baseline requires exact public identity/digest');
  require(/^[a-f0-9]{64}$/.test(record.manifestSha256) || record.manifestSha256 === null && record.channel === 'stable' && ['0.1.0', '0.1.1'].includes(record.version), 'Windows baseline requires manifest provenance');
  require(typeof record.reason === 'string' && record.reason.trim().length >= 30, 'Windows baseline requires a reviewable reason');
  return record;
}

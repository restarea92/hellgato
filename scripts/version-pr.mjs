// Narrow GitHub helpers for the version-only action. No tags or releases.
import { appendFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const REPOSITORY = 'restarea92/hellgato';
const PREFIX = 'This PR was opened by the [Changesets release](https://github.com/changesets/action) GitHub action.';
const INTRO = 'This PR prepares package and Windows app versions and changelogs for review. Merging prepares an unpublished Windows installer draft; npm publishing remains deferred.';
const MARKER = '<!-- hellgato-version-build -->';
const require = (value, message) => { if (!value) throw new Error(message); };

export async function assertCurrentVersionBase(sha, api) {
  require(/^[a-f0-9]{40}$/.test(sha ?? ''), 'Exact version workflow source is required');
  const main = await api('git/ref/heads/main');
  require(main.object?.type === 'commit' && main.object.sha === sha, 'Stale version workflow: main has advanced; use the latest main run');
}

export function versionPullRequest(pr) {
  require(pr.state === 'open' && pr.base?.ref === 'main' && pr.base.repo.full_name === REPOSITORY
    && pr.head?.ref === 'changeset-release/main' && pr.head.repo.full_name === REPOSITORY
    && /^[a-f0-9]{40}$/.test(pr.head.sha), 'Unexpected version PR identity');
  return pr.head.sha;
}

export function versionPullRequestBody(body) {
  require(typeof body === 'string' && (body.startsWith(PREFIX) || body.startsWith(INTRO)), 'Unexpected Changesets PR template; preserve it for review');
  const newline = body.indexOf('\n');
  require(newline >= 0, 'Missing Changesets changelog body');
  // v2.1.2/version assumes a publish script in its standard introduction.
  // Replace only that line; preserve all generated changelogs byte-for-byte.
  return INTRO + body.slice(newline);
}

export async function prepareVersionPullRequest(number, api) {
  const pr = await api(`pulls/${number}`);
  const sha = versionPullRequest(pr);
  const body = versionPullRequestBody(pr.body);
  if (body !== pr.body) await api(`pulls/${number}`, { method: 'PATCH', body: { body } });
  return sha;
}

export async function reportVersionBuild({ number, sha, result, runUrl }, api) {
  require(/^[a-f0-9]{40}$/.test(sha) && ['success', 'failure', 'cancelled', 'skipped'].includes(result), 'Invalid version build result');
  require(new RegExp(`^https://github\\.com/${REPOSITORY}/actions/runs/[1-9][0-9]*(?:/attempts/[1-9][0-9]*)?$`).test(runUrl), 'Unexpected build run URL');
  const pr = await api(`pulls/${number}`);
  if (pr.state === 'closed') return false;
  if (versionPullRequest(pr) !== sha) return false; // A newer refresh owns its own result.
  const body = `${MARKER}\nWindows installer validation: **${result}** for commit \`${sha}\`.\n\n[Exact build run](${runUrl}). This run builds the version PR source; GitHub displays its workflow check on the main trigger commit. No release was published and no npm package was uploaded.\n`;
  let existing;
  for (let page = 1; page <= 10; page++) {
    const comments = await api(`issues/${number}/comments?per_page=100&page=${page}`);
    require(Array.isArray(comments), 'Invalid PR comment response');
    for (const comment of comments) if (comment.user?.login === 'github-actions[bot]' && comment.body?.startsWith(MARKER)) {
      require(!existing, 'Duplicate version build comments need review');
      existing = comment;
    }
    if (comments.length < 100) break;
    require(page < 10, 'Too many PR comments; refusing unbounded pagination');
  }
  if (existing?.body === body) return true;
  await api(existing ? `issues/comments/${existing.id}` : `issues/${number}/comments`, { method: existing ? 'PATCH' : 'POST', body: { body } });
  return true;
}

async function api(path, { method = 'GET', body } = {}) {
  require(process.env.GH_TOKEN, 'GH_TOKEN is required');
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}/${path}`, {
    method, headers: { Authorization: `Bearer ${process.env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(30000),
  });
  require(response.ok, `GitHub ${method} ${path} failed: HTTP ${response.status}`);
  return response.json();
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const command = process.argv[2];
    require(process.argv.length === 3, 'Use version-pr.mjs current|prepare|report');
    const number = process.env.VERSION_PR_NUMBER;
    if (command !== 'current') require(/^[1-9][0-9]*$/.test(number ?? ''), 'An exact version PR number is required');
    if (command === 'current') {
      await assertCurrentVersionBase(process.env.GITHUB_SHA, api);
    } else if (command === 'prepare') {
      // Do not build a stale refresh if main moved while the action was running.
      await assertCurrentVersionBase(process.env.GITHUB_SHA, api);
      const sha = await prepareVersionPullRequest(number, api);
      require(process.env.GITHUB_OUTPUT, 'GITHUB_OUTPUT is required');
      await appendFile(process.env.GITHUB_OUTPUT, `source_sha=${sha}\n`);
    } else if (command === 'report') {
      await reportVersionBuild({ number, sha: process.env.VERSION_SOURCE_SHA, result: process.env.BUILD_RESULT,
        runUrl: `https://github.com/${REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}/attempts/${process.env.GITHUB_RUN_ATTEMPT}` }, api);
    } else throw new Error('Use version-pr.mjs current|prepare|report');
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}

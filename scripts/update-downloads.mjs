// Called only after public Windows bytes have been confirmed by the component resolver.
// Uses a narrow docs-only PR; never updates source versions or merges automatically.
import { execFileSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { github, pages, REPOSITORY } from './release.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const invariant = (value, message) => { if (!value) throw new Error(message); };
const stable = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const encode = value => JSON.stringify(value, null, 2) + '\n';

export function downloadFiles(readme, latest, baseSha) {
  invariant(latest && stable.test(latest.version) && latest.tag === `v${latest.version}`, 'No confirmed Windows stable selected');
  const prefix = `https://github.com/${REPOSITORY}/releases`;
  invariant(latest.url === `${prefix}/tag/${latest.tag}` && latest.installerUrl === `${prefix}/download/${latest.tag}/Hellgato-${latest.version}-Setup.exe` && latest.checksumUrl === `${latest.installerUrl}.sha256`, 'Unexpected public Windows URLs');
  invariant(/^[a-f0-9]{40}$/.test(baseSha), 'Documentation update requires exact main SHA');
  const link = /https:\/\/github\.com\/restarea92\/hellgato\/releases\/download\/v\d+\.\d+\.\d+\/Hellgato-\d+\.\d+\.\d+-Setup\.exe/g;
  invariant((readme.match(link) || []).length === 2 && (readme.match(/\*\*Version:\*\* `\d+\.\d+\.\d+`/g) || []).length === 1, 'README download markers changed; review required');
  const updated = readme.replace(link, latest.installerUrl).replace(/\*\*Version:\*\* `\d+\.\d+\.\d+`/, `**Version:** \`${latest.version}\``);
  const files = {
    'README.md': updated,
    '.release/published-windows.json': encode({ schemaVersion: 1, component: 'windows', version: latest.version, tag: latest.tag, releaseUrl: latest.url, installerUrl: latest.installerUrl, checksumUrl: latest.checksumUrl }),
  };
  files[`.release/no-release/windows-download-${latest.version.replaceAll('.', '-')}.json`] = encode({ schemaVersion: 1, baseSha, reason: 'Update only the public download documentation after the Windows component resolver verified the published installer, checksum and release provenance. No packaged source or release intent changes.', paths: Object.keys(files) });
  return files;
}

export async function createDownloadPullRequest({ latest, api = github }) {
  const branch = `docs/windows-download-${latest.version.replaceAll('.', '-')}`;
  const marker = `<!-- hellgato-downloads:${latest.tag} -->`;
  const prs = await pages(`pulls?state=all&head=restarea92:${branch}`, api);
  if (prs.length) {
    invariant(prs.length === 1 && prs[0].body?.includes(marker) && prs[0].head?.repo?.full_name === REPOSITORY, 'An unknown documentation PR already owns this branch');
    return prs[0].html_url;
  }
  const head = await api('git/ref/heads/main');
  const baseSha = head.object?.sha;
  const original = await api(`contents/README.md?ref=${baseSha}`);
  invariant(original.encoding === 'base64' && typeof original.content === 'string', 'Cannot read current README');
  const readme = Buffer.from(original.content.replace(/\n/g, ''), 'base64').toString('utf8');
  const files = downloadFiles(readme, latest, baseSha);
  const base = await api(`git/commits/${baseSha}`);
  const tree = await api('git/trees', { method: 'POST', body: { base_tree: base.tree.sha, tree: Object.entries(files).map(([path, content]) => ({ path, mode: '100644', type: 'blob', content })) } });
  let ref = await api(`git/ref/heads/${branch}`, { missing: true });
  if (ref) {
    const existing = await api(`git/commits/${ref.object.sha}`);
    invariant(existing.tree.sha === tree.sha && existing.parents.length === 1 && existing.parents[0].sha === baseSha, 'Existing docs branch differs; never overwrite unknown work');
  } else {
    const commit = await api('git/commits', { method: 'POST', body: { message: `docs: point Windows downloads to confirmed ${latest.tag}`, tree: tree.sha, parents: [baseSha] } });
    ref = await api('git/refs', { method: 'POST', body: { ref: `refs/heads/${branch}`, sha: commit.sha } });
  }
  const pr = await api('pulls', { method: 'POST', body: { title: `docs: update confirmed Windows download to ${latest.version}`, head: branch, base: 'main', draft: true, body: `Windows ${latest.version} is published and its installer/checksum were independently verified by the component-aware resolver.\n\n${latest.url}\n\nThis PR updates only README download links, the confirmed publication record and a scoped no-release explanation. It does not change versions, source, workflows or release intent. No automatic merge is enabled.\n\n${marker}` } });
  return pr.html_url;
}

async function main() {
  invariant(process.env.RELEASE_ENABLED === 'true', 'Publication setup has not been enabled');
  const latest = JSON.parse(execFileSync('python', ['scripts/windows_release.py', 'resolve', '--json'], { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }));
  invariant(latest, 'No complete public Windows release exists');
  console.log(await createDownloadPullRequest({ latest }));
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main().catch(error => { console.error(`Download documentation refused: ${error.message}`); process.exitCode = 1; });

import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = new URL('./', import.meta.url);
const output = new URL('dist/', root);
const { version } = JSON.parse(await readFile(new URL('release.json', root), 'utf8'));
if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Invalid Windows release version');
const repo = 'https://github.com/restarea92/hellgato';
const values = { VERSION: version, REPO_URL: repo, RELEASE_URL: `${repo}/releases/tag/v${version}`,
  INSTALLER_URL: `${repo}/releases/download/v${version}/Hellgato-${version}-Setup.exe` };
const template = await readFile(new URL('index.html', root), 'utf8');
const html = template.replace(/\{\{([A-Z_]+)\}\}/g, (_, key) => {
  if (!(key in values)) throw new Error(`Unknown template value: ${key}`);
  return values[key];
});
await mkdir(output, { recursive: true });
await writeFile(new URL('index.html', output), html);
for (const name of ['style.css', 'assets']) {
  await cp(new URL(name, root), new URL(name, output), { recursive: true });
}
await cp(new URL('../../docs/visuals/hellgato-network.html', root), new URL('architecture.html', output));
for (const suffix of ['gif', 'png']) await cp(new URL(`../../docs/visuals/hellgato-network.${suffix}`, root), new URL(`assets/architecture.${suffix}`, output));
console.log(`Built Hellgato website ${version}: ${fileURLToPath(output)}`);

"""Fetch pinned runtime inputs for the Windows beta build."""

import hashlib
import json
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SDK_REVISION = '87ff56674cb3772dc2a13376d174c33c37964607'
NODE_VERSION = '24.19.0'


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Hellgato-build'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main():
    sdk = ROOT / 'work/references/mirabox'
    sdk.mkdir(parents=True, exist_ok=True)
    tree = json.loads(fetch(f'https://api.github.com/repos/MiraboxSpace/StreamDock-Device-SDK/git/trees/{SDK_REVISION}?recursive=1'))['tree']
    for item in tree:
        path = item['path']
        selected = path == 'LICENSE' or (path.startswith('Python-SDK/src/StreamDock/') and path.endswith(('.py', '.dll')))
        if item['type'] != 'blob' or not selected:
            continue
        target = sdk / path
        content = target.read_bytes() if target.exists() else fetch(f'https://raw.githubusercontent.com/MiraboxSpace/StreamDock-Device-SDK/{SDK_REVISION}/{path}')
        digest = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        if digest != item['sha']:
            raise RuntimeError(f'SDK differs from pinned source: {path}; local file was preserved')
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    (sdk / 'revision.txt').write_text(SDK_REVISION)
    runtime = ROOT / 'work/build-runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    base = f'https://nodejs.org/dist/v{NODE_VERSION}'
    checksums = fetch(f'{base}/SHASUMS256.txt').decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.endswith('  win-x64/node.exe'))
    node = runtime / 'node.exe'
    content = node.read_bytes() if node.exists() else fetch(f'{base}/win-x64/node.exe')
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError('Node executable checksum mismatch')
    node.write_bytes(content)
    (runtime / 'NODE-LICENSE.txt').write_bytes(fetch(f'https://raw.githubusercontent.com/nodejs/node/v{NODE_VERSION}/LICENSE'))
    (runtime / 'provenance.json').write_text(json.dumps({'nodeVersion': NODE_VERSION, 'nodeSha256': expected, 'sdkRevision': SDK_REVISION}, indent=2))
    print(f'Prepared Node {NODE_VERSION} and Mirabox SDK {SDK_REVISION}')


if __name__ == '__main__':
    main()

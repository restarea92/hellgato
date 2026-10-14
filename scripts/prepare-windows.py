"""Fetch and verify pinned runtime inputs for the Windows product build.

SDK inventory is checked in. A cache hit performs no SDK network requests and a
miss only downloads the exact immutable raw blob, never a live Git tree.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SDK_REVISION = '87ff56674cb3772dc2a13376d174c33c37964607'
SDK_REPOSITORY = 'MiraboxSpace/StreamDock-Device-SDK'
SDK_MANIFEST = ROOT / 'vendor/mirabox-sdk/manifest.json'
NODE_VERSION = '24.19.0'


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Hellgato-build'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def sdk_manifest(path=SDK_MANIFEST):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise RuntimeError('SDK inventory must be an object')
    if (data.get('schemaVersion') != 1 or data.get('revision') != SDK_REVISION
            or data.get('repository') != SDK_REPOSITORY):
        raise RuntimeError('SDK inventory repository/revision/schema mismatch')
    files = data.get('files')
    if not isinstance(files, list) or not files or len(files) > 200:
        raise RuntimeError('Invalid SDK inventory')
    seen = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise RuntimeError('Invalid SDK inventory entry')
        name = item['path']
        path = PurePosixPath(name)
        if (not name or str(path) != name
                or path.is_absolute() or any(part in ('.', '..') for part in path.parts)
                or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)
                or not (name == 'LICENSE' or (name.startswith('Python-SDK/src/StreamDock/')
                                            and path.suffix in ('.py', '.dll')))
                or name.casefold() in seen):
            raise RuntimeError(f'Unsafe or duplicate SDK inventory path: {name}')
        seen.add(name.casefold())
        if (type(item.get('size')) is not int or not 0 <= item['size'] <= 16 * 1024 * 1024
                or not re.fullmatch(r'[a-f0-9]{40}', item.get('gitBlobSha1', ''))):
            raise RuntimeError(f'Invalid SDK inventory digest/size: {name}')
    required = {'license', 'python-sdk/src/streamdock/devicemanager.py',
                'python-sdk/src/streamdock/devices/streamdockn4pro.py',
                'python-sdk/src/streamdock/transport/transportdll/transport.dll'}
    if not required.issubset(seen):
        raise RuntimeError('SDK inventory omits required N4 runtime or license')
    return data


def regular_target(root, name):
    """Reject symlink/junction-like escapes and non-regular cache entries."""
    target = root / name
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RuntimeError('SDK cache root must be a real directory')
    for path in [target, *target.parents]:
        if path == root.parent:
            break
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise RuntimeError(f'SDK cache link is forbidden: {name}')
    if target.exists() and not target.is_file():
        raise RuntimeError(f'SDK cache entry must be a regular file: {name}')
    return target


def verified_sdk_bytes(content, item):
    digest = hashlib.sha1(b'blob ' + str(len(content)).encode('ascii') + b'\0' + content).hexdigest()
    if len(content) != item['size'] or digest != item['gitBlobSha1']:
        raise RuntimeError(f"SDK differs from pinned source: {item['path']}; local file was preserved")


def atomic_write(target, content):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_sdk(destination, *, manifest_path=SDK_MANIFEST, fetcher=fetch, offline=False):
    inventory = sdk_manifest(manifest_path)
    sdk = Path(destination)
    regular_target(sdk, 'revision.txt')
    allowed = {item['path'] for item in inventory['files']} | {'revision.txt'}
    # Never let stale/unreviewed Python or DLL files enter a product build. Python
    # bytecode caches are runtime-generated and not inputs to the frozen build.
    if sdk.exists():
        for path in sdk.rglob('*'):
            relative = path.relative_to(sdk)
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise RuntimeError(f'SDK cache link is forbidden: {relative.as_posix()}')
            if path.is_file() and relative.as_posix() not in allowed:
                if '__pycache__' in relative.parts and path.suffix == '.pyc':
                    continue
                raise RuntimeError(f'Unexpected SDK cache file: {relative.as_posix()}')
    for item in inventory['files']:
        target = regular_target(sdk, item['path'])
        if target.exists():
            content = target.read_bytes()
        else:
            if offline:
                raise RuntimeError(f"Missing pinned SDK file in offline mode: {item['path']}")
            content = fetcher(f"https://raw.githubusercontent.com/{SDK_REPOSITORY}/{SDK_REVISION}/{item['path']}")
        verified_sdk_bytes(content, item)
        if not target.exists():
            atomic_write(target, content)
    atomic_write(sdk / 'revision.txt', SDK_REVISION.encode('ascii'))
    return {'sdkRevision': SDK_REVISION,
            'sdkInventorySha256': hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
            'sdkFileCount': len(inventory['files'])}


def main():
    sdk_provenance = prepare_sdk(ROOT / 'work/references/mirabox')
    runtime = ROOT / 'work/build-runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    base = f'https://nodejs.org/dist/v{NODE_VERSION}'
    checksums = fetch(f'{base}/SHASUMS256.txt').decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.endswith('  win-x64/node.exe'))
    node = runtime / 'node.exe'
    content = node.read_bytes() if node.exists() else fetch(f'{base}/win-x64/node.exe')
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError('Node executable checksum mismatch')
    atomic_write(node, content)
    atomic_write(runtime / 'NODE-LICENSE.txt', fetch(f'https://raw.githubusercontent.com/nodejs/node/v{NODE_VERSION}/LICENSE'))
    atomic_write(runtime / 'provenance.json', json.dumps({'nodeVersion': NODE_VERSION, 'nodeSha256': expected, **sdk_provenance}, indent=2).encode('utf-8'))
    print(f'Prepared Node {NODE_VERSION} and verified {sdk_provenance["sdkFileCount"]} Mirabox SDK files at {SDK_REVISION}')


if __name__ == '__main__':
    main()

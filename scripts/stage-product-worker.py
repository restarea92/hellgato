"""Describe the frozen USB worker bundled with the ordinary desktop app."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def stage(root=ROOT):
    worker = root / 'dist/HellgatoMiraboxWorker'
    executable = worker / 'HellgatoMiraboxWorker.exe'
    required = (executable,
                worker / '_internal/hellgato_mirabox/models/n4_pro/firmware-policy.json',
                worker / '_internal/vendor/mirabox/src/StreamDock/DeviceManager.py')
    if not all(path.is_file() for path in required):
        raise ValueError('The frozen Mirabox worker is incomplete')
    files = []
    for path in sorted(worker.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Worker runtime cannot contain filesystem links')
        if path.is_file() and path.name != 'runtime-manifest.json':
            content = path.read_bytes()
            files.append({'path': path.relative_to(worker).as_posix(), 'size': len(content),
                          'sha256': hashlib.sha256(content).hexdigest()})
    if not 1 <= len(files) <= 1024:
        raise ValueError('Worker runtime inventory exceeds its bound')
    identity = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest = {'schemaVersion': 1, 'platform': 'win32', 'arch': 'x64',
                'buildId': 'mirabox-' + identity,
                'executableSha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                'files': files}
    target = worker / 'runtime-manifest.json'
    target.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return target


if __name__ == '__main__':
    print(stage())

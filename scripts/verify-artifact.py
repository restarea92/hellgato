"""Verify immutable GitHub Actions artifact ZIP and downloaded local bytes.

Download helpers may only warn about digest mismatch. This stdlib verifier fails
closed, never extracts archive paths, and never accepts a missing/expired source.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

REPOSITORY = 'restarea92/hellgato'
_VERIFIED_ARCHIVES = set()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def archive_bytes(identifier):
    return subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/actions/artifacts/{identifier}/zip'],
                          check=True, capture_output=True).stdout


def verify_artifact(directory, artifact_id, digest, *, expected_names=None, fetch=archive_bytes):
    if isinstance(artifact_id, bool) or not re.fullmatch(r'[1-9][0-9]*', str(artifact_id)):
        raise ValueError('A positive original artifact ID is required')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('An exact original artifact archive SHA-256 is required')
    if Path(directory).is_symlink():
        raise ValueError('Unsafe downloaded artifact directory')
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Artifact directory is missing')
    paths = list(root.rglob('*')) if expected_names is None else [root / name for name in expected_names]
    expected = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe downloaded artifact path')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Missing downloaded artifact file')
        expected[path.relative_to(root).as_posix()] = path.read_bytes()
    if not expected or len(expected) != len(set(expected)):
        raise ValueError('Empty or duplicate downloaded artifact files')
    identity = (int(artifact_id), digest, tuple(sorted((name, sha256(data)) for name, data in expected.items())))
    if identity in _VERIFIED_ARCHIVES:
        return
    data = fetch(int(artifact_id))
    if not isinstance(data, bytes) or sha256(data) != digest:
        raise ValueError('Original Actions artifact archive SHA-256 mismatch')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise ValueError('Original artifact archive has duplicate members')
            files = []
            for member in members:
                name = member.filename
                parts = PurePosixPath(name).parts
                if (not name or name.startswith('/') or '\\' in name or ':' in name
                        or any(part in ('', '.', '..') for part in parts)
                        or stat.S_ISLNK(member.external_attr >> 16)
                        or member.flag_bits & 1):
                    raise ValueError('Original artifact archive has unsafe members')
                if member.is_dir():
                    if not any(expected_name.startswith(name) for expected_name in expected):
                        raise ValueError('Original artifact archive has unexpected directories')
                else:
                    files.append(member)
            if {member.filename for member in files} != set(expected):
                raise ValueError('Original artifact archive has unexpected or missing members')
            for member in files:
                content = expected[member.filename]
                if member.file_size != len(content) or archive.read(member) != content:
                    raise ValueError('Downloaded files differ from the original Actions artifact')
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError('Invalid original artifact archive') from error
    _VERIFIED_ARCHIVES.add(identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-id', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    try:
        verify_artifact(args.directory, args.artifact_id, args.sha256)
        print(json.dumps({'artifactId': int(args.artifact_id), 'sha256': args.sha256, 'verified': True}))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Artifact verification failed: {error}\n')


if __name__ == '__main__':
    main()

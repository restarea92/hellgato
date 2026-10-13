"""Windows release metadata and fail-closed publication checks (stdlib only)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request


REPOSITORY = 'restarea92/hellgato'
VERSION_PATTERN = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'


def stable_version(tag: str) -> str:
    if not re.fullmatch('v' + VERSION_PATTERN, tag):
        raise ValueError('Expected a stable tag vMAJOR.MINOR.PATCH, without leading zeros')
    return tag[1:]


def installer_version(root: Path) -> str:
    source = (root / 'installer/hellgato.iss').read_text(encoding='utf-8')
    versions = re.findall(r'^#define AppVersion "([^"\r\n]+)"\s*$', source, re.MULTILINE)
    if len(versions) != 1:
        raise ValueError('Expected exactly one AppVersion in installer/hellgato.iss')
    return stable_version('v' + versions[0])


def api_get(path: str, *, allow_missing: bool = False) -> dict | None:
    token = os.environ.get('GH_TOKEN')
    if not token:
        raise ValueError('GH_TOKEN is required for release checks')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{REPOSITORY}/{path}',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if allow_missing and error.code == 404:
            return None
        raise ValueError(f'GitHub lookup failed (HTTP {error.code}): {path}') from error
    except urllib.error.URLError as error:
        raise ValueError(f'GitHub lookup failed: {path}') from error
    if not isinstance(result, dict):
        raise ValueError(f'Unexpected GitHub response: {path}')
    return result


def tag_commit(tag: str) -> str:
    # Peel annotated tags too; a missing tag is an error, never auto-created.
    obj = api_get(f'git/ref/tags/{tag}')['object']
    seen = set()
    while obj['type'] == 'tag':
        sha = obj['sha']
        if sha in seen or len(seen) >= 10:
            raise ValueError('Invalid annotated tag chain')
        seen.add(sha)
        obj = api_get(f'git/tags/{sha}')['object']
    if obj['type'] != 'commit':
        raise ValueError('The release tag must point to a commit')
    return obj['sha']


def check_release(root: Path, environ=None) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    if (env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REPOSITORY') != REPOSITORY
            or env.get('GITHUB_REF') != 'refs/heads/main'
            or env.get('RELEASE_CONFIRM') != 'true'):
        raise ValueError('Publishing requires an explicitly confirmed manual run on main')
    tag = env.get('RELEASE_TAG', '')
    version = stable_version(tag)
    if version != installer_version(root):
        raise ValueError('Release tag does not match the Windows installer AppVersion')
    notes = root / f'docs/releases/{version}.md'
    if not notes.is_file() or not notes.read_text(encoding='utf-8').strip():
        raise ValueError('Non-empty, version-specific release notes are required')
    expected = env.get('GITHUB_SHA', '')
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('A full workflow commit SHA is required')
    head = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if head != expected or tag_commit(tag) != expected:
        raise ValueError('Checkout, existing remote tag and workflow commit must match')
    if api_get(f'releases/tags/{tag}', allow_missing=True) is not None:
        raise ValueError(f'Release {tag} already exists; it will never be overwritten')
    latest = api_get('releases/latest', allow_missing=True)
    if latest is not None:
        if latest.get('draft') is not False or latest.get('prerelease') is not False:
            raise ValueError('Unexpected latest stable release metadata')
        previous = stable_version(latest['tag_name'])
        if tuple(map(int, version.split('.'))) <= tuple(map(int, previous.split('.'))):
            raise ValueError('The new version must be greater than the latest stable release')
    return tag, version


def check_artifacts(root: Path, version: str) -> tuple[Path, Path]:
    stable_version('v' + version)
    installer = root / f'dist/Hellgato-{version}-Setup.exe'
    checksum = installer.with_name(installer.name + '.sha256')
    for path in (installer, checksum):
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError(f'Missing or invalid build artifact: {path.name}')
    line = checksum.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[0-9a-fA-F]{64}  ' + re.escape(installer.name), line):
        raise ValueError('Checksum must name exactly the requested installer')
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != line[:64].lower():
        raise ValueError('Installer SHA-256 mismatch')
    return installer, checksum


def publish_release(root: Path) -> None:
    # Repeat every remote check after the build, immediately before publication.
    tag, version = check_release(root)
    installer, checksum = check_artifacts(root, version)
    subprocess.run([
        'gh', 'release', 'create', tag, str(installer), str(checksum),
        '--repo', REPOSITORY, '--verify-tag', '--fail-on-no-commits',
        '--target', os.environ['GITHUB_SHA'], '--latest',
        '--title', f'Hellgato Stream Dog {version}',
        '--notes-file', str(root / f'docs/releases/{version}.md'),
    ], cwd=root, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('version', 'check', 'artifacts', 'publish'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == 'version':
            print(installer_version(root))
        elif args.command == 'check':
            tag, _ = check_release(root)
            print(f'Release preflight passed: {tag}')
        elif args.command == 'artifacts':
            check_artifacts(root, installer_version(root))
            print('Installer and SHA-256 verified')
        else:
            publish_release(root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Windows release check failed: {error}\n')


if __name__ == '__main__':
    main()

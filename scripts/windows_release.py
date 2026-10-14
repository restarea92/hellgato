"""Windows release metadata, exact-build provenance, and immutable publication.

Publication is reached only through the shared release coordinator. Build-only
commands never write to GitHub. Network failures and unknown remote state fail
closed; retries never move tags, replace assets, or edit unknown releases.
"""

import argparse
from contextvars import ContextVar
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPOSITORY = 'restarea92/hellgato'
VERSION_PATTERN = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
BETA_PATTERN = VERSION_PATTERN + r'-beta\.(0|[1-9][0-9]*)'
SHA_PATTERN = r'[0-9a-f]{40}'
DIGEST_PATTERN = r'[0-9a-f]{64}'
LEGACY_VERSIONS = {'0.1.0', '0.1.1'}
MANIFEST_NAME = 'release-manifest.json'
BUILD_MANIFEST_NAME = 'build-manifest.json'
_VERIFY_SPEC = importlib.util.spec_from_file_location('hellgato_verify_artifact', Path(__file__).with_name('verify-artifact.py'))
_VERIFY_ARTIFACT = importlib.util.module_from_spec(_VERIFY_SPEC)
_VERIFY_SPEC.loader.exec_module(_VERIFY_ARTIFACT)
_VERIFIED_ARCHIVES = _VERIFY_ARTIFACT._VERIFIED_ARCHIVES
_SIMULATION = ContextVar('windows_release_simulation', default=None)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def stable_version(tag: str) -> str:
    if not isinstance(tag, str) or not matches('v' + VERSION_PATTERN, tag):
        raise ValueError('Expected a stable tag vMAJOR.MINOR.PATCH, without leading zeros')
    return tag[1:]


def version_channel(version: str) -> str:
    if matches(VERSION_PATTERN, version):
        return 'stable'
    if matches(BETA_PATTERN, version):
        return 'beta'
    raise ValueError('Expected canonical stable or beta.N Windows version')


def release_version(tag: str) -> str:
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise ValueError('Expected a canonical Windows release tag')
    version_channel(tag[1:])
    return tag[1:]


def version_key(version: str) -> tuple:
    channel = version_channel(version)
    core = tuple(map(int, version.split('-')[0].split('.')))
    return (*core, 1, 0) if channel == 'stable' else (*core, 0, int(version.rsplit('.', 1)[1]))


def channel_policy(channel: str) -> dict:
    if channel not in ('stable', 'beta'):
        raise ValueError('Unsupported Windows release channel')
    branch = 'main' if channel == 'stable' else 'next'
    return {'branch': branch, 'releaseBranch': 'changeset-release/' + branch,
            'distTag': 'latest' if channel == 'stable' else 'beta', 'prerelease': channel == 'beta'}


def numeric_version(version: str) -> str:
    channel = version_channel(version)
    core = list(map(int, version.split('-')[0].split('.')))
    revision = 65535 if channel == 'stable' else int(version.rsplit('.', 1)[1]) + 1
    if any(part > 65535 for part in core) or channel == 'beta' and revision >= 65535:
        raise ValueError('Windows numeric version fields or beta sequence exceed their allowed range')
    return '.'.join(map(str, [*core, revision]))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def parse_json(content):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON object keys are not allowed')
            value[key] = item
        return value
    return json.loads(content, object_pairs_hook=unique_object)


def read_object(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'Missing or unsafe metadata: {path.name}')
    value = parse_json(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path.name}')
    return value


def positive_id(value, label: str) -> int:
    if isinstance(value, bool) or not matches(r'[1-9][0-9]*', str(value)):
        raise ValueError(f'A positive {label} is required')
    return int(value)


def validate_channel_plan(root: Path, version: str, plan_path: str) -> dict:
    if plan_path not in ('.release/plan.json', '.release/candidate-plan.json'):
        raise ValueError('Use the exact checkout release or candidate plan')
    plan = read_object(root / plan_path)
    if type(plan.get('schemaVersion')) is int and plan['schemaVersion'] == 1:
        if plan_path != '.release/plan.json' or version_channel(version) != 'stable':
            raise ValueError('Legacy release plans support stable Windows only')
        return plan
    fields = {'schemaVersion', 'repository', 'candidateOnly', 'branch', 'baseSha', 'channel',
              'transition', 'policy', 'changesets', 'archivedBefore', 'archivedAfter', 'preBefore',
              'preAfter', 'components', 'generatedFiles'}
    candidate = plan_path == '.release/candidate-plan.json'
    channel = version_channel(version)
    policy = channel_policy(channel)
    if (set(plan) != fields or type(plan.get('schemaVersion')) is not int
            or plan['schemaVersion'] != (2 if candidate else 3) or plan['candidateOnly'] is not candidate
            or plan['repository'] != REPOSITORY or plan['channel'] != channel
            or plan['branch'] != ('next' if candidate else policy['branch']) or plan['policy'] != policy
            or not matches(SHA_PATTERN, plan['baseSha'])
            or plan['preBefore'] not in (None, {'mode': 'pre', 'tag': 'beta'})
            or plan['preAfter'] != ({'mode': 'pre', 'tag': 'beta'} if channel == 'beta' else None)
            or plan['transition'] != ('exit' if channel == 'stable' else 'advance' if plan['preBefore'] else 'enter')
            or channel == 'stable' and plan['preBefore'] is None):
        raise ValueError('Invalid exact Windows channel plan')
    components = plan['components']
    if (not isinstance(components, list) or not 1 <= len(components) <= 2
            or any(not isinstance(item, dict) for item in components)
            or len({item.get('component') for item in components}) != len(components)):
        raise ValueError('Invalid selected channel components')
    entries = [item for item in components if item.get('component') == 'windows']
    if len(entries) != 1:
        raise ValueError('Channel plan must select Windows exactly once')
    component = entries[0]
    component_fields = {'component', 'oldVersion', 'version', 'channel', 'tag', 'notes'}
    if (set(component) != component_fields | (set() if candidate else {'publicBaseline'})
            or component['version'] != version or component['channel'] != channel
            or component['tag'] != 'v' + version or component['notes'] != f'docs/releases/{version}.md'
            or version_key(component['oldVersion']) >= version_key(version)):
        raise ValueError('Channel plan does not bind the exact Windows source/target')
    if not candidate:
        validate_public_baseline(component, bootstrap=False, channel_plan=True)
    if any(not isinstance(plan[name], list) for name in ('changesets', 'archivedBefore', 'archivedAfter', 'generatedFiles')):
        raise ValueError('Invalid channel input or generated-file lists')
    expected = {'packages/windows-release/package.json', 'app/release.json', 'installer/version.iss', component['notes']}
    seen = set()
    for entry in plan['generatedFiles']:
        if (not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}
                or not isinstance(entry['path'], str) or entry['path'].startswith('/')
                or '\\' in entry['path'] or '..' in Path(entry['path']).parts or entry['path'] in seen
                or not matches(DIGEST_PATTERN, entry['sha256'])):
            raise ValueError('Invalid channel generated-file digest')
        path = root / entry['path']
        if not path.is_file() or path.is_symlink() or sha256(path.read_bytes()) != entry['sha256']:
            raise ValueError('Channel generated-file bytes differ from the exact plan')
        seen.add(entry['path'])
    if not expected.issubset(seen):
        raise ValueError('Channel plan omits Windows metadata or release notes')
    return plan


def installer_version(root: Path, *, plan_path: str | None = None, source_channel: str | None = None) -> str:
    if source_channel is not None:
        if plan_path is not None or source_channel != 'beta':
            raise ValueError('Source-channel beta is exclusive with a release or candidate plan')
        if read_object(root / '.changeset/pre.json') != {'mode': 'pre', 'tag': 'beta'}:
            raise ValueError('Source-channel beta requires active canonical prerelease state')
    package = read_object(root / 'packages/windows-release/package.json')
    if package.get('name') != '@hellgato/windows-release' or package.get('private') is not True:
        raise ValueError('The Windows version source must be the private Windows package')
    version = package['version']
    channel = version_channel(version)
    include = (root / 'installer/version.iss').read_text(encoding='utf-8')
    definitions = re.findall(r'^#define AppVersion "([^"\r\n]+)"\s*$', include, re.MULTILINE)
    numeric = re.findall(r'^#define AppVersionNumeric "([^"\r\n]+)"\s*$', include, re.MULTILINE)
    source = (root / 'installer/hellgato.iss').read_text(encoding='utf-8')
    if definitions != [version] or numeric != [numeric_version(version)] or len(re.findall(r'^#include "version.iss"[ \t]*$', source, re.MULTILINE)) != 1 or re.search(r'^#define AppVersion(?:Numeric)?\b', source, re.MULTILINE):
        raise ValueError('Generated AppVersion/include does not match the Windows version source')
    metadata = read_object(root / 'app/release.json')
    if metadata != {'version': version, 'channel': channel}:
        raise ValueError('App runtime release metadata does not match the Windows version source')
    if plan_path is not None:
        validate_channel_plan(root, version, plan_path)
    elif channel != 'stable' and source_channel is None:
        raise ValueError('Beta Windows builds require an explicit exact release or candidate plan')
    return version


def api_get(path: str, *, allow_missing: bool = False, array: bool = False):
    if _SIMULATION.get() is not None:
        return _SIMULATION.get().api_get(path, allow_missing=allow_missing, array=array)
    token = os.environ.get('GH_TOKEN')
    if not token:
        raise ValueError('GH_TOKEN is required for release checks')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{REPOSITORY}/{path}',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28'},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as error:
            if allow_missing and error.code == 404:
                return None
            if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f'GitHub lookup failed (HTTP {error.code}): {path}') from error
        except urllib.error.URLError as error:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f'GitHub lookup failed: {path}') from error
    if not isinstance(result, list if array else dict):
        raise ValueError(f'Unexpected GitHub response: {path}')
    return result


def api_pages(path: str, *, key: str | None = None) -> list:
    """Read through an explicit empty page, rejecting repeats/partial envelopes."""
    items, seen, total = [], set(), None
    for page in range(1, 1001):
        response = api_get(f'{path}?per_page=100&page={page}', array=key is None)
        batch = response if key is None else response.get(key)
        if not isinstance(batch, list) or len(batch) > 100:
            raise ValueError('Invalid paginated GitHub response')
        if key is not None:
            reported = response.get('total_count')
            if isinstance(reported, bool) or not isinstance(reported, int) or reported < 0:
                raise ValueError('Missing pagination total_count')
            if total is not None and reported != total:
                raise ValueError('GitHub pagination changed while reading')
            total = reported
        if not batch:
            if total is not None and len(items) != total:
                raise ValueError('Incomplete paginated GitHub response')
            return items
        for item in batch:
            if not isinstance(item, dict):
                raise ValueError('Malformed paginated GitHub item')
            identifier = positive_id(item.get('id'), 'GitHub item ID')
            if identifier in seen:
                raise ValueError('Duplicate or incomplete paginated GitHub response')
            seen.add(identifier)
            items.append(item)
    raise ValueError('GitHub pagination did not terminate')


def tag_commit(tag: str, *, allow_missing=False) -> str | None:
    release_version(tag)
    ref = api_get(f'git/ref/tags/{tag}', allow_missing=allow_missing)
    if ref is None:
        return None
    obj, seen = ref['object'], set()
    while obj['type'] == 'tag':
        sha = obj['sha']
        if not matches(SHA_PATTERN, sha) or sha in seen or len(seen) >= 10:
            raise ValueError('Invalid annotated tag chain')
        seen.add(sha)
        obj = api_get(f'git/tags/{sha}')['object']
    if obj['type'] != 'commit' or not matches(SHA_PATTERN, obj['sha']):
        raise ValueError('The release tag must point to a commit')
    return obj['sha']


def asset_bytes(asset: dict) -> bytes:
    identifier = positive_id(asset.get('id'), 'release asset ID')
    if _SIMULATION.get() is not None:
        result = _SIMULATION.get().assets[identifier]
        if not isinstance(result, bytes) or len(result) != asset.get('size'):
            raise ValueError('Simulated release asset download is incomplete')
        return result
    # gh handles signed storage redirects without forwarding our Authorization.
    result = subprocess.run(
        ['gh', 'api', f'repos/{REPOSITORY}/releases/assets/{identifier}',
         '-H', 'Accept: application/octet-stream'], check=True, capture_output=True,
    ).stdout
    if not isinstance(result, bytes) or len(result) != asset.get('size'):
        raise ValueError('Release asset download is incomplete')
    return result


def artifact_archive_bytes(identifier: int) -> bytes:
    if _SIMULATION.get() is not None:
        return _SIMULATION.get().archives[identifier]
    return subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/actions/artifacts/{identifier}/zip'],
                          check=True, capture_output=True).stdout


def verify_archive(root: Path, artifact: dict, manifest: dict) -> None:
    _VERIFY_ARTIFACT.verify_artifact(root / 'dist', artifact['id'], artifact['digest'][7:],
                                    expected_names=[entry['name'] for entry in manifest['files']] + [BUILD_MANIFEST_NAME],
                                    fetch=artifact_archive_bytes)


def release_assets(release: dict) -> dict:
    identifier = positive_id(release.get('id'), 'release ID')
    result = {}
    for asset in api_pages(f'releases/{identifier}/assets'):
        name = asset.get('name')
        if not isinstance(name, str) or name in result or asset.get('state') != 'uploaded' or not isinstance(asset.get('size'), int) or asset['size'] <= 0:
            raise ValueError('Invalid or duplicate release asset')
        result[name] = asset
    return result


def validate_manifest(manifest: dict, *, published=False) -> None:
    required = {'schemaVersion', 'repository', 'component', 'version', 'channel', 'tag',
                'releaseSha', 'planSha256', 'build', 'files', 'sources', 'toolchain'}
    if set(manifest) != required | ({'artifact'} if published else set()):
        raise ValueError('Unexpected Windows manifest fields')
    version = release_version(manifest.get('tag'))
    if (type(manifest.get('schemaVersion')) is not int or manifest.get('schemaVersion') != 1 or manifest.get('repository') != REPOSITORY
            or manifest.get('component') != 'windows' or manifest.get('channel') != version_channel(version)
            or manifest.get('version') != version or not matches(SHA_PATTERN, manifest.get('releaseSha', ''))):
        raise ValueError('Invalid Windows manifest identity')
    digest = manifest.get('planSha256')
    if (digest is not None and not matches(DIGEST_PATTERN, digest)) or (published and digest is None):
        raise ValueError('Invalid approved plan digest')
    build = manifest['build']
    if not isinstance(build, dict) or set(build) != {'runId', 'runAttempt'}:
        raise ValueError('Invalid source build provenance')
    positive_id(build['runId'], 'build run ID')
    positive_id(build['runAttempt'], 'build run attempt')
    filenames = {f'Hellgato-{version}-Setup.exe', f'Hellgato-{version}-Setup.exe.sha256'}
    files = manifest['files']
    if not isinstance(files, list) or len(files) != 2 or {entry.get('name') for entry in files if isinstance(entry, dict)} != filenames:
        raise ValueError('Manifest must name exactly the requested installer and checksum')
    for entry in files:
        if (set(entry) != {'name', 'size', 'sha256'} or isinstance(entry['size'], bool)
                or not isinstance(entry['size'], int) or entry['size'] <= 0
                or not matches(DIGEST_PATTERN, entry['sha256'])):
            raise ValueError('Invalid manifest file digest or size')
    if not isinstance(manifest['sources'], dict) or not manifest['sources'] or not isinstance(manifest['toolchain'], dict) or not manifest['toolchain']:
        raise ValueError('Missing source or toolchain provenance')
    for name, digest in manifest['sources'].items():
        if not isinstance(name, str) or name.startswith('/') or '..' in Path(name).parts or not matches(DIGEST_PATTERN, digest):
            raise ValueError('Invalid manifest source digest')
    if published:
        artifact = manifest['artifact']
        if not isinstance(artifact, dict) or set(artifact) != {'id', 'name', 'sha256'}:
            raise ValueError('Missing published artifact provenance')
        positive_id(artifact['id'], 'artifact ID')
        if artifact['name'] != f"windows-installer-{build['runId']}-{build['runAttempt']}" or not matches(DIGEST_PATTERN, artifact['sha256']):
            raise ValueError('Invalid published artifact provenance')


def validate_public_baseline(component: dict, *, bootstrap: bool, channel_plan=False) -> dict | None:
    """An explicit reviewed public baseline never replaces the source version."""
    if 'publicBaseline' not in component:
        return None
    baseline = component['publicBaseline']
    required = {'schemaVersion', 'component', 'channel', 'sourceVersion', 'version', 'tag',
                'releaseId', 'releaseSha', 'installerSha256', 'manifestSha256', 'reason'}
    if not isinstance(baseline, dict) or set(baseline) != required:
        raise ValueError('Unexpected Windows public baseline fields')
    if bootstrap:
        raise ValueError('A Windows public baseline is not allowed on bootstrap plans')
    pattern = '(?:' + VERSION_PATTERN + ')(?:-beta\\.(0|[1-9][0-9]*))?' if channel_plan else VERSION_PATTERN
    if (type(baseline['schemaVersion']) is not int or baseline['schemaVersion'] != 1
            or baseline['component'] != 'windows' or baseline['channel'] not in ('stable', 'beta')
            or not matches(pattern, baseline['sourceVersion'])
            or baseline['sourceVersion'] != component.get('oldVersion')
            or not matches(pattern, baseline['version'])
            or baseline['channel'] != version_channel(baseline['version'])
            or baseline['tag'] != 'v' + baseline['version']
            or not matches(pattern, component.get('version'))
            or type(baseline['releaseId']) is not int or not 0 < baseline['releaseId'] <= 2 ** 53 - 1
            or not matches(SHA_PATTERN, baseline['releaseSha'])
            or not matches(DIGEST_PATTERN, baseline['installerSha256'])
            or not (matches(DIGEST_PATTERN, baseline['manifestSha256'])
                    or baseline['manifestSha256'] is None and baseline['version'] in LEGACY_VERSIONS)
            or not isinstance(baseline['reason'], str) or len(baseline['reason'].strip()) < 30):
        raise ValueError('Invalid reviewed Windows public baseline')
    public, source, candidate = (version_key(version) for version in
                                 (baseline['version'], baseline['sourceVersion'], component['version']))
    if not public <= source < candidate:
        raise ValueError('Windows public baseline must be at or below source, with candidate above source')
    return baseline


def verify_public_baseline(latest: dict | None, baseline: dict) -> None:
    """Re-read reviewed release identity and exact public bytes before a write."""
    if latest is None or latest['version'] != baseline['version']:
        raise ValueError('Reviewed public baseline does not match the latest published Windows ' + baseline['channel'])
    published = api_get(f"releases/tags/{baseline['tag']}")
    if (published.get('tag_name') != baseline['tag'] or published.get('draft') is not False
            or published.get('prerelease') is not (baseline['channel'] == 'beta') or type(published.get('id')) is not int
            or published['id'] != baseline['releaseId']
            or tag_commit(baseline['tag']) != baseline['releaseSha']):
        raise ValueError('Reviewed public baseline release ID, tag, or source has changed')
    assets = release_assets(published)
    installer = f"Hellgato-{baseline['version']}-Setup.exe"
    if not {installer, installer + '.sha256'}.issubset(assets):
        raise ValueError('Reviewed public baseline installer/checksum is incomplete')
    contents = {name: asset_bytes(assets[name]) for name in (installer, installer + '.sha256')}
    checksum = contents[installer + '.sha256'].decode('ascii').strip()
    if (not matches(r'[0-9a-fA-F]{64}  ' + re.escape(installer), checksum)
            or sha256(contents[installer]) != checksum[:64].lower()
            or sha256(contents[installer]) != baseline['installerSha256']):
        raise ValueError('Reviewed public baseline installer or checksum has changed')
    raw_manifest = asset_bytes(assets[MANIFEST_NAME]) if MANIFEST_NAME in assets else None
    if (sha256(raw_manifest) if raw_manifest is not None else None) != baseline['manifestSha256']:
        raise ValueError('Reviewed public baseline manifest bytes have changed')
    if raw_manifest is not None:
        manifest = parse_json(raw_manifest)
        if not isinstance(manifest, dict):
            raise ValueError('Reviewed public baseline manifest is malformed')
        validate_manifest(manifest, published=True)
        if manifest['tag'] != baseline['tag'] or manifest['releaseSha'] != baseline['releaseSha']:
            raise ValueError('Reviewed public baseline manifest tag/source has changed')
        for entry in manifest['files']:
            if len(contents[entry['name']]) != entry['size'] or sha256(contents[entry['name']]) != entry['sha256']:
                raise ValueError('Reviewed public baseline assets do not match its manifest')


def _resolve_windows_channel(channel: str) -> dict:
    """Ignore npm namespaces; inspect every page and compare numeric versions."""
    candidates, diagnostics = [], []
    for release in api_pages('releases'):
        if not isinstance(release.get('draft'), bool) or not isinstance(release.get('prerelease'), bool):
            raise ValueError('Unexpected release state')
        if release['draft'] or release['prerelease'] != (channel == 'beta'):
            continue
        tag = release.get('tag_name')
        if not isinstance(tag, str):
            raise ValueError('Missing release tag')
        if not matches('v' + (BETA_PATTERN if channel == 'beta' else VERSION_PATTERN), tag):
            continue
        version = release_version(tag)
        assets = release_assets(release)
        installer = f'Hellgato-{version}-Setup.exe'
        required = {installer, installer + '.sha256'}
        manifest = None
        if MANIFEST_NAME in assets:
            manifest = parse_json(asset_bytes(assets[MANIFEST_NAME]))
            if not isinstance(manifest, dict):
                raise ValueError(f'{tag}: malformed Windows manifest')
            validate_manifest(manifest, published=True)
            if manifest['tag'] != tag or manifest['channel'] != channel or manifest['releaseSha'] != tag_commit(tag):
                raise ValueError(f'{tag}: manifest does not match release tag/source')
        if not required.issubset(assets):
            diagnostics.append(f'{tag}: incomplete Windows installer/checksum assets')
            continue
        if version not in LEGACY_VERSIONS and manifest is None:
            raise ValueError(f'{tag}: new Windows release is missing its manifest')
        if manifest is not None:
            for entry in manifest['files']:
                asset = assets[entry['name']]
                if asset['size'] != entry['size'] or sha256(asset_bytes(asset)) != entry['sha256']:
                    raise ValueError(f'{tag}: asset does not match manifest')
        checksum = asset_bytes(assets[installer + '.sha256']).decode('ascii').strip()
        if not matches(r'[0-9a-fA-F]{64}  ' + re.escape(installer), checksum):
            raise ValueError(f'{tag}: malformed installer checksum')
        if sha256(asset_bytes(assets[installer])) != checksum[:64].lower():
            raise ValueError(f'{tag}: installer checksum mismatch')
        candidates.append({'version': version, 'tag': tag, 'url': f'https://github.com/{REPOSITORY}/releases/tag/{tag}',
                           'installerUrl': f'https://github.com/{REPOSITORY}/releases/download/{tag}/{installer}',
                           'checksumUrl': f'https://github.com/{REPOSITORY}/releases/download/{tag}/{installer}.sha256'})
    latest = max(candidates, key=lambda item: version_key(item['version']), default=None)
    return {'latest': latest, 'diagnostics': diagnostics}


def resolve_windows_stable() -> dict:
    return _resolve_windows_channel('stable')


def resolve_windows_beta() -> dict:
    return _resolve_windows_channel('beta')


def check_artifacts(root: Path, version: str) -> tuple[Path, Path]:
    version_channel(version)
    installer = root / f'dist/Hellgato-{version}-Setup.exe'
    checksum = installer.with_name(installer.name + '.sha256')
    for path in (installer, checksum):
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError(f'Missing or invalid build artifact: {path.name}')
    line = checksum.read_text(encoding='ascii').strip()
    if not matches(r'[0-9a-fA-F]{64}  ' + re.escape(installer.name), line):
        raise ValueError('Checksum must name exactly the requested installer')
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != line[:64].lower():
        raise ValueError('Installer SHA-256 mismatch')
    return installer, checksum


def checkout_sha(root: Path) -> str:
    if _SIMULATION.get() is not None:
        return _SIMULATION.get().checkout
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def source_provenance(root: Path, version: str) -> tuple[dict, dict]:
    paths = ['packages/windows-release/package.json', 'app/release.json', 'installer/version.iss',
             'installer/requirements-build.txt', 'scripts/prepare-windows.py', 'scripts/prepare-cora.mjs',
             'vendor/mirabox-sdk/manifest.json', 'pyproject.toml', 'uv.lock',
             f'docs/releases/{version}.md']
    sources = {}
    for name in paths:
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.read_bytes().strip():
            raise ValueError(f'Missing source provenance: {name}')
        sources[name] = sha256(path.read_bytes())
    prepare = (root / 'scripts/prepare-windows.py').read_text(encoding='utf-8')
    pins = {}
    for field in ('SDK_REVISION', 'NODE_VERSION'):
        values = re.findall(r'^' + field + r" = '([^']+)'$", prepare, re.MULTILINE)
        if len(values) != 1:
            raise ValueError(f'Missing pinned toolchain value: {field}')
        pins[field] = values[0]
    cora = (root / 'scripts/prepare-cora.mjs').read_text(encoding='utf-8')
    revisions = re.findall(r"^const revision = '([0-9a-f]{40})';$", cora, re.MULTILINE)
    if len(revisions) != 1:
        raise ValueError('Missing pinned CORA upstream revision')
    pins['CORA_REVISION'] = revisions[0]
    pins['pythonRequirements'] = (root / 'installer/requirements-build.txt').read_text(encoding='utf-8').splitlines()
    return sources, pins


def build_manifest(root: Path, environ=None, *, plan_path: str | None = None, source_channel: str | None = None) -> dict:
    env = os.environ if environ is None else environ
    version = installer_version(root, plan_path=plan_path, source_channel=source_channel)
    source = env.get('RELEASE_SHA') or env.get('GITHUB_SHA', '')
    if not matches(SHA_PATTERN, source) or checkout_sha(root) != source:
        raise ValueError('Build checkout and full release SHA must match')
    files = check_artifacts(root, version)
    plan = root / plan_path if plan_path is not None else None
    sources, toolchain = source_provenance(root, version)
    manifest = {'schemaVersion': 1, 'repository': REPOSITORY, 'component': 'windows',
                'version': version, 'channel': version_channel(version), 'tag': 'v' + version, 'releaseSha': source,
                'planSha256': sha256(plan.read_bytes()) if plan is not None else None,
                'build': {'runId': positive_id(env.get('GITHUB_RUN_ID'), 'build run ID'),
                          'runAttempt': positive_id(env.get('GITHUB_RUN_ATTEMPT'), 'build run attempt')},
                'files': [{'name': path.name, 'size': path.stat().st_size, 'sha256': sha256(path.read_bytes())} for path in files],
                'sources': sources, 'toolchain': toolchain}
    validate_manifest(manifest)
    (root / 'dist' / BUILD_MANIFEST_NAME).write_bytes(json_bytes(manifest))
    return manifest


def approval_context(root: Path, env: dict, *, require_enabled=True) -> tuple[dict, dict]:
    if ((require_enabled and env.get('RELEASE_ENABLED') != 'true') or env.get('GITHUB_REPOSITORY') != REPOSITORY
            or env.get('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch')):
        raise ValueError('Publishing requires enabled shared release orchestration on the channel branch')
    source = env.get('RELEASE_SHA', '')
    if not matches(SHA_PATTERN, source):
        raise ValueError('A full approved release SHA is required')
    filename = env.get('RELEASE_APPROVAL_FILE')
    if not filename:
        raise ValueError('The shared coordinator approval file is required')
    approval = read_object(root / filename)
    plan_path = root / '.release/plan.json'
    plan = read_object(plan_path)
    if (type(approval.get('schemaVersion')) is not int or approval.get('schemaVersion') != 1 or approval.get('repository') != REPOSITORY
            or approval.get('releaseSha') != source or approval.get('planSha256') != sha256(plan_path.read_bytes())
            or approval.get('components') != plan.get('components')
            or approval.get('baseSha') != plan.get('baseSha')
            or approval.get('bootstrap') != plan.get('bootstrap', False)
            or not isinstance(approval.get('bootstrap'), bool)):
        raise ValueError('Approval does not match the exact release plan/source')
    pr = approval.get('pullRequest')
    if not isinstance(pr, dict) or not matches(SHA_PATTERN, pr.get('headSha', '')) or not pr.get('mergedBy'):
        raise ValueError('A verified merged release PR approval is required')
    positive_id(pr.get('number'), 'approved pull request')
    if not isinstance(approval.get('components'), list):
        raise ValueError('Invalid selected components')
    entries = [item for item in approval['components'] if isinstance(item, dict) and item.get('component') == 'windows']
    if len(entries) != 1:
        raise ValueError('The approved plan must select Windows exactly once')
    component = entries[0]
    version = installer_version(root, plan_path='.release/plan.json')
    channel = version_channel(version)
    policy = channel_policy(channel)
    if env.get('GITHUB_REF') != 'refs/heads/' + policy['branch']:
        raise ValueError('Publishing requires the exact channel branch')
    if component.get('version') != version or component.get('channel') != channel or component.get('tag') != 'v' + version or component.get('notes') != f'docs/releases/{version}.md':
        raise ValueError('Approved Windows version/tag/notes does not match AppVersion')
    validate_public_baseline(component, bootstrap=approval['bootstrap'], channel_plan=plan['schemaVersion'] == 3)
    notes = root / component['notes']
    if not notes.is_file() or notes.is_symlink() or not notes.read_text(encoding='utf-8').strip():
        raise ValueError('Non-empty, version-specific release notes are required')
    if checkout_sha(root) != source:
        raise ValueError('Checkout and approved release SHA must match')
    ancestry = api_get(f"compare/{source}...{policy['branch']}")
    if ancestry.get('status') not in ('ahead', 'identical') or ancestry.get('merge_base_commit', {}).get('sha') != source:
        raise ValueError(f"The approved release SHA must be in {policy['branch']} history")
    remote_pr = api_get(f"pulls/{pr['number']}")
    owner = REPOSITORY.split('/')[0]
    if (remote_pr.get('merged') is not True or remote_pr.get('state') != 'closed'
            or remote_pr.get('merge_commit_sha') != source
            or remote_pr.get('base', {}).get('ref') != policy['branch']
            or remote_pr.get('base', {}).get('repo', {}).get('full_name') != REPOSITORY
            or remote_pr.get('head', {}).get('repo', {}).get('full_name') != REPOSITORY
            or remote_pr.get('head', {}).get('sha') != pr['headSha']
            or remote_pr.get('merged_by', {}).get('login') != owner or pr['mergedBy'] != owner):
        raise ValueError('Approval is not the exact owner-merged release PR')
    if approval['bootstrap']:
        trusted_pr = remote_pr.get('user', {}).get('login') == owner and matches(r'release/bootstrap-[a-z0-9-]+', remote_pr.get('head', {}).get('ref', ''))
    else:
        trusted_pr = remote_pr.get('user', {}).get('login') == 'github-actions[bot]' and remote_pr.get('head', {}).get('ref') == policy['releaseBranch']
    commit = api_get(f'commits/{source}')
    head = api_get(f"git/commits/{pr['headSha']}")
    if (not trusted_pr or commit.get('sha') != source or not commit.get('parents')
            or commit['parents'][0].get('sha') != approval['baseSha']
            or not matches(SHA_PATTERN, head.get('tree', {}).get('sha', ''))
            or head['tree']['sha'] != commit.get('commit', {}).get('tree', {}).get('sha')):
        raise ValueError('Release PR provenance, base, or approved tree mismatch')
    return approval, component


def verify_build(root: Path, approval: dict, env: dict) -> dict:
    manifest = read_object(root / 'dist' / BUILD_MANIFEST_NAME)
    validate_manifest(manifest)
    if manifest['releaseSha'] != approval['releaseSha'] or manifest['planSha256'] != approval['planSha256'] or manifest['version'] != installer_version(root, plan_path='.release/plan.json'):
        raise ValueError('Build manifest does not match approved source/plan/version')
    build = {'runId': positive_id(env.get('WINDOWS_BUILD_RUN_ID'), 'original build run ID'),
             'runAttempt': positive_id(env.get('WINDOWS_BUILD_RUN_ATTEMPT'), 'original build run attempt')}
    if manifest['build'] != build:
        raise ValueError('Original build run/attempt does not match manifest')
    paths = check_artifacts(root, manifest['version'])
    actual = [{'name': path.name, 'size': path.stat().st_size, 'sha256': sha256(path.read_bytes())} for path in paths]
    sources, toolchain = source_provenance(root, manifest['version'])
    if manifest['files'] != actual or manifest['sources'] != sources or manifest['toolchain'] != toolchain:
        raise ValueError('Build bytes/source/toolchain provenance mismatch')
    identifier = positive_id(env.get('WINDOWS_ARTIFACT_ID'), 'original artifact ID')
    artifact = api_get(f'actions/artifacts/{identifier}')
    name = f"windows-installer-{build['runId']}-{build['runAttempt']}"
    run_info = artifact.get('workflow_run', {})
    branch = channel_policy(manifest['channel'])['branch']
    if (artifact.get('id') != identifier or artifact.get('name') != name or artifact.get('expired') is not False
            or run_info.get('id') != build['runId'] or not matches(SHA_PATTERN, run_info.get('head_sha', ''))
            or run_info.get('head_branch') != branch or not matches('sha256:' + DIGEST_PATTERN, artifact.get('digest', ''))):
        raise ValueError('Artifact ID does not identify the original unexpired approved build')
    run = api_get(f"actions/runs/{build['runId']}/attempts/{build['runAttempt']}")
    if (run.get('id') != build['runId'] or run.get('run_attempt') != build['runAttempt']
            or run.get('repository', {}).get('full_name') != REPOSITORY
            or run.get('head_repository', {}).get('full_name') != REPOSITORY
            or run.get('head_sha') != run_info['head_sha'] or run.get('head_branch') != branch
            or run.get('event') not in ('push', 'workflow_dispatch')
            or run.get('path', '').split('@')[0] != '.github/workflows/release.yml'
            or run.get('status') not in ('in_progress', 'completed')):
        raise ValueError(f'Original source run is not an approved {branch} Windows build')
    relation = api_get(f"compare/{approval['releaseSha']}...{run['head_sha']}")
    if relation.get('status') not in ('ahead', 'identical') or relation.get('merge_base_commit', {}).get('sha') != approval['releaseSha']:
        raise ValueError('Build run head does not contain the approved source')
    jobs = api_pages(f"actions/runs/{build['runId']}/attempts/{build['runAttempt']}/jobs", key='jobs')
    built = [job for job in jobs if job.get('name') == 'windows-build / build']
    if len(built) != 1 or built[0].get('status') != 'completed' or built[0].get('conclusion') != 'success':
        raise ValueError('Original Windows build job did not complete successfully')
    verify_archive(root, artifact, manifest)
    manifest = {**manifest, 'artifact': {'id': identifier, 'name': name, 'sha256': artifact['digest'][7:]}}
    validate_manifest(manifest, published=True)
    return manifest


def check_release(root: Path, environ=None, *, require_enabled=True) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    approval, component = approval_context(root, env, require_enabled=require_enabled)
    target = tag_commit(component['tag'], allow_missing=True)
    if target is not None and target != approval['releaseSha']:
        raise ValueError('Existing remote tag and approved release SHA must match')
    channel = component['channel']
    result = resolve_windows_beta() if channel == 'beta' else resolve_windows_stable()
    for warning in result['diagnostics']:
        print(warning, file=sys.stderr)
    latest = result['latest']
    existing = api_get(f"releases/tags/{component['tag']}", allow_missing=True)
    completed = False
    if existing is not None:
        # This validates every existing byte before check can report success.
        manifest = verify_build(root, approval, env)
        completed, _ = validate_existing(root, existing, manifest)
    version_tuple = version_key(component['version'])
    # An exact-byte completed retry is a no-op, even after its reviewed baseline
    # stops being latest. Publication must never promote that old release again.
    if not completed:
        if latest and version_tuple <= version_key(latest['version']):
            raise ValueError('The new version must be greater than the latest Windows ' + channel + ' release')
        previous = latest['version'] if latest else None
        if 'publicBaseline' in component:
            baseline = component['publicBaseline']
            if baseline['channel'] == channel:
                verify_public_baseline(latest, baseline)
            elif channel == 'beta':
                if latest is not None:
                    raise ValueError('A first beta baseline requires no existing public Windows beta')
                verify_public_baseline(resolve_windows_stable()['latest'], baseline)
            else:
                verify_public_baseline(resolve_windows_beta()['latest'], baseline)
        elif component.get('oldVersion') != previous:
            raise ValueError('Approved oldVersion does not match the latest published Windows stable')
        if approval['bootstrap'] and (component['version'] != '0.1.2' or previous != '0.1.1'):
            raise ValueError('Windows bootstrap is only the approved 0.1.1 to 0.1.2 candidate')
    return component['tag'], component['version']


def release_body(root: Path, manifest: dict) -> str:
    digest = sha256(json_bytes(manifest))
    notes = (root / f"docs/releases/{manifest['version']}.md").read_text(encoding='utf-8').rstrip()
    return notes + f'\n\n<!-- hellgato-release:windows:{digest} -->\n'


def validate_existing(root: Path, existing: dict, manifest: dict) -> tuple[bool, list[str]]:
    if (existing.get('tag_name') != manifest['tag'] or existing.get('prerelease') is not (manifest['channel'] == 'beta')
            or not isinstance(existing.get('draft'), bool)
            or existing.get('name') != f"Hellgato Stream Dog {manifest['version']}"
            or existing.get('body') != release_body(root, manifest)
            or existing.get('author', {}).get('login') != 'github-actions[bot]'
            or existing.get('author', {}).get('type') != 'Bot'
            or tag_commit(manifest['tag']) != manifest['releaseSha']):
        raise ValueError('Existing release/draft has unknown source or conflicting content; it will never be overwritten')
    assets = release_assets(existing)
    expected = {entry['name']: (root / 'dist' / entry['name']).read_bytes() for entry in manifest['files']}
    expected[MANIFEST_NAME] = json_bytes(manifest)
    if set(assets) - set(expected):
        raise ValueError('Existing release has unknown assets; it will never be overwritten')
    for name, asset in assets.items():
        if asset_bytes(asset) != expected[name]:
            raise ValueError('Existing release asset bytes conflict; it will never be overwritten')
    missing = sorted(set(expected) - set(assets))
    if not existing['draft'] and missing:
        raise ValueError('Published release is incomplete; published assets are immutable')
    return not existing['draft'], missing


class BetaReleaseSimulation:
    """Explicit in-memory GitHub state. No callbacks or remote fallback exist."""

    def __init__(self, *, checkout: str, responses: dict, releases: dict, tags: dict,
                 assets: dict, archives: dict):
        if not matches(SHA_PATTERN, checkout):
            raise ValueError('Simulation requires an exact checkout identity')
        self.checkout = checkout
        self.responses, self.releases, self.tags = copy.deepcopy((responses, releases, tags))
        self.assets, self.archives = copy.deepcopy((assets, archives))
        self.commands = []
        self.next_id = max([0, *self.assets, *(item['id'] for item in self.releases.values())])

    def api_get(self, path, *, allow_missing=False, array=False):
        if path in self.responses:
            result = self.responses[path]
        elif path.startswith('git/ref/tags/'):
            result = self.tags.get(path.removeprefix('git/ref/tags/'))
        elif path.startswith('releases/tags/'):
            result = self.releases.get(path.removeprefix('releases/tags/'))
        elif re.fullmatch(r'releases\?per_page=100&page=[1-9][0-9]*', path):
            page = int(path.rsplit('=', 1)[1])
            result = list(self.releases.values())[(page - 1) * 100:page * 100]
        elif match := re.fullmatch(r'releases/([1-9][0-9]*)/assets\?per_page=100&page=([1-9][0-9]*)', path):
            item = next((item for item in self.releases.values() if item['id'] == int(match[1])), None)
            if item is None:
                raise ValueError('Unknown simulated release ID')
            page = int(match[2]); result = item.get('assets', [])[(page - 1) * 100:page * 100]
        else:
            raise ValueError('No simulated GitHub response: ' + path)
        if result is None and allow_missing:
            return None
        if not isinstance(result, list if array else dict):
            raise ValueError('Invalid or missing simulated GitHub response: ' + path)
        return copy.deepcopy(result)

    def command(self, args, *, cwd):
        if any(value.startswith('--latest') for value in args):
            raise ValueError('Beta simulation forbids every Latest mutation')
        if args[:3] == ['gh', 'api', '--method']:
            tag = next(value.removeprefix('ref=refs/tags/') for value in args if value.startswith('ref=refs/tags/'))
            source = next(value[4:] for value in args if value.startswith('sha='))
            if args[3:5] != ['POST', f'repos/{REPOSITORY}/git/refs'] or tag in self.tags or not matches(SHA_PATTERN, source):
                raise ValueError('Invalid simulated tag creation')
            if version_channel(release_version(tag)) != 'beta':
                raise ValueError('Simulation can create beta tags only')
            self.tags[tag] = {'object': {'type': 'commit', 'sha': source}}
        elif args[:2] == ['gh', 'release'] and len(args) > 3:
            action, tag = args[2:4]
            if version_channel(release_version(tag)) != 'beta':
                raise ValueError('Simulation can change beta releases only')
            if action == 'create':
                if tag in self.releases or '--draft' not in args or '--prerelease' not in args:
                    raise ValueError('Simulation requires a new prerelease draft')
                self.next_id += 1
                self.releases[tag] = {'id': self.next_id, 'tag_name': tag, 'draft': True, 'prerelease': True,
                                      'name': args[args.index('--title') + 1],
                                      'body': Path(args[args.index('--notes-file') + 1]).read_text(encoding='utf-8'),
                                      'author': {'login': 'github-actions[bot]', 'type': 'Bot'}, 'assets': []}
            elif action == 'upload':
                item, path = self.releases[tag], Path(args[4])
                if not item['draft'] or any(asset['name'] == path.name for asset in item['assets']):
                    raise ValueError('Simulated uploads cannot replace or mutate public assets')
                data = path.read_bytes(); self.next_id += 1
                item['assets'].append({'id': self.next_id, 'name': path.name, 'size': len(data), 'state': 'uploaded'})
                self.assets[self.next_id] = data
            elif action == 'edit' and '--draft=false' in args and '--prerelease=true' in args:
                item = self.releases[tag]
                if item['prerelease'] is not True:
                    raise ValueError('Simulation cannot change stable release identity')
                item['draft'] = False
            else:
                raise ValueError('Unsupported simulated release mutation')
        else:
            raise ValueError('Unsupported simulated command')
        self.commands.append(list(args))


def _release_command(args, *, cwd):
    simulation = _SIMULATION.get()
    if simulation is not None:
        simulation.command(args, cwd=cwd)
    else:
        subprocess.run(args, cwd=cwd, check=True)


def simulate_beta_publication(root: Path, simulation: BetaReleaseSimulation, environ=None) -> str:
    """Exercise the real publication engine using only supplied in-memory I/O."""
    if type(simulation) is not BetaReleaseSimulation or _SIMULATION.get() is not None:
        raise ValueError('Beta simulation requires an isolated in-memory state object')
    if version_channel(read_object(root / 'packages/windows-release/package.json')['version']) != 'beta':
        raise ValueError('Beta simulation requires a beta Windows version')
    token = _SIMULATION.set(simulation)
    try:
        return _publish_release_engine(root, environ)
    finally:
        _SIMULATION.reset(token)


def publish_release(root: Path, environ=None) -> str:
    return _publish_release_engine(root, environ)


def _publish_release_engine(root: Path, environ=None) -> str:
    env = os.environ if environ is None else environ
    channel = version_channel(read_object(root / 'packages/windows-release/package.json')['version'])
    # Re-read approval, exact source, provenance, remote state immediately before writes.
    tag, version = check_release(root, env)
    approval, _ = approval_context(root, env)
    manifest = verify_build(root, approval, env)
    existing = api_get(f'releases/tags/{tag}', allow_missing=True)
    if existing is not None:
        complete, missing = validate_existing(root, existing, manifest)
        if complete:
            return f'Already confirmed, unchanged: {tag}'
    else:
        target = tag_commit(tag, allow_missing=True)
        if target is None:
            _release_command(['gh', 'api', '--method', 'POST', f'repos/{REPOSITORY}/git/refs',
                              '-f', f'ref=refs/tags/{tag}', '-f', f"sha={approval['releaseSha']}"], cwd=root)
        elif target != approval['releaseSha']:
            raise ValueError('Existing remote tag and approved release SHA must match')
        if tag_commit(tag) != approval['releaseSha']:
            raise ValueError('Created tag and approved release SHA must match')
        notes = root / 'dist/windows-release-body.md'
        notes.write_text(release_body(root, manifest), encoding='utf-8')
        _release_command(['gh', 'release', 'create', tag, '--repo', REPOSITORY, '--verify-tag', '--draft',
                          '--target', approval['releaseSha'], '--title', f'Hellgato Stream Dog {version}',
                          '--notes-file', str(notes), *(['--prerelease'] if channel == 'beta' else [])], cwd=root)
        existing = api_get(f'releases/tags/{tag}')
        _, missing = validate_existing(root, existing, manifest)
    (root / 'dist' / MANIFEST_NAME).write_bytes(json_bytes(manifest))
    # Upload identity manifest first. An interrupted upload can only resume matching missing assets.
    for name in sorted(missing, key=lambda name: name != MANIFEST_NAME):
        _release_command(['gh', 'release', 'upload', tag, str(root / 'dist' / name), '--repo', REPOSITORY], cwd=root)
    existing = api_get(f'releases/tags/{tag}')
    complete, missing = validate_existing(root, existing, manifest)
    if missing:
        raise ValueError('Draft upload is incomplete; publication stopped')
    # Guard the tag and monotonic Latest again after uploads, before finalization.
    check_release(root, env)
    if not complete:
        _release_command(['gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false',
                          *(['--prerelease=true'] if channel == 'beta' else ['--latest'])], cwd=root)
    final = api_get(f'releases/tags/{tag}')
    complete, missing = validate_existing(root, final, manifest)
    if not complete or missing:
        raise ValueError('Publication has not been independently confirmed')
    return f'Confirmed Windows {channel}: https://github.com/{REPOSITORY}/releases/tag/{tag}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('version', 'check', 'verify', 'artifacts', 'manifest', 'resolve', 'publish'))
    parser.add_argument('--json', action='store_true', help='Resolve command JSON output (also the default)')
    binding = parser.add_mutually_exclusive_group()
    binding.add_argument('--plan', choices=('.release/plan.json', '.release/candidate-plan.json'))
    binding.add_argument('--source-channel', choices=('beta',))
    parser.add_argument('--channel', choices=('stable', 'beta'), default='stable', help='Resolve channel only')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if (args.plan is not None or args.source_channel is not None) and args.command not in ('version', 'artifacts', 'manifest'):
            raise ValueError('Build plan/source-channel options apply only to version, artifacts, and manifest')
        if args.channel != 'stable' and args.command != 'resolve':
            raise ValueError('Release/check/publish channel is bound by the approved plan')
        binding = {'plan_path': args.plan, 'source_channel': args.source_channel}
        if args.command == 'version':
            print(installer_version(root, **binding))
        elif args.command == 'check':
            tag, _ = check_release(root)
            print(f'Release preflight passed: {tag}')
        elif args.command == 'verify':
            check_release(root, require_enabled=False)
            approval, _ = approval_context(root, os.environ, require_enabled=False)
            verify_build(root, approval, os.environ)
            print('Approved Windows build and immutable original artifact verified (read-only)')
        elif args.command == 'artifacts':
            check_artifacts(root, installer_version(root, **binding))
            print('Installer and SHA-256 verified')
        elif args.command == 'manifest':
            print(json.dumps(build_manifest(root, **binding)))
        elif args.command == 'resolve':
            result = resolve_windows_beta() if args.channel == 'beta' else resolve_windows_stable()
            for warning in result['diagnostics']:
                print(warning, file=sys.stderr)
            print(json.dumps(result['latest']))
        else:
            print(publish_release(root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Windows release check failed: {error}\n')


if __name__ == '__main__':
    main()

"""Windows release metadata, exact-build provenance, and immutable publication.

Publication is reached only through the shared release coordinator. Build-only
commands never write to GitHub. Network failures and unknown remote state fail
closed; retries never move tags, replace assets, or edit unknown releases.
"""

import argparse
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
SHA_PATTERN = r'[0-9a-f]{40}'
DIGEST_PATTERN = r'[0-9a-f]{64}'
LEGACY_VERSIONS = {'0.1.0', '0.1.1'}
MANIFEST_NAME = 'release-manifest.json'
BUILD_MANIFEST_NAME = 'build-manifest.json'
_VERIFY_SPEC = importlib.util.spec_from_file_location('hellgato_verify_artifact', Path(__file__).with_name('verify-artifact.py'))
_VERIFY_ARTIFACT = importlib.util.module_from_spec(_VERIFY_SPEC)
_VERIFY_SPEC.loader.exec_module(_VERIFY_ARTIFACT)
_VERIFIED_ARCHIVES = _VERIFY_ARTIFACT._VERIFIED_ARCHIVES


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def stable_version(tag: str) -> str:
    if not isinstance(tag, str) or not matches('v' + VERSION_PATTERN, tag):
        raise ValueError('Expected a stable tag vMAJOR.MINOR.PATCH, without leading zeros')
    return tag[1:]


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


def installer_version(root: Path) -> str:
    package = read_object(root / 'packages/windows-release/package.json')
    if package.get('name') != '@hellgato/windows-release' or package.get('private') is not True:
        raise ValueError('The Windows version source must be the private Windows package')
    version = stable_version('v' + package['version'])
    include = (root / 'installer/version.iss').read_text(encoding='utf-8')
    definitions = re.findall(r'^#define AppVersion "([^"\r\n]+)"\s*$', include, re.MULTILINE)
    source = (root / 'installer/hellgato.iss').read_text(encoding='utf-8')
    if definitions != [version] or len(re.findall(r'^#include "version.iss"[ \t]*$', source, re.MULTILINE)) != 1 or re.search(r'^#define AppVersion\b', source, re.MULTILINE):
        raise ValueError('Generated AppVersion/include does not match the Windows version source')
    metadata = read_object(root / 'app/release.json')
    if metadata != {'version': version, 'channel': 'stable'}:
        raise ValueError('App runtime release metadata does not match the Windows version source')
    return version


def api_get(path: str, *, allow_missing: bool = False, array: bool = False):
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
    stable_version(tag)
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
    # gh handles signed storage redirects without forwarding our Authorization.
    result = subprocess.run(
        ['gh', 'api', f'repos/{REPOSITORY}/releases/assets/{identifier}',
         '-H', 'Accept: application/octet-stream'], check=True, capture_output=True,
    ).stdout
    if not isinstance(result, bytes) or len(result) != asset.get('size'):
        raise ValueError('Release asset download is incomplete')
    return result


def artifact_archive_bytes(identifier: int) -> bytes:
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
    version = stable_version(manifest.get('tag'))
    if (type(manifest.get('schemaVersion')) is not int or manifest.get('schemaVersion') != 1 or manifest.get('repository') != REPOSITORY
            or manifest.get('component') != 'windows' or manifest.get('channel') != 'stable'
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


def resolve_windows_stable() -> dict:
    """Ignore npm namespaces; inspect every page and compare numeric versions."""
    candidates, diagnostics = [], []
    for release in api_pages('releases'):
        if not isinstance(release.get('draft'), bool) or not isinstance(release.get('prerelease'), bool):
            raise ValueError('Unexpected release state')
        if release['draft'] or release['prerelease']:
            continue
        tag = release.get('tag_name')
        if not isinstance(tag, str):
            raise ValueError('Missing release tag')
        if not matches('v' + VERSION_PATTERN, tag):
            continue
        version = stable_version(tag)
        assets = release_assets(release)
        installer = f'Hellgato-{version}-Setup.exe'
        required = {installer, installer + '.sha256'}
        manifest = None
        if MANIFEST_NAME in assets:
            manifest = parse_json(asset_bytes(assets[MANIFEST_NAME]))
            if not isinstance(manifest, dict):
                raise ValueError(f'{tag}: malformed Windows manifest')
            validate_manifest(manifest, published=True)
            if manifest['tag'] != tag or manifest['releaseSha'] != tag_commit(tag):
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
    latest = max(candidates, key=lambda item: tuple(map(int, item['version'].split('.'))), default=None)
    return {'latest': latest, 'diagnostics': diagnostics}


def check_artifacts(root: Path, version: str) -> tuple[Path, Path]:
    stable_version('v' + version)
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
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def source_provenance(root: Path, version: str) -> tuple[dict, dict]:
    paths = ['packages/windows-release/package.json', 'app/release.json', 'installer/version.iss',
             'installer/requirements-build.txt', 'scripts/prepare-windows.py', 'scripts/prepare-cora.mjs',
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


def build_manifest(root: Path, environ=None) -> dict:
    env = os.environ if environ is None else environ
    version = installer_version(root)
    source = env.get('RELEASE_SHA') or env.get('GITHUB_SHA', '')
    if not matches(SHA_PATTERN, source) or checkout_sha(root) != source:
        raise ValueError('Build checkout and full release SHA must match')
    files = check_artifacts(root, version)
    plan = root / '.release/plan.json'
    sources, toolchain = source_provenance(root, version)
    manifest = {'schemaVersion': 1, 'repository': REPOSITORY, 'component': 'windows',
                'version': version, 'channel': 'stable', 'tag': 'v' + version, 'releaseSha': source,
                'planSha256': sha256(plan.read_bytes()) if plan.is_file() else None,
                'build': {'runId': positive_id(env.get('GITHUB_RUN_ID'), 'build run ID'),
                          'runAttempt': positive_id(env.get('GITHUB_RUN_ATTEMPT'), 'build run attempt')},
                'files': [{'name': path.name, 'size': path.stat().st_size, 'sha256': sha256(path.read_bytes())} for path in files],
                'sources': sources, 'toolchain': toolchain}
    validate_manifest(manifest)
    (root / 'dist' / BUILD_MANIFEST_NAME).write_bytes(json_bytes(manifest))
    return manifest


def approval_context(root: Path, env: dict, *, require_enabled=True) -> tuple[dict, dict]:
    if ((require_enabled and env.get('RELEASE_ENABLED') != 'true') or env.get('GITHUB_REPOSITORY') != REPOSITORY
            or env.get('GITHUB_REF') != 'refs/heads/main'
            or env.get('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch')):
        raise ValueError('Publishing requires enabled shared release orchestration on main')
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
    version = installer_version(root)
    if component.get('version') != version or component.get('channel') != 'stable' or component.get('tag') != 'v' + version or component.get('notes') != f'docs/releases/{version}.md':
        raise ValueError('Approved Windows version/tag/notes does not match AppVersion')
    notes = root / component['notes']
    if not notes.is_file() or notes.is_symlink() or not notes.read_text(encoding='utf-8').strip():
        raise ValueError('Non-empty, version-specific release notes are required')
    if checkout_sha(root) != source:
        raise ValueError('Checkout and approved release SHA must match')
    ancestry = api_get(f'compare/{source}...main')
    if ancestry.get('status') not in ('ahead', 'identical') or ancestry.get('merge_base_commit', {}).get('sha') != source:
        raise ValueError('The approved release SHA must be in main history')
    remote_pr = api_get(f"pulls/{pr['number']}")
    owner = REPOSITORY.split('/')[0]
    if (remote_pr.get('merged') is not True or remote_pr.get('state') != 'closed'
            or remote_pr.get('merge_commit_sha') != source
            or remote_pr.get('base', {}).get('ref') != 'main'
            or remote_pr.get('base', {}).get('repo', {}).get('full_name') != REPOSITORY
            or remote_pr.get('head', {}).get('repo', {}).get('full_name') != REPOSITORY
            or remote_pr.get('head', {}).get('sha') != pr['headSha']
            or remote_pr.get('merged_by', {}).get('login') != owner or pr['mergedBy'] != owner):
        raise ValueError('Approval is not the exact owner-merged release PR')
    if approval['bootstrap']:
        trusted_pr = remote_pr.get('user', {}).get('login') == owner and matches(r'release/bootstrap-[a-z0-9-]+', remote_pr.get('head', {}).get('ref', ''))
    else:
        trusted_pr = remote_pr.get('user', {}).get('login') == 'github-actions[bot]' and remote_pr.get('head', {}).get('ref') == 'changeset-release/main'
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
    if manifest['releaseSha'] != approval['releaseSha'] or manifest['planSha256'] != approval['planSha256'] or manifest['version'] != installer_version(root):
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
    if (artifact.get('id') != identifier or artifact.get('name') != name or artifact.get('expired') is not False
            or run_info.get('id') != build['runId'] or not matches(SHA_PATTERN, run_info.get('head_sha', ''))
            or run_info.get('head_branch') != 'main' or not matches('sha256:' + DIGEST_PATTERN, artifact.get('digest', ''))):
        raise ValueError('Artifact ID does not identify the original unexpired approved build')
    run = api_get(f"actions/runs/{build['runId']}/attempts/{build['runAttempt']}")
    if (run.get('id') != build['runId'] or run.get('run_attempt') != build['runAttempt']
            or run.get('repository', {}).get('full_name') != REPOSITORY
            or run.get('head_repository', {}).get('full_name') != REPOSITORY
            or run.get('head_sha') != run_info['head_sha'] or run.get('head_branch') != 'main'
            or run.get('event') not in ('push', 'workflow_dispatch')
            or run.get('path', '').split('@')[0] != '.github/workflows/release.yml'
            or run.get('status') not in ('in_progress', 'completed')):
        raise ValueError('Original source run is not an approved main Windows build')
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
    result = resolve_windows_stable()
    for warning in result['diagnostics']:
        print(warning, file=sys.stderr)
    latest = result['latest']
    existing = api_get(f"releases/tags/{component['tag']}", allow_missing=True)
    completed = False
    if existing is not None:
        # This validates every existing byte before check can report success.
        manifest = verify_build(root, approval, env)
        completed, _ = validate_existing(root, existing, manifest)
    version_tuple = tuple(map(int, component['version'].split('.')))
    if not completed:
        if latest and version_tuple <= tuple(map(int, latest['version'].split('.'))):
            raise ValueError('The new version must be greater than the latest Windows stable release')
        previous = latest['version'] if latest else None
        if component.get('oldVersion') != previous:
            raise ValueError('Approved oldVersion does not match the latest published Windows stable')
        if approval['bootstrap'] and (component['version'] != '0.1.2' or previous != '0.1.1'):
            raise ValueError('Windows bootstrap is only the approved 0.1.1 to 0.1.2 candidate')
    return component['tag'], component['version']


def release_body(root: Path, manifest: dict) -> str:
    digest = sha256(json_bytes(manifest))
    notes = (root / f"docs/releases/{manifest['version']}.md").read_text(encoding='utf-8').rstrip()
    return notes + f'\n\n<!-- hellgato-release:windows:{digest} -->\n'


def validate_existing(root: Path, existing: dict, manifest: dict) -> tuple[bool, list[str]]:
    if (existing.get('tag_name') != manifest['tag'] or existing.get('prerelease') is not False
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


def publish_release(root: Path, environ=None) -> str:
    env = os.environ if environ is None else environ
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
            subprocess.run(['gh', 'api', '--method', 'POST', f'repos/{REPOSITORY}/git/refs',
                            '-f', f'ref=refs/tags/{tag}', '-f', f"sha={approval['releaseSha']}"], cwd=root, check=True)
        elif target != approval['releaseSha']:
            raise ValueError('Existing remote tag and approved release SHA must match')
        if tag_commit(tag) != approval['releaseSha']:
            raise ValueError('Created tag and approved release SHA must match')
        notes = root / 'dist/windows-release-body.md'
        notes.write_text(release_body(root, manifest), encoding='utf-8')
        subprocess.run(['gh', 'release', 'create', tag, '--repo', REPOSITORY, '--verify-tag', '--draft',
                        '--target', approval['releaseSha'], '--title', f'Hellgato Stream Dog {version}',
                        '--notes-file', str(notes)], cwd=root, check=True)
        existing = api_get(f'releases/tags/{tag}')
        _, missing = validate_existing(root, existing, manifest)
    (root / 'dist' / MANIFEST_NAME).write_bytes(json_bytes(manifest))
    # Upload identity manifest first. An interrupted upload can only resume matching missing assets.
    for name in sorted(missing, key=lambda name: name != MANIFEST_NAME):
        subprocess.run(['gh', 'release', 'upload', tag, str(root / 'dist' / name), '--repo', REPOSITORY], cwd=root, check=True)
    existing = api_get(f'releases/tags/{tag}')
    complete, missing = validate_existing(root, existing, manifest)
    if missing:
        raise ValueError('Draft upload is incomplete; publication stopped')
    # Guard the tag and monotonic Latest again after uploads, before finalization.
    check_release(root, env)
    if not complete:
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest'], cwd=root, check=True)
    final = api_get(f'releases/tags/{tag}')
    complete, missing = validate_existing(root, final, manifest)
    if not complete or missing:
        raise ValueError('Publication has not been independently confirmed')
    return f'Confirmed Windows stable: https://github.com/{REPOSITORY}/releases/tag/{tag}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('version', 'check', 'verify', 'artifacts', 'manifest', 'resolve', 'publish'))
    parser.add_argument('--json', action='store_true', help='Resolve command JSON output (also the default)')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == 'version':
            print(installer_version(root))
        elif args.command == 'check':
            tag, _ = check_release(root)
            print(f'Release preflight passed: {tag}')
        elif args.command == 'verify':
            check_release(root, require_enabled=False)
            approval, _ = approval_context(root, os.environ, require_enabled=False)
            verify_build(root, approval, os.environ)
            print('Approved Windows build and immutable original artifact verified (read-only)')
        elif args.command == 'artifacts':
            check_artifacts(root, installer_version(root))
            print('Installer and SHA-256 verified')
        elif args.command == 'manifest':
            print(json.dumps(build_manifest(root)))
        elif args.command == 'resolve':
            result = resolve_windows_stable()
            for warning in result['diagnostics']:
                print(warning, file=sys.stderr)
            print(json.dumps(result['latest']))
        else:
            print(publish_release(root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Windows release check failed: {error}\n')


if __name__ == '__main__':
    main()

"""Bounded, fail-closed candidate inventories and reversible local snapshots."""
from __future__ import annotations

from collections.abc import Collection
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
from typing import Any, Literal, TypedDict, cast


# Raw JSON remains untrusted until the existing runtime checks pass. File-copy
# and backup operations exchange typed records after that validation boundary.
JsonObject = dict[str, Any]


class FileDigest(TypedDict):
    size: int
    sha256: str


class InventoryEntry(FileDigest):
    path: str


class BackupManifest(TypedDict):
    schemaVersion: Literal[1]
    component: Literal["ipc-candidate-backup"]
    sourceSha: str
    createdAt: int
    identity: int
    settingsAbsent: list[str]
    profilesExisted: bool
    files: list[InventoryEntry]

MAX_FILES = 10000
MAX_BYTES = 1024 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
SHA256 = re.compile(r'[a-f0-9]{64}\Z')
SHA = re.compile(r'[a-f0-9]{40}\Z')


def _ordinary_path(path: Path) -> Path:
    if os.name != 'nt':
        return Path(os.path.abspath(path))
    value = str(path).replace('/', '\\')
    if value.startswith('\\\\.\\'):
        raise ValueError('Windows device namespaces are forbidden')
    if value[:8].upper() == '\\\\?\\UNC\\':
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
        if not re.match(r'^[A-Za-z]:\\', value):
            raise ValueError('Unsupported Windows extended path namespace')
    return Path(os.path.abspath(value))


def _io_path(path: Path) -> Path:
    """Use extended Windows I/O without relying on the machine's long-path policy."""
    if os.name != 'nt':
        return path
    value = str(_ordinary_path(path))
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def _canonical_path(path: Path) -> Path:
    # Call only after checking original ancestors so resolution cannot hide links.
    return _ordinary_path(_io_path(path).resolve())


def regular(path: Path) -> None:
    path = _io_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Links and non-regular files are forbidden')


def safe_relative(value: str) -> PurePosixPath:
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value or '\0' in value
            or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Unsafe inventory member')
    return PurePosixPath(value)


def local_path(root: Path, relative: str) -> Path:
    parts = safe_relative(relative).parts
    current = _io_path(root)
    for part in parts:
        current = current / part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked inventory path is forbidden')
    return current


def digest(path: Path) -> str:
    path = _io_path(path)
    regular(path)
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read_json(path: Path, limit: int = MAX_JSON) -> JsonObject:
    path = _io_path(path)
    regular(path)
    if path.stat().st_size > limit:
        raise ValueError('JSON file size limit exceeded')
    def unique_object(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate JSON object member')
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise ValueError('Non-finite JSON value')

    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
    except RecursionError as error:
        raise ValueError('JSON nesting limit exceeded') from error
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return cast(JsonObject, value)


def inventory(root: Path, exclude: Collection[str] = ()) -> list[InventoryEntry]:
    root = _io_path(root)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink() or getattr(root_info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Linked or non-directory inventory root is forbidden')
    result: list[InventoryEntry] = []
    total = 0
    def fail_walk(error: OSError) -> None:
        raise error
    for folder, directories, names in os.walk(root, followlinks=False, onerror=fail_walk):
        directory = Path(folder)
        for name in directories:
            child = directory / name
            if child.is_symlink() or getattr(child.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked directory is forbidden')
        for name in names:
            child = directory / name
            relative = child.relative_to(root).as_posix()
            safe_relative(relative)
            if relative in exclude:
                continue
            regular(child)
            size = child.stat().st_size
            total += size
            if len(result) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError('Snapshot size or file-count limit exceeded')
            result.append({'path': relative, 'size': size, 'sha256': digest(child)})
    return sorted(result, key=lambda entry: entry['path'])


def verify_inventory(root: Path, entries: object, exclude: Collection[str] = ()) -> None:
    if not isinstance(entries, list) or len(entries) > MAX_FILES:
        raise ValueError('Invalid inventory')
    previous = None
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}
                or type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_BYTES
                or not isinstance(entry['sha256'], str) or not SHA256.fullmatch(entry['sha256'])):
            raise ValueError('Invalid inventory entry')
        safe_relative(entry['path'])
        if previous is not None and entry['path'] <= previous:
            raise ValueError('Inventory must be unique and sorted')
        previous = entry['path']
    if inventory(root, exclude) != entries:
        raise ValueError('Candidate or backup inventory does not match its manifest')


def verify_bundle(root: Path) -> JsonObject:
    root = _io_path(root)
    manifest = read_json(root / 'candidate-manifest.json')
    if (type(manifest.get('schemaVersion')) is not int or manifest.get('schemaVersion') != 1 or manifest.get('component') != 'stream-dog-ipc-candidate'
            or manifest.get('channel') != 'experimental-ci' or manifest.get('physicalHardwareVerified') is not False
            or not isinstance(manifest.get('sourceSha'), str) or not SHA.fullmatch(manifest['sourceSha'])
            or manifest.get('buildId') != 'windows-ipc-' + manifest['sourceSha']
            or manifest.get('entrypoint') != 'HellgatoIpcTest.exe'):
        raise ValueError('Invalid experimental candidate manifest')
    verify_inventory(root, manifest.get('files'), ('candidate-manifest.json',))
    runtime = read_json(root / 'worker/runtime-manifest.json')
    if (type(runtime.get('schemaVersion')) is not int or runtime.get('schemaVersion') != 1 or runtime.get('buildId') != manifest['buildId']
            or runtime.get('platform') != 'win32' or runtime.get('arch') != 'x64'
            or runtime.get('executableSha256') != digest(root / 'worker/HellgatoMiraboxWorker.exe')):
        raise ValueError('Frozen worker identity mismatch')
    verify_inventory(root / 'worker', runtime.get('files'), ('runtime-manifest.json',))
    return manifest


def existing_identity(state: Path) -> tuple[JsonObject, JsonObject]:
    state = _io_path(state)
    settings = read_json(state / 'settings.json', 128 * 1024)
    registry = read_json(state / 'devices.json', 1024 * 1024)
    if type(settings.get('version')) is not int or settings.get('version') != 1 or settings.get('stripMode') not in ('frame', 'panels'):
        raise ValueError('Existing Hellgato settings are missing or unsupported; identity will not be created')
    if any(type(settings.get(key)) is not int or not 1 <= settings[key] <= 999999 for key in ('identity', 'dockIdentity')):
        raise ValueError('Invalid saved virtual identity')
    records = registry.get('devices')
    if type(registry.get('version')) is not int or registry.get('version') != 1 or not isinstance(records, list) or len(records) > 1000:
        raise ValueError('Invalid saved device registry')
    selected = [item for item in records if isinstance(item, dict) and item.get('identity') == settings['identity']]
    if len(selected) != 1:
        raise ValueError('One previously registered physical device is required for this trial')
    record = selected[0]
    if (type(record.get('identity')) is not int or type(record.get('dockIdentity')) is not int
            or record.get('dockIdentity') != settings['dockIdentity'] or record.get('model') != 'StreamDockN4Pro'
            or record.get('virtualSerial') != f"HGMOCK{settings['identity']:06d}"
            or not isinstance(record.get('id'), str) or not re.fullmatch(r'n4-[a-f0-9]{64}', record['id'])
            or record.get('identitySource') not in ('serial', 'usb-path')
            or any(type(record.get(key)) is not int or not 0 <= record[key] <= 65535 for key in ('vendorId', 'productId'))):
        raise ValueError('Saved physical and virtual identities do not agree')
    if record['identitySource'] == 'serial':
        serial = record.get('physicalSerial')
        if not isinstance(serial, str) or not serial or len(serial) > 1024:
            raise ValueError('Invalid saved physical serial')
        fingerprint = json.dumps(['StreamDockN4Pro', record['vendorId'], record['productId'], 'serial', serial], ensure_ascii=True, separators=(',', ':'))
        if record['id'] != 'n4-' + hashlib.sha256(fingerprint.encode()).hexdigest():
            raise ValueError('Saved physical fingerprint does not match')
    elif record.get('physicalSerial') is not None:
        raise ValueError('USB-path identities cannot have a serial')
    return settings, record


def copy_verified(source: Path, destination: Path, expected: FileDigest) -> None:
    source, destination = _io_path(source), _io_path(destination)
    regular(source)
    if source.stat().st_size != expected['size'] or digest(source) != expected['sha256']:
        raise ValueError('Source changed during snapshot')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if destination.stat().st_size != expected['size'] or digest(destination) != expected['sha256'] or digest(source) != expected['sha256']:
        raise ValueError('Snapshot copy changed or failed verification')


def make_snapshot(state: Path, profiles: Path, destination: Path, identity: int, source_sha: str) -> BackupManifest:
    plain_directory_path(state)
    plain_directory_path(profiles.parent)
    if _io_path(profiles).exists() or _io_path(profiles).is_symlink():
        plain_directory_path(profiles)
    plain_directory_path(destination.parent, missing=True)
    state, profiles, destination = (_canonical_path(path) for path in (state, profiles, destination))
    if _io_path(destination).exists() or _io_path(destination).is_symlink():
        raise ValueError('Snapshot destination already exists')
    if any(destination == path or destination.is_relative_to(path) or path.is_relative_to(destination)
           for path in (state, profiles)):
        raise ValueError('Snapshot source and destination locations must be separate')
    if not isinstance(source_sha, str) or not SHA.fullmatch(source_sha):
        raise ValueError('Invalid candidate source')
    if type(identity) is not int or not 1 <= identity <= 999999:
        raise ValueError('Invalid snapshot identity')
    state, profiles, destination = (_io_path(path) for path in (state, profiles, destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    prepared = Path(tempfile.mkdtemp(prefix='.ipc-backup-stage-', dir=destination.parent))
    wanted = ('settings.json', 'devices.json', f'device-controls-HGMOCK{identity:06d}.json', f'display-HGMOCK{identity:06d}.json')
    absent: list[str] = []
    saved: dict[str, FileDigest] = {}
    profiles_existed = profiles.exists()
    try:
        for name in wanted:
            source = state / name
            if not source.exists() and not source.is_symlink():
                absent.append(name)
                continue
            regular(source)
            if source.stat().st_size > 1024 * 1024:
                raise ValueError('Saved settings exceed snapshot limit')
            saved[name] = {'size': source.stat().st_size, 'sha256': digest(source)}
            copy_verified(source, prepared / 'settings' / name, saved[name])
        profile_files = inventory(profiles) if profiles_existed else []
        for entry in profile_files:
            copy_verified(local_path(profiles, entry['path']), local_path(prepared / 'profiles', entry['path']), entry)
        if (profiles.exists() != profiles_existed or profiles.is_symlink()
                or profiles_existed and inventory(profiles) != profile_files):
            raise ValueError('Stream Deck profiles changed during snapshot; close Stream Deck and retry')
        for name in wanted:
            source = state / name
            if name in absent:
                if source.exists() or source.is_symlink():
                    raise ValueError('Saved settings appeared during snapshot')
            elif source.stat().st_size != saved[name]['size'] or digest(source) != saved[name]['sha256']:
                raise ValueError('Saved settings changed during snapshot')
        result: BackupManifest = {'schemaVersion': 1, 'component': 'ipc-candidate-backup', 'sourceSha': source_sha,
                  'createdAt': int(time.time()), 'identity': identity,
                  'settingsAbsent': absent, 'profilesExisted': profiles_existed, 'files': inventory(prepared)}
        (prepared / 'backup-manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        verify_snapshot(prepared)
        if destination.exists() or destination.is_symlink():
            raise ValueError('Snapshot destination appeared during preparation')
        prepared.rename(destination)
        return result
    finally:
        if prepared.exists():
            shutil.rmtree(prepared)


def verify_snapshot(root: Path) -> BackupManifest:
    root = _io_path(root)
    value = read_json(root / 'backup-manifest.json')
    if (type(value.get('schemaVersion')) is not int or value.get('schemaVersion') != 1 or value.get('component') != 'ipc-candidate-backup'
            or not isinstance(value.get('sourceSha'), str) or not SHA.fullmatch(value['sourceSha'])
            or type(value.get('createdAt')) is not int or value['createdAt'] < 0 or type(value.get('identity')) is not int
            or not 1 <= value['identity'] <= 999999 or type(value.get('profilesExisted')) is not bool):
        raise ValueError('Invalid backup metadata')
    wanted = {'settings.json', 'devices.json', f"device-controls-HGMOCK{value['identity']:06d}.json", f"display-HGMOCK{value['identity']:06d}.json"}
    absent = value.get('settingsAbsent')
    if (not isinstance(absent, list) or not all(isinstance(name, str) for name in absent)
            or len(absent) != len(set(absent)) or not set(absent) <= wanted):
        raise ValueError('Invalid backup settings metadata')
    verify_inventory(root, value.get('files'), ('backup-manifest.json',))
    for entry in value['files']:
        path = safe_relative(entry['path'])
        if path.parts[0] == 'settings':
            if len(path.parts) != 2 or path.name not in wanted or path.name in absent:
                raise ValueError('Unexpected saved settings file')
        elif path.parts[0] != 'profiles' or len(path.parts) < 2 or not value['profilesExisted']:
            raise ValueError('Unexpected backup entry')
    names = {entry['path'].split('/')[1] for entry in value['files'] if entry['path'].startswith('settings/')}
    if names | set(absent) != wanted or not {'settings.json', 'devices.json'} <= names:
        raise ValueError('Incomplete settings backup')
    return cast(BackupManifest, value)


def plain_directory_path(path: Path, *, missing: bool = False) -> None:
    """Reject every link/reparse ancestor before snapshot or restore mutations."""
    path = path.absolute()
    for current in [*reversed(path.parents), path]:
        current = _io_path(current)
        if not current.exists() and not current.is_symlink():
            if missing:
                continue
            raise ValueError('Required directory is missing')
        info = current.lstat()
        if (current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400
                or not stat.S_ISDIR(info.st_mode)):
            raise ValueError('Linked or non-directory restore path is forbidden')


def restore_snapshot(backup: Path, state: Path, profiles: Path) -> Path:
    """Preflight/stage everything before a reversible, bounded local transaction.

    Caller holds launcher ownership, requires the host stopped, and gets consent.
    Unknown files stay untouched; displaced profile trees and settings are retained.
    """
    plain_directory_path(backup)
    plain_directory_path(state)
    plain_directory_path(profiles.parent)
    if _io_path(profiles).exists() or _io_path(profiles).is_symlink():
        plain_directory_path(profiles)
    backup, state, profiles = (_canonical_path(path) for path in (backup, state, profiles))
    manifest = verify_snapshot(backup)
    if state == profiles or state.is_relative_to(profiles) or profiles.is_relative_to(state):
        raise ValueError('Restore locations must be separate')
    if any(backup == path or backup.is_relative_to(path) or path.is_relative_to(backup) for path in (state, profiles)):
        raise ValueError('Backup and restore locations must be separate')
    backup, state, profiles = (_io_path(path) for path in (backup, state, profiles))
    settings = [entry for entry in manifest['files'] if entry['path'].startswith('settings/')]
    current: dict[str, FileDigest | None] = {}
    for entry in settings:
        name = PurePosixPath(entry['path']).name
        target = state / name
        if target.exists() or target.is_symlink():
            regular(target)
            if target.stat().st_size > MAX_JSON:
                raise ValueError('Existing settings are too large to preserve safely')
            current[name] = {'size': target.stat().st_size, 'sha256': digest(target)}
        else:
            current[name] = None
    profile_before = inventory(profiles) if profiles.exists() else None
    prepared = Path(tempfile.mkdtemp(prefix='.ipc-restore-stage-', dir=state.parent))
    prepared_profiles = None
    preserved = None
    previous_profiles = None
    profile_installed = False
    replaced = []
    cleanup = True
    try:
        # All replacements and all old settings are copied and verified before
        # any original settings/profile file is replaced.
        for entry in settings:
            name = PurePosixPath(entry['path']).name
            copy_verified(backup / entry['path'], prepared / 'replacement' / name, entry)
            if current[name] is not None:
                copy_verified(state / name, prepared / 'previous' / name, cast(FileDigest, current[name]))
        if manifest['profilesExisted']:
            prepared_profiles = Path(tempfile.mkdtemp(prefix='.ipc-profiles-stage-', dir=profiles.parent))
            for entry in manifest['files']:
                if entry['path'].startswith('profiles/'):
                    relative = entry['path'][len('profiles/'):]
                    copy_verified(backup / entry['path'], local_path(prepared_profiles, relative), entry)
        # Recheck the complete target snapshot immediately before mutation.
        plain_directory_path(state)
        plain_directory_path(profiles.parent)
        for name, previous_entry in current.items():
            target = state / name
            if previous_entry is None:
                if target.exists() or target.is_symlink():
                    raise ValueError('Settings appeared during restore preparation')
            elif target.stat().st_size != previous_entry['size'] or digest(target) != previous_entry['sha256']:
                raise ValueError('Settings changed during restore preparation')
        if profiles.exists() or profiles.is_symlink():
            plain_directory_path(profiles)
        if (inventory(profiles) if profiles.exists() else None) != profile_before:
            raise ValueError('Profiles changed during restore preparation')
        preserved = state / ('before-ipc-rollback-' + str(time.time_ns()))
        (prepared / 'previous').mkdir(exist_ok=True)
        os.replace(prepared / 'previous', preserved)
        if prepared_profiles is not None:
            if profiles.exists():
                previous_profiles = profiles.parent / ('ProfilesV3.before-ipc-rollback-' + str(time.time_ns()))
                os.replace(profiles, previous_profiles)
            os.replace(prepared_profiles, profiles)
            prepared_profiles = None
            profile_installed = True
        for entry in settings:
            name = PurePosixPath(entry['path']).name
            os.replace(prepared / 'replacement' / name, state / name)
            replaced.append(name)
        return _ordinary_path(preserved)
    except BaseException as original:
        # Restore previous contents on recoverable mid-transaction failure.
        # A failed undo must not prevent the other independent restores, and
        # repeated interruption must never discard the staged recovery copies.
        cleanup = False
        failures: list[Exception] = []
        for name in reversed(replaced):
            try:
                if current[name] is None:
                    (state / name).unlink(missing_ok=True)
                else:
                    retry = prepared / ('undo-' + name)
                    shutil.copyfile(cast(Path, preserved) / name, retry)
                    os.replace(retry, state / name)
            except Exception as error:
                failures.append(error)
        if profile_installed:
            try:
                failed = profiles.parent / ('ProfilesV3.interrupted-ipc-restore-' + str(time.time_ns()))
                os.replace(profiles, failed)
            except Exception as error:
                failures.append(error)
        if previous_profiles is not None and previous_profiles.exists() and not profiles.exists():
            try:
                os.replace(previous_profiles, profiles)
            except Exception as error:
                failures.append(error)
        if failures:
            raise RuntimeError('Restore was interrupted and recovery could not finish. Keep both apps closed. '
                               f'Recovery staging: {prepared}; previous settings: {preserved}; '
                               f'previous profiles: {previous_profiles}') from original
        cleanup = True
        raise
    finally:
        if cleanup:
            if prepared_profiles is not None and prepared_profiles.exists():
                shutil.rmtree(prepared_profiles)
            shutil.rmtree(prepared)

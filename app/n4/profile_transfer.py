"""Transfer N4 profiles without replacing the current device identity."""

from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import zipfile


PROFILE_NAME = re.compile(r'^[0-9a-fA-F-]{36}\.sdProfile$')


class ProfileError(ValueError):
    def __init__(self, key):
        super().__init__(key)
        self.key = key


def device_uuid(identity):
    return f'@(128)[4057/132/HGMOCK{identity:06d}]'


def export_profiles(destination, profiles_root, identity):
    profiles_root = Path(profiles_root)
    selected = []
    for profile in sorted(profiles_root.glob('*.sdProfile')):
        manifest = json.loads((profile / 'manifest.json').read_text(encoding='utf-8-sig'))
        if manifest.get('Device', {}).get('UUID') == device_uuid(identity):
            selected.append(profile)
    if not selected:
        raise ProfileError('profile.no_profiles')
    destination = Path(destination)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('package.json', json.dumps({'FormatVersion': 1, 'HellgatoBundleVersion': 1,
                                                       'DeviceModel': '20GBD9901'}))
            for profile in selected:
                for source in sorted(profile.rglob('*')):
                    if not source.is_file():
                        continue
                    data = source.read_bytes()
                    if source == profile / 'manifest.json':
                        manifest = json.loads(data.decode('utf-8-sig'))
                        manifest['AppIdentifier'] = '*'
                        data = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
                    archive.writestr('Profiles/' + source.relative_to(profiles_root).as_posix(), data)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(selected)


def read_profiles(sources):
    profiles = {}
    total = 0
    entries = 0
    for source in sources:
        with zipfile.ZipFile(source) as archive:
            package_entry = archive.getinfo('package.json')
            if package_entry.file_size > 65536:
                raise ProfileError('profile.metadata_size')
            package = json.loads(archive.read(package_entry).decode('utf-8-sig'))
            if package.get('FormatVersion') != 1 or package.get('DeviceModel') != '20GBD9901':
                raise ProfileError('profile.device_model')
            current = {}
            for entry in archive.infolist():
                if entry.is_dir() or entry.filename == 'package.json':
                    continue
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or '\\' in entry.filename or ':' in entry.filename
                        or '..' in path.parts or len(path.parts) < 3 or path.parts[0] != 'Profiles'
                        or not PROFILE_NAME.fullmatch(path.parts[1])):
                    raise ProfileError('profile.invalid_path')
                total += entry.file_size
                entries += 1
                if total > 256 * 1024 * 1024 or entries > 10000:
                    raise ProfileError('profile.size_limit')
                relative = Path(*path.parts[2:])
                files = current.setdefault(path.parts[1], {})
                if relative in files:
                    raise ProfileError('profile.duplicate_file')
                files[relative] = archive.read(entry)
            if profiles.keys() & current.keys():
                raise ProfileError('profile.duplicate_profile')
            profiles.update(current)
    if not profiles:
        raise ProfileError('profile.empty')
    for files in profiles.values():
        manifest = json.loads(files[Path('manifest.json')].decode('utf-8-sig'))
        if manifest.get('Version') != '3.0' or not isinstance(manifest.get('Device'), dict):
            raise ProfileError('profile.format')
    return profiles


def install_profiles(profiles, profiles_root, identity, state_root):
    """Install validated profiles while Stream Deck is stopped; retain rollback copies."""
    profiles_root = Path(profiles_root).resolve()
    profiles_root.mkdir(parents=True, exist_ok=True)
    backup = Path(state_root) / 'profile-import-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    installed = []
    moved = []
    with tempfile.TemporaryDirectory(prefix='hellgato-import-', dir=profiles_root.parent) as staging:
        staging = Path(staging)
        for name, files in profiles.items():
            if not PROFILE_NAME.fullmatch(name) or (profiles_root / name).resolve().parent != profiles_root:
                raise ProfileError('profile.destination')
            for relative, data in files.items():
                target = staging / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative == Path('manifest.json'):
                    manifest = json.loads(data.decode('utf-8-sig'))
                    manifest['Device'] = {'Model': '20GBD9901', 'UUID': device_uuid(identity)}
                    manifest['AppIdentifier'] = '*'
                    data = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
                target.write_bytes(data)
        try:
            for name in profiles:
                target = profiles_root / name
                if target.exists():
                    shutil.move(str(target), str(backup / name))
                    moved.append(name)
                os.replace(staging / name, target)
                installed.append(name)
        except Exception:
            for name in installed:
                os.replace(profiles_root / name, staging / name)
            for name in moved:
                shutil.move(str(backup / name), str(profiles_root / name))
            raise
    return len(profiles), backup

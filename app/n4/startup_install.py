"""Install only Hellgato-owned startup files beside an unchanged host executable."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

from startup_plan import build_plan


OWNER = 'Hellgato.Startup.VERSION/1'
MANIFEST = 'HellgatoStartup.json'
PLAN = 'HellgatoStartup.plan'
DLL = 'version.dll'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def default_executable():
    return Path(os.environ['ProgramFiles']) / 'Elgato/StreamDeck/StreamDeck.exe'


def startup_dll(root):
    candidates = [Path(root) / 'native/version.dll', Path(root) / 'work/build-native-startup/version.dll']
    return next((path for path in candidates if path.is_file()), candidates[0])


def owned_files(folder):
    """An altered or unrelated file blocks both replacement and removal."""
    folder = Path(folder)
    manifest = folder / MANIFEST
    if not manifest.exists():
        if any((folder / name).exists() for name in (DLL, PLAN)):
            raise ValueError('An existing startup DLL or plan is not owned by Hellgato')
        return None
    record = json.loads(manifest.read_text(encoding='utf-8'))
    if record.get('owner') != OWNER or set(record.get('files', {})) != {DLL, PLAN}:
        raise ValueError('Unknown startup ownership manifest')
    for name, expected in record['files'].items():
        path = folder / name
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f'Startup file changed outside Hellgato: {name}')
    return record


def installed(executable, dll):
    executable, dll = Path(executable), Path(dll)
    record = owned_files(executable.parent)
    return bool(record and record.get('executableSha256') == digest(executable.read_bytes())
                and record['files'][DLL] == digest(dll.read_bytes()))


def validate_dll(data):
    import pefile
    pe = pefile.PE(data=data, fast_load=True)
    try:
        pe.parse_data_directories(directories=[0])
        exports = {entry.name for entry in pe.DIRECTORY_ENTRY_EXPORT.symbols}
        if pe.FILE_HEADER.Machine != 0x8664 or b'HellgatoStartupIdentity' not in exports:
            raise ValueError('Not a Hellgato x64 startup DLL')
    finally:
        pe.close()


def replace_files(folder, contents):
    """Stage every write before replacing originals; restore a failed transaction."""
    folder = Path(folder)
    originals = {name: (folder / name).read_bytes() if (folder / name).exists() else None
                 for name in contents}
    staged, changed = {}, []
    try:
        for name, data in contents.items():
            if data == originals[name]:
                continue
            with tempfile.NamedTemporaryFile(prefix='.hellgato-', dir=folder, delete=False) as stream:
                staged[name] = Path(stream.name)
                stream.write(data)
        for name, temporary in staged.items():
            os.replace(temporary, folder / name)
            changed.append(name)
    except Exception:
        for name in reversed(changed):
            if originals[name] is None:
                (folder / name).unlink()
            else:
                (folder / name).write_bytes(originals[name])
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def install(executable, dll):
    executable, dll = Path(executable).resolve(), Path(dll).resolve()
    if executable.name.casefold() != 'streamdeck.exe':
        raise ValueError('Select the installed StreamDeck.exe')
    owned_files(executable.parent)
    data = dll.read_bytes()
    validate_dll(data)
    plan, metadata = build_plan(executable.read_bytes())
    record = {'owner': OWNER, 'executableSha256': metadata['sha256'],
              'files': {DLL: digest(data), PLAN: digest(plan)}}
    # Check ownership again after the structural resolver, before any write.
    owned_files(executable.parent)
    replace_files(executable.parent, {DLL: data, PLAN: plan,
                  MANIFEST: (json.dumps(record, indent=2) + '\n').encode('utf-8')})
    return metadata


def remove(executable):
    folder = Path(executable).resolve().parent
    record = owned_files(folder)
    if not record:
        return False
    # Rename the potentially loaded DLL first. If Windows denies access, leave
    # the plan and ownership record available for a later uninstall attempt.
    temporary = folder / 'HellgatoStartup.removing.dll'
    if temporary.exists():
        raise ValueError('A previous startup removal needs attention')
    (folder / DLL).rename(temporary)
    try:
        temporary.unlink()
    except Exception:
        temporary.rename(folder / DLL)
        raise
    (folder / PLAN).unlink()
    (folder / MANIFEST).unlink()
    return True

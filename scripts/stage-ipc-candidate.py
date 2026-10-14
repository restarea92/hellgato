"""Stage an immutable CI-only portable IPC trial with exact frozen dependencies."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/stream-dog/python'))
from hellgato_candidate.files import SHA, digest, inventory, read_json, verify_bundle, verify_inventory

README = '''HELLGATO EXPERIMENTAL IPC CANDIDATE

This is a CI test bundle, not an installer, public beta, or stable release.
The existing installed Hellgato engine remains the default. Real N4 behavior has
not been verified by CI. The frozen self-test uses simulated USB and host state.
No admin access, host preparation, installation or startup registration is performed.

Requirements: Windows x64, one registered N4 Pro and the prepared supported Stream
Deck host. Use the normal Hellgato installer from this SAME CI run first if the
existing startup preparation differs. Its normal GUI retains the old USB engine;
complete its explicit startup-setup flow and confirm it works before this trial. Genuine Stream Deck +
coexistence is unsupported, even when N4 is unplugged. Do not use two USB engines.

1. Extract this entire folder. Do not move individual executables or edit files.
2. Exit Hellgato fully from the tray, then close Stream Deck.
3. Open Run experimental test.cmd. Read the warning and type TEST to continue.
4. The runner verifies every bundle file and backs up your existing identities,
   control settings and Stream Deck profiles locally while Stream Deck is closed.
   It does not create a new virtual device identity or overwrite original settings.
5. Open your existing Stream Deck app when asked, then press Enter in this console.
   Readiness is checked again before connecting and reconnecting. If refused, stop
   and report the displayed reason; this bundle cannot prepare/patch the host.
6. Console commands: status, brightness 50, knob 50, follow on/off, sleep-time 60,
   sleep, wake, saver, logo. Invalid commands are rejected.
   Check ten keys, four dials, touch strip/key/LCD images, both brightness controls,
   saved screensaver and sleep/wake. Dial presses remain pulses, not holds.
7. Press Enter or Ctrl+C to stop. All child processes belong to a kill-on-close job.
   Close the console, then start ordinary Hellgato to return to the previous engine.

Local run files: %LOCALAPPDATA%\\Hellgato-Ipc-Candidate\\<source-sha>\\<run-id>
Keep config.json, screensaver files and backup/ PRIVATE. They contain identities,
profiles and potentially personal images. diagnostics.jsonl and supervisor-events.jsonl
are bounded and redact raw serials, profiles, paths and frame contents. Review them
before sharing; no logs are uploaded automatically.

Rollback normally means stopping this candidate and starting ordinary Hellgato.
If you need your pre-test profiles/settings back, close Hellgato and Stream Deck,
then run HellgatoIpcTest.exe --restore-backup "<run-folder>\\backup" and type RESTORE.
The helper verifies the backup, restores only known locations, preserves displaced
settings and keeps the previous ProfilesV3 folder next to the restored one. Do not
manually overwrite a live Stream Deck profile folder. New files absent at baseline
are preserved; restoration is not a deletion/cleanup operation.

Physical acceptance still requires recorded versions/firmware, comparison to the
old engine, repeated USB/host restart cycles, reboot/sleep/resume, one full workday,
latency/resource measurements and failure recovery. This bundle does not assert
those results. Obtain a new candidate if source or bundled bytes change.
'''

CANDIDATE_NOTICES = '''Hellgato experimental IPC candidate: bundled components

All paths below are relative to the extracted Hellgato-Ipc-Candidate folder.
The candidate's exact files and hashes are in candidate-manifest.json.

Hellgato (MIT): supervisor, SDK, worker and native startup reference
License: licenses/Hellgato.txt

Mirabox StreamDock Device SDK (MIT)
https://github.com/MiraboxSpace/StreamDock-Device-SDK
Revision: 87ff56674cb3772dc2a13376d174c33c37964607
License: licenses/Mirabox.txt

DeckBridge (MIT), copyright 2026 Lukas
https://github.com/lukasMega/DeckBridge
Revision: 7c00693d01ac6c47c071907835ed0724d28d8183
Adaptations: Node TCP transport, disabled mDNS, TypeScript transformation.
License: licenses/DeckBridge.txt
Detailed provenance: sdk/node_modules/@hellgato/core/dist/vendor/provenance.json

Node.js 24.19.0 and its bundled third-party components
License: licenses/Node.txt

Python 3.13 runtime and its included third-party notices
License: licenses/Python.txt

Pillow 12.0.0 and bundled image libraries
License: licenses/Pillow.txt

pefile 2023.2.7 (MIT), copyright Ero Carrera
https://github.com/erocarrera/pefile
License: licenses/pefile.txt

six 1.17.0 (MIT), copyright Benjamin Peterson
https://github.com/benjaminp/six
License: licenses/six.txt

PyInstaller 6.16.0 bootloader and runtime support
Distributed under PyInstaller's bootloader distribution exception.
License: licenses/PyInstaller.txt
https://pyinstaller.org/en/v6.16.0/license.html

The Stream Deck application and third-party Stream Deck plugins are not bundled.
The candidate does not distribute the ordinary product's Tk GUI, pystray tray,
Qt native preferences extension, or Qt runtime libraries/plugins.
Hellgato is independent of Elgato and Mirabox.
'''


def copy_tree(source, destination):
    inventory(source)  # Reject links/special files before copying executables.
    shutil.copytree(source, destination)


def source_identity(root):
    source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if not SHA.fullmatch(source_sha):
        raise ValueError('Invalid candidate source identity')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=root, text=True).strip():
        raise ValueError('Immutable candidate requires a clean, committed source checkout')
    return source_sha


def verify_sdk_stage(directory, source_sha):
    sdk = read_json(directory / 'product-build-manifest.json')
    if (type(sdk.get('schemaVersion')) is not int or sdk['schemaVersion'] != 1
            or sdk.get('component') != 'stream-dog-sdk' or sdk.get('engine') != 'legacy-compatibility'
            or sdk.get('releaseSha') != source_sha):
        raise ValueError('SDK candidate source differs from the frozen source')
    files = sdk.get('files')
    if not isinstance(files, list) or not all(isinstance(entry, dict) and isinstance(entry.get('path'), str) for entry in files):
        raise ValueError('Invalid staged SDK inventory')
    # The producer sorts with localeCompare; canonicalize only ordering, never
    # the file set, bytes, paths, or per-file metadata before verification.
    verify_inventory(directory, sorted(files, key=lambda entry: entry['path']), ('product-build-manifest.json',))
    return sdk


def stage(root=ROOT):
    dist = root / 'dist'
    target = dist / 'Hellgato-Ipc-Candidate'
    temporary = dist / '.Hellgato-Ipc-Candidate-staging'
    if temporary.exists():
        shutil.rmtree(temporary)
    source_sha = source_identity(root)
    build_id = 'windows-ipc-' + source_sha
    try:
        copy_tree(dist / 'HellgatoIpcTest', temporary)
        copy_tree(dist / 'HellgatoMiraboxWorker', temporary / 'worker')
        copy_tree(root / 'work/stream-dog-sdk', temporary / 'sdk')
        sdk = verify_sdk_stage(temporary / 'sdk', source_sha)
        (temporary / 'sdk/product-build-manifest.json').unlink()
        for source in sorted((root / 'apps/stream-dog/node').glob('candidate*.mjs')):
            shutil.copyfile(source, temporary / 'sdk' / source.name)
        if not (temporary / 'sdk/candidate-runner.mjs').is_file():
            raise ValueError('Candidate session runner is missing')
        shutil.copyfile(root / 'work/build-runtime/node.exe', temporary / 'node.exe')
        (temporary / 'assets').mkdir()
        shutil.copyfile(root / 'app/assets/icons/hellgato-front-64.png', temporary / 'assets/hellgato-front-64.png')
        (temporary / 'native').mkdir()
        shutil.copyfile(root / 'work/build-native-startup/version.dll', temporary / 'native/version.dll')
        licenses = temporary / 'licenses'
        licenses.mkdir()
        for source, name in ((root / 'LICENSE', 'Hellgato.txt'),
                             (root / 'work/build-runtime/NODE-LICENSE.txt', 'Node.txt'),
                             (root / 'work/references/mirabox/LICENSE', 'Mirabox.txt'),
                             (temporary / 'sdk/node_modules/@hellgato/core/dist/vendor/LICENSE', 'DeckBridge.txt'),
                             (Path(sys.base_prefix) / 'LICENSE.txt', 'Python.txt'),
                             (Path(importlib.metadata.distribution('pillow').locate_file('pillow-12.0.0.dist-info/licenses/LICENSE')), 'Pillow.txt'),
                             (Path(importlib.metadata.distribution('pefile').locate_file('pefile-2023.2.7.dist-info/LICENSE')), 'pefile.txt'),
                             (Path(importlib.metadata.distribution('six').locate_file('six-1.17.0.dist-info/LICENSE')), 'six.txt'),
                             (Path(importlib.metadata.distribution('pyinstaller').locate_file('pyinstaller-6.16.0.dist-info/licenses/COPYING.txt')), 'PyInstaller.txt')):
            shutil.copyfile(source, licenses / name)
        (licenses / 'THIRD-PARTY-NOTICES.txt').write_text(CANDIDATE_NOTICES, encoding='utf-8')
        worker = temporary / 'worker'
        firmware_policy = 'hellgato_mirabox/models/n4_pro/firmware-policy.json'
        if digest(worker / '_internal' / firmware_policy) != digest(root / 'adapters/mirabox-python/src' / firmware_policy):
            raise ValueError('Frozen firmware policy differs from the reviewed source')
        runtime = {'schemaVersion': 1, 'platform': 'win32', 'arch': 'x64', 'buildId': build_id,
                   'executableSha256': digest(worker / 'HellgatoMiraboxWorker.exe'),
                   'files': inventory(worker)}
        if len(runtime['files']) > 1024:
            raise ValueError('Worker manifest exceeds SDK inventory bound')
        (worker / 'runtime-manifest.json').write_text(json.dumps(runtime, indent=2) + '\n', encoding='utf-8')
        (temporary / 'README-FIRST.txt').write_text(README, encoding='utf-8')
        (temporary / 'Run experimental test.cmd').write_bytes(b'@echo off\r\n"%~dp0HellgatoIpcTest.exe"\r\necho.\r\npause\r\n')
        manifest = {'schemaVersion': 1, 'component': 'stream-dog-ipc-candidate', 'channel': 'experimental-ci',
                    'sourceSha': source_sha, 'buildId': build_id, 'physicalHardwareVerified': False,
                    'productVersion': sdk['productVersion'], 'sdk': sdk['sdk'],
                    'entrypoint': 'HellgatoIpcTest.exe', 'files': inventory(temporary)}
        (temporary / 'candidate-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        verify_bundle(temporary)
        if source_identity(root) != source_sha:
            raise ValueError('Candidate source changed during staging')
        if target.exists():
            shutil.rmtree(target)
        temporary.rename(target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def archive(directory):
    manifest = verify_bundle(directory)
    target = directory.parent / f"Hellgato-Ipc-Candidate-{manifest['sourceSha'][:12]}.zip"
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for entry in inventory(directory):
            output.write(directory / entry['path'], 'Hellgato-Ipc-Candidate/' + entry['path'])
    (target.parent / (target.name + '.sha256')).write_text(digest(target) + '  ' + target.name + '\n', encoding='ascii')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--verify', type=Path)
    group.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.verify:
        value = verify_bundle(args.verify)
        print('Verified immutable IPC candidate: ' + value['sourceSha'])
    elif args.archive:
        print(archive(args.archive))
    else:
        print(stage())


if __name__ == '__main__':
    main()

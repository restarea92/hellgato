"""Explicit experimental IPC test runner. The installed product stays unchanged."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

SOURCE = None if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[3]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(SOURCE / 'host-integrations/streamdeck/windows/python'))
    sys.path.insert(0, str(SOURCE / 'app/n4'))

from hellgato_candidate.files import (digest, existing_identity, make_snapshot, read_json,
    regular, restore_snapshot, verify_bundle, verify_snapshot)
from hellgato_candidate.windows import LauncherOwnership, ProcessTree

SAFE_TEXT = re.compile(r'[A-Za-z0-9_.:-]{1,96}\Z')
SELF_TEST_CHECKS = frozenset(('verified-worker-manifest', 'fake-backend', 'explicit-identity',
    'fresh-host-reconnect-readiness', 'bounded-session-reconnect', 'settings-and-frame-restoration',
    'no-input-replay', 'clean-stop'))
DISPLAY_SURFACES = (*('key:' + str(index) for index in range(10)), 'touch', 'lcd')
DISPLAY_WRITERS = frozenset(('none', 'initialize', 'key', 'lcd', 'clear', 'fill', 'restore', 'screensaver', 'logo'))
VENDOR_RETURN_KINDS = ('none', 'zero', 'negative', 'positive', 'other')
MAX_OBSERVATION_COUNT = 9007199254740991
MAX_EVENT_BYTES = 16 * 1024


def bundle_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    raise ValueError('Use the verified packaged candidate, not the source-development supervisor')


def locations():
    return (Path(os.environ['LOCALAPPDATA']) / 'Hellgato',
            Path(os.environ['APPDATA']) / 'Elgato/StreamDeck/ProfilesV3',
            Path(os.environ['LOCALAPPDATA']) / 'Hellgato-Ipc-Candidate')


def require_windows():
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise ValueError('This candidate requires 64-bit Windows')


def require_host_stopped():
    from hellgato_host.presence import streamdeck_processes
    if streamdeck_processes():
        raise RuntimeError('Close Stream Deck completely before making or restoring the profile backup')


def ask(message):
    if not sys.stdin.isatty():
        raise ValueError('A physical trial or rollback requires an interactive console')
    return input(message).strip()


def initial_controls(state, serial):
    values = {}
    display = state / f'display-{serial}.json'
    if display.exists():
        saved = read_json(display, 128 * 1024)
        duration = saved.get('sleepDuration')
        if type(duration) is not int or not 0 <= duration <= 86400:
            raise ValueError('Saved sleep duration is invalid')
        values['sleepDuration'] = duration
    controls = state / f'device-controls-{serial}.json'
    if controls.exists():
        saved = read_json(controls, 128 * 1024)
        if saved.get('version') != 1 or saved.get('serial') != serial:
            raise ValueError('Saved device controls belong to another device')
        value = saved.get('knobBrightness')
        if value is not None and (type(value) is not int or not 0 <= value <= 100):
            raise ValueError('Saved knob brightness is invalid')
        values['followDisplayBrightness'] = value is None
        if value is not None:
            values['knobBrightness'] = value
    return values


def snapshot_screensaver(serial, run):
    from screensaver import ScreensaverSource
    saver = ScreensaverSource(serial)
    saver.reload()
    if saver.error:
        raise ValueError('Saved screensaver could not be validated; fix it in Stream Deck before testing')
    if saver.path is None:
        return None
    regular(saver.path)
    target = run / ('screensaver' + saver.path.suffix.lower())
    before = digest(saver.path)
    shutil.copyfile(saver.path, target)
    if digest(target) != before or digest(saver.path) != before:
        raise ValueError('Screensaver changed during snapshot')
    return target


def configuration(root, manifest, run, settings, record, controls, saver=None):
    from hellgato_host.startup_install import default_executable
    dock = settings['dockIdentity']
    runtime_args = ['--sdk-path', str(root / 'worker/_internal/vendor/mirabox/src'),
                    '--build-id', manifest['buildId'], '--strip-mode', settings['stripMode'], '--logo-path', str(root / 'assets/hellgato-front-64.png')]
    if saver is not None:
        runtime_args += ['--screensaver-path', str(saver)]
    return {'schemaVersion': 1, 'deviceId': record['id'],
            'identity': {'dockSerial': f'HGDOCK{dock:06d}', 'childSerial': record['virtualSerial'],
                         'macAddress': [2, 0, 0, (dock >> 16) & 255, (dock >> 8) & 255, dock & 255]},
            'runtime': {'command': str(root / 'worker/HellgatoMiraboxWorker.exe'),
                        'manifestPath': str(root / 'worker/runtime-manifest.json'), 'args': runtime_args},
            'readiness': {'command': str(root / 'HellgatoIpcTest.exe'),
                          'args': ['--inspect-host', '--json', '--expected-executable', str(default_executable()),
                                   '--expected-startup-dll', str(root / 'native/version.dll')]},
            'screensaverMode': 'native' if saver else 'stream', 'initialControls': controls, 'statePath': str(run / 'candidate-state.json'),
            'diagnosticsPath': str(run / 'diagnostics.jsonl'),
            'reconnect': {'maxAttempts': 3, 'delayMs': 1000}}


def safe_display_observations(value):
    if not isinstance(value, dict):
        return None
    def count(item, maximum=MAX_OBSERVATION_COUNT):
        return type(item) is int and 0 <= item <= maximum
    def dimension(item, maximum):
        return item is None or type(item) is int and 0 < item <= maximum
    def brightness(item):
        return item is None or type(item) in (int, float) and math.isfinite(item) and 0 <= item <= 255
    source = value.get('surfaces')
    source = source if isinstance(source, dict) else {}
    surfaces = {}
    for name in DISPLAY_SURFACES:
        item = source.get(name)
        item = item if isinstance(item, dict) else {}
        surfaces[name] = {key: item[key] for key in
                          ('received', 'coalesced', 'submitted', 'failed', 'restored') if count(item.get(key))}
    worker = value.get('worker')
    safe_worker = None
    if (isinstance(worker, dict) and count(worker.get('keyCacheCount'), 10)
            and count(worker.get('keyCacheClears')) and isinstance(worker.get('keys'), list)
            and len(worker['keys']) == 10 and all(isinstance(item, dict) for item in worker['keys'])):
        keys = []
        for item in worker['keys']:
            writer = item.get('lastWriter')
            returned = item.get('lastVendorReturnKind')
            returns = item.get('vendorReturnKinds')
            if (not all(count(item.get(key)) for key in ('attempted', 'submitted', 'failed'))
                    or not isinstance(writer, str) or writer not in DISPLAY_WRITERS
                    or not all(key in item for key in ('width', 'height', 'meanBrightness', 'black', 'lastVendorReturnKind'))
                    or not dimension(item['width'], 800) or not dimension(item['height'], 480)
                    or not brightness(item['meanBrightness'])
                    or not (item['black'] is None or type(item['black']) is bool)
                    or not (returned is None or isinstance(returned, str) and returned in VENDOR_RETURN_KINDS)
                    or not isinstance(returns, dict) or not all(count(returns.get(kind)) for kind in VENDOR_RETURN_KINDS)):
                return {'surfaces': surfaces, 'worker': None}
            keys.append({
                **{key: item[key] for key in ('attempted', 'submitted', 'failed', 'width', 'height', 'meanBrightness', 'black')},
                'lastWriter': writer, 'lastVendorReturnKind': returned,
                'vendorReturnKinds': {kind: returns[kind] for kind in VENDOR_RETURN_KINDS},
            })
        safe_worker = {'keyCacheCount': worker['keyCacheCount'],
                       'keyCacheClears': worker['keyCacheClears'], 'keys': keys}
    return {'surfaces': surfaces, 'worker': safe_worker}


def safe_observations(value):
    """Fixed numeric timing/resource structures only, never arbitrary nested text."""
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get('quantilesAreUpperBounds') is True:
        result['quantilesAreUpperBounds'] = True
    def number(item):
        return type(item) in (int, float) and math.isfinite(item) and 0 <= item <= 1e18
    bounds = value.get('bucketUpperBoundsMs')
    if isinstance(bounds, list) and len(bounds) <= 64 and all(number(item) for item in bounds):
        result['bucketUpperBoundsMs'] = bounds
    fields = {
        'nodeInputForwardMs': ('count', 'p50', 'p95', 'p99', 'max', 'mean'),
        'frameSubmitMs': ('count', 'p50', 'p95', 'p99', 'max', 'mean'),
        'controlSubmitMs': ('count', 'p50', 'p95', 'p99', 'max', 'mean'),
        'resources': ('elapsedMs', 'cpuUserMs', 'cpuSystemMs', 'cpuPercent', 'rssBytes', 'observedPeakRssBytes'),
        'queue': ('configuredOutputLimit', 'peakPendingConsoleControls'),
    }
    for name, allowed in fields.items():
        source = value.get(name)
        if not isinstance(source, dict):
            continue
        target = {key: source[key] for key in allowed if number(source.get(key)) or source.get(key) is None}
        buckets = source.get('buckets')
        if name.endswith('Ms') and isinstance(buckets, list) and len(buckets) <= 64 and all(number(item) for item in buckets):
            target['buckets'] = buckets
        result[name] = target
    display = safe_display_observations(value.get('display'))
    if display is not None:
        result['display'] = display
    return result


class SanitizedEvents:
    def __init__(self, path):
        self.path = path
        self.stream = path.open('x', encoding='utf-8')
        self.lock = threading.Lock()
        self.written = 0

    def line(self, data, source):
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            value = {}
        safe = {'time': datetime.now(timezone.utc).isoformat(), 'source': source}
        if isinstance(value, dict):
            for key in ('event', 'state', 'code', 'phase'):
                text = value.get(key)
                if isinstance(text, str) and SAFE_TEXT.fullmatch(text):
                    safe[key] = text
            for key in ('attempt', 'inputCount', 'frameCount', 'queueDepth', 'dropped', 'elapsedMs'):
                number = value.get(key)
                if type(number) in (int, float) and -1 <= number <= 1e15:
                    safe[key] = number
            counters = value.get('counters')
            if isinstance(counters, dict):
                keys = ('readinessChecks', 'workerStarts', 'reconnectAttempts', 'frameWrites', 'controlWrites',
                        'restoredWrites', 'unconfirmedWrites', 'simulationWrites', 'inputObserved', 'inputForwarded',
                        'inputNotDelivered', 'inputSuppressed', 'diagnostics', 'cleanStops')
                safe['counters'] = {key: counters[key] for key in keys if type(counters.get(key)) is int and 0 <= counters[key] <= 1e18}
            if 'observations' in value:
                safe['observations'] = safe_observations(value['observations'])
        with self.lock:
            encoded = json.dumps(safe, allow_nan=False) + '\n'
            if len(encoded.encode('utf-8')) > MAX_EVENT_BYTES:
                return
            if self.written + len(encoded) > 1024 * 1024:
                self.stream.close()
                os.replace(self.path, self.path.with_name(self.path.name + '.1'))
                self.stream = self.path.open('x', encoding='utf-8')
                self.written = 0
            self.stream.write(encoded)
            self.stream.flush()
            self.written += len(encoded)

    def close(self):
        with self.lock:
            self.stream.close()


def validate_self_test_receipt(value):
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1
            or value.get('engine') != 'ipc-candidate' or value.get('state') != 'stopped'
            or value.get('simulation') is not True or value.get('physicalHardwareVerified') is not False
            or type(value.get('generation')) is not int or value['generation'] < 2
            or not isinstance(value.get('checks'), list) or len(value['checks']) != len(SELF_TEST_CHECKS)
            or any(not isinstance(item, str) for item in value['checks'])
            or set(value['checks']) != SELF_TEST_CHECKS):
        raise ValueError('Missing or invalid frozen self-test completion receipt')
    return {key: value[key] for key in ('schemaVersion', 'engine', 'state', 'simulation',
            'physicalHardwareVerified', 'generation', 'checks')}


def console_command(text):
    parts = text.strip().lower().split()
    if not parts or parts == ['stop']:
        return {'command': 'stop'}
    if parts == ['status']:
        return {'command': 'status'}
    plain = {'sleep': 'sleep', 'wake': 'wake', 'saver': 'screensaver', 'logo': 'showLogo'}
    if len(parts) == 1 and parts[0] in plain:
        return {'command': 'control', 'control': {'control': plain[parts[0]]}}
    if len(parts) == 2 and parts[0] in ('brightness', 'knob', 'sleep-time'):
        try:
            value = int(parts[1])
        except ValueError:
            raise ValueError('Control value must be an integer') from None
        maximum = 86400 if parts[0] == 'sleep-time' else 100
        if not 0 <= value <= maximum:
            raise ValueError('Control value is out of range')
        control = {'brightness': 'brightness', 'knob': 'knobBrightness', 'sleep-time': 'sleepDuration'}[parts[0]]
        return {'command': 'control', 'control': {'control': control, 'value': value}}
    if len(parts) == 2 and parts[0] == 'follow' and parts[1] in ('on', 'off'):
        return {'command': 'control', 'control': {'control': 'followDisplayBrightness', 'value': parts[1] == 'on'}}
    raise ValueError('Use status, brightness 0..100, knob 0..100, follow on/off, sleep-time 0..86400, sleep, wake, saver, logo, or stop')


def supervised(root, config_path, run, *, self_test=False):
    command = [str(root / 'node.exe'), str(root / 'sdk/candidate-runner.mjs'),
               '--config', str(config_path), '--supervised']
    if self_test:
        command += ['--self-test']
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'COMSPEC', 'PATHEXT'}}
    log = SanitizedEvents(run / 'supervisor-events.jsonl')
    child = None
    readers = []
    receipt = []
    ready_for_status = threading.Event()
    last_printed = []
    stop = queue.Queue()
    started = time.monotonic()
    try:
        with ProcessTree() as job:
            child = subprocess.Popen(command, cwd=root / 'sdk', env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                job.assign(child)
            except Exception:
                child.kill()
                child.wait(timeout=10)
                raise
            def drain(pipe, source):
                buffer = bytearray()
                while True:
                    chunk = pipe.read1(1024)
                    if not chunk:
                        break
                    for byte in chunk:
                        if byte == 10:
                            log.line(bytes(buffer), source)
                            if source == 'node':
                                try:
                                    update = json.loads(buffer)
                                except (ValueError, UnicodeError):
                                    update = {}
                                if isinstance(update, dict) and self_test and 'checks' in update:
                                    try:
                                        value = validate_self_test_receipt(update)
                                    except ValueError:
                                        value = None
                                    if value is not None:
                                        receipt.append(value)
                                elif isinstance(update, dict) and not self_test:
                                    if isinstance(update.get('state'), str):
                                        ready_for_status.set()
                                    tokens = [update.get(key) for key in ('event', 'state', 'code')]
                                    safe = [token for token in tokens if isinstance(token, str) and SAFE_TEXT.fullmatch(token)]
                                    if safe and safe != last_printed:
                                        last_printed[:] = safe
                                        print('Candidate: ' + ' / '.join(safe), flush=True)
                            buffer.clear()
                        elif len(buffer) < MAX_EVENT_BYTES:
                            buffer.append(byte)
                if buffer:
                    log.line(bytes(buffer), source)
            for pipe, name in ((child.stdout, 'node'), (child.stderr, 'node-stderr')):
                thread = threading.Thread(target=drain, args=(pipe, name), daemon=True)
                thread.start()
                readers.append(thread)
            child.stdin.write(b'{"command":"start"}\n')
            child.stdin.flush()
            if not self_test:
                print('Starting experimental session. Watch state updates; press Enter or Ctrl+C to stop.')
                print('Commands: status, brightness 50, knob 50, follow on/off, sleep-time 60, sleep, wake, saver, logo.')
                def input_commands():
                    while child.poll() is None:
                        try:
                            text = input()
                        except EOFError:
                            stop.put({'command': 'stop'})
                            return
                        try:
                            request = console_command(text)
                        except ValueError as error:
                            print(str(error))
                            continue
                        stop.put(request)
                        if request['command'] == 'stop':
                            return
                threading.Thread(target=input_commands, daemon=True).start()
            next_status = time.monotonic() + 5
            try:
                while child.poll() is None:
                    if self_test and time.monotonic() - started > 90:
                        raise TimeoutError('Packaged fake self-test timed out')
                    if not self_test and ready_for_status.is_set() and time.monotonic() >= next_status:
                        child.stdin.write(b'{"command":"status"}\n')
                        child.stdin.flush()
                        next_status = time.monotonic() + 5
                    if not stop.empty():
                        request = stop.get_nowait()
                        if request['command'] == 'stop':
                            break
                        child.stdin.write((json.dumps(request) + '\n').encode())
                        child.stdin.flush()
                    time.sleep(.1)
            except KeyboardInterrupt:
                pass
            finally:
                if child.poll() is None:
                    try:
                        child.stdin.write(b'{"command":"stop"}\n')
                        child.stdin.flush()
                    except (OSError, BrokenPipeError):
                        pass
                    try:
                        child.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        print('Graceful stop timed out; closing the isolated process tree.')
                # Closing the job below kills every remaining descendant even if Node exited first.
        if child.poll() is None:
            child.wait(timeout=10)
        for thread in readers:
            thread.join(timeout=5)
        if self_test:
            if child.returncode != 0 or len(receipt) != 1 or any(thread.is_alive() for thread in readers):
                raise RuntimeError('Frozen self-test did not produce exactly one complete positive receipt')
            (run / 'self-test-receipt.json').write_text(json.dumps(receipt[0], indent=2) + '\n', encoding='utf-8')
        return child.returncode
    finally:
        if child is not None:
            for pipe in (child.stdin, child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()
        for thread in readers:
            thread.join(timeout=1)
        log.close()


def self_test(root, manifest, output):
    output.mkdir(parents=True, exist_ok=False)
    config = {'schemaVersion': 1, 'deviceId': 'fake:buttons-dial',
              'identity': {'dockSerial': 'HGDOCK000001', 'childSerial': 'HGMOCK000001', 'macAddress': [2, 0, 0, 0, 0, 1]},
              'runtime': {'command': str(root / 'worker/HellgatoMiraboxWorker.exe'),
                          'manifestPath': str(root / 'worker/runtime-manifest.json'),
                          'args': ['--build-id', manifest['buildId']]},
              'statePath': str(output / 'state.json'), 'diagnosticsPath': str(output / 'diagnostics.jsonl'),
              'initialControls': {}, 'reconnect': {'maxAttempts': 1, 'delayMs': 50}}
    path = output / 'config.json'
    path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    result = supervised(root, path, output, self_test=True)
    if result:
        raise RuntimeError('Packaged fake worker self-test failed; inspect bounded diagnostics')
    receipt = validate_self_test_receipt(read_json(output / 'self-test-receipt.json'))
    (output / 'result.json').write_text(json.dumps({'passed': True, 'physicalHardwareVerified': False,
        'sourceSha': manifest['sourceSha'], 'buildId': manifest['buildId'], 'processTreeOwned': True, 'checks': receipt['checks']}) + '\n')
    print('Frozen Node/Python candidate self-test passed. No USB or real host was used.')


def trial(root, manifest):
    state, profiles, runs = locations()
    print('EXPERIMENTAL IPC CANDIDATE. Hardware compatibility is not yet verified.')
    print('This does not install, update, or prepare Stream Deck. The existing Hellgato engine stays the default.')
    print('Genuine Stream Deck + coexistence is unsupported, including when N4 is unplugged.')
    print('Exit Hellgato from the tray and close Stream Deck before continuing.')
    if ask('Type TEST to back up your current settings/profiles and run the isolated candidate: ') != 'TEST':
        return
    with LauncherOwnership():
        require_host_stopped()
        settings, record = existing_identity(state)
        run = runs / manifest['sourceSha'] / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + str(os.getpid()))
        run.mkdir(parents=True, exist_ok=False)
        make_snapshot(state, profiles, run / 'backup', settings['identity'], manifest['sourceSha'])
        serial = record['virtualSerial']
        controls = initial_controls(state, serial)
        saver = snapshot_screensaver(serial, run)
        config = configuration(root, manifest, run, settings, record, controls, saver)
        config_path = run / 'config.json'
        config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        print('Verified local backup created. Keep this folder private; it contains your profiles.')
        print(str(run))
        print('Open your existing Stream Deck app now. Do not start Hellgato at the same time.')
        if ask('Press Enter when Stream Deck is ready, or type CANCEL: ').upper() == 'CANCEL':
            return
        result = supervised(root, config_path, run)
        print('Candidate stopped. Close this console, then start your normal Hellgato app to return to the old engine.')
        print('Diagnostics are local and omit raw serials/images. Never share config.json or the backup folder.')
        print('If profiles need restoration, close both apps and run: HellgatoIpcTest.exe --restore-backup "' + str(run / 'backup') + '"')
        if result:
            raise RuntimeError('Candidate stopped with an error; existing settings remain preserved')


def rollback(root, path):
    state, profiles, runs = locations()
    backup = path.resolve(strict=True)
    if not backup.is_relative_to(runs.resolve()) or backup.name != 'backup':
        raise ValueError('Choose a backup inside this user\'s Hellgato-Ipc-Candidate folder')
    verify_snapshot(backup)
    print('This restores the saved Hellgato settings and Stream Deck profiles from this trial.')
    print('Later profile changes will leave the active profile folder but are preserved in a separate rollback copy.')
    if ask('Close Hellgato and Stream Deck, then type RESTORE to continue: ') != 'RESTORE':
        return
    with LauncherOwnership():
        require_host_stopped()
        preserved = restore_snapshot(backup, state, profiles)
    print('Backup restored. Previous settings are preserved at: ' + str(preserved))
    print('Previous profiles, if present, are preserved next to ProfilesV3. Start Stream Deck and Hellgato normally.')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == '--inspect-host':
        from hellgato_host.readiness import main as inspect_main
        return inspect_main(['inspect', *args[1:]])
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--self-test', action='store_true')
    group.add_argument('--restore-backup', type=Path)
    parser.add_argument('--output', type=Path)
    parsed = parser.parse_args(args)
    require_windows()
    root = bundle_root()
    manifest = verify_bundle(root)
    if parsed.self_test:
        if not parsed.output or not parsed.output.is_absolute() or parsed.output.resolve().is_relative_to(root):
            parser.error('--self-test requires an absolute --output outside the immutable bundle')
        self_test(root, manifest, parsed.output)
    elif parsed.restore_backup:
        rollback(root, parsed.restore_backup)
    else:
        trial(root, manifest)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, TimeoutError) as error:
        # User action hints are deliberately local. Raw Node/worker stderr is never printed here.
        print('Candidate refused: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

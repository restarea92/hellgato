"""Desktop lifecycle and native preferences over the installed SDK worker."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(__file__).resolve().parents[4]
for location in ('apps/stream-dog/python', 'app/n4', 'host-integrations/streamdeck/windows/python'):
    sys.path.insert(0, str(ROOT / location))

MAX_LINE_BYTES = 64 * 1024
SETTINGS = {'brightness': (0, 100), 'knobBrightness': (0, 100), 'sleepDuration': (0, 0x7fffffff)}
LIFECYCLE_EVENTS = frozenset(('bridgeReady', 'started', 'deviceReady', 'deviceReconnected',
                             'deviceDisconnected', 'primary.clientConnected', 'primary.clientDisconnected',
                             'child.clientConnected', 'child.clientDisconnected', 'reconnecting'))


class ProductRuntimeError(OSError):
    def __init__(self, code):
        self.code = code
        super().__init__('The SDK runtime failed: ' + code)


def validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid device settings')
    for key, setting in value.items():
        if key == 'followDisplayBrightness':
            valid = type(setting) is bool
        else:
            bounds = SETTINGS.get(key)
            valid = bounds is not None and type(setting) is int and bounds[0] <= setting <= bounds[1]
        if not valid:
            raise ValueError('Invalid device settings')
    return dict(value)


def read_json(path, limit=64 * 1024):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('Invalid product runtime file')
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initial_controls(state, serial):
    values = {'sleepDuration': 0, 'followDisplayBrightness': True}
    path = state / f'display-{serial}.json'
    if path.exists():
        values['sleepDuration'] = read_json(path)['sleepDuration']
    path = state / f'device-controls-{serial}.json'
    if path.exists():
        saved = read_json(path)
        if not isinstance(saved, dict) or saved.get('version') != 1 or saved.get('serial') != serial:
            raise ValueError('Saved device controls belong to another device')
        value = saved.get('knobBrightness')
        values['followDisplayBrightness'] = value is None
        if value is not None:
            values['knobBrightness'] = value
    return validate_settings(values)


def runtime_paths(root=ROOT):
    node = root / 'runtime/node.exe'
    if not node.is_file():
        configured = os.environ.get('HELLGATO_NODE') or shutil.which('node')
        node = Path(configured) if configured else node
    worker = root / 'runtime/mirabox'
    if not worker.is_dir() and not getattr(sys, 'frozen', False):
        worker = root / 'dist/HellgatoMiraboxWorker'
    manifest = read_json(worker / 'runtime-manifest.json', 1024 * 1024)
    if not isinstance(manifest, dict) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', manifest.get('buildId', '')):
        raise ValueError('Invalid worker build identity')
    executable = worker / 'HellgatoMiraboxWorker.exe'
    runner = root / 'work/stream-dog-sdk/candidate-runner.mjs'
    if not node.is_file() or not executable.is_file() or not runner.is_file():
        raise ValueError('The installed SDK runtime is incomplete')
    return node.resolve(), runner.resolve(), worker.resolve(), manifest['buildId']


def configuration(root, state, settings, controls, device_id=None, saver=None):
    from hellgato_host.startup_install import default_executable, startup_dll
    node, runner, worker, build_id = runtime_paths(root)
    script = root / 'apps/stream-dog/python/hellgato_stream_dog/ipc_bridge.py'
    if getattr(sys, 'frozen', False):
        readiness = [str(Path(sys.executable).with_name('HellgatoWorker.exe')), '--run-script', str(script)]
    else:
        readiness = [sys.executable, str(script)]
    dock = settings['dockIdentity']
    serial = f"HGMOCK{settings['identity']:06d}"
    runtime_args = ['--sdk-path', str(worker / '_internal/vendor/mirabox/src'), '--build-id', build_id,
                    '--strip-mode', settings['stripMode'], '--logo-path', str(root / 'app/assets/icons/hellgato-front-64.png')]
    if saver is not None:
        runtime_args.extend(('--screensaver-path', str(saver)))
    value = {'schemaVersion': 1, 'productMode': True,
             'identity': {'dockSerial': f'HGDOCK{dock:06d}', 'childSerial': serial,
                          'macAddress': [2, 0, 0, (dock >> 16) & 255, (dock >> 8) & 255, dock & 255]},
             'runtime': {'command': str(worker / 'HellgatoMiraboxWorker.exe'),
                         'manifestPath': str(worker / 'runtime-manifest.json'), 'args': runtime_args},
             'readiness': {'command': readiness[0], 'args': [*readiness[1:], '--inspect-host', '--json',
                           '--expected-executable', str(default_executable()), '--expected-startup-dll', str(startup_dll(root))]},
             'screensaverMode': 'native' if saver else 'stream', 'initialControls': validate_settings(controls),
             'statePath': str(state / f'ipc-state-{serial}.json'), 'diagnosticsPath': str(state / 'ipc-diagnostics.jsonl'),
             'reconnect': {'maxAttempts': 3, 'delayMs': 1000}}
    if device_id is not None:
        value['deviceId'] = device_id
    return value, [str(node), str(runner)]


class ProductProcess:
    """Correlated, bounded commands; Node cannot start descendants before job assignment."""
    def __init__(self, command, config_path, on_event, *, discover=False, process_factory=subprocess.Popen,
                 job_factory=None):
        if job_factory is None:
            from hellgato_candidate.windows import ProcessTree
            job_factory = ProcessTree
        self.command = [*command, '--config', str(config_path), '--supervised', *(['--discover'] if discover else [])]
        self.on_event, self.process_factory, self.job_factory = on_event, process_factory, job_factory
        self.process = None
        self.job = None
        self.reader = None
        self.error_reader = None
        self.pending = {}
        self.lock = threading.Lock()
        self.next_id = 0
        self.failure = None

    def start(self):
        self.job = self.job_factory().__enter__()
        try:
            env = {key: value for key, value in os.environ.items()
                   if key not in ('NODE_OPTIONS', 'NODE_PATH', 'NODE_AUTH_TOKEN', 'NPM_TOKEN')}
            self.process = self.process_factory(self.command, cwd=Path(self.command[1]).parent, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.job.assign(self.process)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.error_reader = threading.Thread(target=self._discard_errors, daemon=True)
            self.reader.start()
            self.error_reader.start()
            self.send({'command': 'start'})
            return self
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                    raise ValueError('SDK response exceeds its frame limit')
                value = json.loads(line.decode('utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('Invalid SDK response')
                request_id = value.get('requestId')
                with self.lock:
                    pending = self.pending.get(request_id) if type(request_id) is int else None
                    if pending is not None:
                        pending.put_nowait(value)
                if pending is None:
                    self.on_event(value)
        except Exception:
            self.failure = 'ERR_IPC_PROTOCOL'
        finally:
            with self.lock:
                for pending in self.pending.values():
                    try:
                        pending.put_nowait({'event': 'controlFailed', 'code': 'ERR_WORKER_EOF'})
                    except queue.Full:
                        pass

    def _discard_errors(self):
        try:
            while True:
                line = self.process.stderr.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                    continue
                try:
                    value = json.loads(line.decode('utf-8'))
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict) and value.get('state') == 'failed':
                    code = value.get('code')
                    if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,80}', code):
                        self.failure = code
                        self.on_event({'state': 'failed', 'code': code})
        except (OSError, ValueError):
            pass

    def send(self, value):
        data = (json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if len(data) > 1024:
            raise ValueError('SDK command exceeds its frame limit')
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                raise OSError('The SDK runtime is disconnected')
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def request(self, command, *, control=None, guard=lambda: None, timeout=8):
        guard()
        pending = queue.Queue(maxsize=1)
        with self.lock:
            if len(self.pending) >= 16:
                raise OSError('The SDK request queue is full')
            self.next_id += 1
            request_id = self.next_id
            self.pending[request_id] = pending
        try:
            value = {'command': command, 'requestId': request_id}
            if control is not None:
                value['control'] = control
            self.send(value)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                guard()
                try:
                    result = pending.get(timeout=.05)
                    if result.get('event') in ('controlFailed', 'workerStateFailed'):
                        raise OSError('The SDK rejected the device operation')
                    guard()
                    return result
                except queue.Empty:
                    if self.failure or self.process.poll() is not None:
                        raise OSError('The SDK runtime is disconnected')
            raise TimeoutError('The SDK did not finish this request in time')
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.send({'command': 'stop'})
                    self.process.stdin.close()
                    self.process.wait(timeout=9)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
        finally:
            if self.job is not None:
                self.job.__exit__(None, None, None)
                self.job = None
            if self.process is not None:
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    stream.close()
            for reader in (self.reader, self.error_reader):
                if reader is not None:
                    reader.join(timeout=2)


class ProductControls:
    def __init__(self, state, serial, controls):
        self.state_root, self.serial = state, serial
        self.settings = validate_settings(controls)
        self.observed = {}
        self.connected = self.host_connected = self.knob_supported = False
        self.firmware = None
        self.channel = None
        self.stop_guard = lambda: None
        self.lock = threading.RLock()

    def state(self):
        with self.lock:
            current = {**self.settings, **self.observed}
            return {'serial': self.serial, 'model': 'Mirabox N4 Pro', 'firmware': self.firmware,
                    'available': self.connected and self.knob_supported, 'connected': self.connected,
                    'hostConnected': self.host_connected, 'knobBrightness': current.get('knobBrightness', current.get('brightness', 100)),
                    'followDisplayBrightness': current.get('followDisplayBrightness', True),
                    'capabilities': {'knobBrightness': self.connected and self.knob_supported}}

    def update_state(self, value):
        settings = {key: value[key] for key in (*SETTINGS, 'followDisplayBrightness') if key in value}
        with self.lock:
            self.observed = validate_settings(settings)

    def save(self, value):
        settings = validate_settings(value)
        with self.lock:
            merged = {**self.settings, **settings}
            if 'sleepDuration' in settings:
                write_json(self.state_root / f'display-{self.serial}.json', {'sleepDuration': merged['sleepDuration']})
            if any(key in settings for key in ('knobBrightness', 'followDisplayBrightness')):
                chosen = None if merged.get('followDisplayBrightness', True) else merged.get('knobBrightness', merged.get('brightness', 100))
                write_json(self.state_root / f'device-controls-{self.serial}.json',
                           {'version': 1, 'serial': self.serial, 'knobBrightness': chosen})
            self.settings = merged

    def apply(self, payload, guard=lambda: None):
        if payload.get('serial') != self.serial or payload.get('action') not in ('knobBrightness', 'followDisplayBrightness'):
            raise ValueError('Unsupported device control')
        control = validate_settings({payload['action']: payload.get('value')})
        persist = payload.get('persist', True)
        if type(persist) is not bool:
            raise ValueError('persist must be a boolean')
        if not self.connected or not self.knob_supported or self.channel is None:
            raise OSError('The N4 device is disconnected')
        def active_guard():
            guard()
            self.stop_guard()
        command = {'control': payload['action'], 'value': control[payload['action']]}
        if not persist:
            command['persist'] = False
        result = self.channel.request('control', control=command, guard=active_guard)
        if result.get('event') != 'controlSubmitted' or result.get('status') not in ('applied', 'submitted'):
            raise OSError('The SDK did not accept this device control')
        observed = self.channel.request('workerState', guard=active_guard)
        if observed.get('event') != 'workerState' or not isinstance(observed.get('state'), dict):
            raise OSError('The SDK did not return device state')
        self.update_state(observed['state'])
        return {'ok': True, 'applied': True, 'state': self.state()}


class ProductBridge:
    def __init__(self, state, stop_file, *, root=ROOT, emit=None, process_factory=ProductProcess):
        from hellgato_stream_dog.settings import load_settings
        from hellgato_stream_dog.device_registry import DeviceRegistry
        self.root, self.state_root, self.stop_file = root, state, stop_file
        self.settings = load_settings(state / 'settings.json')
        self.serial = f"HGMOCK{self.settings['identity']:06d}"
        self.registry = DeviceRegistry(state, self.settings['identity'], self.settings['dockIdentity'])
        self.controls = ProductControls(state, self.serial, initial_controls(state, self.serial))
        self.controls.stop_guard = self.check_running
        self.emit = emit or (lambda value: print(json.dumps(value, separators=(',', ':')), flush=True))
        self.process_factory = process_factory
        self.discovered = queue.Queue(maxsize=1)
        self.selected = None
        self.failure = None

    def on_event(self, value):
        event = value.get('event')
        if event == 'devices-discovered':
            self.discovered.put_nowait(value.get('devices'))
        elif event == 'settingsChanged':
            self.controls.save(value.get('settings'))
        elif event in LIFECYCLE_EVENTS:
            detail = value.get('detail', value)
            if event in ('deviceReady', 'deviceReconnected'):
                if self.selected is None or detail.get('deviceId') != self.selected['id']:
                    raise ValueError('The worker selected a different registered device')
                self.registry.remember(self.selected)
                self.controls.connected = True
                self.controls.firmware = detail.get('firmware')
                features = detail.get('capabilities', {}).get('features', {})
                self.controls.knob_supported = features.get('knobBrightness', {}).get('status') == 'native'
            elif event == 'deviceDisconnected':
                self.controls.connected = False
            elif event.startswith('child.client'):
                self.controls.host_connected = event == 'child.clientConnected'
            self.emit({'event': event, 'detail': {key: item for key, item in detail.items()
                       if key in ('deviceId', 'code', 'ports', 'attempt')}})
        elif value.get('state') == 'failed':
            code = value.get('code')
            self.failure = code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,80}', code) else 'CANDIDATE_FAILED'

    def stopped(self):
        return self.stop_file.exists()

    def check_running(self):
        if self.stopped():
            raise InterruptedError('The product connection is stopping')

    def select_device(self, devices):
        if not isinstance(devices, list) or len(devices) > 64:
            raise ValueError('Invalid worker discovery inventory')
        candidates = [item for item in devices if isinstance(item, dict) and item.get('modelId') == 'n4-pro']
        existing = next((item for item in self.registry.devices() if item['identity'] == self.settings['identity']), None)
        if existing is not None:
            candidates = [item for item in candidates if item.get('id') == existing['id']]
        if len(candidates) != 1:
            reason = 'registered-device-mismatch' if existing is not None and devices else 'device-not-found'
            self.emit({'event': 'deviceWaiting', 'detail': {'reason': reason}})
            return None
        return self.registry.prepare_descriptor(candidates[0])

    @contextmanager
    def channel(self, *, discover=False, saver=None):
        config, command = configuration(self.root, self.state_root, self.settings, self.controls.settings,
                                        None if discover else self.selected['id'], saver)
        config_path = self.state_root / 'ipc-config.json'
        write_json(config_path, config)
        channel = self.process_factory(command, config_path, self.on_event, discover=discover)
        try:
            channel.start()
            yield channel
        finally:
            channel.close()
            config_path.unlink(missing_ok=True)

    def discover(self, server):
        while not self.discovered.empty():
            self.discovered.get_nowait()
        with self.channel(discover=True) as channel:
            deadline = time.monotonic() + 35
            while not self.stopped() and time.monotonic() < deadline:
                server.drain()
                try:
                    return self.select_device(self.discovered.get(timeout=.05))
                except queue.Empty:
                    if channel.failure or channel.process.poll() is not None:
                        if self.failure:
                            raise ProductRuntimeError(self.failure)
                        raise ProductRuntimeError(channel.failure or 'ERR_WORKER_EOF')
            if not self.stopped():
                raise TimeoutError('The worker did not finish device discovery')
        return None

    def run(self):
        from device_control_server import DeviceControlServer
        from screensaver import ScreensaverSource
        self.state_root.mkdir(parents=True, exist_ok=True)
        # Existing product preference files are authoritative at each GUI start.
        (self.state_root / f'ipc-state-{self.serial}.json').unlink(missing_ok=True)
        server = DeviceControlServer(self.state_root, self.serial, self.controls.state, self.controls.apply)
        saver = ScreensaverSource(self.serial)
        try:
            self.emit({'event': 'bridgeReady'})
            while not self.stopped():
                self.failure = None
                self.selected = self.discover(server)
                if self.selected is None:
                    for _ in range(20):
                        if self.stopped():
                            break
                        server.drain()
                        time.sleep(.05)
                    continue
                saver.reload()
                with self.channel(saver=saver.path) as channel:
                    self.controls.channel = channel
                    next_query = next_saver = 0
                    while not self.stopped() and channel.process.poll() is None and channel.failure is None:
                        server.drain()
                        now = time.monotonic()
                        if self.controls.connected and now >= next_query:
                            next_query = now + 1
                            try:
                                observed = channel.request('workerState', guard=self.check_running, timeout=2)
                            except (OSError, TimeoutError):
                                if self.stopped():
                                    break
                            else:
                                if isinstance(observed.get('state'), dict):
                                    self.controls.update_state(observed['state'])
                                    if observed['state'].get('connected') is False:
                                        self.controls.connected = False
                        if now >= next_saver:
                            next_saver = now + 1
                            if saver.reload():
                                break
                        time.sleep(.05)
                self.controls.channel = None
                self.controls.connected = self.controls.host_connected = False
                failure = self.failure or channel.failure
                self.emit({'event': 'deviceDisconnected', 'detail': {'code': failure or 'SESSION_ENDED'}})
                if not self.stopped() and failure not in (None, 'DEVICE_NOT_FOUND', 'ERR_DEVICE_NOT_FOUND', 'ADAPTER_DISCONNECTED'):
                    raise ProductRuntimeError(failure)
        finally:
            self.controls.channel = None
            self.controls.connected = self.controls.host_connected = False
            server.close()
            saver.close()


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if '--inspect-host' in arguments:
        from hellgato_host.readiness import main as inspect_main
        arguments.remove('--inspect-host')
        return inspect_main(['inspect', *arguments])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init', action='store_true')
    parser.parse_args(arguments)
    state = Path(os.environ.get('HELLGATO_STATE_DIR', Path(os.environ['LOCALAPPDATA']) / 'Hellgato'))
    stop_file = Path(os.environ.get('HELLGATO_STOP_FILE', state / 'bridge.stop'))
    try:
        ProductBridge(state, stop_file).run()
        return 0
    except (OSError, ValueError, KeyError, TimeoutError) as error:
        code = error.code if isinstance(error, ProductRuntimeError) else 'PRODUCT_IPC_FAILED'
        print(json.dumps({'event': 'ipcFailed', 'detail': {'code': code}}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

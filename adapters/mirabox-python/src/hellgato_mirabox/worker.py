"""Physical-only JSON-RPC worker: stdin/stdout, no Node or host installation access."""
import argparse
from collections import deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import tempfile
import traceback

from . import __version__
from .protocol import (VERSION, FEATURES, MAX_MESSAGE_BYTES, MAX_FRAME_BYTES, MAX_REQUESTS,
                       MAX_QUEUE_BYTES, METHODS, ProtocolError, check_deadline, require,
                       validate_contract, validate_rpc_message)


class Worker:
    def __init__(self, backend, source, sink, build_id):
        self.backend, self.source, self.sink = backend, source, sink
        self.build_id = build_id
        self.stopped = threading.Event()
        self.requests = queue.Queue(maxsize=MAX_REQUESTS)
        self.outgoing = queue.Queue(maxsize=128)
        self.queue_lock = threading.Lock()
        self.queued_bytes = 0
        self.output_bytes = 0
        self.negotiated = False
        self.max_message_bytes = MAX_MESSAGE_BYTES
        self.current = None
        self.descriptor = None
        self.generation = 0
        self.sequence = 0
        self.output_sequence = -1
        self.session_id = None
        self.seen = set()
        self.seen_order = deque(maxlen=1024)
        self.fatal = None

    def send(self, message):
        data = (json.dumps(message, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if len(data) > self.max_message_bytes:
            self.fail('ERR_MESSAGE_TOO_LARGE')
            return
        with self.queue_lock:
            if self.output_bytes + len(data) > MAX_QUEUE_BYTES or self.outgoing.full():
                self.fail('ERR_BACKPRESSURE')
                return
            self.output_bytes += len(data)
            self.outgoing.put_nowait(data)

    def fail(self, code):
        if self.fatal is None:
            self.fatal = code
        self.stopped.set()

    def _write(self):
        while not self.stopped.is_set() or not self.outgoing.empty():
            try:
                data = self.outgoing.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self.sink.write(data)
                self.sink.flush()
            except (BrokenPipeError, OSError, ValueError):
                self.fail('ERR_WORKER_PIPE')
                return
            finally:
                with self.queue_lock:
                    self.output_bytes -= len(data)
                self.outgoing.task_done()

    def _read(self):
        while not self.stopped.is_set():
            try:
                data = self.source.readline(MAX_MESSAGE_BYTES + 1)
            except (OSError, ValueError):
                self.fail('ERR_WORKER_PIPE')
                return
            if not data:
                self.stopped.set()  # EOF cancels pending writes before closing USB.
                return
            if len(data) > MAX_MESSAGE_BYTES or not data.endswith(b'\n'):
                self.fail('ERR_MESSAGE_TOO_LARGE' if len(data) > MAX_MESSAGE_BYTES else 'ERR_IPC_PROTOCOL')
                return
            with self.queue_lock:
                if self.queued_bytes + len(data) > MAX_QUEUE_BYTES or self.requests.full():
                    self.fail('ERR_BACKPRESSURE')
                    return
                self.queued_bytes += len(data)
                self.requests.put_nowait(data)

    def emit_input(self, event):
        if self.current is None or self.stopped.is_set():
            return
        self.sequence += 1
        payload = {**event, 'deviceId': self.current, 'generation': self.generation, 'sequence': self.sequence}
        if self.session_id:
            payload['sessionId'] = self.session_id
        validate_contract('InputEvent', payload)
        self.send({'jsonrpc': '2.0', 'method': 'input', 'params': payload})

    def notify_state(self, state):
        self.send({'jsonrpc': '2.0', 'method': 'state', 'params': {
            'state': state, 'deviceId': self.current, 'generation': self.generation}})

    def _active(self, params):
        require(self.current is not None, 'No open device', 'ERR_DEVICE_CLOSED')
        require(params.get('deviceId') == self.current, 'Unknown open device', 'ERR_DEVICE_ID')
        require(type(params.get('generation')) is int and params['generation'] == self.generation,
                'Stale session generation', 'ERR_STALE_GENERATION')

    def _deadline(self, params):
        require(not self.stopped.is_set(), 'Worker stopping', 'ERR_WORKER_STOPPED')
        check_deadline(params)

    def _error_details(self, details):
        snapshot = getattr(self.backend, 'get_display_observations', None)
        if callable(snapshot):
            try:
                observations = snapshot()
                if isinstance(observations, dict):
                    return {**details, 'displayObservations': observations}
            except Exception:
                pass
        return details

    def dispatch(self, message):
        validate_rpc_message(message)
        require('method' in message and 'id' in message, 'Only requests with IDs are accepted')
        request_id, method, params = message['id'], message['method'], message['params']
        require(request_id not in self.seen, 'Duplicate request ID', 'ERR_DUPLICATE_REQUEST')
        if len(self.seen_order) == self.seen_order.maxlen:
            self.seen.discard(self.seen_order.popleft())
        self.seen.add(request_id)
        self.seen_order.append(request_id)
        require(method in METHODS, 'Unknown operation', 'ERR_UNSUPPORTED_OPERATION', -32601)
        self._deadline(params)
        if method == 'hello':
            require(not self.negotiated, 'Hello was already negotiated')
            protocol = params.get('protocol')
            require(type(protocol) is dict and type(protocol.get('major')) is int and protocol['major'] == VERSION['major'] and
                    type(protocol.get('minor')) is int and protocol['minor'] >= 0, 'Incompatible IPC version', 'ERR_IPC_VERSION', -32001)
            features = params.get('requiredFeatures', [])
            require(type(features) is list and all(isinstance(feature, str) and feature in FEATURES for feature in features),
                    'Unsupported required feature', 'ERR_IPC_VERSION', -32001)
            limit = params.get('maxMessageBytes', MAX_MESSAGE_BYTES)
            require(type(limit) is int and 1024 <= limit <= MAX_MESSAGE_BYTES, 'Invalid negotiated message limit')
            self.max_message_bytes = limit
            self.negotiated = True
            return {'protocol': {'major': 1, 'minor': min(protocol['minor'], VERSION['minor'])},
                    'implementationVersion': __version__, 'buildId': self.build_id, 'features': FEATURES,
                    'maxMessageBytes': limit, 'maxFrameBytes': MAX_FRAME_BYTES, 'supportStatus': 'experimental'}
        require(self.negotiated, 'hello required before device operations', 'ERR_HANDSHAKE_REQUIRED')
        if method == 'discover':
            devices = self.backend.discover(lambda: self._deadline(params))
            require(len(devices) <= 64, 'Too many device candidates')
            return [validate_contract('DeviceDescriptor', item) for item in devices]
        if method == 'open':
            require(self.current is None, 'Device already open', 'ERR_DEVICE_BUSY')
            require(isinstance(params.get('deviceId'), str) and 0 < len(params['deviceId']) <= 128, 'Explicit device ID required')
            require(type(params.get('generation')) is int and self.generation < params['generation'] <= 9007199254740991,
                    'Open requires a newer generation', 'ERR_STALE_GENERATION')
            session = params.get('sessionId')
            require(session is None or isinstance(session, str) and 0 < len(session) <= 128, 'Invalid session ID')
            self.generation = params['generation']
            self.current, self.session_id = params['deviceId'], session
            self.sequence, self.output_sequence = 0, -1
            try:
                self.descriptor = validate_contract('DeviceDescriptor', self.backend.open(self.current, self.emit_input, lambda: self._deadline(params)))
            except Exception:
                self.current = None
                self.backend.close()
                raise
            return {'status': 'open', 'deviceId': self.current, 'generation': self.generation, 'descriptor': self.descriptor}
        if method == 'close':
            self._active(params)
            reason = params.get('releaseReason')
            require(reason is None or reason == 'host-exit', 'Unknown device release reason', 'ERR_CONTRACT')
            if reason == 'host-exit':
                self.backend.close(notify=True)
            else:
                self.backend.close()
            self.current, self.descriptor = None, None
            return {'status': 'closed', 'generation': self.generation}
        if method == 'getState':
            if 'deviceId' in params:
                self._active(params)
            return {**self.backend.get_state(), 'deviceId': self.current, 'generation': self.generation}
        self._active(params)
        payload = {key: value for key, value in params.items() if key != 'deadlineAt'}
        validate_contract('DisplayFrame' if method == 'setOutput' else 'ControlCommand', payload)
        sequence = payload.get('sequence')
        if sequence is not None:
            require(sequence > self.output_sequence, 'Output sequence is not increasing', 'ERR_OUTPUT_ORDER')
        surfaces = {surface['id']: surface for surface in self.descriptor['capabilities']['displays']}
        if method == 'setOutput':
            surface = surfaces.get(payload['surfaceId'])
            require(surface is not None, 'Unknown display surface', 'ERR_UNSUPPORTED_OPERATION')
            require((payload['width'], payload['height'], payload['format']) == (surface['width'], surface['height'], surface['format']),
                    'Frame differs from display capabilities', 'ERR_CONTRACT')
            require('region' not in payload or surface['partial'], 'Partial frames unsupported', 'ERR_UNSUPPORTED_OPERATION')
            if sequence is not None:
                self.output_sequence = sequence
            result = self.backend.set_output(payload, lambda: self._deadline(params))
        else:
            require('surfaceId' not in payload or payload['surfaceId'] in surfaces, 'Unknown control surface', 'ERR_UNSUPPORTED_OPERATION')
            require('surfaceId' not in payload or payload['control'] in ('clear', 'fill'),
                    'This control is device-wide', 'ERR_UNSUPPORTED_OPERATION')
            if sequence is not None:
                self.output_sequence = sequence
            result = self.backend.set_control(payload, lambda: self._deadline(params))
        return {**result, 'generation': self.generation, 'sequence': sequence}

    def run(self):
        writer = threading.Thread(target=self._write, daemon=True, name='ipc-output')
        reader = threading.Thread(target=self._read, daemon=True, name='ipc-input')
        writer.start()
        reader.start()
        try:
            while not self.stopped.is_set():
                # Service idle/animation/input wake even under continuous output load.
                try:
                    if self.current and self.backend.poll(lambda: require(not self.stopped.is_set(),
                            'Worker stopping', 'ERR_WORKER_STOPPED')) == 'disconnected':
                        self.notify_state('disconnected')
                except Exception:
                    if not self.stopped.is_set():
                        self.fail('ERR_DEVICE_OPERATION')
                    break
                try:
                    data = self.requests.get(timeout=0.02)
                except queue.Empty:
                    continue
                with self.queue_lock:
                    self.queued_bytes -= len(data)
                request_id = None
                try:
                    message = json.loads(data.decode('utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid number')))
                    if type(message) is dict and isinstance(message.get('id'), str) and len(message['id']) <= 128:
                        request_id = message['id']
                    result = self.dispatch(message)
                    self.send({'jsonrpc': '2.0', 'id': request_id, 'result': result})
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
                    code = exc.rpc_code if isinstance(exc, ProtocolError) else -32700
                    name = exc.code if isinstance(exc, ProtocolError) else 'ERR_IPC_PARSE'
                    text = str(exc)[:1024] if isinstance(exc, ProtocolError) else 'Malformed protocol message'
                    details = self._error_details({'code': name})
                    self.send({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': text, 'data': details}})
                except Exception as exc:
                    details = self._error_details({'code': 'ERR_DEVICE_OPERATION', 'outcome': 'unknown',
                        'exceptionType': type(exc).__name__, 'message': str(exc)[:1024],
                        'stack': traceback.format_exc(limit=8)[-4096:]})
                    native_code = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
                    if type(native_code) is int:
                        details['nativeCode'] = native_code
                    self.send({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32000,
                        'message': 'Physical backend operation failed', 'data': details}})
                finally:
                    self.requests.task_done()
        finally:
            self.stopped.set()
            try:
                self.backend.close()
            finally:
                writer.join(timeout=1)
        return 2 if self.fatal else 0



@contextmanager
def physical_working_directory(enabled):
    """Contain the pinned SDK's relative temporary PNGs for this worker process.

    SDK and saved assets are explicit absolute paths. A private lifetime cwd also
    supports read-only launch directories and cleans files left by SDK failures.
    The fake backend needs no filesystem workspace.
    Forced OS termination can leave this temporary directory for OS/user cleanup;
    normal shutdown and Python exceptions restore cwd before removing its files.
    """
    if not enabled:
        yield
        return
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='hellgato-mirabox-runtime-') as directory:
        try:
            os.chdir(directory)
            yield
        finally:
            os.chdir(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('mirabox', 'fake'), default='mirabox')
    parser.add_argument('--sdk-path', type=Path, help='Explicit prepared vendor SDK source directory')
    parser.add_argument('--strip-mode', choices=('frame', 'panels'), default='panels', help='Explicit N4 touch output policy; product passes the saved mode')
    parser.add_argument('--lcd-mode', choices=('normal', 'stream-screensaver'), default='normal',
                        help='Interpret streamed host LCD images as screensaver overlays when explicitly selected')
    parser.add_argument('--logo-path', type=Path, help='Explicit absolute software display PNG; never boot-logo/firmware')
    parser.add_argument('--screensaver-path', type=Path, help='Explicit absolute saved image/animation; never modifies the file')
    parser.add_argument('--build-id', default='source-candidate', help='Build ID provided by verified runtime manifest')
    parser.add_argument('--fake-write-delay-ms', type=int, default=0)
    parser.add_argument('--fake-input-count', type=int, default=0)
    args = parser.parse_args(argv)
    if args.logo_path is not None and (args.backend != 'mirabox' or not args.logo_path.is_absolute() or not args.logo_path.is_file()):
        parser.error('--logo-path requires an existing absolute local PNG and physical backend')
    if args.screensaver_path is not None and (args.backend != 'mirabox' or not args.screensaver_path.is_absolute() or not args.screensaver_path.is_file()):
        parser.error('--screensaver-path requires an existing absolute local file and physical backend')
    if not 0 < len(args.build_id) <= 128 or not 0 <= args.fake_write_delay_ms <= 10_000 or not 0 <= args.fake_input_count <= 10_000:
        parser.error('Invalid bounded worker arguments')
    with physical_working_directory(args.backend == 'mirabox'):
        # Isolate stdout at the OS descriptor, including native printf output.
        protocol_stdout = os.fdopen(os.dup(sys.stdout.fileno()), 'wb', buffering=0)
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        if args.backend == 'fake':
            from .transport.fake import FakeBackend
            backend = FakeBackend(args.fake_write_delay_ms, args.fake_input_count)
        else:
            from .transport.mirabox import MiraboxBackend
            if args.sdk_path is None or not args.sdk_path.is_absolute() or not args.sdk_path.is_dir():
                parser.error('--sdk-path must name an explicit prepared absolute SDK directory')
            backend = MiraboxBackend(args.sdk_path, args.screensaver_path, args.logo_path, args.strip_mode, args.lcd_mode)
        worker = Worker(backend, sys.stdin.buffer, protocol_stdout, args.build_id)
        for name in ('SIGTERM', 'SIGINT'):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda _signum, _frame: worker.stopped.set())
        try:
            return worker.run()
        finally:
            if args.backend == 'mirabox' and backend.device is None:
                backend.release_ownership()
            protocol_stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())

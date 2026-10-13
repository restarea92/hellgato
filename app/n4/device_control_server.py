"""Authenticated loopback requests queued for the USB bridge thread."""

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import queue
import secrets
import threading
import time


MAX_BODY_BYTES = 8192


class ControlCancelled(TimeoutError):
    pass


@dataclass
class ControlRequest:
    payload: dict
    deadline: float
    clock: object = time.monotonic
    completed: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    status: int = 200

    def guard(self):
        if self.cancelled.is_set() or self.clock() >= self.deadline:
            raise ControlCancelled('The control request expired before the hardware write')


class DeviceControlServer:
    def __init__(self, state_root, serial, get_state, apply_control, *, port=5345,
                 timeout=10, clock=time.monotonic, token=None):
        self.serial = serial
        self.get_state = get_state
        self.apply_control = apply_control
        self.timeout = timeout
        self.clock = clock
        self.requests = queue.Queue(maxsize=16)
        self.stopped = threading.Event()
        self.token = token or secrets.token_urlsafe(32)
        self.http = ThreadingHTTPServer(('127.0.0.1', port), self._handler())
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]
        self.config_path = Path(state_root) / 'native-ui.json'
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        endpoint = f'http://127.0.0.1:{self.port}'
        self.config_path.write_text(json.dumps({'version': 1, 'serial': serial,
                                               'endpoint': endpoint, 'url': endpoint,
                                               'diagnosticsPath': str(Path(state_root) / 'native-ui-diagnostics.log'),
                                               'token': self.token}) + '\n', encoding='utf-8')
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.05),
                                       name='N4 local controls', daemon=True)
        self.thread.start()

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def reply(self, status, value):
                encoded = (json.dumps(value) + '\n').encode('utf-8')
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass

            def authenticated(self):
                expected = f'Bearer {owner.token}'
                actual = self.headers.get('Authorization', '')
                host = self.headers.get('Host', '')
                if (not hmac.compare_digest(actual, expected)
                        or host not in (f'127.0.0.1:{owner.port}', f'localhost:{owner.port}')
                        or self.headers.get('Origin') is not None):
                    self.reply(403, {'ok': False, 'error': 'Unauthorized local control request'})
                    return False
                if owner.stopped.is_set():
                    self.reply(503, {'ok': False, 'error': 'The device bridge is stopping'})
                    return False
                return True

            def do_GET(self):
                if not self.authenticated():
                    return
                if self.path != '/state':
                    self.reply(404, {'ok': False, 'error': 'Unknown endpoint'})
                    return
                try:
                    self.reply(200, owner.get_state())
                except Exception:
                    self.reply(503, {'ok': False, 'error': 'Device state is not available'})

            def do_POST(self):
                if not self.authenticated():
                    return
                if self.path != '/control':
                    self.reply(404, {'ok': False, 'error': 'Unknown endpoint'})
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= MAX_BODY_BYTES or self.headers.get('Transfer-Encoding'):
                        raise ValueError('Invalid control request size')
                    if self.headers.get_content_type() != 'application/json':
                        raise ValueError('Control requests must contain JSON')
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict) or payload.get('serial') != owner.serial:
                        raise ValueError('Invalid device serial')
                    if payload.get('action') not in ('knobBrightness', 'followDisplayBrightness'):
                        raise ValueError('Unknown device control action')
                except (ValueError, UnicodeError, TimeoutError):
                    self.reply(400, {'ok': False, 'error': 'Invalid control request'})
                    return
                request = ControlRequest(payload, owner.clock() + owner.timeout, owner.clock)
                try:
                    owner.requests.put_nowait(request)
                except queue.Full:
                    self.reply(503, {'ok': False, 'error': 'The control request queue is full'})
                    return
                if not request.completed.wait(owner.timeout):
                    request.cancelled.set()
                    self.reply(504, {'ok': False, 'error': 'The device did not complete this request in time'})
                    return
                self.reply(request.status, request.result)

        return Handler

    def drain(self, limit=4):
        """Call only from the bridge thread; callbacks check guard before every USB write."""
        count = 0
        while count < limit:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                break
            count += 1
            try:
                if self.stopped.is_set():
                    request.cancelled.set()
                request.guard()
                result = self.apply_control(request.payload, request.guard)
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise OSError('The device did not confirm this control operation')
                request.result = result
            except ControlCancelled as error:
                request.status = 504
                request.result = {'ok': False, 'error': str(error)}
            except (TypeError, ValueError) as error:
                request.status = 400
                request.result = {'ok': False, 'error': str(error)}
            except Exception as error:
                request.status = 503
                request.result = {'ok': False, 'error': str(error)}
            finally:
                request.completed.set()
        return count

    def close(self):
        self.stopped.set()
        self.drain(limit=16)
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

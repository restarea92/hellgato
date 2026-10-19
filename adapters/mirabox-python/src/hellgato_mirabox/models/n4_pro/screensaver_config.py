"""Prepare explicit local screensaver updates without touching the USB writer."""
import json
from pathlib import Path
import threading
import time

from .animation import NativeAnimation

MAX_CONFIG_BYTES = 8192
PREPARE_TIMEOUT = 30


class PreparationCancelled(Exception):
    pass


def local_path(value):
    return (isinstance(value, str) and bool(value) and '\x00' not in value
            and not value.startswith(('\\\\', '//')) and Path(value).is_absolute())


def read_config(path):
    with Path(path).open('rb') as source:
        data = source.read(MAX_CONFIG_BYTES + 1)
    if not 0 < len(data) <= MAX_CONFIG_BYTES:
        raise ValueError('Screensaver config byte bound')
    def unique_object(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError('Duplicate screensaver config field')
        return value
    value = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object)
    if (not isinstance(value, dict) or set(value) != {'version', 'revision', 'path'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['revision']) is not int or not 0 <= value['revision'] <= 9007199254740991
            or value['path'] is not None and not local_path(value['path'])):
        raise ValueError('Invalid screensaver config')
    return value['revision'], value['path']


class ScreensaverConfig:
    def __init__(self, path, initial_path, *, animation_factory=NativeAnimation, clock=time.monotonic):
        if not local_path(str(path)):
            raise ValueError('Screensaver config must be an explicit absolute local path')
        self.path = Path(path)
        self.initial_path = None if initial_path is None else str(initial_path)
        self.factory, self.clock = animation_factory, clock
        self.revision = 0
        self.loading = False
        self.error = None
        self.desired = None
        self.highest = -1
        self.attempted = None
        self.thread = None
        self.cancel = threading.Event()
        self.result = None
        self.lock = threading.Lock()
        self.closed = False
        self.initial_request = None
        try:
            self.initial_request = read_config(self.path)
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.error = 'ERR_SCREENSAVER_CONFIG_INVALID'
        else:
            self.highest, self.initial_path = self.initial_request
            self.desired = self.initial_request
            self.attempted = self.highest

    def _prepare(self, request, cancel):
        started = self.clock()

        def check():
            if cancel.is_set():
                raise PreparationCancelled()
            if self.clock() - started >= PREPARE_TIMEOUT:
                raise TimeoutError('Screensaver preparation deadline')

        animation, error = None, None
        try:
            animation = self.factory()
            animation.load(request[1], check)
            check()
        except PreparationCancelled:
            error = 'ERR_SCREENSAVER_CANCELLED'
        except TimeoutError:
            error = 'ERR_SCREENSAVER_TIMEOUT'
        except Exception:
            error = 'ERR_ASSET_INVALID'
        if error and animation is not None:
            animation.clear()
            animation = None
        with self.lock:
            self.result = request, animation, error

    def poll(self):
        if self.closed:
            return None
        try:
            request = read_config(self.path)
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.error = 'ERR_SCREENSAVER_CONFIG_INVALID'
        else:
            revision, path = request
            if revision > self.highest:
                self.cancel.set()
                self.highest = revision
                self.desired = request
                self.loading, self.error = True, None
                if revision == 0 and path == self.initial_path:
                    self.attempted = 0
                    self.loading = False
            elif revision == self.highest and self.desired is not None and request != self.desired:
                self.error = 'ERR_SCREENSAVER_CONFIG_INVALID'
            elif request == self.desired and self.error == 'ERR_SCREENSAVER_CONFIG_INVALID':
                self.error = None
        if self.thread is not None and not self.thread.is_alive():
            self.thread.join()
            self.thread = None
            with self.lock:
                result, self.result = self.result, None
            if result is not None:
                request, animation, error = result
                if request == self.desired:
                    self.loading, self.error = False, error
                    if error is None:
                        return request[0], animation
                if animation is not None:
                    animation.clear()
        if self.desired is None or self.attempted == self.desired[0]:
            return None
        if self.desired[1] is None:
            self.attempted = self.desired[0]
            self.loading = False
            return self.desired[0], None
        if self.thread is None:
            self.attempted = self.desired[0]
            self.cancel = threading.Event()
            self.thread = threading.Thread(target=self._prepare, args=(self.desired, self.cancel),
                                           name='screensaver-prepare', daemon=False)
            self.thread.start()
        return None

    def applied(self, revision):
        self.revision = revision
        self.error = None

    def close(self):
        self.closed = True
        self.cancel.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        with self.lock:
            if self.result is not None and self.result[1] is not None:
                self.result[1].clear()
            self.result = None
        self.loading = False

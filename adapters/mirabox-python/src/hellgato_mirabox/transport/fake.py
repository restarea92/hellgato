"""Explicit in-memory transport for integration/contribution tests; never auto-selected."""
import base64
from collections import deque
import time
from ..protocol import require

DEVICE_ID = 'fake:buttons-dial'


def descriptor():
    return {'id': DEVICE_ID, 'adapterId': 'hellgato.fake-sidecar', 'modelId': 'buttons-dial',
            'firmware': 'simulation', 'capabilities': {'keys': 2, 'dials': 1, 'touch': None,
            'displays': [{'id': f'key:{index}', 'width': 4, 'height': 4, 'format': 'rgb888', 'partial': True} for index in range(2)],
            'dialPress': 'native', 'dialRelease': 'native',
            'features': {**{name: {'status': 'emulated', 'limitations': ['In-memory simulation only']} for name in ('brightness', 'clear', 'fill', 'sleep', 'wake', 'sleepDuration', 'screensaver', 'knobBrightness', 'followDisplayBrightness')}, 'simulation': {'status': 'native', 'limitations': ['No physical hardware evidence']}}}}


class FakeBackend:
    def __init__(self, write_delay_ms=0, input_count=0, *, clock=time.monotonic):
        self.device = None
        self.write_delay_ms = write_delay_ms
        self.input_count = input_count
        self.clock = clock
        self._reset()

    def _reset(self):
        self.writes = deque(maxlen=64)
        self.write_count = 0
        self.frames = {}
        self.brightness = 100
        self.sleeping = False
        self.knob_brightness = None
        self.sleep_seconds = 0
        self.last_activity = self.clock()

    def discover(self, check_deadline=lambda: None):
        check_deadline()
        return [descriptor()]

    def open(self, device_id, emit, check_deadline=lambda: None):
        require(device_id == DEVICE_ID, 'Unknown device', 'ERR_DEVICE_NOT_FOUND')
        require(self.device is None, 'Device already open', 'ERR_DEVICE_BUSY')
        check_deadline()
        self._reset()
        self.device = device_id
        for index in range(self.input_count):
            check_deadline()
            emit({'type': 'key', 'index': 0, 'state': 'down' if index % 2 == 0 else 'up'})
        return descriptor()

    def close(self, *, notify=False):
        self.device = None

    def poll(self, check_deadline=lambda: None):
        check_deadline()
        if self.device is not None and self.sleep_seconds and self.clock() - self.last_activity >= self.sleep_seconds:
            self.sleeping = True
        return None

    def _before_write(self, check_deadline):
        require(self.device is not None, 'Device is closed', 'ERR_DEVICE_CLOSED')
        until = time.monotonic() + self.write_delay_ms / 1000
        while True:
            check_deadline()
            remaining = until - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(.01, remaining))

    def set_output(self, frame, check_deadline):
        self._before_write(check_deadline)
        region = frame.get('region', {'x': 0, 'y': 0, 'width': frame['width'], 'height': frame['height']})
        full = bytearray(self.frames.get(frame['surfaceId'], bytes(frame['width'] * frame['height'] * 3)))
        incoming = base64.b64decode(frame['data'])
        for row in range(region['height']):
            start = ((row + region['y']) * frame['width'] + region['x']) * 3
            length = region['width'] * 3
            full[start:start + length] = incoming[row * length:(row + 1) * length]
        self.frames[frame['surfaceId']] = bytes(full)
        self.write_count += 1
        self.writes.append({'type': 'frame', 'surfaceId': frame['surfaceId'], 'sequence': frame.get('sequence')})
        return {'status': 'applied', 'verification': 'simulation'}

    def set_control(self, control, check_deadline):
        require(control['control'] in ('brightness', 'clear', 'fill', 'sleep', 'wake', 'sleepDuration', 'screensaver', 'knobBrightness', 'followDisplayBrightness'), 'Control not implemented by fake backend', 'ERR_UNSUPPORTED_OPERATION', -32601)
        self._before_write(check_deadline)
        kind = control['control']
        if kind == 'brightness':
            if control['value'] == 0:
                self.sleeping = True
            elif not self.sleeping or control['value'] >= self.brightness:
                self.brightness = control['value']
                self.sleeping = False
                self.last_activity = self.clock()
        if kind == 'sleepDuration':
            self.sleep_seconds = control['value']
            self.last_activity = self.clock()
        if kind in ('sleep', 'screensaver', 'wake'):
            self.sleeping = kind != 'wake'
            if not self.sleeping:
                self.last_activity = self.clock()
        if kind == 'knobBrightness':
            self.knob_brightness = control['value']
        if kind == 'followDisplayBrightness':
            self.knob_brightness = (None if control['value'] else
                self.brightness if self.knob_brightness is None else self.knob_brightness)
        if kind in ('clear', 'fill'):
            color = bytes(control.get('color', [0, 0, 0]))
            surfaces = [control['surfaceId']] if 'surfaceId' in control else ['key:0', 'key:1']
            for surface in surfaces:
                self.frames[surface] = color * 16
        self.write_count += 1
        self.writes.append({'type': 'control', 'control': kind, 'sequence': control.get('sequence')})
        effect = ({'normalStateEffect': 'surface' if 'surfaceId' in control else 'replace-all'}
                  if kind in ('clear', 'fill') else {})
        return {'status': 'applied', 'verification': 'simulation', **effect,
                'idle': self.sleeping, 'sleeping': self.sleeping, 'screensaverActive': False, 'logoActive': False}

    def get_state(self):
        return {'connected': self.device is not None, 'brightness': self.brightness, 'sleeping': self.sleeping,
                'screenBrightness': self.brightness, 'sleepDuration': self.sleep_seconds, 'idle': self.sleeping,
                'screensaverReady': False, 'screensaverActive': False,
                'knobBrightness': self.brightness if self.knob_brightness is None else self.knob_brightness,
                'followDisplayBrightness': self.knob_brightness is None,
                'knobBrightnessLevel': 0 if self.sleeping else self.brightness if self.knob_brightness is None else self.knob_brightness,
                'stateVerification': 'simulation',
                'writeCount': self.write_count, 'writes': list(self.writes),
                'frames': {key: base64.b64encode(value).decode('ascii') for key, value in self.frames.items()}}

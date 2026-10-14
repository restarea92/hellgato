"""Explicit in-memory transport for integration/contribution tests; never auto-selected."""
import base64
from collections import deque
import time
from ..protocol import ProtocolError, require

DEVICE_ID = 'fake:buttons-dial'


def descriptor():
    return {'id': DEVICE_ID, 'adapterId': 'hellgato.fake-sidecar', 'modelId': 'buttons-dial',
            'firmware': 'simulation', 'capabilities': {'keys': 2, 'dials': 1, 'touch': None,
            'displays': [{'id': f'key:{index}', 'width': 4, 'height': 4, 'format': 'rgb888', 'partial': True} for index in range(2)],
            'dialPress': 'native', 'dialRelease': 'native',
            'features': {**{name: {'status': 'emulated', 'limitations': ['In-memory simulation only']} for name in ('brightness', 'clear', 'fill', 'sleep', 'wake')}, 'simulation': {'status': 'native', 'limitations': ['No physical hardware evidence']}}}}


class FakeBackend:
    def __init__(self, write_delay_ms=0, input_count=0):
        self.device = None
        self.write_delay_ms = write_delay_ms
        self.input_count = input_count
        self.writes = deque(maxlen=64)
        self.write_count = 0
        self.frames = {}
        self.brightness = 100
        self.sleeping = False

    def discover(self):
        return [descriptor()]

    def open(self, device_id, emit):
        require(device_id == DEVICE_ID, 'Unknown device', 'ERR_DEVICE_NOT_FOUND')
        require(self.device is None, 'Device already open', 'ERR_DEVICE_BUSY')
        self.device = device_id
        for index in range(self.input_count):
            emit({'type': 'key', 'index': 0, 'state': 'down' if index % 2 == 0 else 'up'})
        return descriptor()

    def close(self):
        self.device = None

    def poll(self):
        return None

    def _before_write(self, check_deadline):
        require(self.device is not None, 'Device is closed', 'ERR_DEVICE_CLOSED')
        time.sleep(self.write_delay_ms / 1000)
        check_deadline()

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
        require(control['control'] in ('brightness', 'clear', 'fill', 'sleep', 'wake'), 'Control not implemented by fake backend', 'ERR_UNSUPPORTED_OPERATION', -32601)
        self._before_write(check_deadline)
        kind = control['control']
        if kind == 'brightness':
            self.brightness = control['value']
        if kind in ('sleep', 'wake'):
            self.sleeping = kind == 'sleep'
        if kind in ('clear', 'fill'):
            color = bytes(control.get('color', [0, 0, 0]))
            surfaces = [control['surfaceId']] if 'surfaceId' in control else ['key:0', 'key:1']
            for surface in surfaces:
                self.frames[surface] = color * 16
        self.write_count += 1
        self.writes.append({'type': 'control', 'control': kind, 'sequence': control.get('sequence')})
        return {'status': 'applied', 'verification': 'simulation'}

    def get_state(self):
        return {'connected': self.device is not None, 'brightness': self.brightness, 'sleeping': self.sleeping,
                'writeCount': self.write_count, 'writes': list(self.writes),
                'frames': {key: base64.b64encode(value).decode('ascii') for key, value in self.frames.items()}}

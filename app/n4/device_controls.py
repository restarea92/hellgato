"""Persistent N4 control preferences with checked hardware writes."""

import json
from pathlib import Path
import re
import sys
import tempfile

SERIAL_PATTERN = re.compile(r'HGMOCK\d{6}\Z')


def percent(value):
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError('Brightness must be an integer between 0 and 100')
    return value


def checked_led_brightness(device, level):
    """The SDK wrapper discards TransportResult, so check its native call directly."""
    percent(level)
    transport = device.transport
    module = sys.modules.get(type(transport).__module__)
    library = getattr(module, '_transport_lib', None)
    handle = getattr(transport, '_handle', None)
    if library is None or not handle:
        raise OSError('The N4 transport is not available for a checked LED write')
    result = int(library.transport_set_led_brightness(handle, level))
    if result != 0:
        raise OSError(f'Knob brightness write failed: 0x{result:08X}')


class DeviceControls:
    def __init__(self, device, state_root, serial, *, firmware=None, led_writer=None):
        if not isinstance(serial, str) or not SERIAL_PATTERN.fullmatch(serial):
            raise ValueError('Invalid virtual device serial')
        self.device = device
        self.serial = serial
        self.firmware = firmware
        self.path = Path(state_root) / f'device-controls-{serial}.json'
        self.led_writer = led_writer or (lambda level: checked_led_brightness(self.device, level))
        self.display_level = 100
        self.normal_display_level = 100
        self.sleeping = False
        self._last_led = None
        self.knob_brightness = None
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('version') != 1
                    or data.get('serial') != serial):
                raise ValueError('Unsupported device-control settings; the file was preserved')
            self.knob_brightness = data.get('knobBrightness')
            if self.knob_brightness is not None:
                percent(self.knob_brightness)

    def state(self):
        connected = self.device is not None
        return {'serial': self.serial, 'model': 'Mirabox N4 Pro', 'firmware': self.firmware,
                'available': connected, 'connected': connected,
                'knobBrightness': (self.normal_display_level if self.knob_brightness is None
                                   else self.knob_brightness),
                'followDisplayBrightness': self.knob_brightness is None,
                'capabilities': {'knobBrightness': connected}}

    def _save(self, brightness):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': 1, 'serial': self.serial, 'knobBrightness': brightness}, stream)
                stream.write('\n')
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _fade_factor(self):
        if self.sleeping:
            return 0
        if self.normal_display_level == 0:
            return 1
        return min(1, self.display_level / self.normal_display_level)

    def led_level(self, display_level, normal_level=None, sleeping=False):
        percent(display_level)
        if normal_level is not None:
            percent(normal_level)
            self.normal_display_level = normal_level
        self.display_level = display_level
        self.sleeping = bool(sleeping)
        if self.knob_brightness is None:
            return 0 if self.sleeping else display_level
        return round(self.knob_brightness * self._fade_factor())

    def apply_led_level(self, display_level, normal_level=None, guard=lambda: None, *, sleeping=False):
        level = self.led_level(display_level, normal_level, sleeping)
        if self.device is None:
            raise OSError('The N4 device is disconnected')
        if level != self._last_led:
            guard()
            self.led_writer(level)
            self._last_led = level
        return level

    def set_knob_brightness(self, value, guard=lambda: None, *, persist=True):
        value = percent(value)
        if type(persist) is not bool:
            raise ValueError('persist must be a boolean')
        if self.device is None:
            raise OSError('The N4 device is disconnected')
        applied = round(value * self._fade_factor())
        guard()
        if applied != self._last_led:
            self.led_writer(applied)
            self._last_led = applied
        if persist:
            guard()
            self._save(value)
        self.knob_brightness = value
        return {'ok': True, 'applied': True, 'state': self.state()}

    def set_follow_display_brightness(self, enabled, guard=lambda: None):
        if type(enabled) is not bool:
            raise ValueError('followDisplayBrightness must be a boolean')
        if self.device is None:
            raise OSError('The N4 device is disconnected')
        chosen = None if enabled else (self.normal_display_level if self.knob_brightness is None
                                       else self.knob_brightness)
        applied = ((0 if self.sleeping else self.display_level) if enabled
                   else round(chosen * self._fade_factor()))
        guard()
        self.led_writer(applied)
        guard()
        self._save(chosen)
        self.knob_brightness = chosen
        self._last_led = applied
        return {'ok': True, 'applied': True, 'state': self.state()}

    def apply_control(self, payload, guard=lambda: None):
        if payload.get('serial') != self.serial:
            raise ValueError('The requested device is not connected to this bridge')
        action = payload.get('action')
        if action == 'knobBrightness':
            return self.set_knob_brightness(payload.get('value'), guard,
                                            persist=payload.get('persist', True))
        if action == 'followDisplayBrightness':
            return self.set_follow_display_brightness(payload.get('value'), guard)
        raise ValueError('Unsupported N4 control action')

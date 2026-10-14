"""Explicit prepared Mirabox SDK binding; no USB/native reimplementation."""
import base64
from dataclasses import asdict
import hashlib
from io import BytesIO
import sys
import tempfile
import threading
import time

from ..models.n4_pro.model import descriptor, input_event, scalar, touch_event
from ..models.n4_pro.touch import TouchGesture
from ..protocol import MAX_PIXELS, ProtocolError, require
from .shutdown import close_device, install_interruptible_heartbeat


class MiraboxBackend:
    def __init__(self, sdk_path):
        # Only the explicitly supplied prepared runtime is imported. No repository fallback.
        sys.path.insert(0, str(sdk_path))
        from StreamDock.DeviceManager import DeviceManager
        from StreamDock.InputTypes import ButtonKey
        self.manager, self.button_key = DeviceManager, ButtonKey
        self.candidates = {}
        self.device = None
        self.lock = threading.RLock()
        self.temporary = None
        self.display = None
        self.touch = TouchGesture()
        self.last_poll = 0
        self.brightness = 100
        self.sleeping = False

    def discover(self):
        require(self.device is None, 'Discovery while device is open is unsupported', 'ERR_DEVICE_BUSY')
        devices = [device for device in self.manager().enumerate() if type(device).__name__ == 'StreamDockN4Pro']
        candidates = {}
        for device in devices:
            serial = device.serial_number or ''
            path = device.path if isinstance(device.path, bytes) else str(device.path).encode('utf-8')
            identity = serial.encode('utf-8') if serial else path
            device_id = 'mirabox:n4:' + hashlib.sha256(identity).hexdigest()[:24]
            require(device_id not in candidates, 'Ambiguous physical identity', 'ERR_DEVICE_ID')
            candidates[device_id] = device
        self.candidates = candidates
        return [descriptor(device_id, str(device.firmware_version or '')) for device_id, device in candidates.items()]

    def open(self, device_id, emit):
        require(self.device is None, 'Device already open', 'ERR_DEVICE_BUSY')
        require(device_id in self.candidates, 'Discover and explicitly select a device first', 'ERR_DEVICE_NOT_FOUND')
        device = self.candidates[device_id]
        self.device = device
        self.touch = TouchGesture()
        original_decode = device.decode_input_event

        def decode(code, state):
            event = original_decode(code, state)
            if 1 <= code <= 15:
                event.key = self.button_key(code)
            return event

        def forward(source, event):
            if source is not self.device:
                return
            event = asdict(event)
            output = input_event(event)
            kind, key = scalar(event.get('event_type')), scalar(event.get('key'))
            if kind == 'swipe':
                output = touch_event(self.touch.finish(time.monotonic(), scalar(event.get('direction'))))
            elif kind == 'button' and key in (11, 12, 13, 14) and event.get('state') == 0:
                output = touch_event(self.touch.finish(time.monotonic()))
            if output:
                emit(output)

        def touch_point(source, event):
            if source is self.device:
                self.touch.point(event.x, event.y, time.monotonic())

        device.decode_input_event = decode
        device.set_key_callback(forward)
        device.set_touch_bar_callback(touch_point)
        install_interruptible_heartbeat(device, report=lambda event: None)
        # Serialize the SDK heartbeat with frame/control writes without replacing native USB code.
        heartbeat = device.transport.heartbeat
        def serial_heartbeat():
            with self.lock:
                return heartbeat()
        device.transport.heartbeat = serial_heartbeat
        try:
            with self.lock:
                device.set_device()
                require(device.open(), 'Native SDK could not open the selected device', 'ERR_DEVICE_OPEN')
                from ..models.n4_pro.display import DisplayWriter
                self.temporary = tempfile.TemporaryDirectory(prefix='hellgato-mirabox-')
                self.display = DisplayWriter(device, self.temporary.name)
        except Exception:
            self.close()
            raise
        return descriptor(device_id, str(device.firmware_version or ''))

    def close(self):
        device = self.device
        if device is not None:
            # Stop producers before acquiring the writer lock; shutdown joins heartbeat.
            device.run_heartbeat_thread = False
            stop = getattr(device, '_hellgato_heartbeat_stop', None)
            if stop is not None:
                stop.set()
            if device.heartbeat_thread and device.heartbeat_thread.is_alive():
                device.heartbeat_thread.join(timeout=2)
            with self.lock:
                require(close_device(device, notify=False, report=lambda event: None),
                        'Native handle has not finished closing', 'ERR_DEVICE_CLOSE')
                self.device = None
        self.display = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def poll(self):
        if self.device is None or time.monotonic() - self.last_poll < 0.5:
            return None
        self.last_poll = time.monotonic()
        present = self.device.transport.enumerate_devices(vendor_id=self.device.vendor_id, product_id=self.device.product_id)
        if not any(item['path'] == self.device.path for item in present):
            self.close()
            return 'disconnected'
        return None

    def set_output(self, frame, check_deadline):
        from PIL import Image
        require(self.device is not None, 'Device disconnected', 'ERR_DEVICE_CLOSED')
        region = frame.get('region', {'x': 0, 'y': 0, 'width': frame['width'], 'height': frame['height']})
        decoded = base64.b64decode(frame['data'], validate=True)
        with Image.open(BytesIO(decoded)) as source:
            require(source.format == 'JPEG' and source.size == (region['width'], region['height']) and source.width * source.height <= MAX_PIXELS,
                    'Decoded image differs from declared dimensions', 'ERR_CONTRACT')
            image = source.convert('RGB')
        with self.lock:
            check_deadline()
            if frame['surfaceId'].startswith('key:'):
                self.display.key(int(frame['surfaceId'][4:]), image, check_deadline)
            elif frame['surfaceId'] == 'touch':
                self.display.touch(image, tuple(region[key] for key in ('x', 'y', 'width', 'height')), check_deadline)
            elif frame['surfaceId'] == 'lcd':
                self.display.lcd(image, check_deadline)
        # Vendor calls return no end-to-end display acknowledgement.
        return {'status': 'submitted', 'verification': 'unconfirmed'}

    def set_control(self, control, check_deadline):
        require(self.device is not None, 'Device disconnected', 'ERR_DEVICE_CLOSED')
        require(control['control'] in ('brightness', 'clear', 'fill', 'sleep', 'wake'),
                'Control is not implemented by the candidate worker', 'ERR_UNSUPPORTED_OPERATION', -32601)
        with self.lock:
            check_deadline()
            kind = control['control']
            if kind == 'brightness':
                self.device.set_brightness(control['value'])
                self.brightness = control['value']
            elif kind == 'sleep':
                self.device.transport.sleep()
                self.sleeping = True
            elif kind == 'wake':
                self.device.wakeScreen()
                self.sleeping = False
            elif kind in ('clear', 'fill'):
                from PIL import Image
                color = tuple(control.get('color', [0, 0, 0]))
                surface = control.get('surfaceId', 'lcd')
                if surface.startswith('key:'):
                    self.display.key(int(surface[4:]), Image.new('RGB', (120, 120), color), check_deadline)
                elif surface == 'touch':
                    self.display.touch(Image.new('RGB', (800, 100), color), (0, 0, 800, 100), check_deadline)
                else:
                    self.display.lcd(Image.new('RGB', (800, 480), color), check_deadline)
        return {'status': 'submitted', 'verification': 'unconfirmed'}

    def get_state(self):
        return {'connected': self.device is not None, 'brightness': self.brightness, 'sleeping': self.sleeping,
                'supportStatus': 'experimental', 'hardwareParityVerified': False}

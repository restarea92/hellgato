"""Explicit prepared Mirabox SDK binding; no USB/native reimplementation."""
import base64
from dataclasses import asdict
import hashlib
import json
from io import BytesIO
import sys
import tempfile
import threading
import time

from ..models.n4_pro.model import descriptor, input_event, scalar, touch_event
from ..models.n4_pro.firmware import firmware_policy, require_candidate_firmware
from ..models.n4_pro.touch import TouchGesture
from ..protocol import MAX_PIXELS, require
from .shutdown import close_device, install_interruptible_heartbeat
from .ownership import RuntimeOwnership


def device_identity(device):
    """Byte-identical legacy registry fingerprint, without importing product code."""
    serial = device.serial_number
    require(serial is None or isinstance(serial, str), 'Invalid physical identity', 'ERR_DEVICE_ID')
    source = 'serial' if serial else 'usb-path'
    if serial:
        physical = serial
    else:
        path = device.path
        require(isinstance(path, (str, bytes)) and path, 'Missing physical identity', 'ERR_DEVICE_ID')
        physical = (path.encode('utf-8') if isinstance(path, str) else path).hex()
    fingerprint = json.dumps(['StreamDockN4Pro', device.vendor_id, device.product_id, source, physical],
                             ensure_ascii=True, separators=(',', ':'))
    return 'n4-' + hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()


def led_supported(device):
    module = sys.modules.get(type(device.transport).__module__)
    return callable(getattr(getattr(module, '_transport_lib', None), 'transport_set_led_brightness', None))


def checked_led_brightness(device, level):
    # Preserve the existing checked native call: the vendor wrapper discards its result.
    require(type(level) is int and 0 <= level <= 100, 'Invalid knob brightness', 'ERR_CONTRACT')
    transport = device.transport
    library = getattr(sys.modules.get(type(transport).__module__), '_transport_lib', None)
    handle = getattr(transport, '_handle', None)
    require(led_supported(device) and handle, 'Checked knob brightness unavailable', 'ERR_UNSUPPORTED_OPERATION')
    result = int(library.transport_set_led_brightness(handle, level))
    if result != 0:
        raise OSError(f'Knob brightness write failed: 0x{result:08X}')


class MiraboxBackend:
    def __init__(self, sdk_path, screensaver_path=None, logo_path=None, strip_mode='panels', lcd_mode='normal'):
        if lcd_mode not in ('normal', 'stream-screensaver'):
            raise ValueError('Unknown N4 LCD output mode')
        self.ownership = RuntimeOwnership()
        self.screensaver_path = screensaver_path
        self.logo_path = logo_path
        self.strip_mode = strip_mode
        self.lcd_mode = lcd_mode
        # Only the explicitly supplied prepared runtime is imported. No repository fallback.
        # Frozen worker dependency inventory is immutable, including dynamic SDK imports.
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(sdk_path))
        from StreamDock.DeviceManager import DeviceManager
        from StreamDock.InputTypes import ButtonKey
        self.manager, self.button_key = DeviceManager, ButtonKey
        self.candidates = {}
        self.device = None
        self.probe_device = None
        self.lock = threading.RLock()
        self.temporary = None
        self.display = None
        self.touch = TouchGesture()
        self.last_poll = 0
        self.activity_lock = threading.Lock()
        self.activity = None

    def _close_probe(self):
        device = getattr(self, 'probe_device', None)
        if device is None:
            return
        transport = device.transport
        handle = getattr(transport, '_handle', None)
        if handle:
            library = getattr(sys.modules.get(type(transport).__module__), '_transport_lib', None)
            destroy = getattr(library, 'transport_destroy', None)
            require(callable(destroy), 'Checked firmware-probe cleanup unavailable', 'ERR_DEVICE_CLOSE')
            # The vendor close wrapper suppresses native failures. Retain both the
            # handle and lifetime mutex until checked cleanup actually succeeds.
            require(int(destroy(handle)) == 0, 'Firmware-probe handle did not close', 'ERR_DEVICE_CLOSE')
        transport._handle = None
        transport._is_open = False
        self.probe_device = None

    def _probe_firmware(self, device, check_deadline):
        """Open only the transport, read firmware, and close before host readiness.

        Pinned SDK discovery leaves firmware_version blank on Windows. This does
        not run init/open on the device, readers, heartbeat, or display controls.
        """
        firmware_policy()  # Missing/corrupt policy must fail before native access.
        require(getattr(self, 'probe_device', None) is None, 'Previous firmware probe still owns USB', 'ERR_DEVICE_CLOSE')
        transport = device.transport
        require(not getattr(transport, '_handle', None) and not getattr(transport, '_is_open', False),
                'Firmware probe requires an unopened transport', 'ERR_DEVICE_BUSY')
        path = device.path
        require(isinstance(path, (bytes, str)) and bool(path), 'Missing physical path', 'ERR_DEVICE_ID')
        check_deadline()
        self.probe_device = device
        try:
            require(transport.open(path.encode('utf-8') if isinstance(path, str) else path),
                    'Could not open device for read-only firmware probe', 'ERR_DEVICE_OPEN')
            check_deadline()
            firmware = require_candidate_firmware(transport.get_firmware_version())
            check_deadline()
            device.firmware_version = firmware
            return firmware
        finally:
            self._close_probe()

    def discover(self, check_deadline=lambda: None):
        require(self.device is None and getattr(self, 'probe_device', None) is None,
                'Discovery while device is open is unsupported', 'ERR_DEVICE_BUSY')
        self.candidates = {}
        check_deadline()
        devices = [device for device in self.manager().enumerate() if type(device).__name__ == 'StreamDockN4Pro']
        require(len(devices) <= 64, 'Too many physical candidates', 'ERR_DEVICE_ID')
        candidates = {}
        for device in devices:
            device_id = device_identity(device)
            require(device_id not in candidates, 'Ambiguous physical identity', 'ERR_DEVICE_ID')
            candidates[device_id] = device
        results = []
        with self.lock:
            for device_id, device in candidates.items():
                firmware = self._probe_firmware(device, check_deadline)
                results.append(descriptor(device_id, firmware, knob_supported=led_supported(device),
                                          logo_supported=self.logo_path is not None))
        self.candidates = candidates
        return results

    def open(self, device_id, emit, check_deadline=lambda: None):
        require(self.device is None, 'Device already open', 'ERR_DEVICE_BUSY')
        require(device_id in self.candidates, 'Discover and explicitly select a device first', 'ERR_DEVICE_NOT_FOUND')
        device = self.candidates[device_id]
        check_deadline()
        require(getattr(self, 'probe_device', None) is None, 'Firmware probe still owns USB', 'ERR_DEVICE_CLOSE')
        discovered_firmware = require_candidate_firmware(device.firmware_version)
        self.device = device
        self.activity = None
        self.touch = TouchGesture()
        original_decode = device.decode_input_event
        self.original_decode = original_decode
        heartbeat = device.transport.heartbeat
        self.original_heartbeat = heartbeat
        input_token = object()
        self.input_token = None

        def decode(code, state):
            event = original_decode(code, state)
            if 1 <= code <= 15:
                event.key = self.button_key(code)
            return event

        def forward(source, event):
            if source is not self.device or self.input_token is not input_token:
                return
            event = asdict(event)
            output = input_event(event)
            kind, key = scalar(event.get('event_type')), scalar(event.get('key'))
            now = time.monotonic()
            # Preserve activity independently of emitted commands. Touch-panel
            # releases may finish without a fresh coordinate, and a dial press
            # is a pulse whose synthetic release must still arm manual Sleep.
            if (kind in ('knob_rotate', 'knob_press', 'swipe')
                    or kind == 'button' and type(key) is int and 1 <= key <= 14):
                release = kind == 'knob_press' or kind == 'button' and event.get('state') == 0
                with self.activity_lock:
                    self.activity = (now, release)
            if kind == 'swipe':
                output = touch_event(self.touch.finish(now, scalar(event.get('direction'))))
            elif kind == 'button' and key in (11, 12, 13, 14) and event.get('state') == 0:
                output = touch_event(self.touch.finish(now))
            if output:
                emit(output)

        def touch_point(source, event):
            if (source is self.device and self.input_token is input_token
                    and type(event.x) is int and type(event.y) is int
                    and 0 <= event.x < 800 and 0 <= event.y < 112):
                now = time.monotonic()
                self.touch.point(event.x, event.y, now)
                with self.activity_lock:
                    self.activity = (now, False)

        # Serialize heartbeat with frame/control writes. Keep setup inside the
        # cleanup boundary, including failures before the native open itself.
        def serial_heartbeat():
            with self.lock:
                return heartbeat()
        try:
            device.decode_input_event = decode
            device.set_key_callback(forward)
            device.set_touch_bar_callback(touch_point)
            install_interruptible_heartbeat(device, report=lambda event: None)
            device.transport.heartbeat = serial_heartbeat
            with self.lock:
                check_deadline()
                device.set_device()
                check_deadline()
                require(device.open(), 'Native SDK could not open the selected device', 'ERR_DEVICE_OPEN')
                check_deadline()
                firmware = require_candidate_firmware(device.transport.get_firmware_version())
                require(firmware == discovered_firmware, 'Firmware changed since discovery', 'ERR_FIRMWARE_CHANGED')
                device.firmware_version = firmware
                check_deadline()
                from ..models.n4_pro.state import DisplayState
                self.temporary = tempfile.TemporaryDirectory(prefix='hellgato-mirabox-')
                self.display = DisplayState(device, self.temporary.name, screensaver_path=self.screensaver_path, logo_path=self.logo_path, strip_mode=self.strip_mode,
                    led_writer=(lambda level: checked_led_brightness(device, level)) if led_supported(device) else None,
                    check=check_deadline, lcd_mode=self.lcd_mode)
                # set_device before open sets decoder features, but the pinned
                # SDK drops report-size configuration until a handle exists.
                check_deadline()
                device.set_device()
                self.display.initialize(check_deadline)
                check_deadline()
                self.input_token = input_token
        except Exception:
            self.close()
            raise
        return descriptor(device_id, str(device.firmware_version or ''), knob_supported=led_supported(device), logo_supported=self.logo_path is not None)

    def close(self):
        self.input_token = None
        self._close_probe()
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
                device.decode_input_event = self.original_decode
                device.transport.heartbeat = self.original_heartbeat
                self.device = None
        self.display = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def release_ownership(self):
        # Never relinquish the shared lock while a native handle may still exist.
        require(self.device is None and getattr(self, 'probe_device', None) is None,
                'Native device is still owned', 'ERR_DEVICE_CLOSE')
        self.ownership.close()

    def poll(self, check_deadline=lambda: None):
        if self.device is None:
            return None
        if time.monotonic() - self.last_poll >= 0.5:
            check_deadline()
            self.last_poll = time.monotonic()
            present = self.device.transport.enumerate_devices(vendor_id=self.device.vendor_id, product_id=self.device.product_id)
            if not any(item['path'] == self.device.path for item in present):
                # Do not advance fades/animation on a device already known to
                # be absent: the native library may fail outside Python.
                self.close()
                return 'disconnected'
        with self.lock:
            check_deadline()
            with self.activity_lock:
                activity, self.activity = self.activity, None
            if activity is not None:
                self.display.notify_input(*activity, check_deadline)
            self.display.tick(check_deadline)
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
            self.display.output(frame['surfaceId'], image,
                tuple(region[key] for key in ('x', 'y', 'width', 'height')), check_deadline)
            observations = self.display.display_observations()
        # Vendor calls return no end-to-end display acknowledgement.
        return {'status': 'submitted', 'verification': 'unconfirmed', 'displayObservations': observations}

    def set_control(self, control, check_deadline):
        require(self.device is not None, 'Device disconnected', 'ERR_DEVICE_CLOSED')
        with self.lock:
            effect = self.display.control(control, check_deadline) or {}
            state = self.display.state()
            observations = self.display.display_observations()
        return {'status': 'submitted', 'verification': 'unconfirmed', **effect,
                'displayObservations': observations,
                **{key: state[key] for key in ('idle', 'sleeping', 'screensaverActive', 'logoActive')}}

    def get_display_observations(self):
        with self.lock:
            return self.display.display_observations() if self.display else None

    def get_state(self):
        return {'connected': self.device is not None, **(self.display.state() if self.display else {}),
                'displayObservations': self.get_display_observations(),
                'supportStatus': 'experimental', 'hardwareParityVerified': False}

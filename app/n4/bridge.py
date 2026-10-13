"""Connect N4 Pro input and display output to the CORA server."""

import argparse
from dataclasses import asdict
from enum import Enum
import gc
import json
import os
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time

from mapping import input_commands, enum_value
from display import DisplayMirror, DISPLAY_EVENTS
from touch import TouchGesture
from settings import load_settings, settings_path
from screensaver import ScreensaverSource
from shutdown import close_device, install_interruptible_heartbeat
from device_controls import DeviceControls
from device_control_server import DeviceControlServer
from device_registry import DeviceIdentityConflict, DeviceRegistry


root = Path(__file__).resolve().parents[2]
sdk_source = root / 'work/references/mirabox/Python-SDK/src'
state_root = Path(os.environ.get('HELLGATO_STATE_DIR', root / 'work'))


class DisconnectedDisplay:
    """Let DisplayMirror retain incoming state while no USB output is available."""

    @property
    def transport(self):
        return self

    def discard(self, *args, **kwargs):
        pass

    set_key_image = discard
    set_seondscreen_image = discard
    set_background_frame_stream = discard
    set_brightness = discard
    set_led_brightness = discard
    refresh = discard
    wakeScreen = discard
    clearAllIcon = discard
    sleep = discard


class UsbAttachment:
    def __init__(self, display, controls, discover, configure, *, initialize=False,
                 report=print, close=None, registry=None):
        self.display = display
        self.controls = controls
        self.discover = discover
        self.configure = configure
        self.initialize = initialize
        self.report = report
        self.close_device = close or close_device
        self.device = None
        self.offline = DisconnectedDisplay()
        self.display.device = self.offline
        self.missing = 0
        self.serial = None
        self.path = None
        self.retired = None
        self.waiting_reason = None
        self.registry = registry
        self.record = None

    def identity_detail(self):
        if self.record is None:
            return {}
        return {'deviceId': self.record['id'], 'virtualSerial': self.record['virtualSerial']}

    def _waiting(self, reason, **detail):
        if reason != self.waiting_reason:
            self.report({'event': 'deviceWaiting', 'detail': {'reason': reason, **detail}})
            self.waiting_reason = reason

    def _restore(self):
        display = self.display
        idle, level, fade = display.idle, display.screen_level, display.fade
        display.device = self.device
        self.controls.device = self.device
        self.controls.firmware = self.device.firmware_version or None
        self.controls._last_led = None
        display.screen_level = -1
        display.sleeping = False
        display.native_active = False
        display.frame_initialized = False
        display.fade = None
        display._set_screen_level(0)
        if idle:
            display._set_screen_level(level)
            display._enter_idle(reload=False)
        else:
            display._restore()
            display._set_screen_level(level)
        display._set_led_level(level)
        display.fade = fade

    def poll(self):
        try:
            if self.retired is not None:
                if not self.close_device(self.retired, notify=False, report=self.report):
                    self._waiting('previous-handle-closing')
                    return
                self.retired = None
            if self.device is not None:
                device = self.device
                present = device.transport.enumerate_devices(vendor_id=device.vendor_id,
                                                            product_id=device.product_id)
                if any(item['path'] == device.path for item in present):
                    if self.missing:
                        self._restore()
                    self.missing = 0
                    return
                self.missing += 1
                self.display.device = self.offline
                self.controls.device = None
                if self.missing < 2:
                    return
                self.detach(notify=False)
                self.report({'event': 'deviceDisconnected', 'detail': {
                    'reason': 'usb-absent', 'serial': self.serial, 'observations': self.missing,
                    **self.identity_detail()}})
                return
            devices = self.discover()
            if len(devices) != 1:
                self._waiting('usb-absent' if not devices else 'multiple-devices', count=len(devices))
                return
            device = devices[0]
            serial = device.serial_number or ''
            try:
                record = self.registry.prepare(device) if self.registry is not None else None
            except DeviceIdentityConflict as error:
                self._waiting('registered-device-mismatch', serial=serial,
                              expectedDeviceId=error.expected['id'],
                              virtualSerial=error.expected['virtualSerial'])
                return
            if self.path is not None and (serial != self.serial if self.serial else device.path != self.path):
                self._waiting('different-device', serial=serial)
                return
            self.device = device
            try:
                self.configure(device)
                device.set_device()
                if not device.open():
                    raise OSError('N4 Pro is present but not ready for USB open')
                if self.initialize:
                    device.init()
                self._restore()
                if self.registry is not None:
                    self.record = self.registry.remember(record)
            except Exception:
                self.detach(notify=False)
                raise
            reconnected = self.path is not None
            self.serial, self.path = serial, device.path
            self.missing = 0
            self.waiting_reason = None
            if reconnected:
                self.report({'event': 'deviceReconnected', 'detail': {
                    'reason': 'usb-returned', 'serial': serial, **self.identity_detail()}})
            self.report({'event': 'deviceReady', 'detail': {'serial': serial, **self.identity_detail()}})
        except (OSError, ValueError, RuntimeError) as error:
            self.report({'event': 'deviceError', 'detail': {'operation': 'usb-presence', 'error': str(error)}})

    def detach(self, *, notify):
        device, self.device = self.device, None
        self.display.device = self.offline
        self.controls.device = None
        if device is not None and not self.close_device(device, notify=notify, report=self.report):
            self.retired = device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('control', 'target'), default='target')
    parser.add_argument('--identity', type=int, help='Initial persistent child identity')
    parser.add_argument('--dock-identity', type=int, help='Initial persistent dock identity')
    parser.add_argument('--init', action='store_true', help='Wake and initialize the device before connecting')
    parser.add_argument('--raw', action='store_true', help='Record raw HID headers and decoded input')
    parser.add_argument('--strip-mode', choices=('panels', 'frame'))
    args = parser.parse_args()
    if args.identity is not None and not 1 <= args.identity <= 999999:
        parser.error('identity must be between 1 and 999999')
    if args.dock_identity is not None and not 1 <= args.dock_identity <= 999999:
        parser.error('dock-identity must be between 1 and 999999')
    if not sdk_source.is_dir():
        parser.error('Mirabox SDK is missing from work/references/mirabox')
    try:
        settings = load_settings(identity=args.identity, dock_identity=args.dock_identity)
        registry = DeviceRegistry(settings_path().parent, settings['identity'], settings['dockIdentity'])
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.identity = settings['identity']
    args.dock_identity = settings['dockIdentity']
    args.strip_mode = args.strip_mode or settings['stripMode']

    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, 'Local\\Hellgato.N4Pro.Bridge')
        if not mutex or ctypes.get_last_error() == 183:
            raise SystemExit('Hellgato N4 Pro bridge is already running')

    sys.path.insert(0, str(sdk_source))
    from StreamDock.DeviceManager import DeviceManager
    from StreamDock.InputTypes import ButtonKey

    def shutdown_report(event):
        if event.get('event') == 'deviceDisconnected':
            release_pressed()
        print(json.dumps(event), flush=True)

    process = None
    controls = None
    control_server = None
    attachment = None
    host_connected = threading.Event()
    write_lock = threading.Lock()
    pressed = set()
    run_id = str(time.time_ns())
    trace_path = state_root / 'traces' / f'{args.profile}-{run_id}.jsonl'
    images_dir = state_root / 'images' / f'HGMOCK{args.identity:06d}-{run_id}'
    def native_screensaver_mode(enabled):
        with write_lock:
            if process is not None and process.poll() is None:
                process.stdin.write(f"screensaver {'native' if enabled else 'stream'}\n")
                process.stdin.flush()

    display = DisplayMirror(DisconnectedDisplay(), images_dir, args.strip_mode, columns=5 if args.profile == 'target' else 4,
                            screensaver_source=ScreensaverSource(f'HGMOCK{args.identity:06d}'),
                            on_native_source=native_screensaver_mode)
    activity = queue.SimpleQueue()
    input_path = state_root / 'traces' / f'n4-bridge-{run_id}.jsonl'
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_lock = threading.Lock()
    touch = TouchGesture()

    def record_input(kind, detail):
        def encode(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, bytes):
                return value.hex()
            raise TypeError(type(value).__name__)
        line = json.dumps({'time': time.time(), 'event': kind, 'detail': detail}, default=encode)
        with input_lock:
            with input_path.open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
            if kind != 'raw':
                print(line, flush=True)

    def release_pressed():
        with write_lock:
            commands = [f'{kind} {index} up' for kind, index in sorted(pressed)]
            pressed.clear()
            if commands and process is not None and process.poll() is None:
                try:
                    process.stdin.write('\n'.join(commands) + '\n')
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass

    def forward(source, event):
        if attachment is None or attachment.device is not source:
            return
        if args.raw:
            record_input('deviceInput', asdict(event))
        commands = input_commands(asdict(event))
        kind = enum_value(event.event_type)
        if kind in ('knob_rotate', 'knob_press', 'swipe') or (kind == 'button' and enum_value(event.key) in range(1, 15)):
            release = ((kind == 'button' and event.state == 0) or kind == 'knob_press')
            activity.put((time.monotonic(), release))
        if kind == 'swipe':
            command = touch.finish(time.monotonic(), enum_value(event.direction))
            commands = [command] if command is not None else []
        elif kind == 'button' and enum_value(event.key) in (11, 12, 13, 14) and event.state == 0:
            command = touch.finish(time.monotonic())
            commands = [command] if command is not None else []
        if not commands:
            return
        if args.profile == 'control' and commands[0].startswith(('key 8 ', 'key 9 ')):
            return
        with write_lock:
            if process is None or process.poll() is not None:
                return
            try:
                process.stdin.write('\n'.join(commands) + '\n')
                process.stdin.flush()
                for command in commands:
                    parts = command.split()
                    if len(parts) == 3 and parts[0] in ('key', 'press'):
                        key = (parts[0], int(parts[1]))
                        if parts[2] == 'down':
                            pressed.add(key)
                        else:
                            pressed.discard(key)
            except (BrokenPipeError, OSError):
                pass

    def touch_point(source, event):
        if attachment is None or attachment.device is not source:
            return
        touch.point(event.x, event.y, time.monotonic())
        if 0 <= event.x < 800 and 0 <= event.y < 112:
            activity.put((time.monotonic(), False))
        if args.raw:
            record_input('touchPoint', {'x': event.x, 'y': event.y})

    def configure_device(device):
        nonlocal touch
        touch = TouchGesture()
        install_interruptible_heartbeat(device, report=shutdown_report)
        sdk_decode = device.decode_input_event

        def decode_input(hardware_code, state):
            event = sdk_decode(hardware_code, state)
            # N4 input codes use physical numbering, unlike its image-write IDs.
            if 1 <= hardware_code <= 15:
                event.key = ButtonKey(hardware_code)
            return event

        device.decode_input_event = decode_input
        device.set_key_callback(forward)
        device.set_touch_bar_callback(touch_point)
        if args.raw:
            device.set_raw_read_callback(lambda _, data: record_input(
                'raw', {'header': bytes(data[:24]).hex(), 'bytes': len(data)}))

    def write_led(level, normal, sleeping):
        if controls.device is None:
            return controls.led_level(level, normal, sleeping)
        return controls.apply_led_level(level, normal, sleeping=sleeping)

    try:
        controls = DeviceControls(None, state_root, f'HGMOCK{args.identity:06d}')
        display.led_writer = write_led
        control_server = DeviceControlServer(state_root, controls.serial,
            lambda: {**controls.state(), 'hostConnected': host_connected.is_set()}, controls.apply_control)
        attachment = UsbAttachment(display, controls,
            lambda: [device for device in DeviceManager().enumerate()
                     if type(device).__name__ == 'StreamDockN4Pro'],
            configure_device, initialize=args.init, report=shutdown_report, registry=registry)
        print(json.dumps({'event': 'bridgeReady'}), flush=True)
        ready_file = os.environ.get('HELLGATO_READY_FILE')
        if ready_file:
            while not Path(ready_file).exists():
                if Path(os.environ['HELLGATO_STOP_FILE']).exists():
                    return
                time.sleep(.2)
        process = subprocess.Popen(
            [os.environ.get('HELLGATO_NODE', 'node'), 'app/cora/server.mjs', args.profile,
             str(args.dock_identity or args.identity), str(args.identity)],
            cwd=root, stdin=subprocess.PIPE, text=True,
            env={**os.environ, 'HELLGATO_RUN_ID': run_id, 'HELLGATO_STATE_DIR': str(state_root)},
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )
        print(f'N4 Pro {args.profile} bridge started for HGMOCK{args.identity:06d}. '
              'Pair 127.0.0.1:5343 if needed. Press Ctrl+C to stop.', flush=True)
        trace = None
        pending = ''
        next_usb_check = 0
        while process.poll() is None:
            if os.environ.get('HELLGATO_STOP_FILE') and Path(os.environ['HELLGATO_STOP_FILE']).exists():
                break
            control_server.drain()
            if time.monotonic() >= next_usb_check:
                attachment.poll()
                next_usb_check = time.monotonic() + .5
            latest_activity = None
            while not activity.empty():
                latest_activity = activity.get_nowait()
            if latest_activity is not None:
                display.notify_input(latest_activity[0], key_release=latest_activity[1])
            if trace is None and trace_path.exists():
                trace = trace_path.open(encoding='utf-8')
            if trace is not None:
                pending += trace.read()
                updates = []
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    if line:
                        event = json.loads(line)
                        if event.get('event') == 'child.clientConnected':
                            host_connected.set()
                        elif event.get('event') == 'child.clientDisconnected':
                            host_connected.clear()
                        if event.get('event') in DISPLAY_EVENTS:
                            updates.append(event)
                if updates:
                    try:
                        display.apply_batch(updates)
                    except OSError as error:
                        print(f'Display update failed: {error}', flush=True)
            try:
                display.tick()
            except (OSError, ValueError) as error:
                print(f'Screensaver update failed: {error}', flush=True)
            time.sleep(0.002)
        if process.poll() not in (None, 0):
            raise RuntimeError(f'CORA bridge exited with code {process.returncode}')
    except KeyboardInterrupt:
        pass
    finally:
        host_connected.clear()
        if control_server is not None:
            control_server.close()
        if controls is not None:
            controls.device = None
        if 'trace' in locals() and trace is not None:
            trace.close()
        if attachment is not None:
            attachment.detach(notify=attachment.missing == 0)
        display.device = None
        del display
        if process is not None and process.poll() is None:
            with write_lock:
                try:
                    process.stdin.write('quit\n')
                    process.stdin.flush()
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        gc.collect()


if __name__ == '__main__':
    main()

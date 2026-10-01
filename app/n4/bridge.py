"""Connect N4 Pro input and display output to the CORA server."""

import argparse
from dataclasses import asdict
from enum import Enum
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from mapping import input_commands, enum_value
from display import DisplayMirror
from touch import TouchGesture
from settings import load_settings


root = Path(__file__).resolve().parents[2]
sdk_source = root / 'work/references/mirabox/Python-SDK/src'
state_root = Path(os.environ.get('HELLGATO_STATE_DIR', root / 'work'))


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

    devices = [device for device in DeviceManager().enumerate()
               if type(device).__name__ == 'StreamDockN4Pro']
    if len(devices) != 1:
        print(f'Expected one N4 Pro, found {len(devices)}', flush=True)
        raise SystemExit(10 if not devices else 11)

    device = devices[0]
    sdk_decode = device.decode_input_event

    def decode_input(hardware_code, state):
        event = sdk_decode(hardware_code, state)
        # N4 input codes use physical numbering, unlike its image-write IDs.
        if 1 <= hardware_code <= 15:
            event.key = ButtonKey(hardware_code)
        return event

    device.decode_input_event = decode_input
    process = None
    removed = False
    write_lock = threading.Lock()
    run_id = str(time.time_ns())
    trace_path = state_root / 'traces' / f'{args.profile}-{run_id}.jsonl'
    images_dir = state_root / 'images' / f'HGMOCK{args.identity:06d}-{run_id}'
    display = DisplayMirror(device, images_dir, args.strip_mode)
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

    def forward(_, event):
        if args.raw:
            record_input('deviceInput', asdict(event))
        commands = input_commands(asdict(event))
        kind = enum_value(event.event_type)
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
            except (BrokenPipeError, OSError):
                pass

    def touch_point(_, event):
        touch.point(event.x, event.y, time.monotonic())
        if args.raw:
            record_input('touchPoint', {'x': event.x, 'y': event.y})

    try:
        device.set_key_callback(forward)
        device.set_touch_bar_callback(touch_point)
        if args.raw:
            device.set_raw_read_callback(lambda _, data: record_input(
                'raw', {'header': bytes(data[:24]).hex(), 'bytes': len(data)}))
        device.set_device()
        if not device.open():
            print('N4 Pro is present but not ready; retrying USB open', flush=True)
            raise SystemExit(12)
        if args.init:
            device.init()
        ready_file = os.environ.get('HELLGATO_READY_FILE')
        if ready_file:
            print(json.dumps({'event': 'deviceReady'}), flush=True)
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
        next_usb_check = time.monotonic() + 2
        while process.poll() is None:
            if os.environ.get('HELLGATO_STOP_FILE') and Path(os.environ['HELLGATO_STOP_FILE']).exists():
                break
            if time.monotonic() >= next_usb_check:
                present = device.transport.enumerate_devices(vendor_id=0x5548, product_id=0x1021)
                if not any(item['path'] == device.path for item in present):
                    removed = True
                    print('N4 Pro disconnected', flush=True)
                    break
                next_usb_check = time.monotonic() + 2
            if trace is None and trace_path.exists():
                trace = trace_path.open(encoding='utf-8')
            if trace is not None:
                pending += trace.read()
                updates = []
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    if line:
                        event = json.loads(line)
                        if event.get('event') in ('keyImage', 'touchImage', 'brightness'):
                            updates.append(event)
                if updates:
                    try:
                        display.apply_batch(updates)
                    except OSError as error:
                        print(f'Display update failed: {error}', flush=True)
            time.sleep(0.002)
        if process.poll() not in (None, 0):
            raise RuntimeError(f'CORA bridge exited with code {process.returncode}')
    except KeyboardInterrupt:
        pass
    finally:
        if 'trace' in locals() and trace is not None:
            trace.close()
        device.close(notify=not removed)
        display.device = None
        del display
        if process is not None and process.poll() is None:
            with write_lock:
                try:
                    process.stdin.write('quit\n')
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        devices.clear()
        del device
        gc.collect()


if __name__ == '__main__':
    main()

"""Prepare ten-key geometry before Stream Deck restores its first CORA device."""

import argparse
import csv
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/n4'))
from compatibility import Executable, INCOMPATIBLE, MESSAGE, IncompatibleError, require, resolve, validate_controllers
from feedback import install_feedback, resolve_feedback

STATE = Path(os.environ.get('HELLGATO_STATE_DIR', ROOT / 'work'))
RESULTS = STATE / 'results'
SPEC = RESULTS / 'compatibility-spec.json'


class StartupInfo(c.Structure):
    _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR),
                ('title', w.LPWSTR), ('x', w.DWORD), ('y', w.DWORD),
                ('width', w.DWORD), ('height', w.DWORD), ('columns', w.DWORD),
                ('rows', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                ('show', w.WORD), ('reservedSize', w.WORD), ('reservedData', c.c_void_p),
                ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]


class ProcessInfo(c.Structure):
    _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE),
                ('pid', w.DWORD), ('tid', w.DWORD)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=Path(os.environ['ProgramFiles']) / 'Elgato/StreamDeck/StreamDeck.exe')
    parser.add_argument('--restart', action='store_true', help='Back up profiles and restart the supported running app')
    parser.add_argument('--stop-only', action='store_true', help='Back up and stop the app for profile import')
    parser.add_argument('--check-only', action='store_true', help='Inspect the installed file without starting or stopping processes')
    args = parser.parse_args()
    if c.sizeof(c.c_void_p) != 8:
        parser.error('64-bit Python is required')
    executable = args.exe.read_bytes()
    spec = resolve(executable)
    spec['feedback'] = resolve_feedback(Executable(executable))
    RESULTS.mkdir(parents=True, exist_ok=True)
    SPEC.write_text(json.dumps(spec, indent=2), encoding='utf-8')
    if args.check_only:
        print(json.dumps({'event': 'staticCompatibilityPassed', 'sha256': spec['sha256']}), flush=True)
        return
    (RESULTS / 'startup-ready.json').unlink(missing_ok=True)
    k = c.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateProcessW': ([w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD,
                            c.c_void_p, w.LPCWSTR, c.POINTER(StartupInfo), c.POINTER(ProcessInfo)], w.BOOL),
        'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'CloseHandle': ([w.HANDLE], w.BOOL),
        'QueryFullProcessImageNameW': ([w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL),
        'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
        'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
        'WaitForDebugEvent': ([c.c_void_p, w.DWORD], w.BOOL),
        'ContinueDebugEvent': ([w.DWORD, w.DWORD, w.DWORD], w.BOOL),
        'DebugSetProcessKillOnExit': ([w.BOOL], w.BOOL),
        'DebugActiveProcessStop': ([w.DWORD], w.BOOL),
        'ReadProcessMemory': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
        'WriteProcessMemory': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
        'VirtualProtectEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
        'VirtualAllocEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, w.DWORD], c.c_void_p),
        'FlushInstructionCache': ([w.HANDLE, c.c_void_p, c.c_size_t], w.BOOL),
        'GetThreadContext': ([w.HANDLE, c.c_void_p], w.BOOL),
        'SetThreadContext': ([w.HANDLE, c.c_void_p], w.BOOL),
        'SuspendThread': ([w.HANDLE], w.DWORD),
        'ResumeThread': ([w.HANDLE], w.DWORD),
    }
    for name, (parameters, result) in signatures.items():
        fn = getattr(k, name)
        fn.argtypes, fn.restype = parameters, result

    def check(value):
        if not value:
            raise c.WinError(c.get_last_error())
        return value

    rows = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq StreamDeck.exe', '/FO', 'CSV', '/NH'],
                          capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    pids = [int(row[1]) for row in csv.reader(io.StringIO(rows))
            if len(row) > 1 and row[0].lower() == 'streamdeck.exe']
    if pids and not args.restart:
        parser.error('Stream Deck is already running; use --restart to preserve profiles and rebuild device geometry')
    if pids:
        profiles = Path(os.environ['APPDATA']) / 'Elgato/StreamDeck/ProfilesV3'
        backup = STATE / 'profile-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        if profiles.exists():
            shutil.copytree(profiles, backup)
            print(f'Profiles backed up: {backup}', flush=True)
        for pid in pids:
            handle = check(k.OpenProcess(0x101001, False, pid))
            try:
                name = c.create_unicode_buffer(32768)
                size = w.DWORD(len(name))
                check(k.QueryFullProcessImageNameW(handle, 0, name, c.byref(size)))
                if Path(name.value).resolve() != args.exe.resolve():
                    raise RuntimeError('Another Stream Deck installation is running; no termination')
                check(k.TerminateProcess(handle, 0))
                if k.WaitForSingleObject(handle, 5000) != 0:
                    raise RuntimeError('Stream Deck did not exit')
            finally:
                k.CloseHandle(handle)

    if args.stop_only:
        return

    require(hashlib.sha256(args.exe.read_bytes()).hexdigest() == spec['sha256'],
            'Executable changed after compatibility inspection')
    startup = StartupInfo()
    startup.cb = c.sizeof(startup)
    startup.flags, startup.show = 1, 0
    process = ProcessInfo()
    command = c.create_unicode_buffer(f'"{args.exe}" --runinbk')
    check(k.CreateProcessW(str(args.exe), command, None, None, False, 2, None,
                           str(args.exe.parent), c.byref(startup), c.byref(process)))
    check(k.DebugSetProcessKillOnExit(False))
    attached, prepared = True, False
    paused_thread = None
    target = None
    original, patched = bytes.fromhex(spec['original']), bytes.fromhex(spec['patched'])
    startup_original = bytes.fromhex(spec['startupBreakpoint']['original'])

    def read(address, size):
        buffer = c.create_string_buffer(size)
        done = c.c_size_t()
        check(k.ReadProcessMemory(process.process, address, buffer, size, c.byref(done)))
        if done.value != size:
            raise RuntimeError('Partial process read')
        return buffer.raw

    def write_code(address, payload):
        previous = w.DWORD()
        check(k.VirtualProtectEx(process.process, address, len(payload), 0x40, c.byref(previous)))
        try:
            done = c.c_size_t()
            check(k.WriteProcessMemory(process.process, address, c.create_string_buffer(payload), len(payload), c.byref(done)))
            if done.value != len(payload) or read(address, len(payload)) != payload:
                raise RuntimeError('Instruction write failed')
            check(k.FlushInstructionCache(process.process, address, len(payload)))
        finally:
            unused = w.DWORD()
            check(k.VirtualProtectEx(process.process, address, len(payload), previous.value, c.byref(unused)))

    def run_patch(script, *arguments):
        prefix = [sys.executable, '--run-script'] if getattr(sys, 'frozen', False) else [sys.executable]
        result = subprocess.run([*prefix, str(ROOT / 'scripts' / script), *map(str, arguments)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            if result.returncode == INCOMPATIBLE:
                raise IncompatibleError(result.stderr or result.stdout)
            raise RuntimeError(result.stderr or result.stdout)

    try:
        deadline = time.monotonic() + 40
        event = (c.c_uint64 * 22)()
        while time.monotonic() < deadline:
            if not k.WaitForDebugEvent(c.byref(event), 500):
                if c.get_last_error() == 121:
                    continue
                raise c.WinError(c.get_last_error())
            data = bytes(event)
            kind, pid, tid = struct.unpack_from('<III', data)
            status = 0x10002
            if kind == 3:
                file_handle, _, _, base = struct.unpack_from('<QQQQ', data, 16)
                if file_handle:
                    k.CloseHandle(file_handle)
                constructor = base + spec['rva']
                target = base + spec['startupBreakpoint']['rva']
                # Validate every affected function before the first write. Relative
                # references survive ASLR, so these guards compare byte-for-byte.
                for guard in spec['guards']:
                    expected = bytes.fromhex(guard['original'])
                    require(read(base + guard['rva'], len(expected)) == expected,
                            'Loaded code differs from the inspected executable')
                require(read(constructor, len(original)) == original, 'Constructor changed')
                require(read(target, len(startup_original)) == startup_original, 'Initialization boundary changed')
                write_code(constructor, patched)
                feedback = install_feedback(k, process.process, base, spec['feedback'], read, write_code)
                # The static model table is ready, but no caller has consumed its geometry.
                write_code(target, b'\xcc' + startup_original[1:])
            elif kind == 6:
                file_handle = struct.unpack_from('<Q', data, 16)[0]
                if file_handle:
                    k.CloseHandle(file_handle)
            elif kind == 1:
                exception = struct.unpack_from('<I', data, 16)[0]
                address = struct.unpack_from('<Q', data, 32)[0]
                if exception == 0x80000003 and address == target:
                    paused_thread = check(k.OpenThread(0x1a, False, tid))
                    # AMD64 CONTEXT requires 16-byte alignment; CONTROL includes RIP.
                    storage = c.create_string_buffer(1248)
                    context = (c.addressof(storage) + 15) & ~15
                    c.c_uint32.from_address(context + 48).value = 0x100001
                    check(k.GetThreadContext(paused_thread, context))
                    if c.c_uint64.from_address(context + 248).value != target + 1:
                        raise RuntimeError('Unexpected breakpoint instruction pointer')
                    write_code(target, startup_original)
                    c.c_uint64.from_address(context + 248).value = target
                    check(k.SetThreadContext(paused_thread, context))
                    if k.SuspendThread(paused_thread) == 0xffffffff:
                        raise c.WinError(c.get_last_error())
                    check(k.ContinueDebugEvent(pid, tid, status))
                    # Detach while holding only the model initialization thread; the heap allocator
                    # used by the grid patch must be able to run its remote thread.
                    check(k.DebugActiveProcessStop(process.pid))
                    attached = False
                    inspection = RESULTS / 'startup-geometry.json'
                    run_patch('patch-device-geometry.py', '--pid', process.pid, '--spec', SPEC,
                              '--mode', 'apply', '--objects', '--controller-width', 5, '--output', inspection)
                    run_patch('patch-device-geometry.py', '--pid', process.pid, '--spec', SPEC,
                              '--objects', '--output', inspection)
                    run_patch('patch-background-grid.py', '--inspection', inspection, '--spec', SPEC,
                              '--output', RESULTS / 'startup-background.json')
                    run_patch('patch-device-geometry.py', '--pid', process.pid, '--spec', SPEC,
                              '--objects', '--final-layout', '--output', inspection)
                    validate_controllers(json.loads(inspection.read_text())['plusModelControllers'], final=True)
                    prepared = True
                    if k.ResumeThread(paused_thread) == 0xffffffff:
                        prepared = False
                        raise c.WinError(c.get_last_error())
                    report = {'pid': process.pid, 'constructorPatchedBeforeFirstDevice': True,
                              'modelPreparedBeforeFirstLookup': True,
                              'keypad': [5, 2], 'backgroundTiles': 10, 'profilesPreserved': True,
                              'compatibility': 'structural', 'sha256': spec['sha256']}
                    report['feedback'] = feedback
                    (RESULTS / 'startup-ready.json').write_text(json.dumps(report, indent=2))
                    print(json.dumps(report), flush=True)
                    break
                if exception != 0x80000003:
                    status = 0x80010001
            elif kind == 5:
                raise RuntimeError('Stream Deck exited before device preparation')
            check(k.ContinueDebugEvent(pid, tid, status))
        if not prepared:
            raise RuntimeError('Model initialization was not reached within 40 seconds')
    finally:
        if not prepared:
            k.TerminateProcess(process.process, 1)
        if attached:
            k.DebugActiveProcessStop(process.pid)
        if paused_thread:
            k.CloseHandle(paused_thread)
        k.CloseHandle(process.thread)
        k.CloseHandle(process.process)


if __name__ == '__main__':
    try:
        main()
    except IncompatibleError as error:
        print(json.dumps({'event': 'incompatible', 'message': MESSAGE, 'detail': str(error)}, ensure_ascii=False), flush=True)
        raise SystemExit(INCOMPATIBLE)

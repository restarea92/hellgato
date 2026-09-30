"""Expand the verified live Plus background crop grid to ten entries."""

import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import INCOMPATIBLE, IncompatibleError, validate_controllers

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inspection', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--spec', type=Path, required=True)
args = parser.parse_args()
report = json.loads(args.inspection.read_text())
spec = json.loads(args.spec.read_text())
if hashlib.sha256(Path(report['executable']).read_bytes()).hexdigest() != spec['sha256']:
    print('Executable changed after inspection', file=sys.stderr)
    raise SystemExit(INCOMPATIBLE)
if spec.get('compatibility') == 'structural':
    try:
        validate_controllers(report['plusModelControllers'])
    except IncompatibleError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(INCOMPATIBLE)
keys = [d for d in report['plusModelControllers'] if d['controller'] == 1]
if len(keys) != 1 or (keys[0]['width'], keys[0]['height']) != (5, 2):
    raise SystemExit('Expected one five-column keypad')
descriptor = int(keys[0]['address'], 16)
k = c.WinDLL('kernel32', use_last_error=True)
p = c.WinDLL('psapi', use_last_error=True)
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
k.WriteProcessMemory.argtypes = k.ReadProcessMemory.argtypes
k.VirtualAllocEx.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, w.DWORD]
k.VirtualAllocEx.restype = c.c_void_p
k.VirtualFreeEx.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD]
k.FlushInstructionCache.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t]
k.CreateRemoteThread.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p, w.DWORD, c.c_void_p]
k.CreateRemoteThread.restype = w.HANDLE
k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k.CloseHandle.argtypes = [w.HANDLE]
k.GetModuleHandleExW.argtypes = [w.DWORD, c.c_void_p, c.POINTER(w.HMODULE)]
k.GetModuleFileNameW.argtypes = [w.HMODULE, w.LPWSTR, w.DWORD]
p.EnumProcessModules.argtypes = [w.HANDLE, c.POINTER(w.HMODULE), w.DWORD, c.POINTER(w.DWORD)]
p.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]

def check(ok):
    if not ok:
        raise c.WinError(c.get_last_error())

handle = k.OpenProcess(0x43a, False, report['pid'])
check(handle)

def read(at, size):
    buf = c.create_string_buffer(size)
    done = c.c_size_t()
    check(k.ReadProcessMemory(handle, at, buf, size, c.byref(done)))
    if done.value != size:
        raise RuntimeError('Partial read')
    return buf.raw

def write(at, data):
    buf = c.create_string_buffer(data)
    done = c.c_size_t()
    check(k.WriteProcessMemory(handle, at, buf, len(data), c.byref(done)))
    if done.value != len(data):
        raise RuntimeError('Partial write')

try:
    modules = (w.HMODULE * 1024)()
    needed = w.DWORD()
    check(p.EnumProcessModules(handle, modules, c.sizeof(modules), c.byref(needed)))
    paths = {}
    for module in modules[:needed.value // c.sizeof(w.HMODULE)]:
        name = c.create_unicode_buffer(32768)
        check(p.GetModuleFileNameExW(handle, module, name, len(name)))
        paths[name.value.lower()] = module
    name = c.create_unicode_buffer(32768)
    check(p.GetModuleFileNameExW(handle, modules[0], name, len(name)))
    if Path(name.value) != Path(report['executable']):
        raise RuntimeError('Process identity changed')

    def remote_function(name):
        address = c.cast(getattr(k, name), c.c_void_p).value
        module = w.HMODULE()
        check(k.GetModuleHandleExW(6, address, c.byref(module)))
        path = c.create_unicode_buffer(32768)
        check(k.GetModuleFileNameW(module, path, len(path)))
        return paths[path.value.lower()] + address - module.value

    for controller in report['plusModelControllers']:
        at = int(controller['address'], 16)
        if read(at, 52) != struct.pack('<13I', *controller['fields'][:13]):
            print('Descriptor changed after inspection', file=sys.stderr)
            raise SystemExit(INCOMPATIBLE)
    original = read(descriptor + 56, 24)
    begin, end, capacity = struct.unpack('<QQQ', original)
    coordinates = [(13 + column * 164, y) for y in (12, 172) for column in range(5)]
    payload = b''.join(struct.pack('<ii', *point) for point in coordinates)
    if end - begin == 80 and read(begin, 80) == payload:
        print('Ten-key background grid already applied')
    else:
        expected = [(x, y) for y in (12, 172) for x in (13, 232, 451, 670)]
        if end - begin != 64 or capacity != end or read(begin, 64) != b''.join(struct.pack('<ii', *v) for v in expected):
            raise RuntimeError('Unexpected original coordinate vector')
        scratch = k.VirtualAllocEx(handle, None, 4096, 0x3000, 0x40)
        check(scratch)
        output = scratch + 512
        imm = lambda value: struct.pack('<Q', value)
        # Validate the existing vector's heap before allocating its replacement.
        code = b'\x53\x48\x83\xec\x20\x48\xbb' + imm(output)
        code += b'\x48\xb8' + imm(remote_function('GetProcessHeap')) + b'\xff\xd0\x48\x89\x43\x10'
        code += b'\x48\x89\xc1\x31\xd2\x49\xb8' + imm(begin)
        code += b'\x48\xb8' + imm(remote_function('HeapValidate')) + b'\xff\xd0\x89\x43\x08\x85\xc0'
        allocation = b'\x48\x8b\x4b\x10\x31\xd2\x41\xb8\x50\x00\x00\x00'
        allocation += b'\x48\xb8' + imm(remote_function('HeapAlloc')) + b'\xff\xd0\x48\x89\x03'
        code += b'\x74' + bytes([len(allocation)]) + allocation
        code += b'\x31\xc0\x48\x83\xc4\x20\x5b\xc3'
        write(scratch, code)
        check(k.FlushInstructionCache(handle, scratch, len(code)))
        thread = k.CreateRemoteThread(handle, None, 0, scratch, None, 0, None)
        check(thread)
        if k.WaitForSingleObject(thread, 5000) != 0:
            raise RuntimeError('Allocator did not finish; scratch retained')
        k.CloseHandle(thread)
        replacement, valid = struct.unpack('<QI', read(output, 12))
        k.VirtualFreeEx(handle, scratch, 0, 0x8000)
        if not valid or not replacement:
            raise RuntimeError('Existing vector was not on the expected heap; no descriptor write')
        write(replacement, payload)
        backup = {'pid': report['pid'], 'descriptor': hex(descriptor), 'originalVector': original.hex(),
                  'replacement': hex(replacement), 'coordinates': coordinates}
        args.output.write_text(json.dumps(backup, indent=2))
        if read(descriptor + 56, 24) != original:
            raise RuntimeError('Vector changed before replacement')
        new_vector = struct.pack('<QQQ', replacement, replacement + 80, replacement + 80)
        write(descriptor + 56, new_vector)
        if read(replacement, 80) != payload or read(descriptor + 56, 24) != new_vector:
            raise RuntimeError('Coordinate write failed')
        # Retain the old 64-byte block so the saved vector remains restorable.
        print(json.dumps(backup, indent=2))
finally:
    k.CloseHandle(handle)

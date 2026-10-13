"""Temporarily change the CORA Plus constructor width in one verified process."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import INCOMPATIBLE, IncompatibleError, validate_controllers


def incompatible(detail):
    print(f'Incompatible layout: {detail}', file=sys.stderr, flush=True)
    raise SystemExit(INCOMPATIBLE)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pid', required=True, type=int)
parser.add_argument('--mode', choices=['inspect', 'apply', 'restore'], default='inspect')
parser.add_argument('--output', type=Path)
parser.add_argument('--spec', type=Path, required=True)
parser.add_argument('--objects', action='store_true', help='Read matching Plus object geometry only')
parser.add_argument('--final-layout', action='store_true', help='Require the complete ten-key layout')
parser.add_argument('--controller-width', type=int, choices=[4, 5])
args = parser.parse_args()
if args.controller_width is not None and (not args.objects or args.mode == 'inspect' or not args.output):
    parser.error('controller-width requires objects, apply/restore and output')
spec = json.loads(args.spec.read_text())
k = c.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
k.WriteProcessMemory.argtypes = k.ReadProcessMemory.argtypes
k.VirtualProtectEx.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, c.POINTER(w.DWORD)]
k.FlushInstructionCache.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t]
p = c.WinDLL('psapi', use_last_error=True)
p.EnumProcessModules.argtypes = [w.HANDLE, c.POINTER(w.HMODULE), w.DWORD, c.POINTER(w.DWORD)]

def check(ok):
    if not ok:
        raise c.WinError(c.get_last_error())

access = 0x410 if args.mode == 'inspect' else 0x438
handle = k.OpenProcess(access, False, args.pid)
if not handle:
    raise SystemExit(f'OpenProcess failed ({c.get_last_error()}); use an administrator terminal for the selected StreamDeck process.')
try:
    path = c.create_unicode_buffer(32768)
    length = w.DWORD(len(path))
    check(k.QueryFullProcessImageNameW(handle, 0, path, c.byref(length)))
    executable = Path(path.value)
    if executable.name.lower() != 'streamdeck.exe':
        raise SystemExit('Selected process is not StreamDeck.exe')
    if hashlib.sha256(executable.read_bytes()).hexdigest() != spec['sha256']:
        incompatible('Executable changed after inspection')
    modules = (w.HMODULE * 1024)()
    needed = w.DWORD()
    check(p.EnumProcessModules(handle, modules, c.sizeof(modules), c.byref(needed)))
    address = modules[0] + spec['rva']
    original = bytes.fromhex(spec['original'])
    patched = bytes.fromhex(spec['patched'])
    def read():
        buf = c.create_string_buffer(len(original))
        done = c.c_size_t()
        check(k.ReadProcessMemory(handle, address, buf, len(original), c.byref(done)))
        if done.value != len(original):
            raise RuntimeError('Partial read')
        return buf.raw
    before = read()
    if before not in (original, patched):
        incompatible('Instruction bytes differ; no write performed')
    result = {'pid': args.pid, 'executable': str(executable), 'mode': args.mode,
              'rva': hex(spec['rva']), 'before': before.hex()}
    if args.mode != 'inspect':
        desired = patched if args.mode == 'apply' else original
        if before != desired:
            old = w.DWORD()
            check(k.VirtualProtectEx(handle, address + 1, 1, 0x40, c.byref(old)))
            try:
                value = c.create_string_buffer(desired[1:2])
                done = c.c_size_t()
                check(k.WriteProcessMemory(handle, address + 1, value, 1, c.byref(done)))
                if done.value != 1:
                    raise RuntimeError('Partial write')
                check(k.FlushInstructionCache(handle, address + 1, 1))
            finally:
                unused = w.DWORD()
                check(k.VirtualProtectEx(handle, address + 1, 1, old.value, c.byref(unused)))
        if read() != desired:
            raise RuntimeError('Verification failed')
    if args.objects:
        import struct
        class Region(c.Structure):
            _fields_ = [('base', c.c_void_p), ('allocation', c.c_void_p), ('allocationProtection', w.DWORD),
                        ('partition', w.WORD), ('size', c.c_size_t), ('state', w.DWORD), ('protect', w.DWORD), ('kind', w.DWORD)]
        k.VirtualQueryEx.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(Region), c.c_size_t]
        k.VirtualQueryEx.restype = c.c_size_t
        needles = {struct.pack('<Q', modules[0] + rva): name for name, rva in
                   spec.get('vtables', {'CORA Plus': 0x1678190, 'USB Plus': 0x1676a10}).items()}
        objects = []
        model_nodes = set()
        model_marker = b'20GBD9901' + bytes(7) + struct.pack('<QQ', 9, 15)
        cursor = 0
        region = Region()
        while k.VirtualQueryEx(handle, cursor, c.byref(region), c.sizeof(region)):
            end = (region.base or 0) + region.size
            if end <= cursor: break
            if region.state == 0x1000 and region.kind == 0x20000 and region.protect in (4, 8):
                start = region.base
                while start < end:
                    size = min(1024 * 1024, end - start)
                    buf = c.create_string_buffer(size)
                    done = c.c_size_t()
                    if k.ReadProcessMemory(handle, start, buf, size, c.byref(done)):
                        data = buf.raw[:done.value]
                        pos = data.find(model_marker)
                        while pos >= 0:
                            if pos >= 0x10 and (start + pos) % 8 == 0:
                                model_nodes.add(start + pos - 0x10)
                            pos = data.find(model_marker, pos + 1)
                        for needle, name in needles.items():
                            pos = data.find(needle)
                            while pos >= 0:
                                if pos % 8 == 0 and pos + 0x9c <= len(data):
                                    width, height = struct.unpack_from('<ii', data, pos + 0x94)
                                    if width in (4, 5) and height == 2:
                                        objects.append({'class': name, 'address': hex(start + pos), 'width': width, 'height': height,
                                                        'geometryFields': list(struct.unpack_from('<8i', data, pos + 0x94)) if pos + 0xb4 <= len(data) else None})
                                pos = data.find(needle, pos + 1)
                    start += size
            cursor = end
        result['matchingObjects'] = objects
        def pointer(at):
            buf = c.c_uint64()
            done = c.c_size_t()
            check(k.ReadProcessMemory(handle, at, c.byref(buf), 8, c.byref(done)))
            return buf.value
        def memory(at, size):
            buf = c.create_string_buffer(size)
            done = c.c_size_t()
            check(k.ReadProcessMemory(handle, at, buf, size, c.byref(done)))
            if done.value != size: raise RuntimeError('Partial descriptor read')
            return buf.raw
        descriptors = []
        for node in sorted(model_nodes):
            raw = memory(node, 0x50)
            length, capacity = struct.unpack_from('<QQ', raw, 0x20)
            if length == 9 and capacity == 15 and raw[0x10:0x19] == b'20GBD9901':
                begin, end, vector_capacity = struct.unpack_from('<QQQ', raw, 0x30)
                if not begin or begin % 8 or end - begin != 160 or vector_capacity != end:
                    continue
                for at in range(begin, end, 80):
                    kind, width, height = struct.unpack_from('<iii', memory(at, 12))
                    descriptors.append({'address': hex(at), 'controller': kind, 'width': width, 'height': height, 'fields': list(struct.unpack('<20I', memory(at, 80)))})
        for descriptor in descriptors:
            at = int(descriptor['address'], 16)
            vb, ve, vc = struct.unpack('<QQQ', memory(at + 56, 24))
            descriptor['coordinateVector'] = [vb, ve, vc]
            if vb and vb <= ve <= vc and ve - vb <= 256 and (ve - vb) % 8 == 0:
                descriptor['coordinatePairs'] = list(struct.iter_unpack('<ii', memory(vb, ve - vb)))
        result['plusModelControllers'] = descriptors
        if spec.get('compatibility') == 'structural' or args.controller_width is not None:
            try:
                validate_controllers(descriptors, final=args.final_layout)
            except IncompatibleError as error:
                incompatible(str(error))
        if args.controller_width is not None:
            keys = [d for d in descriptors if d['controller'] == 1 and d['width'] in (4, 5) and d['height'] == 2]
            encoders = [d for d in descriptors if d['controller'] == 2 and d['width'] == 4 and d['height'] == 1]
            if len(descriptors) != 2 or len(keys) != 1 or len(encoders) != 1:
                raise RuntimeError('Unexpected Plus controller layout; no descriptor write')
            args.output.with_suffix('.before.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            at = int(keys[0]['address'], 16) + 4
            if memory(at, 4) != struct.pack('<i', keys[0]['width']): raise RuntimeError('Descriptor changed during inspection')
            value = c.c_int32(args.controller_width)
            done = c.c_size_t()
            check(k.WriteProcessMemory(handle, at, c.byref(value), 4, c.byref(done)))
            if done.value != 4 or memory(at, 4) != struct.pack('<i', args.controller_width):
                raise RuntimeError('Controller width write verification failed')
            result['controllerWidthAfter'] = args.controller_width



    result['after'] = read().hex()
    result['limitation'] = 'Existing objects are unchanged. Recreate the mock device to test; exit the app to discard the memory change.'
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + '\n', encoding='utf-8')
    print(output)
finally:
    k.CloseHandle(handle)

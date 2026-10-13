"""Prepare an existing Stream Deck process without owning its lifecycle."""

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes as c
from ctypes import wintypes as w
import hashlib
from pathlib import Path
import struct

from compatibility import IncompatibleError, ORIGINAL_GRID, TARGET_GRID, require, validate_controllers
from feedback import build_feedback

RETRY = 11


class TransientPreparationError(Exception):
    """The process is changing or preparation has been cancelled."""


def checkpoint(process, stop=None):
    if stop is not None and stop():
        raise TransientPreparationError('Preparation cancelled')
    if not process.alive():
        raise TransientPreparationError('Stream Deck exited during preparation')


@dataclass(frozen=True)
class Patch:
    address: int
    before: bytes
    after: bytes
    code: bool = False


def verify_feedback(process, spec):
    base, feedback = process.base, spec['feedback']
    _, template, _ = build_feedback(base, 0, feedback)
    request, original, sample = template[0]
    current = process.read(base + request, len(original))
    if current == original:
        patches, report = [], None
    else:
        require(current[:6] == b'\xff\x25\0\0\0\0', 'Unknown feedback request hook')
        offset = struct.unpack_from('<Q', sample, 6)[0]
        allocation = struct.unpack_from('<Q', current, 6)[0] - offset
        require(allocation > 0 and allocation % 4096 == 0, 'Invalid feedback allocation')
        image, patches, report = build_feedback(base, allocation, feedback)
        require(process.executable_readonly(allocation, 4096), 'Feedback code page is not immutable')
        require(process.read(allocation, 4096) == image.ljust(4096, b'\0'), 'Feedback trampoline changed')
        for rva, _, patched in patches:
            require(process.read(base + rva, len(patched)) == patched, 'Feedback hooks do not agree')
    verify_guards(process, feedback['guards'], patches)
    return patches, report


def verify_guards(process, guards, patches):
    for guard in guards:
        address = process.base + guard['rva']
        expected = bytes.fromhex(guard['original'])
        actual = bytearray(process.read(address, len(expected)))
        for rva, original, patched in patches:
            offset = process.base + rva - address
            if 0 <= offset and offset + len(original) <= len(actual):
                require(actual[offset:offset + len(original)] == patched, 'Known patch bytes changed')
                actual[offset:offset + len(original)] = original
        require(bytes(actual) == expected, 'Loaded code differs from the inspected executable')


def verify_live_code(process, spec):
    original, patched = bytes.fromhex(spec['original']), bytes.fromhex(spec['patched'])
    constructor = process.read(process.base + spec['rva'], len(original))
    require(constructor in (original, patched), 'CORA constructor changed')
    known = [(spec['rva'], original, patched)] if constructor == patched else []
    verify_guards(process, spec['guards'], known)
    return verify_feedback(process, spec)


def validate_current_controllers(descriptors):
    keys = [descriptor for descriptor in descriptors if descriptor['controller'] == 1]
    final = len(keys) == 1 and [tuple(point) for point in keys[0].get('coordinatePairs', [])] == TARGET_GRID
    return validate_controllers(descriptors, final=final)


def inspect_layout(process, spec):
    model_nodes, objects = set(), {}
    marker = b'20GBD9901' + bytes(7) + struct.pack('<QQ', 9, 15)
    needles = {struct.pack('<Q', process.base + rva): name for name, rva in spec['vtables'].items()}
    for address, data in process.heap_chunks():
        position = data.find(marker)
        while position >= 0:
            if position >= 16 and (address + position) % 8 == 0:
                model_nodes.add(address + position - 16)
            position = data.find(marker, position + 1)
        for needle, name in needles.items():
            position = data.find(needle)
            while position >= 0:
                at = address + position
                if at % 8 == 0 and position + 0xd4 <= len(data):
                    width, height = struct.unpack_from('<ii', data, position + 0x94)
                    if width in (4, 5) and height == 2:
                        objects[at] = {'class': name, 'address': hex(at), 'width': width,
                                       'height': height, 'geometryFields': list(struct.unpack_from('<8i', data, position + 0x94))}
                position = data.find(needle, position + 1)
    descriptors, anchors = [], []
    for node in sorted(model_nodes):
        raw = process.read(node, 80)
        begin, end, capacity = struct.unpack_from('<QQQ', raw, 48)
        if not begin or begin % 8 or end - begin != 160 or capacity != end:
            continue
        anchors.append((node, raw))
        for at in range(begin, end, 80):
            fields = list(struct.unpack('<20I', process.read(at, 80)))
            vb, ve, vc = struct.unpack('<QQQ', process.read(at + 56, 24))
            pairs = []
            if vb and vb <= ve <= vc and ve - vb <= 256 and (ve - vb) % 8 == 0:
                pairs = list(struct.iter_unpack('<ii', process.read(vb, ve - vb)))
            descriptors.append({'address': hex(at), 'controller': fields[0], 'width': fields[1],
                                'height': fields[2], 'fields': fields,
                                'coordinateVector': [vb, ve, vc], 'coordinatePairs': pairs})
    if len(descriptors) < 2:
        raise TransientPreparationError('Stream Deck model descriptors are still initializing')
    validate_current_controllers(descriptors)
    return {'pid': process.pid, 'executable': str(process.executable), 'base': hex(process.base),
            'plusModelControllers': descriptors, 'matchingObjects': list(objects.values()),
            '_anchors': anchors}


@contextmanager
def suspended(process, spans, stop=None):
    threads, seen = [], set()
    try:
        for _ in range(4):
            checkpoint(process, stop)
            new = set(process.thread_ids()) - seen
            if not new:
                break
            for tid in sorted(new):
                checkpoint(process, stop)
                thread = process.open_thread(tid)
                if thread is None:
                    continue
                try:
                    process.suspend_thread(thread)
                except Exception:
                    process.close_thread(thread)
                    raise
                threads.append(thread)
                seen.add(tid)
                rip = process.instruction_pointer(thread)
                if any(start <= rip < end for start, end in spans):
                    raise TransientPreparationError('A Stream Deck thread is executing a changed span')
        else:
            raise TransientPreparationError('Stream Deck threads did not become stable')
        checkpoint(process, stop)
        if not threads:
            raise TransientPreparationError('Stream Deck threads exited before commit')
        yield seen
    finally:
        failures = []
        for thread in reversed(threads):
            try:
                process.resume_thread(thread)
            except Exception as error:
                failures.append(error)
            finally:
                process.close_thread(thread)
        if failures and process.alive():
            raise RuntimeError(f'Could not resume Stream Deck threads: {failures}')


def commit_patches(process, patches, verify, stop=None):
    checkpoint(process, stop)
    if not patches:
        verify()
        checkpoint(process, stop)
        return
    applied = []
    with suspended(process, [(patch.address, patch.address + len(patch.after)) for patch in patches], stop) as tids:
        verify()
        checkpoint(process, stop)
        if set(process.thread_ids()) - tids:
            raise TransientPreparationError('A new Stream Deck thread appeared before commit')
        for patch in patches:
            if process.read(patch.address, len(patch.before)) != patch.before:
                raise TransientPreparationError('Live data changed before commit')
        try:
            for patch in patches:
                applied.append(patch)
                process.write(patch.address, patch.after, code=patch.code)
                require(process.read(patch.address, len(patch.after)) == patch.after, 'Live write verification failed')
        except Exception:
            for patch in reversed(applied):
                process.write(patch.address, patch.before, code=patch.code)
            raise


def validate_cora_object(process, at):
    require(struct.unpack('<ii', process.read(at + 0xa8, 8)) == (800, 480) and
            struct.unpack('<ii', process.read(at + 0xc4, 8)) == (120, 120) and
            struct.unpack('<ii', process.read(at + 0xcc, 8)) == (100, 100), 'CORA object fields changed')


def prepare_live(process, spec, include_feedback=True, stop=None):
    checkpoint(process, stop)
    _, existing_feedback = verify_live_code(process, spec)
    inspection = inspect_layout(process, spec)
    checkpoint(process, stop)
    for obj in inspection['matchingObjects']:
        if obj['class'] == 'CORA Plus':
            validate_cora_object(process, int(obj['address'], 16))
    patches, guards = [], list(inspection['_anchors'])
    feedback_report = existing_feedback
    if include_feedback and existing_feedback is None:
        checkpoint(process, stop)
        allocation = process.allocate(0x3000)
        image, feedback_patches, feedback_report = build_feedback(process.base, allocation, spec['feedback'])
        process.write(allocation, image)
        process.protect(allocation, 4096, 0x20)
        require(process.read(allocation, 4096) == image.ljust(4096, b'\0') and
                process.executable_readonly(allocation, 4096), 'Feedback staging failed')
        patches.extend(Patch(process.base + rva, original, patched, True)
                       for rva, original, patched in feedback_patches)
    original, patched = bytes.fromhex(spec['original']), bytes.fromhex(spec['patched'])
    constructor = process.read(process.base + spec['rva'], len(original))
    if constructor != patched:
        patches.append(Patch(process.base + spec['rva'], constructor, patched, True))
    key = validate_current_controllers(inspection['plusModelControllers'])
    descriptor = int(key['address'], 16)
    for controller in inspection['plusModelControllers']:
        at = int(controller['address'], 16)
        guards.append((at, struct.pack('<20I', *controller['fields'])))
        begin, end, _ = controller['coordinateVector']
        guards.append((begin, process.read(begin, end - begin)))
    if key['width'] != 5:
        patches.append(Patch(descriptor + 4, struct.pack('<I', key['width']), struct.pack('<I', 5)))
    if [tuple(point) for point in key['coordinatePairs']] != TARGET_GRID:
        checkpoint(process, stop)
        begin, _, _ = key['coordinateVector']
        payload = b''.join(struct.pack('<ii', *point) for point in TARGET_GRID)
        replacement = process.allocate_heap(len(payload), begin)
        process.write(replacement, payload)
        require(process.read(replacement, len(payload)) == payload, 'Coordinate staging failed')
        vector = struct.pack('<QQQ', *key['coordinateVector'])
        patches.append(Patch(descriptor + 56, vector,
                             struct.pack('<QQQ', replacement, replacement + len(payload), replacement + len(payload))))
    changed_objects = 0
    for obj in inspection['matchingObjects']:
        if obj['class'] != 'CORA Plus':
            continue
        at = int(obj['address'], 16)
        validate_cora_object(process, at)
        guards.extend([(at, struct.pack('<Q', process.base + spec['vtables']['CORA Plus'])),
                       (at + 0x94, process.read(at + 0x94, 0x40))])
        if obj['width'] != 5:
            patches.append(Patch(at + 0x94, struct.pack('<I', obj['width']), struct.pack('<I', 5)))
            changed_objects += 1

    def verify():
        checkpoint(process, stop)
        verify_live_code(process, spec)
        for address, expected in guards:
            if process.read(address, len(expected)) != expected:
                raise TransientPreparationError('Model or CORA object changed before commit')

    commit_patches(process, patches, verify, stop)
    final = inspect_layout(process, spec)
    validate_controllers(final['plusModelControllers'], final=True)
    verify_live_code(process, spec)
    require(all(obj['width'] == 5 for obj in final['matchingObjects'] if obj['class'] == 'CORA Plus'),
            'CORA objects changed during preparation')
    return {'pid': process.pid, 'mode': 'attach', 'ready': True, 'keypad': [5, 2],
            'backgroundTiles': 10, 'profilesPreserved': True, 'compatibility': 'structural',
            'sha256': spec['sha256'], 'changes': len(patches), 'coraObjectsPatched': changed_objects,
            'feedback': feedback_report, 'inspection': {key: value for key, value in final.items() if not key.startswith('_')}}


class Region(c.Structure):
    _fields_ = [('base', c.c_void_p), ('allocation', c.c_void_p), ('allocationProtection', w.DWORD),
                ('partition', w.WORD), ('size', c.c_size_t), ('state', w.DWORD),
                ('protect', w.DWORD), ('kind', w.DWORD)]


class ThreadEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD), ('pid', w.DWORD),
                ('priority', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]


class WinProcess:
    def __init__(self, pid, executable=None, writable=True):
        self.pid = pid
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.p = c.WinDLL('psapi', use_last_error=True)
        signatures = {
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'QueryFullProcessImageNameW': ([w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL),
            'ReadProcessMemory': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
            'WriteProcessMemory': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
            'VirtualQueryEx': ([w.HANDLE, c.c_void_p, c.POINTER(Region), c.c_size_t], c.c_size_t),
            'VirtualAllocEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, w.DWORD], c.c_void_p),
            'VirtualFreeEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD], w.BOOL),
            'VirtualProtectEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            'FlushInstructionCache': ([w.HANDLE, c.c_void_p, c.c_size_t], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'GetModuleHandleExW': ([w.DWORD, c.c_void_p, c.POINTER(w.HMODULE)], w.BOOL),
            'GetModuleFileNameW': ([w.HMODULE, w.LPWSTR, w.DWORD], w.DWORD),
            'CreateRemoteThread': ([w.HANDLE, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p, w.DWORD, c.c_void_p], w.HANDLE),
            'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
            'Thread32First': ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            'Thread32Next': ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'SuspendThread': ([w.HANDLE], w.DWORD),
            'ResumeThread': ([w.HANDLE], w.DWORD),
            'GetThreadContext': ([w.HANDLE, c.c_void_p], w.BOOL),
            'GetExitCodeThread': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = arguments, result
        self.p.EnumProcessModules.argtypes = [w.HANDLE, c.POINTER(w.HMODULE), w.DWORD, c.POINTER(w.DWORD)]
        self.p.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]
        self.handle = self.k.OpenProcess(0x10043a if writable else 0x100410, False, pid)
        if not self.handle and c.get_last_error() == 87:
            raise TransientPreparationError('Stream Deck PID exited before attachment')
        self.check(self.handle)
        try:
            path = c.create_unicode_buffer(32768)
            size = w.DWORD(len(path))
            self.check(self.k.QueryFullProcessImageNameW(self.handle, 0, path, c.byref(size)))
            self.executable = Path(path.value).resolve()
            require(self.executable.name.lower() == 'streamdeck.exe', 'Selected process is not Stream Deck')
            if executable is not None:
                require(self.executable == Path(executable).resolve(), 'Another Stream Deck installation is running')
            modules = (w.HMODULE * 2048)()
            needed = w.DWORD()
            self.check(self.p.EnumProcessModules(self.handle, modules, c.sizeof(modules), c.byref(needed)))
            require(0 < needed.value <= c.sizeof(modules), 'Unexpected process module list')
            self.base = modules[0]
            self.modules = {}
            for module in modules[:needed.value // c.sizeof(w.HMODULE)]:
                path = c.create_unicode_buffer(32768)
                self.check(self.p.GetModuleFileNameExW(self.handle, module, path, len(path)))
                self.modules[path.value.lower()] = module
        except Exception:
            exited = not self.alive()
            self.close()
            if exited:
                raise TransientPreparationError('Stream Deck exited during attachment')
            raise

    @staticmethod
    def check(result):
        if not result:
            raise c.WinError(c.get_last_error())
        return result

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.close()

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def alive(self):
        result = self.k.WaitForSingleObject(self.handle, 0)
        if result == 0xffffffff:
            raise c.WinError(c.get_last_error())
        return result == 258

    def verify_executable(self, spec):
        require(hashlib.sha256(self.executable.read_bytes()).hexdigest() == spec['sha256'],
                'Executable changed after compatibility inspection')
        checkpoint(self)

    def read(self, address, size):
        if not size:
            return b''
        buffer, done = c.create_string_buffer(size), c.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, address, buffer, size, c.byref(done)):
            error = c.WinError(c.get_last_error())
            checkpoint(self)
            raise error
        require(done.value == size, 'Partial process read')
        return buffer.raw

    def protect(self, address, size, protection):
        previous = w.DWORD()
        self.check(self.k.VirtualProtectEx(self.handle, address, size, protection, c.byref(previous)))
        if protection in (0x20, 0x40):
            self.check(self.k.FlushInstructionCache(self.handle, address, size))
        return previous.value

    def write(self, address, payload, code=False):
        previous = self.protect(address, len(payload), 0x40) if code else None
        try:
            done = c.c_size_t()
            if not self.k.WriteProcessMemory(self.handle, address, c.create_string_buffer(payload), len(payload), c.byref(done)):
                error = c.WinError(c.get_last_error())
                checkpoint(self)
                raise error
            require(done.value == len(payload), 'Partial process write')
            if code:
                self.check(self.k.FlushInstructionCache(self.handle, address, len(payload)))
        finally:
            if previous is not None:
                self.protect(address, len(payload), previous)

    def allocate(self, size, protection=4):
        return self.check(self.k.VirtualAllocEx(self.handle, None, size, 0x3000, protection))

    def executable_readonly(self, address, size):
        region = Region()
        self.check(self.k.VirtualQueryEx(self.handle, address, c.byref(region), c.sizeof(region)))
        return region.state == 0x1000 and region.kind == 0x20000 and region.protect == 0x20 and \
            region.base <= address and address + size <= region.base + region.size

    def heap_chunks(self):
        cursor, region = 0, Region()
        while self.k.VirtualQueryEx(self.handle, cursor, c.byref(region), c.sizeof(region)):
            end = (region.base or 0) + region.size
            if end <= cursor:
                break
            if region.state == 0x1000 and region.kind == 0x20000 and region.protect in (4, 8):
                start = region.base
                while start < end:
                    size = min(1024 * 1024, end - start)
                    try:
                        yield start, self.read(start, size)
                    except OSError:
                        pass
                    if size <= 512:
                        break
                    start += size - 512
            cursor = end

    def remote_function(self, name):
        address = c.cast(getattr(self.k, name), c.c_void_p).value
        module, path = w.HMODULE(), c.create_unicode_buffer(32768)
        self.check(self.k.GetModuleHandleExW(6, address, c.byref(module)))
        self.check(self.k.GetModuleFileNameW(module, path, len(path)))
        require(path.value.lower() in self.modules, 'Remote allocator module is missing')
        return self.modules[path.value.lower()] + address - module.value

    def allocate_heap(self, size, existing):
        # Allocate while application threads can run; a suspended thread may own the heap lock.
        scratch = self.allocate(4096, 0x40)
        output = scratch + 512
        imm = lambda value: struct.pack('<Q', value)
        code = b'\x53\x48\x83\xec\x20\x48\xbb' + imm(output)
        code += b'\x48\xb8' + imm(self.remote_function('GetProcessHeap')) + b'\xff\xd0\x48\x89\x43\x10'
        code += b'\x48\x89\xc1\x31\xd2\x49\xb8' + imm(existing)
        code += b'\x48\xb8' + imm(self.remote_function('HeapValidate')) + b'\xff\xd0\x89\x43\x08\x85\xc0'
        allocation = b'\x48\x8b\x4b\x10\x31\xd2\x41\xb8' + struct.pack('<I', size)
        allocation += b'\x48\xb8' + imm(self.remote_function('HeapAlloc')) + b'\xff\xd0\x48\x89\x03'
        code += b'\x74' + bytes([len(allocation)]) + allocation + b'\x31\xc0\x48\x83\xc4\x20\x5b\xc3'
        self.write(scratch, code, code=True)
        thread = self.check(self.k.CreateRemoteThread(self.handle, None, 0, scratch, None, 0, None))
        finished = False
        try:
            require(self.k.WaitForSingleObject(thread, 5000) == 0, 'Allocator did not finish; scratch retained')
            finished = True
            replacement, valid = struct.unpack('<QI', self.read(output, 12))
            require(valid and replacement, 'Coordinate vector is not on the expected heap')
            return replacement
        finally:
            self.k.CloseHandle(thread)
            if finished:
                self.check(self.k.VirtualFreeEx(self.handle, scratch, 0, 0x8000))

    def thread_ids(self):
        snapshot = self.k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        self.check(snapshot)
        try:
            entry, result = ThreadEntry(), []
            entry.size = c.sizeof(entry)
            ok = self.k.Thread32First(snapshot, c.byref(entry))
            while ok:
                if entry.pid == self.pid:
                    result.append(entry.tid)
                ok = self.k.Thread32Next(snapshot, c.byref(entry))
            require(c.get_last_error() == 18, 'Thread snapshot enumeration failed')
            return result
        finally:
            self.k.CloseHandle(snapshot)

    def open_thread(self, tid):
        handle = self.k.OpenThread(0x4a, False, tid)
        if not handle and c.get_last_error() == 87:
            return None
        return self.check(handle)

    def suspend_thread(self, thread):
        if self.k.SuspendThread(thread) == 0xffffffff:
            raise c.WinError(c.get_last_error())

    def instruction_pointer(self, thread):
        storage = c.create_string_buffer(1264)
        address = (c.addressof(storage) + 15) & ~15
        c.c_uint32.from_address(address + 48).value = 0x100001
        self.check(self.k.GetThreadContext(thread, address))
        return c.c_uint64.from_address(address + 248).value

    def resume_thread(self, thread):
        if self.k.ResumeThread(thread) == 0xffffffff:
            code = w.DWORD()
            if self.k.GetExitCodeThread(thread, c.byref(code)) and code.value != 259:
                return
            raise c.WinError(c.get_last_error())

    def close_thread(self, thread):
        self.k.CloseHandle(thread)

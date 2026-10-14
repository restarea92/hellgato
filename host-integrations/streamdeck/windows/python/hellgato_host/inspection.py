"""Read-only Win32 observations used by the candidate readiness inspector.

No remote allocation, DLL load, thread creation, or write API is opened here.
"""

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import Path
import struct

from .compatibility import validate_controllers


@dataclass(frozen=True)
class HostModule:
    name: str
    path: Path
    address: int
    size: int


class ModuleEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('moduleId', w.DWORD), ('processId', w.DWORD),
                ('globalUsage', w.DWORD), ('processUsage', w.DWORD), ('base', c.c_void_p),
                ('moduleSize', w.DWORD), ('module', w.HMODULE), ('name', w.WCHAR * 256),
                ('path', w.WCHAR * 260)]


def kernel_api():
    kernel = c.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'CloseHandle': ([w.HANDLE], w.BOOL),
        'QueryFullProcessImageNameW': ([w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL),
        'GetProcessTimes': ([w.HANDLE, *([c.POINTER(w.FILETIME)] * 4)], w.BOOL),
        'GetExitCodeProcess': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
        'Module32FirstW': ([w.HANDLE, c.POINTER(ModuleEntry)], w.BOOL),
        'Module32NextW': ([w.HANDLE, c.POINTER(ModuleEntry)], w.BOOL),
        'ReadProcessMemory': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def checked(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def require_same_process(kernel, process, host):
    filename = c.create_unicode_buffer(32768)
    length = w.DWORD(len(filename))
    checked(kernel.QueryFullProcessImageNameW(process, 0, filename, c.byref(length)))
    created, exited, kernel_time, user_time = (w.FILETIME() for _ in range(4))
    checked(kernel.GetProcessTimes(process, c.byref(created), c.byref(exited),
                                   c.byref(kernel_time), c.byref(user_time)))
    code = w.DWORD()
    checked(kernel.GetExitCodeProcess(process, c.byref(code)))
    timestamp = created.dwLowDateTime | created.dwHighDateTime << 32
    if (code.value != 259 or timestamp != host.created or
            Path(filename.value).resolve() != host.executable.resolve()):
        raise ValueError('Stream Deck process identity changed during inspection')


def loaded_modules(host):
    """Keep duplicate basenames: both the shim and system version.dll may load."""
    kernel = kernel_api()
    snapshot = kernel.CreateToolhelp32Snapshot(0x18, host.pid)
    if snapshot == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        entry = ModuleEntry()
        entry.size = c.sizeof(entry)
        checked(kernel.Module32FirstW(snapshot, c.byref(entry)))
        result = []
        while True:
            result.append(HostModule(entry.name, Path(entry.path), entry.base, entry.moduleSize))
            if not kernel.Module32NextW(snapshot, c.byref(entry)):
                if c.get_last_error() != 18:
                    raise c.WinError(c.get_last_error())
                return result
    finally:
        kernel.CloseHandle(snapshot)


def validate_live_target(host, status):
    """Check the actual final ten-key descriptor and coordinate vectors in memory."""
    kernel = kernel_api()
    # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ. Never write access.
    process = checked(kernel.OpenProcess(0x1010, False, host.pid))
    try:
        require_same_process(kernel, process, host)

        def read(address, length):
            if not isinstance(address, int) or address <= 0 or length not in (8, 80, 160):
                raise ValueError('Invalid target inspection memory range')
            data = c.create_string_buffer(length)
            count = c.c_size_t()
            checked(kernel.ReadProcessMemory(process, address, data, length, c.byref(count)))
            if count.value != length:
                raise ValueError('Incomplete target descriptor read')
            return data.raw

        raw = read(status.descriptor, 160)
        descriptors = []
        for offset, pair_count in ((0, 10), (80, 1)):
            fields = list(struct.unpack_from('<13I', raw, offset))
            begin, end, capacity = struct.unpack_from('<3Q', raw, offset + 56)
            if not begin or begin % 8 or end != capacity or end - begin != pair_count * 8:
                raise ValueError('Unexpected live target coordinate bounds')
            coordinates = read(begin, pair_count * 8)
            descriptors.append({'address': hex(status.descriptor + offset),
                                'controller': fields[0], 'width': fields[1], 'fields': fields,
                                'coordinateVector': [begin, end, capacity],
                                'coordinatePairs': list(struct.iter_unpack('<ii', coordinates))})
        validate_controllers(descriptors, final=True)
        if read(status.descriptor, 160) != raw:
            raise ValueError('Target descriptor changed during inspection')
        require_same_process(kernel, process, host)
    finally:
        kernel.CloseHandle(process)

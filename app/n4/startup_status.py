"""Read startup readiness published by the native loader for one host lifetime."""

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import struct


RECORD = struct.Struct('<8sIIQIIQQQ')


@dataclass(frozen=True)
class StartupStatus:
    state: int
    error: int
    image: int
    feedback: int
    descriptor: int

    @property
    def ready(self):
        return self.state == 2 and all((self.image, self.feedback, self.descriptor))


def decode_status(data, host):
    magic, schema, pid, created, state, error, image, feedback, descriptor = RECORD.unpack(data)
    if (magic, schema, pid, created) != (b'HGREADY1', 1, host.pid, host.created):
        raise ValueError('Startup readiness belongs to a different host lifetime')
    if state not in (1, 2, 3, 4):
        raise ValueError('Unknown startup readiness state')
    return StartupStatus(state, error, image, feedback, descriptor)


def read_status(host):
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.OpenFileMappingW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenFileMappingW.restype = w.HANDLE
    kernel.MapViewOfFile.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.c_size_t]
    kernel.MapViewOfFile.restype = c.c_void_p
    kernel.UnmapViewOfFile.argtypes = [c.c_void_p]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenFileMappingW(4, False, f'Local\\Hellgato.Startup.{host.pid}')
    if not handle:
        if c.get_last_error() == 2:
            return None
        raise c.WinError(c.get_last_error())
    try:
        view = kernel.MapViewOfFile(handle, 4, 0, 0, RECORD.size)
        if not view:
            raise c.WinError(c.get_last_error())
        try:
            # The writer publishes state last. Re-read if publication raced us.
            first = c.string_at(view, RECORD.size)
            second = c.string_at(view, RECORD.size)
            if first != second or struct.unpack_from('<I', second, 24)[0] == 0:
                return None
            return decode_status(second, host)
        finally:
            kernel.UnmapViewOfFile(view)
    finally:
        kernel.CloseHandle(handle)

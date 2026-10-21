"""Identify each user-launched host by process ID and creation time."""

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostProcess:
    pid: int
    created: int
    executable: Path


class HostAmbiguousError(RuntimeError):
    def __init__(self, candidates):
        super().__init__('More than one Stream Deck process is running')
        self.candidates = tuple(candidates)


class ProcessEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                ('name', w.WCHAR * 260)]


def streamdeck_processes():
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.GetProcessTimes.argtypes = [w.HANDLE, *([c.POINTER(w.FILETIME)] * 4)]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    processes = []
    try:
        entry = ProcessEntry()
        entry.size = c.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, c.byref(entry))
        while present:
            if entry.name.casefold() == 'streamdeck.exe':
                handle = kernel.OpenProcess(0x1000, False, entry.pid)
                if handle:
                    try:
                        path = c.create_unicode_buffer(32768)
                        size = w.DWORD(len(path))
                        created, exited, kernel_time, user_time = (w.FILETIME() for _ in range(4))
                        queried = (kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(size))
                                   and kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                                                              c.byref(kernel_time), c.byref(user_time)))
                        query_error = c.get_last_error()
                        code = w.DWORD()
                        if kernel.GetExitCodeProcess(handle, c.byref(code)) and code.value != 259:
                            pass  # A user-initiated exit can race the process snapshot.
                        elif not queried:
                            raise c.WinError(query_error)
                        else:
                            timestamp = created.dwLowDateTime | created.dwHighDateTime << 32
                            processes.append(HostProcess(entry.pid, timestamp, Path(path.value)))
                    finally:
                        kernel.CloseHandle(handle)
                elif c.get_last_error() != 87:
                    raise c.WinError(c.get_last_error())
            present = kernel.Process32NextW(snapshot, c.byref(entry))
        if c.get_last_error() not in (0, 18):
            raise c.WinError(c.get_last_error())
    finally:
        kernel.CloseHandle(snapshot)
    return processes


class SystemPresence:
    def __init__(self, *, process_reader=streamdeck_processes):
        self.process_reader = process_reader

    def host(self):
        candidates = self.process_reader()
        if len(candidates) > 1:
            raise HostAmbiguousError(candidates)
        return candidates[0] if candidates else None

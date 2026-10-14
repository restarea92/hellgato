"""Windows-only ownership and descendant lifetime supervision."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os

LAUNCHER_MUTEX = r'Local\Hellgato.N4Pro.Launcher'
BRIDGE_MUTEX = r'Local\Hellgato.N4Pro.Bridge'


def kernel():
    if os.name != 'nt':
        raise OSError('Physical candidate execution requires Windows x64')
    result = c.WinDLL('kernel32', use_last_error=True)
    result.CloseHandle.argtypes = [w.HANDLE]
    result.CloseHandle.restype = w.BOOL
    return result


class LauncherOwnership:
    """The same name as the installed GUI prevents either engine starting over us."""
    def __init__(self):
        self.handle = None
        self.api = kernel()

    def __enter__(self):
        self.api.CreateMutexW.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
        self.api.CreateMutexW.restype = w.HANDLE
        self.handle = self.api.CreateMutexW(None, False, LAUNCHER_MUTEX)
        error = c.get_last_error()
        if not self.handle:
            raise c.WinError(error)
        if error == 183:
            self.__exit__(None, None, None)
            raise RuntimeError('Exit Hellgato completely from its tray menu before testing')
        self.api.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        self.api.OpenMutexW.restype = w.HANDLE
        other = self.api.OpenMutexW(0x00100000, False, BRIDGE_MUTEX)
        error = c.get_last_error()
        if other:
            self.api.CloseHandle(other)
            self.__exit__(None, None, None)
            raise RuntimeError('An existing N4 bridge is running; stop it before testing')
        if error not in (0, 2):
            self.__exit__(None, None, None)
            raise c.WinError(error)
        return self

    def __exit__(self, *_):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class BasicLimit(c.Structure):
    _fields_ = [('PerProcessUserTimeLimit', c.c_int64), ('PerJobUserTimeLimit', c.c_int64),
                ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class ExtendedLimit(c.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IoCounters),
                ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]


class ProcessTree:
    """Assign the waiting Node process before its explicit start command is sent."""
    def __init__(self):
        self.api = kernel()
        self.handle = None

    def __enter__(self):
        self.api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.get_last_error()
            self.__exit__(None, None, None)
            raise c.WinError(error)
        return self

    def assign(self, process):
        if not self.handle or not self.api.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
            raise c.WinError(c.get_last_error())

    def __exit__(self, *_):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

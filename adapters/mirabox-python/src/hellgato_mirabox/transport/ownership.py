"""Share the legacy Windows engine's lifetime lock before any SDK/USB access."""
import ctypes
import sys
from ..protocol import require

MUTEX_NAME = r'Local\Hellgato.N4Pro.Bridge'


class RuntimeOwnership:
    def __init__(self, *, platform=None, kernel=None, last_error=None):
        self.kernel = None
        self.handle = None
        if (platform or sys.platform) != 'win32':
            return
        if kernel is None:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle.restype = ctypes.c_int
        last_error = last_error or ctypes.get_last_error
        handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
        error = last_error()
        if handle and error == 183:
            kernel.CloseHandle(handle)
            handle = None
        require(handle, 'Another N4 engine is running or its ownership lock is unavailable', 'ERR_DEVICE_BUSY')
        self.kernel, self.handle = kernel, handle

    def close(self):
        if self.handle is not None:
            require(self.kernel.CloseHandle(self.handle), 'Could not release runtime ownership', 'ERR_DEVICE_CLOSE')
            self.handle = None

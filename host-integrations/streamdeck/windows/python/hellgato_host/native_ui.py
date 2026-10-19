"""Load the optional UI extension into an existing Stream Deck process."""

import ctypes as c
from ctypes import wintypes as w
import json
import hashlib
import os
from pathlib import Path
import struct
from urllib.parse import urlsplit


class NativeUiError(RuntimeError):
    pass


def validate_config(config):
    if not isinstance(config, dict):
        raise NativeUiError('Native UI configuration must be an object')
    if 'autoReconnect' in config and type(config['autoReconnect']) is not bool:
        raise NativeUiError('Automatic reconnect must be a boolean')
    endpoint = urlsplit(config.get('endpoint', ''))
    if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.port != 5345 or
            endpoint.path or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
        raise NativeUiError('Native UI requires the local Hellgato control endpoint')
    token = config.get('token')
    if not isinstance(token, str) or len(token) < 32 or any(character.isspace() for character in token):
        raise NativeUiError('Native UI requires a control authentication token')
    serial = config.get('serial')
    if (not isinstance(serial, str) or len(serial) != 12 or not serial.startswith('HGMOCK') or
            not serial[6:].isascii() or not serial[6:].isdigit()):
        raise NativeUiError('Native UI requires one virtual Hellgato serial')
    return config


def pe_machine(path):
    content = Path(path).read_bytes()
    if content[:2] != b'MZ' or len(content) < 64:
        raise NativeUiError('Native UI DLL is not a PE image')
    offset = struct.unpack_from('<I', content, 60)[0]
    if offset > len(content) - 6 or content[offset:offset + 4] != b'PE\0\0':
        raise NativeUiError('Native UI DLL has no valid PE header')
    return struct.unpack_from('<H', content, offset + 4)[0]


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
        'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
        'Module32FirstW': ([w.HANDLE, c.POINTER(ModuleEntry)], w.BOOL),
        'Module32NextW': ([w.HANDLE, c.POINTER(ModuleEntry)], w.BOOL),
        'VirtualAllocEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, w.DWORD], c.c_void_p),
        'VirtualFreeEx': ([w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD], w.BOOL),
        'WriteProcessMemory': ([w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
        'CreateRemoteThread': ([w.HANDLE, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.HANDLE),
        'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
        'GetExitCodeThread': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        'GetModuleHandleW': ([w.LPCWSTR], w.HMODULE),
        'GetProcAddress': ([w.HMODULE, c.c_char_p], c.c_void_p),
        'GetModuleHandleExW': ([w.DWORD, c.c_void_p, c.POINTER(w.HMODULE)], w.BOOL),
        'GetModuleFileNameW': ([w.HMODULE, w.LPWSTR, w.DWORD], w.DWORD),
        'LoadLibraryExW': ([w.LPCWSTR, w.HANDLE, w.DWORD], w.HMODULE),
        'FreeLibrary': ([w.HMODULE], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def checked(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def modules(kernel, pid):
    snapshot = kernel.CreateToolhelp32Snapshot(0x18, pid)
    if snapshot == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    result = {}
    try:
        entry = ModuleEntry()
        entry.size = c.sizeof(entry)
        if not kernel.Module32FirstW(snapshot, c.byref(entry)):
            raise c.WinError(c.get_last_error())
        while True:
            result[entry.name.lower()] = (entry.base, Path(entry.path))
            if not kernel.Module32NextW(snapshot, c.byref(entry)):
                break
    finally:
        kernel.CloseHandle(snapshot)
    return result


def remote_system_export(kernel, target_modules, module, function):
    address = checked(kernel.GetProcAddress(checked(kernel.GetModuleHandleW(module)), function.encode('ascii')))
    owner = w.HMODULE()
    checked(kernel.GetModuleHandleExW(6, address, c.byref(owner)))
    filename = c.create_unicode_buffer(32768)
    checked(kernel.GetModuleFileNameW(owner, filename, len(filename)))
    name = Path(filename.value).name.lower()
    if name not in target_modules:
        raise NativeUiError(f'No matching system module in Stream Deck: {name}')
    return target_modules[name][0] + address - owner.value


def remote_call(kernel, process, address, payload, timeout_ms=10000):
    allocation = checked(kernel.VirtualAllocEx(process, None, len(payload), 0x3000, 0x04))
    thread = None
    completed = False
    try:
        written = c.c_size_t()
        checked(kernel.WriteProcessMemory(process, allocation, c.create_string_buffer(payload), len(payload), c.byref(written)))
        if written.value != len(payload):
            raise NativeUiError('Partial DLL configuration write')
        thread = checked(kernel.CreateRemoteThread(process, None, 0, address, allocation, 0, None))
        if kernel.WaitForSingleObject(thread, timeout_ms) != 0:
            # The running thread may still use this allocation after a timeout.
            raise NativeUiError('Native UI initialization is still running; Stream Deck was left untouched')
        completed = True
        result = w.DWORD()
        checked(kernel.GetExitCodeThread(thread, c.byref(result)))
        return result.value
    finally:
        if thread:
            kernel.CloseHandle(thread)
        if completed or thread is None:
            kernel.VirtualFreeEx(process, allocation, 0, 0x8000)


def load_native_ui(pid, dll_path, config_path, expected_exe=None):
    if os.name != 'nt' or c.sizeof(c.c_void_p) != 8:
        raise NativeUiError('Native UI loading requires 64-bit Windows Python')
    dll_path, config_path = Path(dll_path).resolve(), Path(config_path).resolve()
    validate_config(json.loads(config_path.read_text(encoding='utf-8-sig')))
    if pe_machine(dll_path) != 0x8664:
        raise NativeUiError('Native UI DLL must target AMD64')
    expected_exe = Path(expected_exe or Path(os.environ['ProgramFiles']) / 'Elgato/StreamDeck/StreamDeck.exe').resolve()
    kernel = kernel_api()
    process = checked(kernel.OpenProcess(0x043A, False, pid))
    try:
        filename = c.create_unicode_buffer(32768)
        length = w.DWORD(len(filename))
        checked(kernel.QueryFullProcessImageNameW(process, 0, filename, c.byref(length)))
        if Path(filename.value).resolve() != expected_exe:
            raise NativeUiError('Target is not the expected running Stream Deck installation')
        loaded = modules(kernel, pid)
        if not {'qt6core.dll', 'qt6widgets.dll', 'qt6network.dll'} <= loaded.keys():
            raise NativeUiError('Stream Deck has not loaded the required Qt public APIs')
        if dll_path.name.lower() not in loaded:
            address = remote_system_export(kernel, loaded, 'kernel32.dll', 'LoadLibraryW')
            remote_call(kernel, process, address, (str(dll_path) + '\0').encode('utf-16-le'))
            loaded = modules(kernel, pid)
        target = loaded.get(dll_path.name.lower())
        if not target or (target[1].resolve() != dll_path and
                          hashlib.sha256(target[1].read_bytes()).digest() != hashlib.sha256(dll_path.read_bytes()).digest()):
            raise NativeUiError('Native UI DLL did not load, or another DLL has the same name')
        local = checked(kernel.LoadLibraryExW(str(dll_path), None, 1))
        try:
            export = checked(kernel.GetProcAddress(local, b'HellgatoInitialize'))
            result = remote_call(kernel, process, target[0] + export - local,
                                 (str(config_path) + '\0').encode('utf-16-le'))
        finally:
            kernel.FreeLibrary(local)
        if result:
            raise NativeUiError(f'Native UI initialization rejected configuration (code {result})')
        return {'event': 'nativeUiLoaded', 'pid': pid, 'dll': str(dll_path)}
    finally:
        kernel.CloseHandle(process)

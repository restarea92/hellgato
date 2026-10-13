"""Prepare Stream Deck on its next user-initiated launch, without controlling it."""

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback


ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'app/n4'))
from compatibility import INCOMPATIBLE, IncompatibleError
from startup_install import default_executable, install, installed, remove, startup_dll


def elevated(arguments):
    class ShellExecuteInfo(c.Structure):
        _fields_ = [('size', w.DWORD), ('mask', w.ULONG), ('window', w.HWND),
                    ('verb', w.LPCWSTR), ('file', w.LPCWSTR), ('parameters', w.LPCWSTR),
                    ('directory', w.LPCWSTR), ('show', c.c_int), ('instance', w.HINSTANCE),
                    ('id_list', c.c_void_p), ('class_name', w.LPCWSTR), ('class_key', w.HKEY),
                    ('hotkey', w.DWORD), ('icon', w.HANDLE), ('process', w.HANDLE)]
    shell, kernel = c.WinDLL('shell32', use_last_error=True), c.WinDLL('kernel32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [c.POINTER(ShellExecuteInfo)]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    prefix = ['--run-script', str(Path(__file__).resolve())] if getattr(sys, 'frozen', False) else [str(Path(__file__).resolve())]
    request = ShellExecuteInfo(size=c.sizeof(ShellExecuteInfo), mask=0x40, verb='runas',
                               file=sys.executable, parameters=subprocess.list2cmdline(prefix + arguments),
                               directory=str(ROOT), show=0)
    if not shell.ShellExecuteExW(c.byref(request)):
        raise c.WinError(c.get_last_error())
    try:
        while kernel.WaitForSingleObject(request.process, 500) == 258:
            pass
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(request.process, c.byref(code)):
            raise c.WinError(c.get_last_error())
        return code.value
    finally:
        kernel.CloseHandle(request.process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--install', action='store_true')
    group.add_argument('--remove', action='store_true')
    group.add_argument('--check', action='store_true')
    parser.add_argument('--elevate', action='store_true')
    parser.add_argument('--exe', type=Path, default=default_executable())
    args = parser.parse_args()
    try:
        if args.check:
            return 0 if installed(args.exe, startup_dll(ROOT)) else 10
        if args.remove:
            result = {'removed': remove(args.exe)}
        else:
            result = install(args.exe, startup_dll(ROOT))
        print(json.dumps(result), flush=True)
        return 0
    except PermissionError:
        if args.elevate and not c.windll.shell32.IsUserAnAdmin():
            return elevated([arg for arg in sys.argv[1:] if arg != '--elevate'])
        print('Close Stream Deck yourself before updating or removing its startup files.', file=sys.stderr)
        return 5
    except IncompatibleError as error:
        print(str(error), file=sys.stderr)
        return INCOMPATIBLE
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    state = Path(os.environ.get('HELLGATO_STATE_DIR', Path(os.environ['LOCALAPPDATA']) / 'Hellgato'))
    state.mkdir(parents=True, exist_ok=True)
    # Elevated helpers have no inherited console; retain their result for the UI.
    with (state / 'startup-setup.log').open('a', encoding='utf-8') as log:
        from contextlib import redirect_stdout, redirect_stderr
        with redirect_stdout(log), redirect_stderr(log):
            try:
                result = main()
            except Exception:
                traceback.print_exc()
                result = 1
            print(f'Startup setup result: {result}', flush=True)
    raise SystemExit(result)

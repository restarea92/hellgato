"""Build against Qt development files without bundling another Qt runtime."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def msvc_environment():
    vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*',
                                            '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                            '-property', 'installationPath'], text=True).strip()
    if not installation:
        raise RuntimeError('Visual Studio C++ Build Tools are required')
    activation = Path(installation) / 'VC/Auxiliary/Build/vcvars64.bat'
    result = subprocess.run(f'cmd.exe /d /u /s /c ""{activation}" >nul && set"',
                            encoding='utf-16-le', capture_output=True, check=True)
    environment = {key.upper(): value for key, value in os.environ.items()}
    for line in result.stdout.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            environment[key.upper()] = value
    environment['Path'] = environment.pop('PATH', '')
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qt', type=Path, default=ROOT / 'work/toolchain/qt-6.5.3/6.5.3/msvc2019_64',
                        help='Qt 6 MSVC x64 development kit')
    parser.add_argument('--output', type=Path, default=ROOT / 'work/build-native-ui')
    parser.add_argument('--test-host', action='store_true', help='Build the isolated Qt/HTTP behavior check')
    args = parser.parse_args()
    args.qt, args.output = args.qt.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    arguments = ['cl.exe', '/nologo', '/MD', '/O2', '/EHsc', '/std:c++17', '/Zc:__cplusplus', '/permissive-', '/utf-8',
                 '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/DUNICODE', '/D_UNICODE',
                 '/I' + str(args.qt / 'include')]
    for module in ('Core', 'Gui', 'Widgets', 'Network'):
        arguments.append('/I' + str(args.qt / 'include' / ('Qt' + module)))
    filename = 'test_host' if args.test_host else 'native_ui'
    output = 'HellgatoNativeUiTest.exe' if args.test_host else 'HellgatoNativeUiV2.dll'
    if not args.test_host:
        arguments.append('/LD')
    arguments += [str(Path(__file__).with_name(filename + '.cpp')),
                  '/Fo' + str(args.output / 'native_ui.obj'), '/link', '/Brepro',
                  '/OUT:' + str(args.output / output),
                  '/IMPLIB:' + str(args.output / 'HellgatoNativeUi.lib'),
                  '/LIBPATH:' + str(args.qt / 'lib')]
    arguments += ['Qt6' + module + '.lib' for module in ('Core', 'Gui', 'Widgets', 'Network')]
    environment = msvc_environment()
    if args.test_host:
        moc = args.qt / 'bin/moc.exe'
        if not moc.exists():
            moc = args.qt / 'libexec/moc.exe'
        subprocess.run([str(moc), str(Path(__file__).with_name('test_host.cpp')),
                        '-o', str(args.output / 'test_host.moc')], check=True, env=environment)
        arguments.insert(1, '/I' + str(args.output))
    arguments[0] = shutil.which('cl.exe', path=environment['Path'])
    subprocess.run(arguments, check=True, env=environment)
    print(json.dumps({'artifact': str((args.output / output).resolve())}))


if __name__ == '__main__':
    main()

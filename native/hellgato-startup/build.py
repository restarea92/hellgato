"""Build the standalone startup proxy and its isolated synthetic process fixture."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent


def environment():
    spec = importlib.util.spec_from_file_location('native_ui_build', ROOT / 'native/hellgato-ui/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.msvc_environment()


def compile_proxy(output, env, *, fixture=False):
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([shutil.which('lib.exe', path=env['Path']), '/nologo', '/MACHINE:X64',
                    '/DEF:' + str(SOURCE / 'version.def'),
                    '/OUT:' + str(output / 'HellgatoStartup.lib')], check=True, env=env, cwd=output)
    arguments = [shutil.which('cl.exe', path=env['Path']), '/nologo', '/MT', '/O2', '/W4',
                 '/EHsc', '/std:c++17', '/Zc:__cplusplus', '/permissive-', '/utf-8',
                 '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/DUNICODE', '/D_UNICODE',
                 '/LD', str(SOURCE / 'startup.cpp'), str(output / 'HellgatoStartup.exp'),
                 '/Fo' + str(output / 'startup.obj')]
    if fixture:
        arguments.append('/DHELLGATO_STARTUP_TEST_HOST')
    arguments += ['/link', '/Brepro', '/DYNAMICBASE', '/NXCOMPAT',
                  '/OUT:' + str(output / 'version.dll'),
                  '/IMPLIB:' + str(output / 'HellgatoStartup.lib'), 'bcrypt.lib']
    subprocess.run(arguments, check=True, env=env, cwd=output)


def fixture_plan(executable):
    import pefile
    raw = executable.read_bytes()
    pe = pefile.PE(data=raw)
    mapped = pe.get_memory_mapped_image()
    exports = {entry.name.decode(): entry.address for entry in pe.DIRECTORY_ENTRY_EXPORT.symbols if entry.name}
    constructor, gate = exports['FixtureCtor'], exports['FixtureGate'] + 4
    guard = exports['FixtureGuard']
    before = bytes.fromhex('ba0400000041b802000000')
    after = bytes.fromhex('ba0500000041b802000000')
    assert mapped[constructor:constructor + len(before)] == before
    assert mapped[gate] == 0xe8
    pack = lambda *values: struct.pack('<' + 'I' * len(values), *values)
    data = bytearray(b'HGSTART1' + pack(1, pe.OPTIONAL_HEADER.SizeOfImage, pe.FILE_HEADER.TimeDateStamp,
                                      pe.OPTIONAL_HEADER.AddressOfEntryPoint, constructor, gate, guard))
    data += hashlib.sha256(raw).digest()
    guards = [(constructor, before), (gate, mapped[gate:gate + 5])]
    data += pack(len(guards))
    for rva, content in guards:
        data += pack(rva, len(content)) + content
    data += pack(1) + b'\xc3' + pack(0)
    data += pack(1, constructor, len(before)) + before + after + pack(0)
    return bytes(data)


def test_fixture(output, env):
    folder = output / 'fixture'
    compile_proxy(folder, env, fixture=True)
    subprocess.run([shutil.which('ml64.exe', path=env['Path']), '/nologo', '/c',
                    '/Fo' + str(folder / 'fixture_asm.obj'), str(SOURCE / 'fixture.asm')], check=True, env=env, cwd=folder)
    executable = folder / 'HellgatoStartupFixture.exe'
    subprocess.run([shutil.which('cl.exe', path=env['Path']), '/nologo', '/MT', '/O2', '/EHsc', '/std:c++17',
                    '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', '/DUNICODE', '/D_UNICODE',
                    str(SOURCE / 'fixture.cpp'), str(folder / 'fixture_asm.obj'),
                    '/Fo' + str(folder / 'fixture.obj'), '/link', '/Brepro',
                    '/OUT:' + str(executable), '/EXPORT:FixtureCtor', '/EXPORT:FixtureGate', 'version.lib'],
                   check=True, env=env, cwd=folder)
    plan = fixture_plan(executable)
    plan_file = folder / 'HellgatoStartup.plan'
    changed_hash = bytearray(plan)
    changed_hash[36] ^= 1
    changed_guard = bytearray(plan)
    changed_guard[80] ^= 1
    cases = [('prepared', plan, []), ('wrong-host-hash', bytes(changed_hash), ['--expect-original']),
             ('changed-code-guard', bytes(changed_guard), ['--expect-original']),
             ('invalid-model', plan, ['--expect-original', '--invalid-model']),
             ('partial-model-rollback', plan, ['--expect-original', '--fail-after-model']),
             ('truncated-plan', plan[:76], ['--expect-original']),
             ('missing-plan', None, ['--expect-original'])]
    reports = []
    for name, content, arguments in cases:
        if content is None:
            plan_file.unlink(missing_ok=True)
        else:
            plan_file.write_bytes(content)
        result = subprocess.run([str(executable), *arguments], cwd=folder, capture_output=True,
                                text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(f'{name} failed ({result.returncode}): {result.stdout}\n{result.stderr}')
        reports.append({'case': name, **json.loads(result.stdout)})
    plan_file.write_bytes(plan)
    (folder / 'fixture-results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print(json.dumps({'tests': reports}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'work/build-native-startup')
    parser.add_argument('--test', action='store_true', help='Run only isolated synthetic host processes after building')
    args = parser.parse_args()
    output = args.output.resolve()
    env = environment()
    compile_proxy(output, env)
    if args.test:
        test_fixture(output, env)
    print(json.dumps({'artifact': str(output / 'version.dll'),
                      'sha256': hashlib.sha256((output / 'version.dll').read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()

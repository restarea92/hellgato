from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = ROOT / 'host-integrations/streamdeck/windows/python'
sys.path.insert(0, str(HOST_ROOT))
from hellgato_host import readiness
from hellgato_host import inspection
from hellgato_host.inspection import HostModule
from hellgato_host.presence import HostProcess
from hellgato_host.startup_status import RECORD, StartupStatus, decode_status


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.exe = self.root / 'StreamDeck.exe'
        self.exe.write_bytes(b'fixture executable')
        self.reference = self.root / 'bundle/version.dll'
        self.reference.parent.mkdir()
        self.reference.write_bytes(b'fixture exact packaged startup shim')
        (self.root / readiness.DLL).write_bytes(self.reference.read_bytes())
        self.plan = b'fixture exact structurally resolved plan'
        (self.root / readiness.PLAN).write_bytes(self.plan)
        self.manifest = {'owner': readiness.OWNER, 'executableSha256': readiness.digest(self.exe.read_bytes()),
                         'files': {readiness.DLL: readiness.digest(self.reference.read_bytes()),
                                   readiness.PLAN: readiness.digest(self.plan)}}
        self.write_manifest()
        self.host = HostProcess(71, 133700123456789123, self.exe)
        self.status = StartupStatus(2, 0, 0x140000000, 0x20000000, 0x30000000)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for name, result in {
            'is_supported_platform': True, 'streamdeck_processes': [self.host],
            'build_plan': (self.plan, {'sha256': self.manifest['executableSha256']}),
            'validate_dll': None, 'read_status': self.status,
            'loaded_modules': [HostModule('StreamDeck.exe', self.exe, self.status.image, 0x6000),
                               HostModule('version.dll', self.root / readiness.DLL, 0x150000000, 0x6000),
                               HostModule('version.dll', self.root / 'system/version.dll', 0x160000000, 0x6000)],
            'validate_live_target': None,
        }.items():
            self.mocks[name] = self.stack.enter_context(patch.object(readiness, name, return_value=result))

    def write_manifest(self):
        (self.root / readiness.MANIFEST).write_text(json.dumps(self.manifest))

    def inspect(self, **kwargs):
        return readiness.inspect_readiness(self.exe, self.reference, request_id='request-1', **kwargs)

    def assert_rejected(self, code, **kwargs):
        result = self.inspect(**kwargs)
        self.assertFalse(result['ready'], result)
        self.assertEqual(result['preparation'], [])
        self.assertEqual(result['error']['code'], code, result)
        return result

    def test_exact_fresh_host_and_plan_approve_without_writes(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        result = self.inspect()
        self.assertTrue(result['ready'], result)
        self.assertEqual(result['requestId'], 'request-1')
        self.assertEqual(result['targetId'], readiness.TARGET_ID)
        self.assertEqual(result['profileId'], readiness.PROFILE_ID)
        self.assertEqual(result['preparation'], [readiness.TARGET_ID])
        self.assertEqual(result['host']['pid'], self.host.pid)
        # FILETIME must stay a decimal string; a JavaScript number loses precision.
        self.assertEqual(result['host']['creationTime'], str(self.host.created))
        self.assertEqual(result['host']['executableDigest'], self.manifest['executableSha256'])
        self.assertEqual(self.mocks['streamdeck_processes'].call_count, 2)
        self.assertEqual(self.mocks['read_status'].call_count, 2)
        self.mocks['validate_live_target'].assert_called_once_with(self.host, self.status)
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                  for path in self.root.rglob('*') if path.is_file()})

    def test_unknown_target_or_profile_cannot_echo_success(self):
        self.assert_rejected('UNSUPPORTED_TARGET', target_id='custom-target')
        self.assert_rejected('UNSUPPORTED_TARGET', profile_id='custom-profile')
        self.mocks['streamdeck_processes'].assert_not_called()

    def test_platform_and_relative_paths_rejected_before_process_access(self):
        self.mocks['is_supported_platform'].return_value = False
        self.assert_rejected('UNSUPPORTED_PLATFORM')
        self.mocks['is_supported_platform'].return_value = True
        result = readiness.inspect_readiness('StreamDeck.exe', self.reference)
        self.assertEqual(result['error']['code'], 'INVALID_PATH')
        self.mocks['streamdeck_processes'].assert_not_called()

    def test_missing_and_multiple_hosts_are_closed(self):
        self.mocks['streamdeck_processes'].return_value = []
        self.assert_rejected('HOST_NOT_RUNNING')
        self.mocks['streamdeck_processes'].return_value = [self.host, self.host]
        self.assert_rejected('HOST_AMBIGUOUS')

    def test_same_basename_other_installation_is_rejected(self):
        other = self.root / 'other/StreamDeck.exe'
        other.parent.mkdir()
        other.write_bytes(self.exe.read_bytes())
        self.mocks['streamdeck_processes'].return_value = [HostProcess(self.host.pid, self.host.created, other)]
        self.assert_rejected('WRONG_EXECUTABLE')
        self.mocks['read_status'].assert_not_called()

    def test_unsupported_structural_host_and_missing_startup_fail_closed(self):
        self.mocks['build_plan'].side_effect = readiness.IncompatibleError('unknown layout')
        self.assert_rejected('INCOMPATIBLE_HOST')
        self.mocks['build_plan'].side_effect = None
        (self.root / readiness.DLL).unlink()
        self.assert_rejected('STARTUP_NOT_INSTALLED')

    def test_manifest_cannot_claim_foreign_ownership_or_another_executable(self):
        self.manifest['owner'] = 'Other.Owner'
        self.write_manifest()
        self.assert_rejected('INVALID_STARTUP_MANIFEST')
        self.manifest['owner'] = readiness.OWNER
        self.manifest['executableSha256'] = '0' * 64
        self.write_manifest()
        self.assert_rejected('STARTUP_EXECUTABLE_MISMATCH')

    def test_duplicate_manifest_fields_are_rejected(self):
        (self.root / readiness.MANIFEST).write_text('{"owner":"other","owner":"' + readiness.OWNER + '"}')
        self.assert_rejected('INVALID_STARTUP_MANIFEST')

    def test_manifest_alone_cannot_authorize_changed_dll_or_plan(self):
        (self.root / readiness.DLL).write_bytes(b'foreign dll')
        self.assert_rejected('STARTUP_FILES_CHANGED')
        self.manifest['files'][readiness.DLL] = readiness.digest(b'foreign dll')
        self.write_manifest()
        self.assert_rejected('STARTUP_DLL_MISMATCH')
        (self.root / readiness.DLL).write_bytes(self.reference.read_bytes())
        self.manifest['files'][readiness.DLL] = readiness.digest(self.reference.read_bytes())
        (self.root / readiness.PLAN).write_bytes(b'wrong plan with self-consistent digest')
        self.manifest['files'][readiness.PLAN] = readiness.digest((self.root / readiness.PLAN).read_bytes())
        self.write_manifest()
        self.assert_rejected('STARTUP_PLAN_MISMATCH')

    def test_absent_partial_and_rejected_native_readiness_never_approve(self):
        for status, code in ((None, 'HOST_NOT_PREPARED'), (StartupStatus(1, 0, 1, 2, 3), 'HOST_NOT_PREPARED'),
                             (StartupStatus(2, 1, 1, 2, 3), 'HOST_NOT_PREPARED'),
                             (StartupStatus(2, 0, 1, 0, 3), 'HOST_NOT_PREPARED'),
                             (StartupStatus(3, 2, 1, 0, 0), 'INCOMPATIBLE_HOST'),
                             (StartupStatus(4, 2, 1, 0, 0), 'INCOMPATIBLE_HOST')):
            with self.subTest(status=status):
                self.mocks['read_status'].return_value = status
                self.assert_rejected(code)

    def test_reused_pid_record_is_rejected_by_native_decoder(self):
        raw = RECORD.pack(b'HGREADY1', 1, self.host.pid, self.host.created - 1, 2, 0, 1, 2, 3)
        self.mocks['read_status'].side_effect = lambda host: decode_status(raw, host)
        self.assert_rejected('INSPECTION_FAILED')

    def test_status_image_and_installed_loaded_shim_must_match(self):
        self.mocks['loaded_modules'].return_value = [HostModule('StreamDeck.exe', self.exe, 12, 0x6000)]
        self.assert_rejected('HOST_IMAGE_MISMATCH')
        self.mocks['loaded_modules'].return_value = [HostModule('StreamDeck.exe', self.exe, self.status.image, 0x6000),
                                                    HostModule('version.dll', self.reference, 13, 0x6000)]
        self.assert_rejected('STARTUP_NOT_LOADED')

    def test_live_geometry_must_still_be_exact_ten_key_target(self):
        self.mocks['validate_live_target'].side_effect = readiness.IncompatibleError('Changed live grid')
        self.assert_rejected('INCOMPATIBLE_HOST')

    def test_process_restart_and_pid_reuse_during_check_fail_closed(self):
        replacement = HostProcess(self.host.pid, self.host.created + 1, self.exe)
        self.mocks['streamdeck_processes'].side_effect = [[self.host], [replacement]]
        self.assert_rejected('HOST_CHANGED')

    def test_native_state_change_during_check_fails_closed(self):
        self.mocks['read_status'].side_effect = [self.status, StartupStatus(4, 2, self.status.image, 0, 0)]
        self.assert_rejected('HOST_CHANGED')

    def test_executable_change_during_check_fails_closed(self):
        self.mocks['validate_live_target'].side_effect = lambda *_: self.exe.write_bytes(b'updated host')
        self.assert_rejected('HOST_CHANGED')

    def test_library_parser_exception_stays_a_negative_json_report(self):
        self.mocks['validate_dll'].side_effect = Exception('Invalid PE structure')
        self.assert_rejected('INSPECTION_FAILED')

    def test_process_query_denial_is_reported_without_fallback_mutation(self):
        self.mocks['streamdeck_processes'].side_effect = PermissionError('denied')
        self.assert_rejected('INSPECTION_FAILED')
        self.mocks['read_status'].assert_not_called()


class ReadinessProtocolTest(unittest.TestCase):
    def args(self):
        return ['inspect', '--json', '--expected-executable', '/fixture/StreamDeck.exe',
                '--expected-startup-dll', '/bundle/version.dll']

    def request(self):
        return {'schemaVersion': 1, 'requestId': 'request-7', 'targetId': readiness.TARGET_ID,
                'profileId': readiness.PROFILE_ID}

    def run_main(self, raw, args=None):
        output = io.StringIO()
        code = readiness.main(self.args() if args is None else args, stdin=io.StringIO(raw), stdout=output)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return code, json.loads(lines[0])

    def test_stdio_protocol_preserves_request_identity_without_promoting_failures(self):
        report = {**self.request(), 'ready': False, 'preparation': [], 'error': {'code': 'HOST_NOT_RUNNING'}}
        with patch.object(readiness, 'inspect_readiness', return_value=report) as inspect:
            code, result = self.run_main(json.dumps(self.request()) + '\n')
        self.assertEqual(code, 0)
        self.assertEqual(result, report)
        self.assertEqual(inspect.call_args.kwargs['request_id'], 'request-7')

    def test_invalid_schema_fields_duplicates_and_multiframe_rejected(self):
        for raw in ('', '[]', '{}', '[' * 1100 + ']' * 1100, '{"schemaVersion":1,"schemaVersion":1}',
                    json.dumps({**self.request(), 'pid': 123}),
                    json.dumps({**self.request(), 'schemaVersion': True}),
                    json.dumps({**self.request(), 'requestId': ''}),
                    json.dumps({**self.request(), 'requestId': 'a' * 129}),
                    json.dumps({**self.request(), 'targetId': 'a\nb'}),
                    json.dumps(self.request()) + '\n' + json.dumps(self.request()), 'x' * (readiness.MAX_REQUEST_BYTES + 1)):
            with self.subTest(raw=raw[:80]), patch.object(readiness, 'inspect_readiness') as inspect:
                code, result = self.run_main(raw)
                self.assertEqual(code, 2)
                self.assertFalse(result['ready'])
                self.assertEqual(result['error']['code'], 'INVALID_REQUEST')
                inspect.assert_not_called()

    def test_mutating_cli_operations_not_available(self):
        for operation in ('install', 'prepare', 'inject', 'remove'):
            code, result = self.run_main(json.dumps(self.request()), [operation, *self.args()[1:]])
            self.assertEqual(code, 2)
            self.assertEqual(result['error']['code'], 'INVALID_ARGUMENTS')

    def test_source_entry_works_from_unrelated_working_directory(self):
        if os.name == 'nt':
            self.skipTest('This subprocess case asserts the non-Windows rejection')
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(HOST_ROOT / 'inspect_readiness.py'), *self.args()],
                                    input=json.dumps(self.request()) + '\n', capture_output=True, text=True,
                                    cwd=folder, timeout=10, check=True)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'UNSUPPORTED_PLATFORM')


class LiveTargetInspectionTest(unittest.TestCase):
    def test_only_query_and_read_access_and_exact_ten_key_geometry(self):
        import ctypes
        from hellgato_host.compatibility import IncompatibleError
        raw = bytearray(160)
        struct.pack_into('<13I', raw, 0, 1, 5, 2, 0, 0, 800, 480, 120, 120, 120, 120, 100, 100)
        struct.pack_into('<13I', raw, 80, 2, 4, 1, 0, 3, 800, 100, *([0xffffffff] * 6))
        struct.pack_into('<3Q', raw, 56, 0x4000, 0x4050, 0x4050)
        struct.pack_into('<3Q', raw, 136, 0x5000, 0x5008, 0x5008)
        points = b''.join(struct.pack('<ii', 13 + column * 164, y) for y in (12, 172) for column in range(5))
        memory = {0x3000: raw, 0x4000: points, 0x5000: struct.pack('<ii', 0, 380)}
        kernel = Mock()
        kernel.OpenProcess.return_value = 91
        def read(handle, address, buffer, size, count):
            data = bytes(memory[address])
            self.assertEqual(len(data), size)
            ctypes.memmove(buffer, data, size)
            ctypes.cast(count, ctypes.POINTER(ctypes.c_size_t))[0] = size
            return 1
        kernel.ReadProcessMemory.side_effect = read
        host = HostProcess(31, 42, Path('/fixture/StreamDeck.exe'))
        status = StartupStatus(2, 0, 0x1000, 0x2000, 0x3000)
        with patch.object(inspection, 'kernel_api', return_value=kernel), \
                patch.object(inspection, 'require_same_process') as identity:
            inspection.validate_live_target(host, status)
            kernel.OpenProcess.assert_called_once_with(0x1010, False, 31)
            kernel.CloseHandle.assert_called_once_with(91)
            self.assertEqual(identity.call_count, 2)
            raw[4] = 4
            with self.assertRaises(IncompatibleError):
                inspection.validate_live_target(host, status)
        for name in ('WriteProcessMemory', 'CreateRemoteThread', 'VirtualAllocEx', 'LoadLibraryExW'):
            getattr(kernel, name).assert_not_called()


if __name__ == '__main__':
    unittest.main()

import ctypes as c
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
import native_ui


class NativeUiTest(unittest.TestCase):
    def config(self):
        return {'endpoint': 'http://127.0.0.1:5345', 'token': 'x' * 40, 'serial': 'HGMOCK000003'}

    def test_requires_authenticated_exact_local_endpoint_and_virtual_serial(self):
        self.assertEqual(native_ui.validate_config(self.config()), self.config())
        for key, value in [('endpoint', 'https://remote.example:5345'),
                           ('endpoint', 'http://127.0.0.1:5345/?elsewhere=true'),
                           ('endpoint', 'http://user@127.0.0.1:5345'),
                           ('token', 'short'), ('token', 'x' * 39 + '\n'),
                           ('serial', 'REAL-DEVICE'), ('serial', 'HGMOCK٠٠٠٠٠٣')]:
            with self.subTest(key=key, value=value), self.assertRaises(native_ui.NativeUiError):
                native_ui.validate_config({**self.config(), key: value})

    def test_checks_dll_pe_machine_before_attaching(self):
        with tempfile.TemporaryDirectory() as folder:
            dll = Path(folder) / 'example.dll'
            content = bytearray(80)
            content[:2] = b'MZ'
            struct.pack_into('<I', content, 60, 64)
            content[64:68] = b'PE\0\0'
            struct.pack_into('<H', content, 68, 0x8664)
            dll.write_bytes(content)
            self.assertEqual(native_ui.pe_machine(dll), 0x8664)
            dll.write_bytes(b'not a dll')
            with self.assertRaises(native_ui.NativeUiError):
                native_ui.pe_machine(dll)

    def test_automatic_pairing_is_an_explicit_boolean_option(self):
        self.assertTrue(native_ui.validate_config({**self.config(), 'autoReconnect': True})['autoReconnect'])
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaises(native_ui.NativeUiError):
                native_ui.validate_config({**self.config(), 'autoReconnect': value})

    @unittest.skipUnless(os.name == 'nt', 'Windows process loader')
    def test_refuses_another_process_before_remote_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'native-ui.json'
            config.write_text(json.dumps(self.config()))
            dll = root / 'native.dll'
            kernel = MagicMock()
            kernel.OpenProcess.return_value = 123
            def process_name(handle, flags, buffer, length):
                buffer.value = str(root / 'Other.exe')
                return 1
            kernel.QueryFullProcessImageNameW.side_effect = process_name
            with patch.object(native_ui, 'pe_machine', return_value=0x8664), \
                    patch.object(native_ui, 'kernel_api', return_value=kernel):
                with self.assertRaisesRegex(native_ui.NativeUiError, 'expected running Stream Deck'):
                    native_ui.load_native_ui(42, dll, config, root / 'StreamDeck.exe')
            kernel.CreateRemoteThread.assert_not_called()
            kernel.CloseHandle.assert_called_once_with(123)

    def test_timeout_does_not_free_an_argument_still_used_by_the_remote_thread(self):
        kernel = MagicMock()
        kernel.VirtualAllocEx.return_value = 0x1000
        kernel.CreateRemoteThread.return_value = 0x2000
        def write(process, allocation, buffer, length, completed):
            c.cast(completed, c.POINTER(c.c_size_t))[0] = length
            return 1
        kernel.WriteProcessMemory.side_effect = write
        kernel.WaitForSingleObject.return_value = 258
        with self.assertRaisesRegex(native_ui.NativeUiError, 'still running'):
            native_ui.remote_call(kernel, 12, 0x3000, b'argument', timeout_ms=5)
        kernel.VirtualFreeEx.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(0x2000)
        kernel.TerminateProcess.assert_not_called()


if __name__ == '__main__':
    unittest.main()

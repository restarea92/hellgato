"""Language-shared contracts and native-free standalone worker tests."""
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'adapters/mirabox-python/src'
sys.path.insert(0, str(SOURCE))
from hellgato_mirabox.protocol import (MAX_MESSAGE_BYTES, MAX_REQUESTS, ProtocolError,
    check, validate_contract, validate_rpc_message)
from hellgato_mirabox.worker import Worker
from hellgato_mirabox.transport.fake import FakeBackend, DEVICE_ID
from hellgato_mirabox.models.n4_pro.model import input_event, descriptor


class ContractParityTests(unittest.TestCase):
    def test_canonical_schema_snapshot_matches_exact_bytes(self):
        self.assertEqual((ROOT / 'packages/core/schemas/contracts.schema.json').read_bytes(),
                         (SOURCE / 'hellgato_mirabox/schemas/contracts.schema.json').read_bytes())

    def test_every_shared_valid_and_invalid_contract_fixture(self):
        fixtures = json.loads((ROOT / 'test/fixtures/architecture-contracts.json').read_text())
        for case in fixtures['valid']:
            with self.subTest(valid=case['contract'], value=case['value']):
                self.assertEqual(validate_contract(case['contract'], case['value']), case['value'])
        for case in fixtures['invalid']:
            with self.subTest(invalid=case['contract'], value=case['value']):
                with self.assertRaises(ProtocolError):
                    validate_contract(case['contract'], case['value'])

    def test_every_shared_ipc_fixture(self):
        fixtures = json.loads((ROOT / 'test/fixtures/architecture-ipc.json').read_text())
        for value in fixtures['valid']:
            self.assertEqual(validate_rpc_message(value), value)
        for value in fixtures['invalid']:
            with self.assertRaises(ProtocolError):
                validate_rpc_message(value)

    def test_unknown_schema_keywords_are_not_silently_ignored(self):
        with self.assertRaisesRegex(ProtocolError, 'Unsupported schema keyword'):
            check('x', {'type': 'string', 'format': 'uri'})
        with self.assertRaisesRegex(ProtocolError, 'Unsupported schema type'):
            check(1.5, {'type': 'number'})

    def test_descriptor_never_claims_physical_dial_release(self):
        value = descriptor('test')
        validate_contract('DeviceDescriptor', value)
        self.assertEqual(value['capabilities']['dialPress'], 'pulse')
        self.assertEqual(value['capabilities']['dialRelease'], 'unavailable')

    def test_jpeg_requires_end_marker_and_canonical_base64(self):
        fixtures = json.loads((ROOT / 'test/fixtures/architecture-contracts.json').read_text())
        original = next(case['value'] for case in fixtures['valid'] if case['contract'] == 'DisplayFrame' and case['value']['format'] == 'jpeg')
        value = {**original, 'data': base64.b64encode(base64.b64decode(original['data'])[:-2]).decode()}
        with self.assertRaises(ProtocolError):
            validate_contract('DisplayFrame', value)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.worker = Worker(self.backend, io.BytesIO(), io.BytesIO(), 'test-build')
        self.number = 0

    def request(self, method, **params):
        self.number += 1
        return self.worker.dispatch({'jsonrpc': '2.0', 'id': str(self.number), 'method': method,
                                     'params': {'deadlineAt': int(time.time() * 1000) + 3000, **params}})

    def open(self):
        self.request('hello', protocol={'major': 1, 'minor': 0})
        self.request('open', deviceId=DEVICE_ID, generation=1)

    def test_hello_is_required_and_versions_and_features_fail_closed(self):
        with self.assertRaisesRegex(ProtocolError, 'hello required'):
            self.request('discover')
        with self.assertRaisesRegex(ProtocolError, 'Incompatible IPC'):
            self.request('hello', protocol={'major': 2, 'minor': 0})
        with self.assertRaisesRegex(ProtocolError, 'Unsupported required feature'):
            self.request('hello', protocol={'major': 1, 'minor': 0}, requiredFeatures=['firmware-writing'])
        hello = self.request('hello', protocol={'major': 1, 'minor': 10})
        self.assertEqual(hello['protocol'], {'major': 1, 'minor': 0})
        self.assertEqual(hello['buildId'], 'test-build')

    def test_unknown_operation_rejected_not_acknowledged(self):
        with self.assertRaises(ProtocolError) as caught:
            self.request('firmwareWrite')
        self.assertEqual(caught.exception.rpc_code, -32601)

    def test_backend_failure_preserves_original_native_error_and_trace(self):
        helper = f"""
import sys
sys.path.insert(0, {str(SOURCE)!r})
from hellgato_mirabox.worker import Worker
from hellgato_mirabox.transport.fake import FakeBackend
backend = FakeBackend()
def discover(*args):
    raise OSError(5, 'USB firmware probe failed')
backend.discover = discover
Worker(backend, sys.stdin.buffer, sys.stdout.buffer, 'test-build').run()
"""
        with tempfile.TemporaryDirectory() as folder:
            process = subprocess.Popen([sys.executable, '-c', helper], cwd=folder,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for identifier, method, params in [('hello', 'hello', {'protocol': {'major': 1, 'minor': 0}}),
                                                   ('probe', 'discover', {})]:
                    params['deadlineAt'] = int(time.time() * 1000) + 3000
                    process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': identifier, 'method': method,
                                                    'params': params}) + '\n').encode())
                    process.stdin.flush()
                    response = json.loads(process.stdout.readline())
                self.assertEqual(response['error']['data']['code'], 'ERR_DEVICE_OPERATION')
                self.assertEqual(response['error']['data']['nativeCode'], 5)
                self.assertIn('USB firmware probe failed', response['error']['data']['message'])
                self.assertIn('OSError', response['error']['data']['stack'])
            finally:
                process.stdin.close()
                process.wait(timeout=3)
                process.stdout.close()
                process.stderr.close()

    def test_duplicate_request_id_and_stale_generation_rejected(self):
        self.open()
        with self.assertRaisesRegex(ProtocolError, 'Duplicate'):
            self.worker.dispatch({'jsonrpc': '2.0', 'id': '2', 'method': 'discover', 'params': {}})
        with self.assertRaisesRegex(ProtocolError, 'Stale'):
            self.request('close', deviceId=DEVICE_ID, generation=0)
        self.request('close', deviceId=DEVICE_ID, generation=1)
        with self.assertRaisesRegex(ProtocolError, 'newer generation'):
            self.request('open', deviceId=DEVICE_ID, generation=1)
        self.request('open', deviceId=DEVICE_ID, generation=2)

    def test_deadline_checked_again_after_backend_delay_before_writing(self):
        self.open()
        self.backend.write_delay_ms = 30
        with self.assertRaises(ProtocolError) as caught:
            self.request('setControl', type='control', control='brightness', value=50,
                         deviceId=DEVICE_ID, generation=1, deadlineAt=int(time.time() * 1000) + 10)
        self.assertEqual(caught.exception.code, 'ERR_REQUEST_TIMEOUT')
        self.assertEqual(self.backend.write_count, 0)

    def test_bad_frames_do_not_reach_backend(self):
        self.open()
        for data in ('a', 'AAAA====', 'AAAA'):
            with self.assertRaises(ProtocolError):
                self.request('setOutput', type='frame', surfaceId='key:0', format='rgb888', width=4, height=4,
                             deviceId=DEVICE_ID, generation=1, data=data)
        self.assertEqual(self.backend.write_count, 0)

    def test_input_queue_saturation_is_fatal_and_bounded(self):
        data = b'{"jsonrpc":"2.0","id":"x","method":"getState","params":{}}\n'
        self.worker.source = io.BytesIO(data * (MAX_REQUESTS + 1))
        self.worker._read()
        self.assertEqual(self.worker.fatal, 'ERR_BACKPRESSURE')
        self.assertEqual(self.worker.requests.qsize(), MAX_REQUESTS)

    def test_oversized_and_truncated_ndjson_are_rejected_before_parse(self):
        for data, code in ((b'x' * (MAX_MESSAGE_BYTES + 1), 'ERR_MESSAGE_TOO_LARGE'), (b'{}', 'ERR_IPC_PROTOCOL')):
            worker = Worker(FakeBackend(), io.BytesIO(data), io.BytesIO(), 'test')
            worker._read()
            self.assertEqual(worker.fatal, code)
            self.assertEqual(worker.requests.qsize(), 0)

    def test_output_queue_saturation_is_fatal_no_silent_input_drops(self):
        self.worker.current = DEVICE_ID
        for index in range(129):
            self.worker.emit_input({'type': 'key', 'index': 0, 'state': 'down' if index % 2 == 0 else 'up'})
        self.assertEqual(self.worker.fatal, 'ERR_BACKPRESSURE')
        self.assertEqual(self.worker.outgoing.qsize(), 128)

    def test_preserves_n4_keys_dials_and_pulse_limitation(self):
        for key in range(1, 11):
            self.assertEqual(input_event({'event_type': 'button', 'key': key, 'state': 1}),
                             {'type': 'key', 'index': key - 1, 'state': 'down'})
        for index in range(4):
            event = {'event_type': 'knob_press', 'knob_id': f'knob_{index + 1}', 'state': 1}
            self.assertEqual(input_event(event), {'type': 'dial', 'kind': 'pulse', 'index': index})
            self.assertIsNone(input_event({**event, 'state': 0}))
        self.assertIsNone(input_event({'event_type': 'button', 'key': 11, 'state': 1}))


if __name__ == '__main__':
    unittest.main()

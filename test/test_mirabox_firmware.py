"""Firmware admission/probe fixtures; these never establish hardware support."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mirabox-python/src'))
from hellgato_mirabox.models.n4_pro.firmware import firmware_policy, require_candidate_firmware
from hellgato_mirabox.protocol import ProtocolError
from hellgato_mirabox.transport.mirabox import MiraboxBackend, device_identity

FIRMWARE = 'V4.N4 Pro E.02.010'


class FirmwarePolicyTests(unittest.TestCase):
    def test_historical_baseline_only_never_claims_candidate_verification(self):
        policy = firmware_policy()
        self.assertEqual(policy['allowedFirmware'], [FIRMWARE])
        self.assertIs(policy['allowUnverifiedFirmware'], True)
        self.assertEqual(policy['supportStatus'], 'experimental')
        self.assertIs(policy['physicalHardwareVerified'], False)
        self.assertEqual(policy['evidenceKind'], 'historical-product-baseline-only')
        self.assertEqual(require_candidate_firmware(FIRMWARE), FIRMWARE)

    def test_unverified_versions_are_preserved_without_coercion(self):
        for value in [FIRMWARE.lower(), FIRMWARE + ' ', 'unknown',
                      'V4.N4 Pro E.02.011', 'V4.N4 Pro.02.010']:
            with self.subTest(firmware=value):
                self.assertEqual(require_candidate_firmware(value), value)

    def test_missing_malformed_or_oversized_firmware_is_rejected(self):
        for value in [None, '', ' ', 123, [], {}, FIRMWARE + '\0', 'x' * 129]:
            with self.subTest(firmware=value), self.assertRaises(ProtocolError) as caught:
                require_candidate_firmware(value)
            self.assertEqual(caught.exception.code, 'ERR_FIRMWARE_UNVERIFIED')

    def test_missing_corrupt_duplicate_or_promoted_policy_fails_closed(self):
        policy = firmware_policy()
        cases = [b'{', b'{"schemaVersion":1,"schemaVersion":1}', b'x' * 16385]
        for key, value in [('physicalHardwareVerified', True), ('supportStatus', 'project-supported'),
                           ('allowedFirmware', ['*']), ('allowedFirmware', [FIRMWARE, FIRMWARE]),
                           ('allowUnverifiedFirmware', False),
                           ('schemaVersion', True), ('extra', True)]:
            cases.append(json.dumps({**policy, key: value}).encode())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'firmware-policy.json'
            with patch('hellgato_mirabox.models.n4_pro.firmware.POLICY_PATH', path):
                for content in [None, *cases]:
                    if content is not None:
                        path.write_bytes(content)
                    with self.assertRaises(ProtocolError) as caught:
                        firmware_policy()
                    self.assertEqual(caught.exception.code, 'ERR_FIRMWARE_POLICY')


class ProbeTransport:
    def __init__(self, events):
        self.events = events
        self._handle = None
        self._is_open = False
        self.firmware = FIRMWARE

    def open(self, path):
        self.events.append('transport-open')
        self._handle = 123
        self._is_open = True
        return True

    def get_firmware_version(self):
        self.events.append('firmware-read')
        return self.firmware


class FirmwareProbeTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.transport = ProbeTransport(self.events)
        self.device = type('StreamDockN4Pro', (SimpleNamespace,), {})(
            path='usb-path', serial_number='private-serial', vendor_id=123, product_id=456,
            firmware_version='', transport=self.transport, init=Mock(), open=Mock(), set_device=Mock())
        self.backend = MiraboxBackend.__new__(MiraboxBackend)
        self.backend.device = self.backend.probe_device = None
        self.backend.temporary = self.backend.display = None
        self.backend.candidates = {}
        self.backend.lock = threading.RLock()
        self.backend.logo_path = None
        self.backend.ownership = Mock()
        self.backend.manager = lambda: SimpleNamespace(enumerate=lambda: [self.device])
        self.destroy_result = 0
        def destroy(handle):
            self.assertEqual(handle, 123)
            self.events.append('checked-destroy')
            return self.destroy_result
        native = SimpleNamespace(transport_destroy=Mock(side_effect=destroy))
        native_patch = patch.object(sys.modules[__name__], '_transport_lib', native, create=True)
        native_patch.start()
        self.addCleanup(native_patch.stop)

    def test_discovery_probes_without_starting_device_or_display(self):
        result = self.backend.discover()
        self.assertEqual(self.events, ['transport-open', 'firmware-read', 'checked-destroy'])
        self.assertEqual(result[0]['firmware'], FIRMWARE)
        self.assertIsNone(self.transport._handle)
        self.assertFalse(self.transport._is_open)
        self.assertIsNone(self.backend.probe_device)
        self.device.init.assert_not_called()
        self.device.open.assert_not_called()
        self.device.set_device.assert_not_called()
        self.backend.ownership.close.assert_not_called()

    def test_unverified_firmware_is_discovered_without_device_initialization(self):
        self.transport.firmware = 'V4.N4 Pro.02.011'
        result = self.backend.discover()
        self.assertEqual(result[0]['firmware'], self.transport.firmware)
        self.assertEqual(self.device.firmware_version, self.transport.firmware)
        self.device.init.assert_not_called()
        self.device.open.assert_not_called()

    def test_empty_firmware_closes_probe_and_exposes_no_candidates(self):
        for firmware in ['', ' ']:
            self.events.clear()
            self.transport.firmware = firmware
            self.backend.candidates = {'stale': self.device}
            with self.assertRaises(ProtocolError) as caught:
                self.backend.discover()
            self.assertEqual(caught.exception.code, 'ERR_FIRMWARE_UNVERIFIED')
            self.assertEqual(self.events, ['transport-open', 'firmware-read', 'checked-destroy'])
            self.assertEqual(self.backend.candidates, {})
            self.assertIsNone(self.backend.probe_device)

    def test_expired_probe_closes_without_read_or_configuration(self):
        checks = 0
        def deadline():
            nonlocal checks
            checks += 1
            if checks == 3:
                raise ProtocolError('Expired', 'ERR_REQUEST_TIMEOUT')
        with self.assertRaises(ProtocolError) as caught:
            self.backend.discover(deadline)
        self.assertEqual(caught.exception.code, 'ERR_REQUEST_TIMEOUT')
        self.assertEqual(self.events, ['transport-open', 'checked-destroy'])
        self.assertIsNone(self.backend.probe_device)

    def test_failed_probe_close_retains_handle_mutex_and_blocks_another_open(self):
        self.destroy_result = 5
        with self.assertRaises(ProtocolError) as caught:
            self.backend.discover()
        self.assertEqual(caught.exception.code, 'ERR_DEVICE_CLOSE')
        self.assertIs(self.backend.probe_device, self.device)
        self.assertEqual(self.transport._handle, 123)
        self.assertTrue(self.transport._is_open)
        with self.assertRaises(ProtocolError):
            self.backend.release_ownership()
        with self.assertRaises(ProtocolError):
            self.backend.discover()
        self.assertEqual(self.events.count('transport-open'), 1)
        self.backend.ownership.close.assert_not_called()
        self.destroy_result = 0
        self.backend.close()
        self.assertIsNone(self.backend.probe_device)
        self.backend.release_ownership()
        self.backend.ownership.close.assert_called_once()

    def test_deadline_after_firmware_read_closes_probe_and_commits_no_candidates(self):
        checks = 0
        def deadline():
            nonlocal checks
            checks += 1
            if checks == 4:
                raise ProtocolError('Expired', 'ERR_REQUEST_TIMEOUT')
        with self.assertRaises(ProtocolError) as caught:
            self.backend.discover(deadline)
        self.assertEqual(caught.exception.code, 'ERR_REQUEST_TIMEOUT')
        self.assertEqual(self.events, ['transport-open', 'firmware-read', 'checked-destroy'])
        self.assertEqual(self.backend.candidates, {})
        self.assertIsNone(self.backend.probe_device)
        self.device.set_device.assert_not_called()

    def test_failed_native_probe_open_never_reads_or_initializes(self):
        self.transport.open = Mock(return_value=False)
        with self.assertRaises(ProtocolError) as caught:
            self.backend.discover()
        self.assertEqual(caught.exception.code, 'ERR_DEVICE_OPEN')
        self.assertEqual(self.events, [])
        self.assertIsNone(self.backend.probe_device)
        self.assertEqual(self.backend.candidates, {})
        self.device.init.assert_not_called()

    def test_missing_checked_destroy_symbol_keeps_probe_handle_owned(self):
        native = sys.modules[__name__]._transport_lib
        destroy = native.transport_destroy
        native.transport_destroy = None
        try:
            with self.assertRaises(ProtocolError) as caught:
                self.backend.discover()
            self.assertEqual(caught.exception.code, 'ERR_DEVICE_CLOSE')
            self.assertEqual(self.transport._handle, 123)
            with self.assertRaises(ProtocolError):
                self.backend.release_ownership()
        finally:
            native.transport_destroy = destroy
            self.backend.close()

    def test_corrupt_policy_refuses_before_any_native_access(self):
        with patch('hellgato_mirabox.transport.mirabox.firmware_policy',
                   side_effect=ProtocolError('Invalid', 'ERR_FIRMWARE_POLICY')):
            with self.assertRaises(ProtocolError):
                self.backend.discover()
        self.assertEqual(self.events, [])

    def test_open_rejects_cached_unknown_firmware_before_device_mutation(self):
        identifier = device_identity(self.device)
        self.backend.candidates = {identifier: self.device}
        with self.assertRaises(ProtocolError) as caught:
            self.backend.open(identifier, Mock())
        self.assertEqual(caught.exception.code, 'ERR_FIRMWARE_UNVERIFIED')
        self.assertIsNone(self.backend.device)
        self.device.set_device.assert_not_called()
        self.device.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()

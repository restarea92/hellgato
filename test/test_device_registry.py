import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from device_registry import DeviceIdentityConflict, DeviceRegistry
from settings import load_settings


def device(serial='N4-A', path=b'usb-port-a', vendor_id=1, product_id=2):
    return SimpleNamespace(serial_number=serial, path=path,
                           vendor_id=vendor_id, product_id=product_id)


class DeviceRegistryTests(unittest.TestCase):
    def test_reading_empty_history_does_not_create_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'not-created'
            self.assertEqual(DeviceRegistry(root, 3).devices(), [])
            self.assertFalse(root.exists())

    def test_first_connection_adopts_saved_identity_without_modifying_settings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            original = '{"version": 1, "identity": 23, "dockIdentity": 7, "stripMode": "frame"}\n'
            path.write_text(original, encoding='utf-8')
            settings = load_settings(path)
            registry = DeviceRegistry(directory, settings['identity'], settings['dockIdentity'])
            prepared = registry.prepare(device())
            self.assertFalse(registry.path.exists())
            row = registry.remember(prepared)
            self.assertEqual(row['virtualSerial'], 'HGMOCK000023')
            self.assertEqual(row['physicalSerial'], 'N4-A')
            self.assertEqual(row['dockIdentity'], 7)
            self.assertEqual(path.read_text(encoding='utf-8'), original)
            self.assertNotIn('connected', json.loads(registry.path.read_text())['devices'][0])

    def test_same_serial_survives_restart_and_usb_port_change(self):
        with TemporaryDirectory() as directory:
            first = DeviceRegistry(directory, 3)
            with patch('device_registry._timestamp', return_value='2026-10-12T01:00:00+00:00'):
                row = first.remember(first.prepare(device()))
            second = DeviceRegistry(directory, 3)
            with patch('device_registry._timestamp', return_value='2026-10-13T02:00:00+00:00'):
                restored = second.remember(second.prepare(device(path=b'usb-port-b')))
            self.assertEqual(restored['id'], row['id'])
            self.assertEqual(restored['firstSeen'], row['firstSeen'])
            self.assertEqual(restored['lastSeen'], '2026-10-13T02:00:00+00:00')
            self.assertEqual(restored['virtualSerial'], row['virtualSerial'])
            self.assertEqual(len(second.devices()), 1)
            self.assertFalse(second.devices()[0]['connected'])
            self.assertTrue(second.devices(row['id'])[0]['connected'])

    def test_replacement_cannot_inherit_registered_identity_after_restart(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            expected = registry.remember(registry.prepare(device()))
            original = registry.path.read_bytes()
            restarted = DeviceRegistry(directory, 3)
            with self.assertRaises(DeviceIdentityConflict) as raised:
                restarted.prepare(device(serial='N4-B'))
            self.assertEqual(raised.exception.expected, expected)
            self.assertEqual(registry.path.read_bytes(), original)

    def test_physical_device_cannot_be_reassigned_to_changed_bridge_or_dock(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3, 7)
            registry.remember(registry.prepare(device()))
            for identity, dock in ((4, 7), (3, 8)):
                with self.subTest(identity=identity, dock=dock):
                    with self.assertRaises(DeviceIdentityConflict):
                        DeviceRegistry(directory, identity, dock).prepare(device())

    def test_serial_is_scoped_to_hardware_model_identifiers(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            registry.remember(registry.prepare(device()))
            with self.assertRaises(DeviceIdentityConflict):
                registry.prepare(device(product_id=4))

    def test_no_serial_uses_explicit_path_identity_without_claiming_other_ports(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            row = registry.remember(registry.prepare(device(serial='')))
            self.assertIsNone(row['physicalSerial'])
            self.assertEqual(row['identitySource'], 'usb-path')
            restarted = DeviceRegistry(directory, 3)
            self.assertEqual(restarted.prepare(device(serial=None, path='usb-port-a'))['id'], row['id'])
            with self.assertRaises(DeviceIdentityConflict):
                restarted.prepare(device(serial=None, path=b'usb-port-b'))
            with self.assertRaises(ValueError):
                restarted.prepare(device(serial=None, path=None))

    def test_history_keeps_distinct_assigned_serials_disconnected_by_default(self):
        with TemporaryDirectory() as directory:
            first = DeviceRegistry(directory, 3)
            a = first.remember(first.prepare(device()))
            second = DeviceRegistry(directory, 4)
            b = second.remember(second.prepare(device(serial='N4-B')))
            rows = DeviceRegistry(directory, 4).devices(b['id'])
            self.assertEqual([row['virtualSerial'] for row in rows], ['HGMOCK000003', 'HGMOCK000004'])
            self.assertEqual([row['connected'] for row in rows], [False, True])
            rows[0]['name'] = 'Changed outside registry'
            self.assertEqual(second.devices()[0]['name'], a['name'])

    def test_failed_atomic_replace_preserves_file_and_in_memory_history(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            row = registry.remember(registry.prepare(device()))
            original = registry.path.read_bytes()
            with patch('device_registry._timestamp', return_value='2099-01-01T00:00:00+00:00'), \
                    patch.object(Path, 'replace', side_effect=OSError('write denied')):
                with self.assertRaises(OSError):
                    registry.remember(row)
            self.assertEqual(registry.path.read_bytes(), original)
            self.assertEqual(registry.devices()[0]['lastSeen'], row['lastSeen'])
            self.assertEqual(list(Path(directory).glob('devices-*.tmp')), [])

    def test_invalid_registry_is_preserved(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'devices.json'
            for contents in ('{broken', '{"version": 2, "devices": []}',
                             '{"version": 1, "devices": [null]}'):
                with self.subTest(contents=contents):
                    path.write_text(contents, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        DeviceRegistry(directory, 3)
                    self.assertEqual(path.read_text(encoding='utf-8'), contents)

    def test_duplicate_assignment_or_persisted_connection_state_is_rejected(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            row = registry.remember(registry.prepare(device()))
            invalid_rows = ([row, row], [{**row, 'connected': True}],
                            [{**row, 'virtualSerial': 'HGMOCK000004'}])
            for rows in invalid_rows:
                with self.subTest(rows=rows):
                    content = json.dumps({'version': 1, 'devices': rows})
                    registry.path.write_text(content, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        DeviceRegistry(directory, 3)
                    self.assertEqual(registry.path.read_text(encoding='utf-8'), content)


if __name__ == '__main__':
    unittest.main()

"""Explicit host-exit release metadata, without USB or native SDK access."""
import io
from importlib import import_module
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mirabox-python/src'))
ProtocolError = import_module('hellgato_mirabox.protocol').ProtocolError
fake = import_module('hellgato_mirabox.transport.fake')
FakeBackend, DEVICE_ID = fake.FakeBackend, fake.DEVICE_ID
MiraboxBackend = import_module('hellgato_mirabox.transport.mirabox').MiraboxBackend
Worker = import_module('hellgato_mirabox.worker').Worker


class HostReleaseTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.worker = Worker(self.backend, io.BytesIO(), io.BytesIO(), 'test-build')
        self.number = 0
        self.request('hello', protocol={'major': 1, 'minor': 0})
        self.request('open', deviceId=DEVICE_ID, generation=1)

    def request(self, method, **params):
        self.number += 1
        return self.worker.dispatch({'jsonrpc': '2.0', 'id': str(self.number), 'method': method,
                                     'params': {'deadlineAt': int(time.time() * 1000) + 3000, **params}})

    def test_explicit_host_exit_requests_notification_but_ordinary_close_does_not(self):
        with patch.object(self.backend, 'close', wraps=self.backend.close) as close:
            self.request('close', deviceId=DEVICE_ID, generation=1)
            close.assert_called_once_with()
            self.request('open', deviceId=DEVICE_ID, generation=2)
            close.reset_mock()
            self.request('close', deviceId=DEVICE_ID, generation=2, releaseReason='host-exit')
            close.assert_called_once_with(notify=True)
        self.assertIsNone(self.worker.current)

    def test_unknown_reason_or_stale_generation_cannot_release_the_device(self):
        with patch.object(self.backend, 'close', wraps=self.backend.close) as close:
            for reason in ('usb-disconnected', 'private-text', True, 1, [], {}):
                with self.subTest(reason=reason), self.assertRaises(ProtocolError):
                    self.request('close', deviceId=DEVICE_ID, generation=1, releaseReason=reason)
            with self.assertRaises(ProtocolError):
                self.request('close', deviceId=DEVICE_ID, generation=0, releaseReason='host-exit')
            close.assert_not_called()
            self.assertEqual(self.worker.current, DEVICE_ID)

    def test_physical_backend_forwards_only_the_explicit_notify_choice(self):
        for notify in (False, True):
            backend = object.__new__(MiraboxBackend)
            device = Mock()
            device.heartbeat_thread = None
            backend.device, backend.display = device, Mock()
            backend.lock, backend.temporary = threading.RLock(), None
            backend.original_decode, backend.original_heartbeat = Mock(), Mock()
            with patch.object(backend, '_close_probe'), patch(
                    'hellgato_mirabox.transport.mirabox.close_device', return_value=True) as close:
                if notify:
                    backend.close(notify=True)
                else:
                    backend.close()
            self.assertEqual(close.call_args.kwargs['notify'], notify)
            self.assertIsNone(backend.device)


if __name__ == '__main__':
    unittest.main()

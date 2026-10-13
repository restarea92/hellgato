import io
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import hellgato
from i18n import Message
from presence import HostProcess
from startup_status import RECORD, StartupStatus, decode_status


class StartupLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        change = patch.object(hellgato, 'STATE', self.state)
        change.start()
        self.addCleanup(change.stop)
        self.session = hellgato.Session(queue.Queue())
        self.host = HostProcess(31, 12345, hellgato.default_executable())

    def test_status_rejects_reused_pid_and_partial_ready_state(self):
        data = RECORD.pack(b'HGREADY1', 1, 31, 12345, 2, 0, 1, 2, 3)
        self.assertTrue(decode_status(data, self.host).ready)
        with self.assertRaises(ValueError):
            decode_status(data, HostProcess(31, 54321, self.host.executable))
        self.assertFalse(decode_status(RECORD.pack(b'HGREADY1', 1, 31, 12345, 2, 0, 1, 2, 0), self.host).ready)

    def run_once(self, host, status=None):
        presence = Mock()
        presence.host.return_value = host
        self.session.stop.wait = Mock(side_effect=lambda seconds: self.session.stop.set())
        with patch.object(hellgato, 'installed', return_value=True), \
                patch.object(hellgato, 'SystemPresence', return_value=presence), \
                patch.object(hellgato, 'read_status', return_value=status), \
                patch('settings.load_settings'), \
                patch.object(hellgato.subprocess, 'Popen') as popen, \
                patch.object(hellgato.subprocess, 'run') as run:
            self.session.run()
        popen.assert_not_called()
        run.assert_not_called()

    def test_no_host_waits_without_launching_any_process(self):
        self.run_once(None)
        messages = list(self.session.events.queue)
        self.assertIn(Message('detail.host_waiting'), messages)

    def test_existing_unprepared_host_requires_user_reopen_without_late_patch(self):
        self.run_once(self.host)
        self.assertIn(Message('detail.startup_reopen'), list(self.session.events.queue))

    def test_incompatible_preparation_never_connects_usb(self):
        self.run_once(self.host, StartupStatus(3, 4, 1, 0, 0))
        self.assertEqual(self.session.phase, 'error')

    def test_missing_install_offers_setup_without_automatic_elevation(self):
        with patch.object(hellgato, 'installed', return_value=False), \
                patch('settings.load_settings'), patch.object(hellgato.subprocess, 'run') as run:
            self.session.run()
        self.assertEqual(self.session.phase, 'setup')
        run.assert_not_called()

    def test_host_exit_stops_only_worker_and_usb_events_do_not_restart_it(self):
        presence = Mock()
        presence.host.side_effect = [self.host, None]
        self.session.stop.wait = Mock(return_value=False)
        child = Mock()
        child.stdout = io.StringIO('\n'.join(__import__('json').dumps({'event': event}) for event in
                                           ['bridgeReady', 'deviceReady', 'child.clientConnected',
                                            'deviceDisconnected', 'deviceReconnected']))
        child.poll.return_value = None
        self.session.prepare_native_ui = Mock(return_value=True)
        with patch.object(hellgato.subprocess, 'Popen', return_value=child) as popen, \
                patch.object(hellgato.subprocess, 'run') as run:
            self.assertTrue(self.session.run_bridge(self.host, presence, {}))
        self.assertEqual(popen.call_count, 1)
        self.assertIn('bridge.py', str(popen.call_args.args[0]))
        # The SDK writes rotated image files relative to the worker directory.
        self.assertEqual(Path(popen.call_args.kwargs['cwd']), self.state)
        child.wait.assert_called_once_with(timeout=12)
        run.assert_not_called()
        self.assertTrue(self.session.stop_file.exists())

    def test_usb_disconnect_keeps_network_connection_and_waits_for_hardware(self):
        self.session.connected.set()
        self.session.device_ready.set()
        self.session.log_bridge(io.StringIO('{"event":"deviceDisconnected"}\n'), io.StringIO())
        self.assertTrue(self.session.connected.is_set())
        self.assertFalse(self.session.device_ready.is_set())
        self.assertEqual(self.session.phase, 'waiting')


if __name__ == '__main__':
    unittest.main()

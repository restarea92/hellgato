from pathlib import Path
import queue
import sys
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from hellgato import INCOMPATIBLE, Session, WindowLifecycle
from i18n import Message
from presence import HostAmbiguousError
import hellgato


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.window, self.tray, self.status = Mock(), Mock(), Mock()
        self.session = Mock(stop=threading.Event())
        self.session.thread.is_alive.return_value = True
        self.ready = threading.Event()
        self.transfer = Mock(return_value=False)
        self.lifecycle = WindowLifecycle(self.window, self.session, self.tray,
                                         self.ready, self.transfer, self.status)

    def test_x_hides_and_open_restores_without_stopping_or_restarting_bridge(self):
        self.ready.set()
        self.lifecycle.hide()
        self.window.withdraw.assert_called_once()
        self.assertFalse(self.session.stop.is_set())
        self.tray.stop.assert_not_called()
        self.lifecycle.show()
        self.window.deiconify.assert_called_once()
        self.session.start.assert_not_called()
        self.window.destroy.assert_not_called()

    def test_missing_tray_keeps_window_reachable(self):
        self.lifecycle.hide()
        self.window.iconify.assert_called_once()
        self.window.withdraw.assert_not_called()
        self.assertFalse(self.session.stop.is_set())

    def test_exit_waits_for_bridge_and_transfer_then_removes_tray(self):
        self.lifecycle.close()
        self.lifecycle.close()
        self.assertTrue(self.session.stop.is_set())
        self.window.after.assert_called_once()
        self.window.destroy.assert_not_called()
        self.session.thread.is_alive.return_value = False
        self.transfer.return_value = True
        self.lifecycle.finish()
        self.window.destroy.assert_not_called()
        self.transfer.return_value = False
        self.lifecycle.finish()
        self.tray.stop.assert_called_once()
        self.window.destroy.assert_called_once()
        self.lifecycle.show()
        self.window.deiconify.assert_not_called()

    def test_compatibility_error_is_separate_from_runtime_errors(self):
        events = queue.Queue()
        session = Session(events)
        session.startup_failed(INCOMPATIBLE)
        self.assertEqual(events.get_nowait(), Message('error.incompatible'))
        self.assertEqual(events.get_nowait(), ('error', Message('error.incompatible')))
        session.startup_failed(1)
        self.assertEqual(events.get_nowait(), Message('error.startup'))

    def run_host_sequence(self, hosts, readiness):
        session = Session(queue.Queue())
        session.stop = Mock()
        session.stop.wait.side_effect = [False] * len(hosts) + [True]
        session.shutdown_bridge = Mock()
        session.prepare_native_ui = Mock(return_value=True)
        process = Mock(stdout=[])
        process.poll.return_value = None
        reader = Mock()
        reader.start.side_effect = session.bridge_ready.set
        presence = Mock()
        presence.host.side_effect = hosts
        with TemporaryDirectory() as directory, patch.object(hellgato, 'STATE', Path(directory)), \
                patch.object(hellgato, 'default_executable', return_value=Path('known.exe')), \
                patch.object(hellgato, 'read_status', side_effect=readiness) as inspect, \
                patch.object(hellgato.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(hellgato.threading, 'Thread', return_value=reader), \
                patch.object(hellgato.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            session.stop_file = Path(directory) / 'bridge.stop'
            result = session.run_bridge(SimpleNamespace(pid=1, created='old', executable=Path('known.exe')),
                                        presence, {})
        return result, session, spawn, inspect

    def test_streamdeck_restart_keeps_listener_and_reload_native_ui_for_new_prepared_host(self):
        old = SimpleNamespace(pid=1, created='old', executable=Path('known.exe'))
        new = SimpleNamespace(pid=2, created='new', executable=Path('known.exe'))
        result, session, spawn, inspect = self.run_host_sequence(
            [old, None, new, new, new],
            [None, SimpleNamespace(state=1, ready=False), SimpleNamespace(state=2, ready=True)])
        self.assertTrue(result)
        spawn.assert_called_once()
        session.shutdown_bridge.assert_called_once()
        self.assertEqual([call.args[0] for call in session.prepare_native_ui.call_args_list], [1, 2])
        self.assertEqual([call.args[0] for call in inspect.call_args_list], [new, new, new])
        self.assertEqual([call.args[0] for call in session.bridge.stdin.write.call_args_list],
                         ['{"command":"hostUnavailable"}\n', '{"command":"hostAvailable"}\n'])
        self.assertEqual(spawn.call_args.kwargs['stdin'], hellgato.subprocess.PIPE)

    def test_new_rejected_host_never_loads_native_ui(self):
        new = SimpleNamespace(pid=2, created='new', executable=Path('known.exe'))
        result, session, spawn, inspect = self.run_host_sequence([None, new], [SimpleNamespace(state=3, ready=False)])
        self.assertFalse(result)
        spawn.assert_called_once()
        session.prepare_native_ui.assert_not_called()
        session.shutdown_bridge.assert_called_once()
        self.assertEqual(session.phase, 'error')
        inspect.assert_called_once_with(new)

    def test_new_unknown_executable_is_rejected_before_readiness_or_native_ui(self):
        new = SimpleNamespace(pid=2, created='new', executable=Path('unknown.exe'))
        with self.assertRaisesRegex(RuntimeError, 'different installation'):
            self.run_host_sequence([new], [])

    def test_presence_read_error_does_not_pause_usb_but_confirmed_absence_does(self):
        old = SimpleNamespace(pid=1, created='old', executable=Path('known.exe'))
        with self.assertLogs(level='WARNING'):
            result, session, _, inspect = self.run_host_sequence([old, OSError('snapshot unavailable'), old, None, None], [])
        self.assertTrue(result)
        self.assertEqual([call.args[0] for call in session.bridge.stdin.write.call_args_list],
                         ['{"command":"hostUnavailable"}\n'])
        inspect.assert_not_called()

    def test_uri_forwarding_duplicate_keeps_only_the_existing_prepared_host(self):
        old = SimpleNamespace(pid=1, created='old', executable=Path('known.exe'))
        forwarding = SimpleNamespace(pid=2, created='forwarding', executable=Path('known.exe'))
        result, session, spawn, inspect = self.run_host_sequence(
            [old, HostAmbiguousError([old, forwarding]), old], [])
        self.assertTrue(result)
        spawn.assert_called_once()
        inspect.assert_not_called()
        session.bridge.stdin.write.assert_not_called()
        self.assertEqual([call.args[0] for call in session.prepare_native_ui.call_args_list], [1])

    def test_duplicate_new_hosts_pause_the_absent_old_host_until_one_remains(self):
        new = SimpleNamespace(pid=2, created='new', executable=Path('known.exe'))
        duplicate = SimpleNamespace(pid=3, created='other', executable=Path('known.exe'))
        result, session, spawn, inspect = self.run_host_sequence(
            [HostAmbiguousError([new, duplicate]), HostAmbiguousError([new, duplicate]), new],
            [SimpleNamespace(state=2, ready=True)])
        self.assertTrue(result)
        spawn.assert_called_once()
        inspect.assert_called_once_with(new)
        self.assertEqual([call.args[0] for call in session.bridge.stdin.write.call_args_list],
                         ['{"command":"hostUnavailable"}\n', '{"command":"hostAvailable"}\n'])
        self.assertEqual([call.args[0] for call in session.prepare_native_ui.call_args_list], [2])


if __name__ == '__main__':
    unittest.main()

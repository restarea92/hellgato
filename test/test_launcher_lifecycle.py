from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from hellgato import INCOMPATIBLE, Session, WindowLifecycle
from i18n import Message


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


if __name__ == '__main__':
    unittest.main()

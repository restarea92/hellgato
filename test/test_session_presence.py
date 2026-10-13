"""The launcher observes user-owned Stream Deck lifetime and only owns its workers."""

import io
import ctypes
from pathlib import Path
import queue
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import hellgato
from presence import HostProcess, SystemPresence, streamdeck_processes


HOST = HostProcess(100, 200, Path('C:/StreamDeck/StreamDeck.exe'))
USB = {'path': b'n4-usb'}


class InlineReader:
    def __init__(self, target, args, **unused):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)

    def join(self, **unused):
        pass


class Worker:
    def __init__(self, pid, returncode=None):
        self.pid, self.returncode = pid, returncode
        self.stdout = io.StringIO('{"event":"deviceReady"}\n')
        self.waits = 0

    def poll(self):
        return self.returncode

    def wait(self, **unused):
        self.waits += 1
        self.returncode = 0
        return 0


class Presence:
    def __init__(self, session, states):
        self.session, self.states = session, iter(states)
        self.current_host = None

    def devices(self):
        try:
            devices, self.current_host = next(self.states)
        except StopIteration:
            self.session.stop.set()
            return []
        return devices

    def host(self):
        return self.current_host


class SessionPresenceTests(unittest.TestCase):
    def run_states(self, states, *, preparation_codes=(), cancel_preparation=False, exit_bridge=False,
                   native_error=None):
        with TemporaryDirectory() as directory, patch.object(hellgato, 'STATE', Path(directory)):
            state = Path(directory)
            (state / 'native-ui.json').write_text('{}', encoding='utf-8')
            session = hellgato.Session(queue.Queue(), poll_interval=0)
            session.presence = Presence(session, states)
            workers, prepared, bridges = [], [], []
            codes = iter(preparation_codes)

            def popen(arguments, **unused):
                self.assertNotIn('StreamDeck.exe', arguments[0])
                self.assertNotIn('taskkill', arguments)
                if 'start-streamdeck.py' in Path(arguments[1]).name:
                    self.assertIn('--pid', arguments)
                    prepared.append(arguments)
                    worker = Worker(1000 + len(workers), next(codes, 0))
                    if cancel_preparation:
                        session.stop.set()
                else:
                    self.assertEqual(Path(arguments[1]).name, 'bridge.py')
                    worker = Worker(1000 + len(workers), 10 if exit_bridge and not bridges else None)
                    bridges.append(worker)
                workers.append(worker)
                return worker

            with patch.object(hellgato.subprocess, 'Popen', side_effect=popen), \
                    patch.object(hellgato.subprocess, 'run', return_value=Mock(returncode=0),
                                 side_effect=native_error) as native, \
                    patch.object(hellgato.threading, 'Thread', InlineReader), \
                    patch('settings.load_settings', return_value={}):
                session.run()
            return session, prepared, bridges, native.call_args_list

    def test_usb_first_waits_for_user_to_open_host_and_does_not_relaunch_on_exit(self):
        session, prepared, bridges, _ = self.run_states([
            ([USB], None), ([USB], None), ([USB], HOST), ([USB], None), ([USB], None)])
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(bridges), 1)
        self.assertEqual(bridges[0].waits, 1)
        self.assertIsNone(session.prepared_host)

    def test_host_first_waits_for_usb(self):
        _, prepared, bridges, _ = self.run_states([([], HOST), ([], HOST), ([USB], HOST)])
        self.assertEqual((len(prepared), len(bridges)), (1, 1))

    def test_usb_reconnect_retains_host_preparation_and_refreshes_native_ui_token(self):
        _, prepared, bridges, native = self.run_states([
            ([USB], HOST), ([], HOST), ([USB], HOST)])
        self.assertEqual((len(prepared), len(bridges), len(native)), (1, 2, 2))

    def test_same_pid_new_creation_time_is_prepared_again(self):
        replacement = HostProcess(HOST.pid, HOST.created + 1, HOST.executable)
        _, prepared, bridges, _ = self.run_states([([USB], HOST), ([USB], replacement)])
        self.assertEqual((len(prepared), len(bridges)), (2, 2))

    def test_path_change_releases_previous_device(self):
        _, prepared, bridges, _ = self.run_states([([USB], HOST), ([{'path': b'other'}], HOST)])
        self.assertEqual((len(prepared), len(bridges)), (1, 2))
        self.assertEqual(bridges[0].waits, 1)

    def test_reopened_bridge_refreshes_controls_for_same_host(self):
        _, prepared, bridges, native = self.run_states([
            ([USB], HOST), ([USB], HOST)], exit_bridge=True)
        self.assertEqual((len(prepared), len(bridges), len(native)), (1, 2, 2))

    def test_optional_native_ui_timeout_does_not_drop_the_device_bridge(self):
        with self.assertLogs(level='ERROR'):
            session, _, bridges, native = self.run_states([([USB], HOST), ([USB], HOST)],
                native_error=hellgato.subprocess.TimeoutExpired('native-ui', 20))
        self.assertEqual((len(bridges), len(native)), (1, 1))
        self.assertEqual(bridges[0].waits, 1)
        self.assertNotEqual(session.phase, 'error')

    def test_incompatible_host_does_not_open_usb(self):
        session, prepared, bridges, _ = self.run_states(
            [([USB], HOST)], preparation_codes=[hellgato.INCOMPATIBLE])
        self.assertEqual(len(prepared), 1)
        self.assertEqual(bridges, [])
        self.assertEqual(session.detail.key, 'error.incompatible')

    def test_host_initialization_retry_and_cancellation_do_not_launch_host(self):
        _, prepared, bridges, _ = self.run_states([
            ([USB], HOST), ([USB], HOST)], preparation_codes=[11, 0])
        self.assertEqual((len(prepared), len(bridges)), (2, 1))
        _, prepared, bridges, _ = self.run_states([([USB], HOST)], cancel_preparation=True)
        self.assertEqual((len(prepared), len(bridges)), (1, 0))

    def test_presence_deduplicates_usb_paths_and_rejects_multiple_hosts(self):
        reader = Mock(return_value=[USB, USB])
        presence = SystemPresence('.', process_reader=lambda: [HOST], usb_reader=reader,
                                  products={(1, 2), (3, 4)})
        self.assertEqual(presence.devices(), [USB])
        self.assertEqual(presence.host(), HOST)
        reader.assert_any_call(1, 2)
        presence.process_reader = lambda: [HOST, HOST]
        with self.assertRaises(RuntimeError):
            presence.host()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process snapshot')
    def test_process_query_exit_race_is_absence_but_access_denied_is_not(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = 1
        kernel.OpenProcess.return_value = 2

        def first(snapshot, entry):
            entry._obj.pid, entry._obj.name = HOST.pid, 'StreamDeck.exe'
            return 1

        def next_process(*unused):
            ctypes.set_last_error(18)
            return 0

        def denied_query(*unused):
            ctypes.set_last_error(5)
            return 0

        kernel.Process32FirstW.side_effect = first
        kernel.Process32NextW.side_effect = next_process
        kernel.QueryFullProcessImageNameW.side_effect = denied_query
        for exit_code in (0, 259):
            kernel.GetExitCodeProcess.side_effect = lambda handle, code: (setattr(code._obj, 'value', exit_code) or 1)
            with patch.object(ctypes, 'WinDLL', return_value=kernel):
                if exit_code == 0:
                    self.assertEqual(streamdeck_processes(), [])
                else:
                    with self.assertRaises(PermissionError):
                        streamdeck_processes()


if __name__ == '__main__':
    unittest.main()

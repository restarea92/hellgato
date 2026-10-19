"""Saved assets change in place without replacing the USB worker or action cache."""
from contextlib import nullcontext, redirect_stderr
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mirabox-python/src'))
from hellgato_mirabox.models.n4_pro.animation import NativeAnimation
from hellgato_mirabox.models.n4_pro.screensaver_config import ScreensaverConfig, read_config
from hellgato_mirabox.models.n4_pro.state import DisplayState


class ScreensaverReloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / 'screensaver.json'
        self.red = self.image('red')
        self.blue = self.image('blue')
        self.now = 20
        self.device = Mock()
        self.state = None

    def image(self, color):
        path = self.root / f'{color}.png'
        Image.new('RGB', (24, 24), color).save(path)
        return path

    def write(self, revision, path):
        pending = self.config.with_suffix('.tmp')
        pending.write_text(json.dumps({'version': 1, 'revision': revision,
                                      'path': str(path) if path is not None else None}), encoding='utf-8')
        pending.replace(self.config)

    def create(self, path=None, revision=0):
        self.write(revision, path)
        self.state = DisplayState(self.device, self.root, screensaver_path=path,
                                  screensaver_config=self.config, clock=lambda: self.now,
                                  lcd_mode='stream-screensaver')
        self.addCleanup(self.state.close)
        return self.state

    def wait(self, predicate):
        end = time.monotonic() + 3
        while not predicate():
            self.state.tick(lambda: None)
            if time.monotonic() > end:
                self.fail('Screensaver preparation did not settle')
            time.sleep(.005)

    def color(self):
        data = self.device.transport.set_background_frame_stream.call_args.args[0]
        with Image.open(BytesIO(data)) as image:
            return image.getpixel((400, 240))

    def test_revision_zero_consumes_the_initial_asset_without_decoding_it_again(self):
        state = self.create(self.red)
        state.screensaver_config.factory = Mock(side_effect=AssertionError('Duplicate initial decode'))
        state.tick(lambda: None)
        self.assertTrue(state.state()['screensaverReady'])
        self.assertEqual(state.state()['screensaverRevision'], 0)
        self.assertFalse(state.state()['screensaverLoading'])
        self.assertIsNone(state.state()['screensaverError'])
        state.screensaver_config.factory.assert_not_called()
        self.assertIsNone(state.screensaver_config.thread)

    def test_current_configuration_is_authoritative_when_only_the_usb_worker_restarts(self):
        self.write(7, self.blue)
        missing_initial = self.root / 'deleted-previous.png'
        state = DisplayState(self.device, self.root, screensaver_path=missing_initial,
                             screensaver_config=self.config)
        self.addCleanup(state.close)
        self.assertEqual(state.state()['screensaverRevision'], 7)
        state.control({'control': 'screensaver'}, lambda: None)
        self.assertGreater(self.color()[2], 240)
        state.screensaver_config.factory = Mock(side_effect=AssertionError('Duplicate restart decode'))
        state.tick(lambda: None)
        state.screensaver_config.factory.assert_not_called()

    def test_normal_action_state_is_untouched_while_the_asset_is_replaced(self):
        state = self.create(self.red)
        key = Image.new('RGB', (120, 120), 'lime')
        state.keys[0] = key
        state.strip.paste('yellow', (0, 0, 800, 100))
        background, strip, activity = state.background, state.strip.copy(), state.last_activity
        self.device.reset_mock()
        self.write(1, self.blue)
        self.wait(lambda: state.state()['screensaverRevision'] == 1)
        self.assertIs(state.keys[0], key)
        self.assertIs(state.background, background)
        self.assertEqual(state.strip.tobytes(), strip.tobytes())
        self.assertEqual(state.last_activity, activity)
        self.assertFalse(state.idle)
        self.assertEqual(self.device.mock_calls, [])

    def test_active_native_asset_restarts_immediately_without_discarding_the_action_cache(self):
        state = self.create(self.red)
        state.keys[1] = Image.new('RGB', (120, 120), 'lime')
        key = state.keys[1]
        state.control({'control': 'screensaver'}, lambda: None)
        self.assertGreater(self.color()[0], 240)
        self.write(1, self.blue)
        self.wait(lambda: state.state()['screensaverRevision'] == 1)
        self.assertGreater(self.color()[2], 240)
        self.assertTrue(state.native_active)
        self.assertTrue(state.idle)
        self.assertIs(state.keys[1], key)
        self.device.open.assert_not_called()

    def test_no_asset_to_native_wakes_idle_sleep_and_plays_the_prepared_asset(self):
        state = self.create()
        state.control({'control': 'screensaver'}, lambda: None)
        self.assertTrue(state.sleeping)
        self.write(1, self.blue)
        self.wait(lambda: state.state()['screensaverRevision'] == 1)
        self.assertTrue(state.native_active)
        self.assertFalse(state.sleeping)
        self.assertGreater(self.color()[2], 240)
        self.device.wakeScreen.assert_called_once()

    def test_removal_sleeps_until_a_fresh_stream_frame_arrives(self):
        state = self.create(self.red)
        state.control({'control': 'screensaver'}, lambda: None)
        state.streamed_screensaver = Image.new('RGB', (800, 480), 'lime')
        self.write(1, None)
        state.tick(lambda: None)
        self.assertEqual(state.state()['screensaverRevision'], 1)
        self.assertFalse(state.screensaver_ready)
        self.assertIsNone(state.streamed_screensaver)
        self.assertTrue(state.sleeping)
        state.output('lcd', Image.new('RGB', (800, 480), 'blue'), None, lambda: None)
        self.assertTrue(state.stream_active)
        self.assertFalse(state.sleeping)
        self.assertFalse(state.native_active)

    def test_failed_preparation_preserves_the_previous_asset_and_applied_revision(self):
        state = self.create(self.red)
        animation = state.animation
        invalid = self.root / 'invalid.png'
        invalid.write_bytes(b'invalid image')
        self.write(1, invalid)
        self.wait(lambda: state.state()['screensaverError'] == 'ERR_ASSET_INVALID')
        self.assertIs(state.animation, animation)
        self.assertTrue(state.screensaver_ready)
        self.assertEqual(state.state()['screensaverRevision'], 0)
        self.assertFalse(state.state()['screensaverLoading'])
        state.control({'control': 'screensaver'}, lambda: None)
        self.assertGreater(self.color()[0], 240)
        self.write(2, self.blue)
        self.wait(lambda: state.state()['screensaverRevision'] == 2)
        self.assertIsNone(state.state()['screensaverError'])

    def test_invalid_initial_hot_asset_uses_stream_fallback_without_failing_usb_open(self):
        missing = self.root / 'missing.png'
        state = self.create(missing)
        self.assertFalse(state.screensaver_ready)
        self.assertEqual(state.state()['screensaverError'], 'ERR_ASSET_INVALID')
        state.output('lcd', Image.new('RGB', (800, 480), 'blue'), None, lambda: None)
        self.assertTrue(state.stream_active)

    def test_latest_revision_cancels_older_preparation_and_only_one_loader_runs(self):
        state = self.create()
        started = threading.Event()
        paths = []

        class SlowFirst(NativeAnimation):
            def load(inner, path, check=lambda: None):
                paths.append(str(path))
                if str(path) == str(self.red):
                    started.set()
                    while True:
                        check()
                        time.sleep(.002)
                return super().load(path, check)

        state.screensaver_config.factory = SlowFirst
        self.write(1, self.red)
        state.tick(lambda: None)
        self.assertTrue(started.wait(1))
        first_thread = state.screensaver_config.thread
        self.assertFalse(first_thread.daemon)
        self.write(2, self.blue)
        self.wait(lambda: state.state()['screensaverRevision'] == 2)
        self.assertFalse(first_thread.is_alive())
        self.assertEqual(paths, [str(self.red), str(self.blue)])
        self.assertIsNone(state.state()['screensaverError'])
        state.control({'control': 'screensaver'}, lambda: None)
        self.assertGreater(self.color()[2], 240)

    def test_removal_cancels_a_pending_asset_and_close_joins_the_loader(self):
        state = self.create(self.red)
        started = threading.Event()

        class Waiting(NativeAnimation):
            def load(inner, path, check=lambda: None):
                started.set()
                while True:
                    check()
                    time.sleep(.002)

        state.screensaver_config.factory = Waiting
        self.write(1, self.blue)
        state.tick(lambda: None)
        self.assertTrue(started.wait(1))
        loader = state.screensaver_config.thread
        self.write(2, None)
        state.tick(lambda: None)
        self.assertEqual(state.state()['screensaverRevision'], 2)
        self.assertFalse(state.screensaver_ready)
        state.close()
        self.assertFalse(loader.is_alive())
        self.assertIsNone(state.screensaver_config.thread)
        self.assertFalse(state.state()['screensaverLoading'])

    def test_deadline_failure_keeps_the_previous_asset(self):
        state = self.create(self.red)
        started, release = threading.Event(), threading.Event()
        clock = [0]
        self.addCleanup(release.set)

        class Delayed(NativeAnimation):
            def load(inner, path, check=lambda: None):
                started.set()
                release.wait(1)
                check()
                return super().load(path, check)

        state.screensaver_config.clock = lambda: clock[0]
        state.screensaver_config.factory = Delayed
        old = state.animation
        self.write(1, self.blue)
        state.tick(lambda: None)
        self.assertTrue(started.wait(1))
        clock[0] = 31
        release.set()
        self.wait(lambda: state.state()['screensaverError'] == 'ERR_SCREENSAVER_TIMEOUT')
        self.assertIs(state.animation, old)
        self.assertEqual(state.state()['screensaverRevision'], 0)

    def test_malformed_config_does_not_cancel_an_accepted_load_and_same_revision_recovers(self):
        state = self.create(self.red)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        class Delayed(NativeAnimation):
            def load(inner, path, check=lambda: None):
                started.set()
                release.wait(1)
                check()
                return super().load(path, check)

        state.screensaver_config.factory = Delayed
        self.write(1, self.blue)
        state.tick(lambda: None)
        self.assertTrue(started.wait(1))
        self.config.write_bytes(b'{broken')
        state.tick(lambda: None)
        self.assertEqual(state.state()['screensaverError'], 'ERR_SCREENSAVER_CONFIG_INVALID')
        self.assertTrue(state.state()['screensaverLoading'])
        self.write(1, self.blue)
        state.tick(lambda: None)
        self.assertIsNone(state.state()['screensaverError'])
        release.set()
        self.wait(lambda: state.state()['screensaverRevision'] == 1)

    def test_older_and_reused_revisions_cannot_overwrite_newer_desired_state(self):
        state = self.create(self.red)
        self.write(2, self.blue)
        self.wait(lambda: state.state()['screensaverRevision'] == 2)
        current = state.animation
        self.write(1, self.red)
        state.tick(lambda: None)
        self.assertIs(state.animation, current)
        self.assertEqual(state.state()['screensaverRevision'], 2)
        self.write(2, self.red)
        state.tick(lambda: None)
        self.assertEqual(state.state()['screensaverError'], 'ERR_SCREENSAVER_CONFIG_INVALID')
        self.assertIs(state.animation, current)
        self.write(2, self.blue)
        state.tick(lambda: None)
        self.assertIsNone(state.state()['screensaverError'])

    def test_invalid_initial_config_falls_back_then_accepts_its_repaired_revision(self):
        self.config.write_bytes(b'{broken')
        self.state = DisplayState(self.device, self.root, screensaver_path=self.red, screensaver_config=self.config)
        self.addCleanup(self.state.close)
        self.assertTrue(self.state.screensaver_ready)
        self.assertEqual(self.state.state()['screensaverError'], 'ERR_SCREENSAVER_CONFIG_INVALID')
        self.write(0, self.blue)
        self.wait(lambda: self.state.state()['screensaverError'] is None and not self.state.state()['screensaverLoading'])
        self.state.control({'control': 'screensaver'}, lambda: None)
        self.assertGreater(self.color()[2], 240)

    def test_transient_file_read_failure_preserves_a_prepared_configuration(self):
        state = self.create(self.red)
        with patch('hellgato_mirabox.models.n4_pro.screensaver_config.read_config', side_effect=PermissionError('busy')):
            state.tick(lambda: None)
        self.assertTrue(state.screensaver_ready)
        self.assertEqual(state.state()['screensaverError'], 'ERR_SCREENSAVER_CONFIG_INVALID')
        state.tick(lambda: None)
        self.assertIsNone(state.state()['screensaverError'])

    def test_local_config_validation_is_bounded_and_rejects_invalid_revision_and_paths(self):
        valid = {'version': 1, 'revision': 0, 'path': str(self.red)}
        invalid = [{**valid, 'revision': -1}, {**valid, 'revision': True},
                   {**valid, 'revision': 9007199254740992}, {**valid, 'version': True},
                   {**valid, 'path': 'relative.png'}, {**valid, 'path': '\\\\server\\share\\image.png'},
                   {**valid, 'path': '//server/share/image.png'}, {**valid, 'extra': True},
                   {**valid, 'path': 7}]
        for value in invalid:
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_config(self.config)
        for data in (b' ' * 8193, b'{"version":1,"version":1,"revision":0,"path":null}', b'\xff'):
            self.config.write_bytes(data)
            with self.assertRaises((ValueError, UnicodeError)):
                read_config(self.config)
        with self.assertRaises(ValueError):
            ScreensaverConfig('relative.json', None)

    def test_backend_hot_reload_preserves_usb_generation_handle_and_callbacks(self):
        import test_mirabox_controls as mechanisms
        fixture = mechanisms.NativeLifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.write(0, self.red)
        backend = fixture.backend
        backend.screensaver_path = self.red
        backend.screensaver_config = self.config
        backend.lcd_mode = 'stream-screensaver'
        callback = Mock()
        backend.open('selected', callback)
        device, temporary, decode = backend.device, backend.temporary, backend.device.decode_input_event
        self.state = backend.display
        self.write(1, self.blue)
        self.wait(lambda: backend.get_state()['screensaverRevision'] == 1)
        backend.poll()
        self.assertIs(backend.device, device)
        self.assertIs(backend.temporary, temporary)
        self.assertIs(backend.device.decode_input_event, decode)
        fixture.device.open.assert_called_once()
        fixture.native_close.assert_not_called()
        self.assertTrue(backend.get_state()['connected'])

    def test_worker_cli_accepts_a_deleted_initial_asset_when_current_config_is_supplied(self):
        from hellgato_mirabox import worker
        self.write(7, self.blue)
        deleted = self.root / 'deleted-previous.png'
        backend = Mock(device=None)
        protocol_stdout = Mock()
        with patch('hellgato_mirabox.transport.mirabox.MiraboxBackend', return_value=backend) as factory, \
                patch.object(worker, 'Worker') as runtime, \
                patch.object(worker, 'physical_working_directory', return_value=nullcontext()), \
                patch.object(worker.os, 'dup', return_value=42), \
                patch.object(worker.os, 'fdopen', return_value=protocol_stdout), \
                patch.object(worker.os, 'dup2'), \
                patch.object(worker.signal, 'signal'):
            runtime.return_value.run.return_value = 0
            self.assertEqual(worker.main(['--sdk-path', str(self.root), '--screensaver-path', str(deleted),
                                         '--screensaver-config', str(self.config)]), 0)
            factory.assert_called_once_with(self.root, deleted, None, 'panels', 'normal',
                                            screensaver_config=self.config)
            runtime.return_value.run.assert_called_once()
        backend.release_ownership.assert_called_once()
        protocol_stdout.close.assert_called_once()

    def test_worker_cli_without_hot_configuration_still_rejects_a_missing_initial_asset(self):
        from hellgato_mirabox import worker
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as caught:
            worker.main(['--sdk-path', str(self.root), '--screensaver-path', str(self.root / 'missing.png')])
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()

"""Candidate worker physical mechanisms under native mocks, never hardware evidence."""
import base64
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mirabox-python/src'))
from hellgato_mirabox.models.n4_pro.state import DisplayState
from hellgato_mirabox.models.n4_pro.display import DisplayWriter
from hellgato_mirabox.models.n4_pro.animation import NativeAnimation
from hellgato_mirabox.models.n4_pro.model import descriptor
from hellgato_mirabox.protocol import ProtocolError, validate_contract
from hellgato_mirabox.transport.fake import FakeBackend, DEVICE_ID
from hellgato_mirabox.transport.mirabox import MiraboxBackend, device_identity, checked_led_brightness, led_supported
from hellgato_mirabox.transport.ownership import RuntimeOwnership, MUTEX_NAME
from hellgato_mirabox.transport.shutdown import close_device
from hellgato_mirabox.worker import Worker, physical_working_directory


def cancelled():
    raise ProtocolError('Cancelled', 'ERR_WORKER_STOPPED')


@dataclass
class InputEvent:
    event_type: str
    key: int | None = None
    state: int | None = None
    knob_id: str | None = None
    direction: str | None = None


class DisplayStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = 0
        self.device = Mock()
        self.led = Mock()
        self.state = self.make_state()

    def make_state(self, **kwargs):
        return DisplayState(self.device, self.temporary.name, led_writer=self.led,
                            clock=lambda: self.now, **kwargs)

    def control(self, control, **kwargs):
        self.state.control({'control': control, **kwargs}, lambda: None)

    def advance(self, seconds):
        self.now += seconds
        self.state.tick(lambda: None)

    def test_brightness_zero_preserves_desired_level_and_really_sleeps(self):
        self.control('brightness', value=35)
        self.control('brightness', value=0)
        self.control('brightness', value=0)
        self.assertTrue(self.state.sleeping)
        self.device.transport.sleep.assert_called_once()
        self.assertEqual(self.state.state()['brightness'], 35)
        self.assertEqual(self.led.call_args.args, (0,))
        self.control('wake')
        self.device.wakeScreen.assert_called_once()
        self.advance(.4)
        self.assertFalse(self.state.sleeping)
        self.assertEqual(self.state.screen_level, 35)
        self.assertEqual(self.led.call_args.args, (35,))
        self.assertEqual(self.state.state()['stateVerification'], 'unconfirmed')

    def test_knob_independent_level_and_follow_survive_sleep_and_fade(self):
        self.control('brightness', value=20)
        self.control('knobBrightness', value=65)
        self.assertEqual(self.led.call_args.args, (65,))
        self.control('sleep')
        self.control('knobBrightness', value=70)
        self.assertEqual(self.led.call_args.args, (0,))
        self.control('wake')
        self.advance(.4)
        self.assertEqual(self.led.call_args.args, (70,))
        self.assertEqual(self.state.screen_level, 20)
        self.control('followDisplayBrightness', value=True)
        self.assertEqual(self.led.call_args.args, (20,))
        self.control('brightness', value=30)
        self.assertEqual(self.led.call_args.args, (30,))
        self.control('followDisplayBrightness', value=False)
        self.control('brightness', value=50)
        self.assertEqual(self.led.call_args.args, (30,))

    def test_knob_capability_fails_explicitly_without_checked_writer(self):
        self.state.led_writer = None
        for kind, value in [('knobBrightness', 30), ('followDisplayBrightness', True)]:
            with self.assertRaises(ProtocolError) as caught:
                self.control(kind, value=value)
            self.assertEqual(caught.exception.code, 'ERR_UNSUPPORTED_OPERATION')
        self.control('brightness', value=20)
        self.assertEqual(self.state.brightness, 20)
        self.assertEqual(descriptor('test')['capabilities']['features']['knobBrightness']['status'], 'unsupported')
        validate_contract('DeviceDescriptor', descriptor('test', knob_supported=True))

    def test_idle_timeout_disable_and_key_release_sleep_sequence(self):
        self.control('sleepDuration', value=2)
        self.advance(1)
        self.assertFalse(self.state.sleeping)
        self.advance(1)
        self.assertTrue(self.state.sleeping)
        self.state.notify_input(self.now, True, lambda: None)
        self.control('sleepDuration', value=0)
        self.control('brightness', value=90)
        self.assertTrue(self.state.sleeping)
        self.control('wake')
        self.advance(4)
        self.assertFalse(self.state.sleeping)
        self.control('sleepDuration', value=0)
        self.control('brightness', value=40)
        self.assertFalse(self.state.sleeping)

    def test_frames_received_asleep_are_cached_and_restored_with_touch_regions(self):
        self.control('sleep')
        self.device.reset_mock()
        key = Image.new('RGB', (120, 120), 'red')
        strip = Image.new('RGB', (20, 10), 'blue')
        self.state.output('key:2', key, None, lambda: None)
        self.state.output('touch', strip, (199, 5, 20, 10), lambda: None)
        self.device.set_key_image.assert_not_called()
        self.device.set_seondscreen_image.assert_not_called()
        observed_keys, observed_panels = {}, {}
        def record_key(index, path):
            with Image.open(path) as image:
                observed_keys[index] = image.copy()
        def record_panel(index, path):
            with Image.open(path) as image:
                observed_panels[index] = image.copy()
        self.device.set_key_image.side_effect = record_key
        self.device.set_seondscreen_image.side_effect = record_panel
        self.control('wake')
        self.assertEqual(observed_keys[3].getpixel((1, 1)), (255, 0, 0))
        self.assertEqual(observed_panels[11].getpixel((199, 5)), (0, 0, 255))
        self.assertEqual(observed_panels[12].getpixel((0, 5)), (0, 0, 255))
        self.assertEqual(len(observed_keys), 10)
        self.assertEqual(len(observed_panels), 4)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_saved_animation_clears_overlays_and_rotates_without_mutating_cached_output(self):
        path = Path(self.temporary.name) / 'saved.gif'
        frames = [Image.new('RGB', (800, 480), color) for color in ('red', 'blue')]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=[100, 100])
        self.state = self.make_state(screensaver_path=path)
        self.control('screensaver')
        self.assertTrue(self.state.native_active)
        self.assertTrue(self.state.idle)
        self.assertFalse(self.state.sleeping)
        self.device.clearAllIcon.assert_called_once()
        self.assertEqual(self.led.call_args.args, (0,))
        self.assertEqual(self.device.transport.set_background_frame_stream.call_count, 1)
        self.advance(.11)
        self.assertEqual(self.device.transport.set_background_frame_stream.call_count, 2)
        self.device.set_key_image.assert_not_called()
        self.control('sleep')
        self.assertTrue(self.state.sleeping)
        self.assertFalse(self.state.native_active)
        count = self.device.transport.set_background_frame_stream.call_count
        self.advance(.1)
        self.assertEqual(self.device.transport.set_background_frame_stream.call_count, count)

    def test_streamed_screensaver_uses_full_frames_without_key_or_touch_uploads(self):
        for mode in ('frame', 'panels'):
            with self.subTest(mode=mode):
                device = Mock()
                state = DisplayState(device, self.temporary.name, strip_mode=mode,
                                     lcd_mode='stream-screensaver', clock=lambda: self.now)
                state.output('key:0', Image.new('RGB', (120, 120), 'blue'), None, lambda: None)
                device.reset_mock()
                for number, color in enumerate(('red', 'green'), 1):
                    image = Image.new('RGB', (800, 480), color)
                    image.paste('blue', (0, 0, 100, 100))
                    state.output('lcd', image, None, lambda: None)
                    self.assertEqual(device.transport.set_background_frame_stream.call_count, number)
                    self.assertEqual(device.refresh.call_count, number)
                    data, *geometry = device.transport.set_background_frame_stream.call_args.args
                    self.assertEqual(geometry, [800, 480, 0, 0])
                    with Image.open(io.BytesIO(data)) as frame:
                        self.assertGreater(frame.getpixel((750, 430))[2], 240)
                    device.set_key_image.assert_not_called()
                    device.set_seondscreen_image.assert_not_called()
                state.control({'control': 'screensaver'}, lambda: None)
                self.assertEqual(device.transport.set_background_frame_stream.call_count, 3)
                device.clearAllIcon.assert_called_once()
                device.set_key_image.assert_not_called()
                device.set_seondscreen_image.assert_not_called()
                self.assertEqual(state.keys[0].getpixel((60, 60)), (0, 0, 255))
                self.assertEqual(state.strip.getpixel((300, 50)), (0, 0, 0))
                self.assertEqual(state.background.getpixel((400, 240)), (0, 0, 0))
                self.assertEqual(state.key_cache_clears, 0)
                state.notify_input(self.now, False, lambda: None)
                self.assertFalse(state.idle)
                device.set_key_image.assert_called()

    def test_cancellation_prevents_control_and_every_individual_native_write(self):
        for control in ({'control': 'brightness', 'value': 30}, {'control': 'sleep'}, {'control': 'knobBrightness', 'value': 20}):
            with self.assertRaises(ProtocolError):
                self.state.control(control, cancelled)
        self.device.set_brightness.assert_not_called()
        self.device.transport.sleep.assert_not_called()
        self.led.assert_not_called()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 3:
                cancelled()
        with self.assertRaises(ProtocolError):
            self.state.output('lcd', Image.new('RGB', (800, 480), 'red'), None, guard)
        self.assertEqual(self.device.transport.set_background_frame_stream.call_count, 1)
        self.device.set_key_image.assert_not_called()
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_software_logo_is_optional_overlay_and_never_changes_cached_content(self):
        with self.assertRaises(ProtocolError):
            self.control('showLogo')
        path = Path(self.temporary.name) / 'logo.png'
        Image.new('RGBA', (64, 64), 'red').save(path)
        self.state = self.make_state(logo_path=path)
        self.control('showLogo')
        self.assertTrue(self.state.logo_active)
        self.assertEqual(self.state.background.getpixel((400, 240)), (0, 0, 0))
        self.state.output('key:1', Image.new('RGB', (120, 120), 'blue'), None, lambda: None)
        self.assertFalse(self.state.logo_active)
        self.assertEqual(self.state.keys[1].getpixel((0, 0)), (0, 0, 255))
        self.assertFalse(any('boot' in str(call).lower() for call in self.device.mock_calls))
        self.assertEqual(descriptor('test', logo_supported=True)['capabilities']['features']['showLogo']['status'], 'emulated')

    def test_saved_animation_bounds_and_cancelled_load_are_atomic(self):
        path = Path(self.temporary.name) / 'saved.png'
        Image.new('RGB', (800, 480), 'red').save(path)
        animation = NativeAnimation(path)
        first = animation.frame(0, restart=True)
        with self.assertRaises(ProtocolError):
            animation.load(path, cancelled)
        self.assertEqual(animation.frame(1, restart=True), first)
        with patch('hellgato_mirabox.models.n4_pro.animation.MAX_FILE_BYTES', 1):
            with self.assertRaises(ValueError):
                animation.load(path)
        with patch('hellgato_mirabox.models.n4_pro.animation.MAX_FRAME_PIXELS', 1):
            with self.assertRaises(ValueError):
                animation.load(path)
        self.assertEqual(animation.frame(2, restart=True), first)


    def test_initialize_wakes_retained_sleep_and_resets_surfaces_before_brightness(self):
        self.state.initialize(lambda: None)
        names = [call[0] for call in self.device.mock_calls]
        self.assertEqual(names[:2], ['set_brightness', 'clearAllIcon'])
        self.assertEqual(self.device.set_brightness.call_args_list[0].args, (0,))
        self.assertEqual(self.device.set_brightness.call_args_list[-1].args, (100,))
        self.assertLess(names.index('refresh'), names.index('wakeScreen'))
        self.assertLess(names.index('wakeScreen'), len(names) - 1)
        self.device.set_key_image.assert_not_called()
        self.assertEqual(self.device.clearAllIcon.call_count, 2)
        self.assertEqual(self.device.set_seondscreen_image.call_count, 4)
        self.assertEqual(self.state.screen_level, 100)
        self.assertEqual(self.led.call_args.args, (100,))

    def test_initialize_cancellation_stops_before_next_native_operation(self):
        checks = 0
        def guard():
            nonlocal checks
            checks += 1
            if checks == 3:
                cancelled()
        with self.assertRaises(ProtocolError):
            self.state.initialize(guard)
        self.device.set_brightness.assert_called_once_with(0)
        self.device.clearAllIcon.assert_called_once()
        self.device.transport.set_background_frame_stream.assert_not_called()
        self.device.wakeScreen.assert_not_called()
        self.led.assert_not_called()

    def test_host_clear_after_wake_restarts_idle_timeout(self):
        self.control('sleepDuration', value=1)
        self.control('sleep')
        self.control('wake')
        self.advance(.8)
        self.control('clear')
        self.advance(.3)
        self.assertFalse(self.state.idle)
        self.advance(.7)
        self.assertTrue(self.state.idle)

    def test_display_command_disarms_pending_manual_sleep_fade(self):
        self.state.notify_input(self.now, True, lambda: None)
        self.control('sleepDuration', value=0)
        self.assertIsNotNone(self.state.sleep_candidate)
        self.control('fill', surfaceId='key:0', color=[255, 0, 0])
        self.control('brightness', value=90)
        self.assertFalse(self.state.idle)
        self.assertEqual(self.state.brightness, 90)

    def test_repeated_wake_preserves_in_progress_fade(self):
        self.control('sleep')
        self.control('wake')
        fade = self.state.fade
        self.advance(.15)
        self.assertLess(self.state.screen_level, self.state.brightness)
        self.control('wake')
        self.assertEqual(self.state.fade, fade)
        self.assertLess(self.state.screen_level, self.state.brightness)
        self.advance(.3)
        self.assertEqual(self.state.screen_level, 100)

    def test_brightness_restores_normal_surfaces_from_logo_overlay(self):
        path = Path(self.temporary.name) / 'logo.png'
        Image.new('RGBA', (64, 64), 'red').save(path)
        self.state = self.make_state(logo_path=path)
        self.control('showLogo')
        self.device.reset_mock()
        self.control('brightness', value=55)
        self.assertFalse(self.state.logo_active)
        self.assertEqual(self.state.brightness, 55)
        self.assertEqual(self.device.set_key_image.call_count, 10)
        self.assertEqual(self.device.set_seondscreen_image.call_count, 4)

    def test_saved_frames_are_byte_identical_to_legacy_for_each_format_and_timing(self):
        from app.n4.animation import NativeAnimation as LegacyAnimation
        # Asymmetric transparent source detects rotation, centering and alpha changes.
        first = Image.new('RGBA', (128, 72), (0, 0, 0, 0))
        first.paste((255, 0, 0, 128), (0, 0, 64, 36))
        first.paste((0, 255, 0, 255), (64, 36, 128, 72))
        second = Image.new('RGBA', (128, 72), (0, 0, 255, 255))
        for extension in ('png', 'jpg', 'gif', 'webp', 'bmp'):
            with self.subTest(format=extension):
                path = Path(self.temporary.name) / f'saved.{extension}'
                if extension in ('gif', 'webp'):
                    first.save(path, save_all=True, append_images=[second], duration=[70, 130])
                elif extension == 'jpg':
                    first.convert('RGB').save(path)
                else:
                    first.save(path)
                actual, baseline = NativeAnimation(path), LegacyAnimation(path)
                for now in (0, .01, .07, .1, .199, .2, .27, 5.01):
                    self.assertEqual(actual.frame(now), baseline.frame(now))
                self.assertEqual(actual.frame(6, restart=True), baseline.frame(6, restart=True))


class StripPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_frame_mode_is_byte_identical_to_legacy_baseline_and_partial_transform(self):
        from app.n4.display import DisplayMirror
        device, legacy_device = Mock(), Mock()
        writer = DisplayWriter(device, self.temporary.name, 'frame')
        legacy = DisplayMirror(legacy_device, Path(self.temporary.name), strip_mode='frame')
        first = Image.new('RGB', (20, 10), 'red')
        writer.touch(first, (30, 8, 20, 10), lambda: None)
        legacy.strip.paste(first, (30, 8))
        legacy._write_strip(legacy.strip, (30, 8, 20, 10))
        second = Image.new('RGB', (30, 20), 'blue')
        writer.touch(second, (200, 15, 30, 20), lambda: None)
        legacy.strip.paste(second, (200, 15))
        legacy._write_strip(legacy.strip, (200, 15, 30, 20))
        actual = device.transport.set_background_frame_stream.call_args_list
        self.assertEqual(actual, legacy_device.transport.set_background_frame_stream.call_args_list)
        self.assertEqual(actual[0].args[1:], (800, 130, 0, 0))
        self.assertEqual(actual[1].args[1:], (30, 20, 570, 85))
        device.set_seondscreen_image.assert_not_called()

    def test_lcd_and_wake_restore_reinitialize_frame_strip(self):
        device = Mock()
        state = DisplayState(device, self.temporary.name, strip_mode='frame')
        state.output('touch', Image.new('RGB', (20, 10), 'red'), (30, 8, 20, 10), lambda: None)
        state.output('lcd', Image.new('RGB', (800, 480), 'blue'), None, lambda: None)
        self.assertEqual(device.transport.set_background_frame_stream.call_args.args[1:], (800, 130, 0, 0))
        state.control({'control': 'sleep'}, lambda: None)
        state.control({'control': 'wake'}, lambda: None)
        self.assertEqual(device.transport.set_background_frame_stream.call_args.args[1:], (800, 130, 0, 0))
        self.assertEqual(state.state()['stripMode'], 'frame')
        device.set_seondscreen_image.assert_not_called()

    def test_full_lcd_commits_after_all_keys_and_final_touch_frame_or_panel(self):
        for mode in ('panels', 'frame'):
            with self.subTest(mode=mode):
                device = Mock()
                writer = DisplayWriter(device, self.temporary.name, mode)
                writer.lcd(Image.new('RGB', (800, 480), 'white'), lambda: None)
                names = [call[0] for call in device.mock_calls]
                self.assertEqual(names[-1], 'refresh')
                self.assertEqual(names.count('refresh'), 1)
                self.assertEqual(names.count('set_key_image'), 10)
                self.assertEqual(names[-2], 'set_seondscreen_image' if mode == 'panels'
                                 else 'transport.set_background_frame_stream')

    def test_black_full_lcd_removes_key_overlays_without_submitting_opaque_black_pngs(self):
        for mode in ('panels', 'frame'):
            with self.subTest(mode=mode):
                device = Mock()
                writer = DisplayWriter(device, self.temporary.name, mode)
                writer.lcd(Image.new('RGB', (800, 480), 'black'), lambda: None)
                device.clearAllIcon.assert_called_once()
                device.set_key_image.assert_not_called()
                self.assertEqual(device.transport.set_background_frame_stream.call_args_list[0].args[1:],
                                 (800, 480, 0, 0))
                self.assertEqual(device.mock_calls[-1][0], 'refresh')
                self.assertTrue(all(key['lastWriter'] == 'lcd' and key['submitted'] == 1 and key['black']
                                    for key in writer.observations()))

    def test_startup_clear_is_refreshed_before_following_action_images_without_sleep_wake(self):
        device = Mock()
        state = DisplayState(device, self.temporary.name, strip_mode='frame', lcd_mode='stream-screensaver')
        state.initialize(lambda: None)
        self.assertEqual(device.refresh.call_count, 1)
        device.reset_mock()
        state.control({'control': 'clear'}, lambda: None)
        state.output('key:0', Image.new('RGB', (120, 120), 'blue'), None, lambda: None)
        names = [call[0] for call in device.mock_calls]
        self.assertEqual(names[-2:], ['refresh', 'set_key_image'])
        self.assertEqual(device.refresh.call_count, 1)
        self.assertFalse(state.idle)
        state.control({'control': 'sleep'}, lambda: None)
        device.reset_mock()
        state.output('key:0', Image.new('RGB', (120, 120), 'green'), None, lambda: None)
        state.output('touch', Image.new('RGB', (20, 10), 'red'), (0, 0, 20, 10), lambda: None)
        device.refresh.assert_not_called()
        device.set_key_image.assert_not_called()

    def test_partial_lcd_failure_does_not_refresh_an_incomplete_composite(self):
        device = Mock()
        device.set_key_image.side_effect = [0, 0, -1]
        writer = DisplayWriter(device, self.temporary.name, 'frame')
        with self.assertRaises(OSError):
            writer.lcd(Image.new('RGB', (800, 480), 'white'), lambda: None)
        device.refresh.assert_not_called()
        self.assertEqual(device.set_key_image.call_count, 3)

    def test_cancelled_frame_baseline_is_not_marked_initialized(self):
        device = Mock()
        writer = DisplayWriter(device, self.temporary.name, 'frame')
        with self.assertRaises(ProtocolError):
            writer.touch(Image.new('RGB', (20, 10), 'red'), (0, 0, 20, 10), cancelled)
        self.assertFalse(writer.frame_initialized)
        device.transport.set_background_frame_stream.assert_not_called()
        with self.assertRaises(ValueError):
            DisplayWriter(device, self.temporary.name, 'unknown')

    def test_panels_touch_write_only_intersecting_regions(self):
        device = Mock()
        writer = DisplayWriter(device, self.temporary.name, 'panels')
        writer.touch(Image.new('RGB', (20, 10), 'red'), (199, 10, 20, 10), lambda: None)
        self.assertEqual([call.args[0] for call in device.set_seondscreen_image.call_args_list], [11, 12])
        device.transport.set_background_frame_stream.assert_not_called()


    def test_lcd_rotation_crop_and_panel_png_bytes_match_legacy(self):
        from app.n4.display import DisplayMirror
        canvas = Image.new('RGB', (800, 480))
        for y in range(480):
            for x in range(800):
                canvas.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
        for mode in ('panels', 'frame'):
            with self.subTest(mode=mode):
                device, legacy_device = Mock(), Mock()
                observed, expected = [], []
                def capture(target, kind):
                    return lambda index, path: target.append((kind, index, Path(path).read_bytes()))
                for target, record in ((device, observed), (legacy_device, expected)):
                    target.set_key_image.side_effect = capture(record, 'key')
                    target.set_seondscreen_image.side_effect = capture(record, 'panel')
                writer = DisplayWriter(device, self.temporary.name, mode)
                baseline = DisplayMirror(legacy_device, Path(self.temporary.name), strip_mode=mode)
                writer.lcd(canvas, lambda: None)
                baseline._show_lcd(canvas)
                self.assertEqual(observed, expected)
                self.assertEqual(device.transport.set_background_frame_stream.call_args_list,
                                 legacy_device.transport.set_background_frame_stream.call_args_list)
                self.assertEqual([index for kind, index, _ in observed if kind == 'key'], list(range(1, 11)))
                for kind, index, data in observed:
                    if kind == 'key':
                        with Image.open(io.BytesIO(data)) as crop:
                            x, y = 13 + ((index - 1) % 5) * 164, 12 + ((index - 1) // 5) * 160
                            self.assertEqual(crop.size, (120, 120))
                            self.assertEqual(crop.getpixel((0, 0)), canvas.getpixel((x, y)))
                self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_vendor_image_rejection_raises_and_always_cleans_temporary_files(self):
        device = Mock()
        writer = DisplayWriter(device, self.temporary.name)
        device.set_key_image.return_value = -1
        with self.assertRaises(OSError):
            writer.key(0, Image.new('RGB', (120, 120)), lambda: None)
        device.set_seondscreen_image.return_value = -1
        with self.assertRaises(OSError):
            writer.touch(Image.new('RGB', (20, 10)), (0, 0, 20, 10), lambda: None)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])


class OwnershipAndIdentityTests(unittest.TestCase):
    def test_legacy_registry_identity_matches_without_exposing_raw_serial(self):
        # Independent reference matches the product's canonical serialization exactly.
        import hashlib
        for serial, path in [('private-serial-ä', b'usb-path'), (None, b'usb-path')]:
            device = SimpleNamespace(serial_number=serial, path=path, vendor_id=123, product_id=456)
            physical = serial or path.hex()
            data = json.dumps(['StreamDockN4Pro', 123, 456, 'serial' if serial else 'usb-path', physical],
                              ensure_ascii=True, separators=(',', ':')).encode('utf-8')
            self.assertEqual(device_identity(device), 'n4-' + hashlib.sha256(data).hexdigest())
            self.assertNotIn('private-serial', json.dumps(descriptor(device_identity(device))))

    def test_same_legacy_mutex_retained_until_explicit_close(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 123
        kernel.CloseHandle.return_value = 1
        lock = RuntimeOwnership(platform='win32', kernel=kernel, last_error=lambda: 0)
        kernel.CreateMutexW.assert_called_once_with(None, False, MUTEX_NAME)
        kernel.CloseHandle.assert_not_called()
        lock.close()
        lock.close()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_existing_mutex_is_rejected_and_duplicate_handle_closed(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 456
        with self.assertRaises(ProtocolError) as caught:
            RuntimeOwnership(platform='win32', kernel=kernel, last_error=lambda: 183)
        self.assertEqual(caught.exception.code, 'ERR_DEVICE_BUSY')
        kernel.CloseHandle.assert_called_once_with(456)
        kernel.CreateMutexW.return_value = None
        with self.assertRaises(ProtocolError):
            RuntimeOwnership(platform='win32', kernel=kernel, last_error=lambda: 5)

    def test_native_led_symbol_result_checked_and_unsupported_rejected(self):
        class Transport:
            _handle = 123
        device = SimpleNamespace(transport=Transport())
        native = SimpleNamespace(transport_set_led_brightness=Mock(return_value=0))
        with patch.object(sys.modules[__name__], '_transport_lib', native, create=True):
            self.assertTrue(led_supported(device))
            checked_led_brightness(device, 42)
            native.transport_set_led_brightness.assert_called_once_with(123, 42)
            native.transport_set_led_brightness.return_value = 5
            with self.assertRaises(OSError):
                checked_led_brightness(device, 42)
        self.assertFalse(led_supported(device))
        with self.assertRaises(ProtocolError):
            checked_led_brightness(device, 42)


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.backend = MiraboxBackend.__new__(MiraboxBackend)
        self.backend.device = None
        self.backend.temporary = self.backend.display = None
        self.backend.lock = threading.RLock()
        self.backend.activity_lock = threading.Lock()
        self.backend.activity = None
        self.backend.last_poll = 0
        self.backend.screensaver_path = None
        self.backend.logo_path = None
        self.backend.strip_mode = 'panels'
        self.backend.lcd_mode = 'normal'
        self.backend.button_key = lambda code: code
        self.backend.ownership = Mock()
        self.device = SimpleNamespace(transport=SimpleNamespace(heartbeat=Mock(),
            get_firmware_version=Mock(return_value='V4.N4 Pro E.02.010'),
            set_background_frame_stream=Mock(), sleep=Mock(),
            enumerate_devices=Mock(return_value=[{'path': b'usb-path'}])),
            decode_input_event=Mock(), set_key_callback=Mock(), set_touch_bar_callback=Mock(),
            heartbeat_thread=None, set_device=Mock(), open=Mock(return_value=True),
            set_brightness=Mock(), set_key_image=Mock(), set_seondscreen_image=Mock(),
            clearAllIcon=Mock(), refresh=Mock(), wakeScreen=Mock(),
            serial_number='private-serial', path=b'usb-path', vendor_id=123, product_id=456,
            firmware_version='V4.N4 Pro E.02.010', run_heartbeat_thread=False)
        self.backend.candidates = {'selected': self.device}
        self.close_mock = patch('hellgato_mirabox.transport.mirabox.close_device', return_value=True)
        self.native_close = self.close_mock.start()
        self.addCleanup(self.close_mock.stop)
        self.addCleanup(lambda: self.backend.close())

    def test_close_restores_hooks_and_retains_mutex_until_process_shutdown(self):
        decode, heartbeat = self.device.decode_input_event, self.device.transport.heartbeat
        self.backend.open('selected', Mock())
        temporary = Path(self.backend.temporary.name)
        self.assertTrue(temporary.is_dir())
        self.assertIsNot(self.device.decode_input_event, decode)
        self.backend.close()
        self.assertIs(self.device.decode_input_event, decode)
        self.assertIs(self.device.transport.heartbeat, heartbeat)
        self.assertFalse(temporary.exists())
        self.backend.ownership.close.assert_not_called()
        self.backend.release_ownership()
        self.backend.ownership.close.assert_called_once()

    def test_failed_native_close_never_releases_ownership_or_clears_handle_state(self):
        self.backend.open('selected', Mock())
        self.native_close.return_value = False
        with self.assertRaises(ProtocolError):
            self.backend.close()
        with self.assertRaises(ProtocolError):
            self.backend.release_ownership()
        self.assertIs(self.backend.device, self.device)
        self.backend.ownership.close.assert_not_called()
        self.native_close.return_value = True

    def test_control_result_hints_distinguish_preserved_background_from_replacement(self):
        self.backend.open('selected', Mock())
        self.device.set_brightness = Mock()
        self.device.set_key_image = Mock()
        self.device.set_seondscreen_image = Mock()
        self.device.transport.sleep = Mock()
        self.device.transport.set_background_frame_stream = Mock()
        self.device.refresh = Mock()
        self.device.wakeScreen = Mock()
        self.backend.set_control({'control': 'sleep'}, lambda: None)
        result = self.backend.set_control({'control': 'clear'}, lambda: None)
        self.assertEqual(result['normalStateEffect'], 'background-only')
        self.assertEqual({key: result[key] for key in ('idle', 'sleeping', 'screensaverActive', 'logoActive')},
                         dict.fromkeys(('idle', 'sleeping', 'screensaverActive', 'logoActive'), False))
        result = self.backend.set_control({'control': 'fill', 'color': [255, 0, 0]}, lambda: None)
        self.assertEqual(result['normalStateEffect'], 'replace-all')
        result = self.backend.set_control({'control': 'clear', 'surfaceId': 'key:2'}, lambda: None)
        self.assertEqual(result['normalStateEffect'], 'surface')

    def test_open_propagates_explicit_streamed_lcd_mode_to_physical_state(self):
        self.backend.lcd_mode = 'stream-screensaver'
        self.backend.open('selected', Mock())
        self.assertEqual(self.backend.get_state()['lcdMode'], 'stream-screensaver')
        encoded = io.BytesIO()
        Image.new('RGB', (800, 480), 'red').save(encoded, format='JPEG')
        result = self.backend.set_output({'surfaceId': 'lcd', 'width': 800, 'height': 480,
                                         'data': base64.b64encode(encoded.getvalue()).decode()}, lambda: None)
        self.assertEqual(result['displayObservations']['keyCacheClears'], 0)
        self.assertTrue(self.backend.get_state()['screensaverActive'])

    def test_dynamic_vendor_import_does_not_write_bytecode_into_immutable_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            package = Path(root) / 'StreamDock'
            package.mkdir()
            (package / '__init__.py').write_text('')
            (package / 'DeviceManager.py').write_text('class DeviceManager: pass\n')
            (package / 'InputTypes.py').write_text('ButtonKey = int\n')
            with patch.dict(sys.modules), patch.object(sys, 'path', list(sys.path)), patch.object(sys, 'dont_write_bytecode', False):
                for key in list(sys.modules):
                    if key == 'StreamDock' or key.startswith('StreamDock.'):
                        del sys.modules[key]
                with patch('hellgato_mirabox.transport.mirabox.RuntimeOwnership'):
                    backend = MiraboxBackend(root)
                self.assertTrue(sys.dont_write_bytecode)
                self.assertEqual(list(package.rglob('*.pyc')), [])
                self.assertEqual(list(package.rglob('__pycache__')), [])
                self.assertIsNone(backend.device)

    def test_cancelled_open_does_not_touch_usb(self):
        with self.assertRaises(ProtocolError):
            self.backend.open('selected', Mock(), cancelled)
        self.device.set_device.assert_not_called()
        self.device.open.assert_not_called()
        self.assertIsNone(self.backend.device)

    def test_open_rechecks_actual_firmware_before_display_or_state_creation(self):
        self.device.transport.get_firmware_version.return_value = 'unreviewed-firmware'
        with patch('hellgato_mirabox.models.n4_pro.state.DisplayState') as display:
            with self.assertRaises(ProtocolError) as caught:
                self.backend.open('selected', Mock())
        self.assertEqual(caught.exception.code, 'ERR_FIRMWARE_CHANGED')
        display.assert_not_called()
        self.assertIsNone(self.backend.device)
        self.assertIsNone(self.backend.temporary)
        self.native_close.assert_called_once()
        self.backend.ownership.close.assert_not_called()

    def test_unverified_firmware_attempts_normal_open_and_preserves_version(self):
        firmware = 'V4.N4 Pro.02.011'
        self.device.firmware_version = firmware
        self.device.transport.get_firmware_version.return_value = firmware
        result = self.backend.open('selected', Mock())
        self.assertEqual(result['firmware'], firmware)
        self.device.open.assert_called_once()
        self.assertIsNotNone(self.backend.display)
        self.backend.close()

    def test_even_reviewed_firmware_change_requires_new_discovery(self):
        with patch('hellgato_mirabox.transport.mirabox.require_candidate_firmware',
                   side_effect=['reviewed-before', 'reviewed-after']):
            with self.assertRaises(ProtocolError) as caught:
                self.backend.open('selected', Mock())
        self.assertEqual(caught.exception.code, 'ERR_FIRMWARE_CHANGED')
        self.assertIsNone(self.backend.device)
        self.native_close.assert_called_once()

    def test_input_activity_wakes_only_from_serialized_poll_not_callback(self):
        self.backend.open('selected', Mock())
        display = self.backend.display = Mock()
        touch = self.device.set_touch_bar_callback.call_args.args[0]
        touch(self.device, SimpleNamespace(x=20, y=10))
        display.notify_input.assert_not_called()
        self.backend.poll()
        display.notify_input.assert_called_once()
        display.tick.assert_called_once()


    def test_physical_button_decoder_is_not_the_vendor_image_key_map(self):
        self.device.decode_input_event.side_effect = lambda code, state: InputEvent('button', key=99, state=state)
        self.backend.open('selected', Mock())
        for code in range(1, 16):
            self.assertEqual(self.device.decode_input_event(code, 1).key, code)

    def test_all_keys_dial_pulses_and_rotations_preserve_input_and_activity(self):
        emit = Mock()
        self.backend.open('selected', emit)
        forward = self.device.set_key_callback.call_args.args[0]
        for index in range(10):
            for state in (0, 1):
                forward(self.device, InputEvent('button', key=index + 1, state=state))
                self.assertEqual(emit.call_args.args[0], {'type': 'key', 'index': index,
                                                         'state': 'down' if state else 'up'})
                self.assertEqual(self.backend.activity[1], state == 0)
        for index in range(4):
            forward(self.device, InputEvent('knob_press', knob_id=f'knob_{index + 1}', state=1))
            self.assertEqual(emit.call_args.args[0], {'type': 'dial', 'kind': 'pulse', 'index': index})
            self.assertTrue(self.backend.activity[1])
            for direction, delta in [('left', -1), ('right', 1)]:
                forward(self.device, InputEvent('knob_rotate', knob_id=f'knob_{index + 1}', direction=direction))
                self.assertEqual(emit.call_args.args[0], {'type': 'dial', 'kind': 'rotate', 'index': index, 'delta': delta})
                self.assertFalse(self.backend.activity[1])
        emit.reset_mock()
        forward(self.device, InputEvent('knob_press', knob_id='knob_1', state=0))
        forward(SimpleNamespace(), InputEvent('button', key=1, state=1))
        emit.assert_not_called()

    def test_panel_release_without_coordinates_still_arms_manual_sleep(self):
        emit = Mock()
        self.backend.open('selected', emit)
        forward = self.device.set_key_callback.call_args.args[0]
        for key in (11, 12, 13, 14):
            forward(self.device, InputEvent('button', key=key, state=0))
            self.assertTrue(self.backend.activity[1])
        emit.assert_not_called()
        self.backend.poll()
        self.assertIsNotNone(self.backend.display.last_key_release)
        self.backend.set_control({'control': 'sleepDuration', 'value': 0}, lambda: None)
        self.backend.set_control({'control': 'brightness', 'value': 90}, lambda: None)
        self.assertTrue(self.backend.display.idle)

    def test_invalid_touch_samples_do_not_wake_or_replace_valid_gesture(self):
        self.backend.open('selected', Mock())
        touch = self.device.set_touch_bar_callback.call_args.args[0]
        touch(self.device, SimpleNamespace(x=20, y=111))
        activity = self.backend.activity
        self.assertEqual(self.backend.touch.first, (20, 99))
        for x, y in [(800, 0), (-1, 0), (0, 112), (0, -1), (True, 0), (0, 'bad')]:
            touch(self.device, SimpleNamespace(x=x, y=y))
            self.assertEqual(self.backend.activity, activity)
            self.assertEqual(self.backend.touch.first, (20, 99))

    def test_touch_tap_hold_swipe_and_fallback_coordinates_match_legacy(self):
        emit = Mock()
        self.backend.open('selected', emit)
        touch = self.device.set_touch_bar_callback.call_args.args[0]
        forward = self.device.set_key_callback.call_args.args[0]
        def point(x, y, now):
            with patch('hellgato_mirabox.transport.mirabox.time.monotonic', return_value=now):
                touch(self.device, SimpleNamespace(x=x, y=y))
        def finish(event, now):
            with patch('hellgato_mirabox.transport.mirabox.time.monotonic', return_value=now):
                forward(self.device, event)
            return emit.call_args.args[0]
        point(20, 111, 10)
        self.assertEqual(finish(InputEvent('button', key=11, state=0), 10.1),
                         {'type': 'touch', 'gesture': 'tap', 'x': 20, 'y': 99})
        for now in (20, 20.2, 20.4, 20.6):
            point(399, 56, now)
        self.assertEqual(finish(InputEvent('button', key=12, state=0), 20.61),
                         {'type': 'touch', 'gesture': 'hold', 'x': 399, 'y': 50})
        point(710, 56, 30)
        point(110, 56, 30.1)
        self.assertEqual(finish(InputEvent('swipe', direction='right'), 30.11),
                         {'type': 'touch', 'gesture': 'swipe', 'x': 710, 'y': 50, 'endX': 110, 'endY': 50})
        self.assertEqual(finish(InputEvent('swipe', direction='left'), 40),
                         {'type': 'touch', 'gesture': 'swipe', 'x': 100, 'y': 50, 'endX': 700, 'endY': 50})
        emit.reset_mock()
        point(20, 20, 50)
        finish_event = InputEvent('button', key=11, state=0)
        with patch('hellgato_mirabox.transport.mirabox.time.monotonic', return_value=51):
            forward(self.device, finish_event)
        emit.assert_not_called()

    def test_presence_loss_closes_before_animation_or_pending_wake_writes(self):
        self.backend.open('selected', Mock())
        display = self.backend.display = Mock()
        self.backend.activity = (time.monotonic(), False)
        self.device.transport.enumerate_devices.return_value = []
        self.assertEqual(self.backend.poll(), 'disconnected')
        display.tick.assert_not_called()
        display.notify_input.assert_not_called()
        self.assertIsNone(self.backend.device)
        self.backend.ownership.close.assert_not_called()

    def test_open_initializes_only_after_firmware_and_closes_on_cancelled_initialization(self):
        events = []
        self.device.transport.get_firmware_version.side_effect = lambda: events.append('firmware') or 'V4.N4 Pro E.02.010'
        self.device.set_device.side_effect = lambda: events.append('configure')
        self.device.clearAllIcon.side_effect = lambda: events.append('clear')
        self.device.wakeScreen.side_effect = lambda: events.append('wake')
        self.backend.open('selected', Mock())
        self.assertEqual(events, ['configure', 'firmware', 'configure', 'clear', 'clear', 'wake'])
        self.backend.close()
        self.native_close.reset_mock()
        def guard():
            if events and events[-1] == 'clear':
                cancelled()
        events.clear()
        with self.assertRaises(ProtocolError):
            self.backend.open('selected', Mock(), guard)
        self.assertEqual(events, ['configure', 'firmware', 'configure', 'clear'])
        self.assertIsNone(self.backend.device)
        self.assertIsNone(self.backend.temporary)
        self.native_close.assert_called_once()

    def test_input_is_suppressed_until_admission_finishes_and_from_retired_callbacks(self):
        emit = Mock()
        def during_open():
            forward = self.device.set_key_callback.call_args.args[0]
            touch = self.device.set_touch_bar_callback.call_args.args[0]
            forward(self.device, InputEvent('button', key=1, state=1))
            touch(self.device, SimpleNamespace(x=30, y=20))
            self.assertIsNone(self.backend.activity)
            return True
        self.device.open.side_effect = during_open
        self.backend.open('selected', emit)
        emit.assert_not_called()
        retired = self.device.set_key_callback.call_args.args[0]
        self.backend.close()
        self.backend.open('selected', emit)
        retired(self.device, InputEvent('button', key=1, state=1))
        emit.assert_not_called()
        self.device.set_key_callback.call_args.args[0](self.device, InputEvent('button', key=1, state=1))
        emit.assert_called_once()

    def test_callback_setup_failure_cleans_up_and_restores_hooks(self):
        decode, heartbeat = self.device.decode_input_event, self.device.transport.heartbeat
        self.device.set_touch_bar_callback.side_effect = RuntimeError('SDK callback failed')
        with self.assertRaisesRegex(RuntimeError, 'SDK callback failed'):
            self.backend.open('selected', Mock())
        self.device.open.assert_not_called()
        self.native_close.assert_called_once()
        self.assertIsNone(self.backend.device)
        self.assertIs(self.device.decode_input_event, decode)
        self.assertIs(self.device.transport.heartbeat, heartbeat)

    def test_reopen_initializes_fresh_native_state_without_replaying_inputs(self):
        emit = Mock()
        self.backend.open('selected', emit)
        old = self.backend.display
        self.backend.set_control({'control': 'brightness', 'value': 40}, lambda: None)
        self.backend.set_control({'control': 'sleepDuration', 'value': 30}, lambda: None)
        self.backend.close()
        self.backend.open('selected', emit)
        self.assertIsNot(self.backend.display, old)
        self.assertEqual(self.backend.display.brightness, 100)
        self.assertEqual(self.backend.display.sleep_seconds, 0)
        self.assertEqual(self.device.wakeScreen.call_count, 2)
        emit.assert_not_called()

    def test_active_writer_timeout_retains_ownership_until_writer_finishes(self):
        self.backend.open('selected', Mock())
        self.device.transport._handle = 123
        self.device.transport._is_open = True
        self.device.transport.close = Mock()
        self.device.gif_controller = SimpleNamespace(stop_animation_loop=Mock(), close=Mock())
        self.device._notify_on_close = True
        self.device.heartbeat_thread = Mock()
        self.device.heartbeat_thread.is_alive.return_value = True
        self.device.close = lambda notify=False: self.device.transport.close()
        native = SimpleNamespace(transport_destroy=Mock(return_value=0))
        self.native_close.side_effect = lambda device, **kwargs: close_device(device, native=native, **kwargs)
        with self.assertRaises(ProtocolError):
            self.backend.close()
        native.transport_destroy.assert_not_called()
        self.assertEqual(self.device.transport._handle, 123)
        self.device.heartbeat_thread.join.assert_called_with(timeout=2)
        with self.assertRaises(ProtocolError):
            self.backend.release_ownership()
        self.device.heartbeat_thread.is_alive.return_value = False
        self.backend.close()
        native.transport_destroy.assert_called_once_with(123)
        self.backend.release_ownership()
        self.backend.ownership.close.assert_called_once()

    def test_checked_destroy_failure_retains_native_handle_across_close_retry(self):
        self.backend.open('selected', Mock())
        self.device.transport._handle = 123
        self.device.transport._is_open = True
        self.device.transport.close = Mock()
        self.device.gif_controller = SimpleNamespace(stop_animation_loop=Mock(), close=Mock())
        self.device._notify_on_close = True
        self.device.close = lambda notify=False: self.device.transport.close()
        native = SimpleNamespace(transport_destroy=Mock(return_value=5))
        self.native_close.side_effect = lambda device, **kwargs: close_device(device, native=native, **kwargs)
        for _ in range(2):
            with self.assertRaises(ProtocolError):
                self.backend.close()
            self.assertEqual(self.device.transport._handle, 123)
            self.assertTrue(self.device.transport._is_open)
            with self.assertRaises(ProtocolError):
                self.backend.release_ownership()
        self.backend.ownership.close.assert_not_called()
        native.transport_destroy.return_value = 0
        self.backend.close()
        self.assertIsNone(self.device.transport._handle)
        self.assertFalse(self.device.transport._is_open)
        self.backend.release_ownership()
        self.backend.ownership.close.assert_called_once()
        self.assertEqual(native.transport_destroy.call_count, 3)


class WorkerFilesystemTests(unittest.TestCase):
    def test_native_workspace_contains_vendor_files_and_cleans_on_success_or_failure(self):
        original = Path.cwd()
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as caller:
                os.chdir(caller)
                try:
                    try:
                        with physical_working_directory(True):
                            workspace = Path.cwd()
                            self.assertNotEqual(workspace, Path(caller))
                            self.assertTrue(workspace.name.startswith('hellgato-mirabox-runtime-'))
                            Path('rotated_key_image_123.png').write_bytes(b'vendor temporary fixture')
                            self.assertEqual(list(Path(caller).iterdir()), [])
                            if failure:
                                raise RuntimeError('Vendor failure')
                    except RuntimeError:
                        if not failure:
                            raise
                    self.assertEqual(Path.cwd(), Path(caller))
                    self.assertFalse(workspace.exists())
                    self.assertEqual(list(Path(caller).iterdir()), [])
                finally:
                    os.chdir(original)

    def test_fake_backend_does_not_create_a_native_workspace_or_change_cwd(self):
        original = Path.cwd()
        with patch('hellgato_mirabox.worker.tempfile.TemporaryDirectory') as temporary:
            with physical_working_directory(False):
                self.assertEqual(Path.cwd(), original)
            temporary.assert_not_called()


class WorkerControlTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.backend = FakeBackend(clock=lambda: self.now)
        self.worker = Worker(self.backend, io.BytesIO(), io.BytesIO(), 'tests')
        self.request_id = 0
        self.request('hello', protocol={'major': 1, 'minor': 0})
        self.request('open', deviceId=DEVICE_ID, generation=1)

    def request(self, method, **params):
        self.request_id += 1
        return self.worker.dispatch({'jsonrpc': '2.0', 'id': str(self.request_id), 'method': method,
            'params': {'deadlineAt': int(time.time() * 1000) + 3000, **params}})

    def control(self, kind, **kwargs):
        return self.request('setControl', type='control', control=kind, deviceId=DEVICE_ID, generation=1, **kwargs)

    def test_discovery_passes_a_live_deadline_guard_into_the_backend(self):
        self.request('close', deviceId=DEVICE_ID, generation=1)
        guards = []
        def discover(guard):
            guards.append(guard)
            self.worker.stopped.set()
            guard()
        self.backend.discover = discover
        with self.assertRaises(ProtocolError) as caught:
            self.request('discover')
        self.assertEqual(caught.exception.code, 'ERR_WORKER_STOPPED')
        self.assertEqual(len(guards), 1)

    def test_new_controls_validate_strictly_and_fake_is_explicit_simulation(self):
        for kind, value in [('knobBrightness', True), ('knobBrightness', -1), ('knobBrightness', 101),
                            ('followDisplayBrightness', 1), ('sleepDuration', 2147483648)]:
            with self.assertRaises(ProtocolError):
                self.control(kind, value=value)
        result = self.control('knobBrightness', value=45)
        self.assertEqual(result['verification'], 'simulation')
        self.control('sleepDuration', value=2)
        self.now = 2
        self.backend.poll()
        self.assertTrue(self.backend.get_state()['sleeping'])
        self.assertEqual(self.backend.get_state()['knobBrightnessLevel'], 0)
        self.control('wake')
        self.assertEqual(self.backend.get_state()['knobBrightnessLevel'], 45)
        self.control('followDisplayBrightness', value=True)
        self.assertEqual(self.backend.get_state()['knobBrightnessLevel'], 100)
        self.control('screensaver')
        self.assertTrue(self.backend.get_state()['sleeping'])
        self.assertFalse(self.backend.get_state()['screensaverReady'])

    def test_firmware_boot_logo_and_scoped_global_controls_rejected(self):
        for kind in ['firmwareWrite', 'bootLogo', 'showLogo']:
            with self.assertRaises(ProtocolError):
                self.control(kind)
        with self.assertRaises(ProtocolError):
            self.control('brightness', value=30, surfaceId='key:0')
        self.assertEqual(self.backend.write_count, 0)

    def test_stopped_worker_and_expired_control_never_change_saved_state(self):
        self.control('knobBrightness', value=45)
        with self.assertRaises(ProtocolError):
            self.control('knobBrightness', value=20, deadlineAt=int(time.time() * 1000) - 1)
        self.worker.stopped.set()
        with self.assertRaises(ProtocolError):
            self.control('knobBrightness', value=20)
        self.assertEqual(self.backend.knob_brightness, 45)

    def test_fake_control_hints_are_bounded_and_explicitly_simulated(self):
        result = self.control('clear')
        self.assertEqual(result['normalStateEffect'], 'replace-all')
        self.assertEqual(result['verification'], 'simulation')
        result = self.control('fill', surfaceId='key:1', color=[1, 2, 3])
        self.assertEqual(result['normalStateEffect'], 'surface')
        self.assertFalse(result['screensaverActive'])
        self.assertFalse(result['logoActive'])

    def test_failed_output_attempt_consumes_sequence_to_prevent_ambiguous_retry(self):
        original = self.backend.set_control
        def partial(_control, _guard):
            raise OSError('Native submission may have applied')
        self.backend.set_control = partial
        with self.assertRaises(OSError):
            self.control('brightness', value=20, sequence=7)
        self.backend.set_control = original
        with self.assertRaises(ProtocolError) as caught:
            self.control('brightness', value=20, sequence=7)
        self.assertEqual(caught.exception.code, 'ERR_OUTPUT_ORDER')
        self.control('brightness', value=20, sequence=8)

    def test_explicit_reopen_resets_volatile_state_and_generation(self):
        self.control('knobBrightness', value=45)
        self.control('sleepDuration', value=2)
        self.request('close', deviceId=DEVICE_ID, generation=1)
        self.request('open', deviceId=DEVICE_ID, generation=2)
        self.assertIsNone(self.backend.knob_brightness)
        self.assertEqual(self.backend.sleep_seconds, 0)

    def test_poll_runs_during_queued_output_flood_and_eof_cancels_remaining_work(self):
        # Keep reader alive until ordered work is queued. Poll must not require an empty queue.
        release = threading.Event()
        class BlockingSource:
            def readline(self, _limit):
                release.wait(3)
                return b''
        self.worker.source = BlockingSource()
        for index in range(20):
            data = (json.dumps({'jsonrpc': '2.0', 'id': f'flood-{index}', 'method': 'setControl',
                'params': {'type': 'control', 'control': 'brightness', 'value': 42,
                    'deviceId': DEVICE_ID, 'generation': 1, 'deadlineAt': int(time.time() * 1000) + 3000}}) + '\n').encode()
            self.worker.requests.put(data)
            self.worker.queued_bytes += len(data)
        polls = []
        original = self.backend.poll
        def poll(check):
            polls.append(self.worker.requests.qsize())
            result = original(check)
            if len(polls) == 3:
                release.set()
                self.worker.stopped.set()
            return result
        self.backend.poll = poll
        self.assertEqual(self.worker.run(), 0)
        self.assertEqual(polls[:3], [20, 19, 18])
        self.assertLess(self.backend.write_count, 20)
        self.assertIsNone(self.backend.device)
        self.assertLessEqual(self.worker.outgoing.qsize(), 128)


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from app.n4.display import DisplayMirror
from app.n4.device_controls import DeviceControls


class SavedScreensaver:
    def __init__(self, path):
        self.path = path
        self.changed = True

    def reload(self):
        changed, self.changed = self.changed, False
        return changed

    def reset(self):
        self.path = None
        self.changed = True


def saved_animation(root):
    path = root / 'saved.webp'
    frames = [Image.new('RGB', (800, 480), color) for color in ('red', 'blue')]
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=[100, 200], lossless=True, loop=0)
    return SavedScreensaver(path)


class DisplayTests(unittest.TestCase):
    def test_independent_knob_brightness_follows_sleep_and_wake_without_changing_lcd(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            device = Mock()
            writes = []
            controls = DeviceControls(device, directory, 'HGMOCK000003', led_writer=writes.append)
            mirror = DisplayMirror(device, Path(directory), 'frame', clock=lambda: now[0],
                                   led_writer=lambda level, normal, asleep: controls.apply_led_level(
                                       level, normal, sleeping=asleep))
            mirror.apply({'event': 'brightness', 'detail': {'level': 20}})
            controls.set_knob_brightness(65)
            mirror.apply({'event': 'brightness', 'detail': {'level': 0}})
            self.assertEqual(writes[-1], 0)
            mirror.notify_input()
            now[0] = .4
            mirror.tick()
            self.assertEqual(writes[-1], 65)
            self.assertEqual(device.set_brightness.call_args.args, (20,))

    def test_brightness_validates_and_applies_percent(self):
        device = Mock()
        mirror = DisplayMirror(device, Path('.'))
        for level in (35, 100, -1, 101, True, '50', None):
            mirror.apply({'event': 'brightness', 'detail': {'level': level}})
        self.assertEqual([call.args[0] for call in device.set_brightness.call_args_list],
                         [35, 100])

    def test_host_sleep_turns_backlight_off_and_wake_restores_brightness(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            device = Mock()
            mirror = DisplayMirror(device, Path(directory), 'frame', clock=lambda: now[0])
            mirror.apply({'event': 'brightness', 'detail': {'level': 35}})
            mirror.apply({'event': 'brightness', 'detail': {'level': 0}})
            mirror.apply({'event': 'brightness', 'detail': {'level': 0}})
            device.transport.sleep.assert_called_once()
            self.assertEqual(device.set_brightness.call_args.args, (35,))
            self.assertTrue(mirror.sleeping)
            mirror.apply({'event': 'brightness', 'detail': {'level': 35}})
            device.wakeScreen.assert_called_once()
            self.assertFalse(mirror.idle)
            self.assertFalse(mirror.sleeping)
            self.assertEqual(device.set_brightness.call_args.args, (0,))
            now[0] = .4
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (35,))

    def test_screensaver_survives_late_key_frames_and_reset_sleep_drops_old_saver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame')
            Image.new('RGB', (800, 480), 'red').save(root / 'lcd.jpg')
            Image.new('RGB', (120, 120), 'blue').save(root / 'key-0.jpg')
            mirror.apply({'event': 'lcdImage', 'detail': {'file': 'lcd.jpg'}})
            self.assertTrue(mirror.idle)
            device.reset_mock()
            mirror.apply({'event': 'keyImage', 'detail': {'keyIndex': 0}})
            device.set_key_image.assert_not_called()
            mirror.apply({'event': 'brightness', 'detail': {'level': 0}})
            device.transport.sleep.assert_called_once()
            self.assertIsNone(mirror.screensaver)
            device.set_brightness.assert_not_called()
            mirror.notify_input()
            device.wakeScreen.assert_called_once()
            self.assertFalse(mirror.idle)
            self.assertEqual(mirror.keys[0].getpixel((60, 60)), (0, 0, 254))

    def test_host_clear_exits_screensaver_before_normal_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame')
            Image.new('RGB', (800, 480), 'red').save(root / 'lcd.jpg')
            mirror.apply({'event': 'lcdImage', 'detail': {'file': 'lcd.jpg'}})
            mirror.apply({'event': 'displayCommand', 'detail': {'type': 'fill', 'color': [0, 0, 0]}})
            self.assertFalse(mirror.idle)
            self.assertFalse(mirror.overlay)

    def test_host_clear_restarts_idle_timeout_after_wake(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            device = Mock()
            mirror = DisplayMirror(device, Path(directory), 'frame', clock=lambda: now[0])
            mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 10}})
            now[0] = 10
            mirror.tick()
            mirror.apply({'event': 'displayCommand', 'detail': {'type': 'fill', 'color': [0, 0, 0]}})
            mirror.tick()
            self.assertFalse(mirror.idle)
            device.transport.sleep.assert_called_once()
            now[0] = 20
            mirror.tick()
            self.assertTrue(mirror.sleeping)
            self.assertEqual(device.transport.sleep.call_count, 2)

    def test_frame_updates_only_changed_region_after_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame')
            image = Image.new('RGB', (200, 100), 'red')
            image.paste('blue', (0, 0, 100, 100))
            image.save(root / 'strip.jpg', quality=95)
            event = {'event': 'touchImage', 'detail': {
                'file': 'strip.jpg', 'region': {'x': 600, 'y': 0, 'w': 200, 'h': 100}}}
            mirror.apply(event)
            first = device.transport.set_background_frame_stream.call_args.args
            self.assertEqual(first[1:], (800, 130, 0, 0))
            mirror.apply(event)
            second = device.transport.set_background_frame_stream.call_args.args
            self.assertEqual(second[1:], (200, 100, 0, 20))
            with Image.open(BytesIO(second[0])) as decoded:
                self.assertEqual(decoded.size, (200, 100))
                self.assertGreater(decoded.getpixel((30, 50))[0], 200)
                self.assertGreater(decoded.getpixel((170, 50))[2], 200)
            event['detail']['region']['x'] = 0
            mirror.apply(event)
            self.assertEqual(device.transport.set_background_frame_stream.call_args.args[1:],
                             (200, 100, 600, 20))

    def test_backlog_keeps_latest_frame_per_region_and_preserves_order(self):
        mirror = DisplayMirror(Mock(), Path('.'), 'frame')
        mirror.apply = Mock()
        def event(x, file):
            return {'event': 'touchImage', 'detail': {
                'file': file, 'region': {'x': x, 'y': 0, 'w': 200, 'h': 100}}}
        old, other, new = event(600, 'old'), event(0, 'other'), event(600, 'new')
        self.assertEqual(mirror.apply_batch([old, other, new]), 2)
        self.assertEqual([call.args[0] for call in mirror.apply.call_args_list], [other, new])

    def test_sleep_ignores_animation_updates_and_input_restores_latest_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [0]
            device = Mock()
            rendered = {}
            def key_image(index, path):
                with Image.open(path) as image:
                    rendered[index] = image.getpixel((60, 60))
            device.set_key_image.side_effect = key_image
            mirror = DisplayMirror(device, root, 'frame', clock=lambda: now[0])
            mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 10}})
            mirror.apply({'event': 'brightness', 'detail': {'level': 35}})
            Image.new('RGB', (120, 120), 'red').save(root / 'key-0.jpg', format='PNG')
            mirror.apply({'event': 'keyImage', 'detail': {'keyIndex': 0}})
            now[0] = 9
            mirror.tick()
            device.transport.sleep.assert_not_called()
            now[0] = 10
            mirror.tick()
            mirror.tick()
            device.transport.sleep.assert_called_once()
            Image.new('RGB', (120, 120), 'blue').save(root / 'key-0.jpg', format='PNG')
            mirror.apply({'event': 'keyImage', 'detail': {'keyIndex': 0}})
            self.assertEqual(rendered[1], (255, 0, 0))
            now[0] = 11
            mirror.notify_input(11)
            self.assertEqual(rendered[1], (0, 0, 255))
            device.wakeScreen.assert_called_once()
            self.assertEqual(device.set_brightness.call_args.args, (0,))
            now[0] = 11.4
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (35,))
            self.assertFalse(mirror.idle)
            now[0] = 20
            mirror.tick()
            self.assertFalse(mirror.idle)

    def test_full_lcd_maps_all_ten_keys_and_strip_and_keeps_action_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = Mock()
            rendered = {}
            def key_image(index, path):
                with Image.open(path) as image:
                    rendered[index] = image.getpixel((60, 60))
            device.set_key_image.side_effect = key_image
            mirror = DisplayMirror(device, root, 'frame')
            Image.new('RGB', (120, 120), 'blue').save(root / 'key-0.jpg', format='PNG')
            mirror.apply({'event': 'keyImage', 'detail': {'keyIndex': 0}})
            image = Image.new('RGB', (800, 480), 'black')
            for index in range(10):
                x, y = 13 + index % 5 * 164, 12 + index // 5 * 160
                image.paste((index * 20, 0, 0), (x, y, x + 120, y + 120))
            image.paste('green', (0, 380, 800, 480))
            image.save(root / 'lcd.jpg', format='PNG')
            mirror.apply_batch([{'event': 'lcdImage', 'detail': {'file': 'lcd.jpg'}}])
            for index in range(10):
                self.assertEqual(rendered[index + 1], (index * 20, 0, 0))
            self.assertFalse((root / 'lcd.jpg').exists())
            strip = device.transport.set_background_frame_stream.call_args.args[0]
            with Image.open(BytesIO(strip)) as frame:
                self.assertGreater(frame.getpixel((300, 50))[1], 100)
            self.assertEqual(mirror.strip.getpixel((300, 50)), (0, 0, 0))
            mirror.notify_input()
            self.assertEqual(rendered[1], (0, 0, 255))

    def test_full_screen_then_individual_key_fill_preserves_order_and_wake_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mirror = DisplayMirror(Mock(), root, 'frame')
            Image.new('RGB', (120, 120), 'red').save(root / 'key-0.jpg', format='PNG')
            events = [
                {'event': 'keyImage', 'detail': {'keyIndex': 0}},
                {'event': 'displayCommand', 'detail': {'type': 'fill', 'color': [0, 0, 0]}},
                {'event': 'displayCommand', 'detail': {'type': 'fill', 'keyIndex': 9, 'color': [1, 2, 3]}},
            ]
            mirror.apply_batch(events)
            self.assertNotIn(0, mirror.keys)
            self.assertEqual(mirror.keys[9].getpixel((0, 0)), (1, 2, 3))
            self.assertEqual(mirror.background.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(mirror.strip.getpixel((0, 0)), (0, 0, 0))

    def test_disabling_timer_leaves_screen_off_until_wake_and_logo_preserves_boot_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            device = Mock()
            mirror = DisplayMirror(device, Path(directory), 'frame', clock=lambda: now[0])
            mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 1}})
            now[0] = 2
            mirror.tick()
            mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 0}})
            self.assertTrue(mirror.idle)
            device.wakeScreen.assert_not_called()
            mirror.apply({'event': 'brightness', 'detail': {'level': 100}})
            self.assertFalse(mirror.idle)
            device.wakeScreen.assert_called_once()
            mirror.apply({'event': 'displayCommand', 'detail': {'type': 'showLogo'}})
            self.assertTrue(mirror.overlay)
            device.send_config.assert_not_called()

    def test_malformed_images_and_colors_leave_display_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame')
            Image.new('RGB', (1, 1), 'red').save(root / 'lcd.jpg')
            for event in [
                {'event': 'lcdImage', 'detail': {'file': 'lcd.jpg'}},
                {'event': 'lcdImage', 'detail': {'file': '../other.jpg'}},
                {'event': 'displayCommand', 'detail': {'type': 'fill', 'color': [1, True, 3]}},
                {'event': 'displayCommand', 'detail': {'type': 'fill', 'keyIndex': 10, 'color': [1, 2, 3]}},
                {'event': 'sleepDuration', 'detail': {'seconds': -1}},
            ]:
                mirror.apply(event)
            self.assertEqual(device.mock_calls, [])

    def test_consumed_and_coalesced_strip_files_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            for name in ('old.jpg', 'new.jpg'):
                Image.new('RGB', (200, 100), 'red').save(root / name)
                events.append({'event': 'touchImage', 'detail': {
                    'file': name, 'region': {'x': 0, 'y': 0, 'w': 200, 'h': 100}}})
            mirror = DisplayMirror(Mock(), root, 'frame')
            self.assertEqual(mirror.apply_batch(events), 1)
            self.assertEqual(list(root.iterdir()), [])

    def test_full_lcd_backlog_coalesces_frames_without_crossing_control_commands(self):
        mirror = DisplayMirror(Mock(), Path('.'), 'frame')
        mirror.apply = Mock()
        def lcd(name):
            return {'event': 'lcdImage', 'detail': {'file': name}}
        old, new, last = lcd('old.jpg'), lcd('new.jpg'), lcd('last.jpg')
        fill = {'event': 'displayCommand', 'detail': {'type': 'fill', 'color': [0, 0, 0]}}
        self.assertEqual(mirror.apply_batch([old, new, fill, last]), 3)
        self.assertEqual([call.args[0] for call in mirror.apply.call_args_list], [new, fill, last])

    def test_saved_animation_uses_one_native_frame_and_ignores_host_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [0]
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame', clock=lambda: now[0],
                                   screensaver_source=saved_animation(root))
            mirror.apply({'event': 'screensaver'})
            device.clearAllIcon.assert_called_once()
            device.set_key_image.assert_not_called()
            device.set_seondscreen_image.assert_not_called()
            self.assertEqual(device.transport.set_background_frame_stream.call_count, 1)
            self.assertEqual(device.refresh.call_count, 1)
            first = device.transport.set_background_frame_stream.call_args.args
            self.assertEqual(first[1:], (800, 480, 0, 0))
            with Image.open(BytesIO(first[0])) as frame:
                self.assertGreater(frame.getpixel((400, 240))[0], 240)
            Image.new('RGB', (800, 480), 'lime').save(root / 'host.jpg')
            mirror.apply_batch([{'event': 'lcdImage', 'detail': {'file': 'host.jpg'}}])
            self.assertEqual(device.transport.set_background_frame_stream.call_count, 1)
            self.assertFalse((root / 'host.jpg').exists())
            now[0] = .05
            mirror.tick()
            self.assertEqual(device.transport.set_background_frame_stream.call_count, 1)
            now[0] = .1
            mirror.tick()
            self.assertEqual(device.transport.set_background_frame_stream.call_count, 2)
            self.assertEqual(device.refresh.call_count, 2)
            with Image.open(BytesIO(device.transport.set_background_frame_stream.call_args.args[0])) as frame:
                self.assertGreater(frame.getpixel((400, 240))[2], 240)
            device.set_key_image.assert_not_called()
            device.set_seondscreen_image.assert_not_called()

    def test_saved_screensaver_displays_before_dimming_and_keeps_knob_lights_off(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [0]
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame', clock=lambda: now[0],
                                   screensaver_source=saved_animation(root))
            mirror.apply({'event': 'brightness', 'detail': {'level': 40}})
            device.reset_mock()
            mirror.apply({'event': 'screensaver'})
            device.transport.set_background_frame_stream.assert_called_once()
            device.set_brightness.assert_not_called()
            device.set_led_brightness.assert_called_once_with(0)
            now[0] = .15
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (20,))
            now[0] = .31
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (0,))
            device.set_led_brightness.assert_called_once_with(0)
            device.transport.sleep.assert_not_called()
            self.assertTrue(mirror.idle)

    def test_wake_restores_cached_surfaces_before_backlight_and_fades_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [0]
            device = Mock()
            rendered = {}
            def key_image(index, path):
                with Image.open(path) as image:
                    rendered[index] = image.getpixel((60, 60))
            device.set_key_image.side_effect = key_image
            mirror = DisplayMirror(device, root, 'frame', clock=lambda: now[0])
            mirror.apply({'event': 'brightness', 'detail': {'level': 40}})
            Image.new('RGB', (120, 120), 'blue').save(root / 'key-0.jpg', format='PNG')
            mirror.apply({'event': 'keyImage', 'detail': {'keyIndex': 0}})
            mirror.apply({'event': 'brightness', 'detail': {'level': 0}})
            device.reset_mock()
            mirror.notify_input()
            names = [call[0] for call in device.mock_calls]
            self.assertEqual(names[0], 'set_brightness')
            self.assertEqual(device.set_brightness.call_args.args, (0,))
            self.assertLess(names.index('transport.set_background_frame_stream'), names.index('wakeScreen'))
            self.assertLess(max(index for index, name in enumerate(names) if name == 'set_key_image'),
                            names.index('wakeScreen'))
            self.assertLess(names.index('refresh'), names.index('wakeScreen'))
            self.assertEqual(rendered[1], (0, 0, 255))
            now[0] = .04
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (0,))
            now[0] = .23
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (20,))
            self.assertEqual(device.set_led_brightness.call_args.args, (20,))
            now[0] = .4
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (40,))
            self.assertEqual(device.set_led_brightness.call_args.args, (40,))
            device.reset_mock()
            mirror.apply({'event': 'displayCommand', 'detail': {'type': 'fill', 'color': [0, 0, 0]}})
            device.set_key_image.assert_not_called()
            device.transport.set_background_frame_stream.assert_not_called()
            self.assertEqual(mirror.keys[0].getpixel((60, 60)), (0, 0, 255))

    def test_reset_saved_screensaver_while_idle_turns_screen_off(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [0]
            source = saved_animation(root)
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame', clock=lambda: now[0], screensaver_source=source)
            mirror.apply({'event': 'screensaver'})
            source.reset()
            now[0] = 1
            mirror.tick()
            device.transport.sleep.assert_called_once()
            self.assertTrue(mirror.sleeping)
            self.assertFalse(mirror.native_active)
            device.reset_mock()
            now[0] = 1.2
            mirror.tick()
            device.transport.set_background_frame_stream.assert_not_called()

    def test_manual_sleep_starts_on_first_fade_step_after_key_release(self):
        now = [0]
        device = Mock()
        mirror = DisplayMirror(device, Path('.'), clock=lambda: now[0])
        mirror.apply({'event': 'brightness', 'detail': {'level': 40}})
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 300}})
        now[0] = 5
        mirror.notify_input(key_release=True)
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 0}})
        device.reset_mock()
        now[0] = 5.01
        mirror.apply({'event': 'brightness', 'detail': {'level': 39}})
        device.transport.sleep.assert_called_once()
        device.set_brightness.assert_not_called()
        self.assertTrue(mirror.idle)
        mirror.apply({'event': 'brightness', 'detail': {'level': 20}})
        device.transport.sleep.assert_called_once()
        device.set_brightness.assert_not_called()

    def test_host_sleep_sequence_after_automatic_sleep_does_not_flash_screen_on(self):
        now = [0]
        device = Mock()
        mirror = DisplayMirror(device, Path('.'), clock=lambda: now[0])
        mirror.apply({'event': 'brightness', 'detail': {'level': 40}})
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 10}})
        device.reset_mock()
        now[0] = 10
        mirror.tick()
        now[0] = 10.01
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 0}})
        for level in (39, 20, 0):
            mirror.apply({'event': 'brightness', 'detail': {'level': level}})
        device.transport.sleep.assert_called_once()
        device.wakeScreen.assert_not_called()
        device.set_brightness.assert_not_called()
        self.assertTrue(mirror.sleeping)

    def test_key_update_during_logo_wake_preserves_brightness_fade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = [0]
            device = Mock()
            mirror = DisplayMirror(device, root, 'frame', clock=lambda: now[0])
            mirror.apply({'event': 'brightness', 'detail': {'level': 40}})
            mirror.apply({'event': 'brightness', 'detail': {'level': 0}})
            mirror.apply({'event': 'displayCommand', 'detail': {'type': 'showLogo'}})
            self.assertEqual(device.set_brightness.call_args.args, (0,))
            Image.new('RGB', (120, 120), 'blue').save(root / 'key-0.jpg', format='PNG')
            now[0] = .1
            mirror.apply({'event': 'keyImage', 'detail': {'keyIndex': 0}})
            now[0] = .15
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (20,))
            now[0] = .4
            mirror.tick()
            self.assertEqual(device.set_brightness.call_args.args, (40,))
            self.assertFalse(mirror.idle)

    def test_ordinary_brightness_or_disabling_timer_alone_does_not_sleep(self):
        now = [0]
        device = Mock()
        mirror = DisplayMirror(device, Path('.'), clock=lambda: now[0])
        mirror.apply({'event': 'brightness', 'detail': {'level': 40}})
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 300}})
        mirror.notify_input(key_release=True)
        mirror.apply({'event': 'brightness', 'detail': {'level': 30}})
        self.assertFalse(mirror.idle)
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 0}})
        now[0] = 1
        mirror.tick()
        self.assertFalse(mirror.idle)
        mirror.apply({'event': 'brightness', 'detail': {'level': 20}})
        self.assertFalse(mirror.idle)
        mirror.notify_input(key_release=True)
        mirror.apply({'event': 'sleepDuration', 'detail': {'seconds': 0}})
        now[0] = 1.2
        mirror.tick()
        mirror.apply({'event': 'brightness', 'detail': {'level': 10}})
        device.transport.sleep.assert_not_called()
        self.assertFalse(mirror.idle)


if __name__ == '__main__':
    unittest.main()

"""Stream Deck LCD screensavers preserve normal action images and touch caches."""
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mirabox-python/src'))
from app.n4.display import DisplayMirror
from hellgato_mirabox.models.n4_pro.state import DisplayState


class StreamedLcdTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = 0
        self.device = Mock()
        self.device.set_key_image.return_value = 0
        self.state = DisplayState(self.device, self.root, clock=lambda: self.now, lcd_mode='stream-screensaver')

    def output(self, surface, color, size=(120, 120), region=None):
        self.state.output(surface, Image.new('RGB', size, color), region, lambda: None)

    def control(self, kind, **options):
        return self.state.control({'control': kind, **options}, lambda: None)

    def capture_keys(self):
        keys = {}
        def capture(index, path):
            with Image.open(path) as image:
                keys[index - 1] = image.copy()
            return 0
        self.device.set_key_image.side_effect = capture
        return keys

    def test_streamed_overlay_preserves_blue_action_and_green_touch_then_input_restores_them(self):
        keys = self.capture_keys()
        panels = {}
        def capture_panel(index, path):
            with Image.open(path) as image:
                panels[index] = image.copy()
            return 0
        self.device.set_seondscreen_image.side_effect = capture_panel
        self.output('key:0', 'blue')
        self.output('touch', 'green', (800, 100), (0, 0, 800, 100))
        self.output('lcd', 'red', (800, 480))
        self.assertTrue(self.state.idle)
        self.assertTrue(self.state.state()['screensaverActive'])
        self.assertEqual(keys[0].getpixel((60, 60)), (255, 0, 0))
        self.assertEqual(self.state.keys[0].getpixel((60, 60)), (0, 0, 255))
        self.assertEqual(self.state.background.getpixel((60, 60)), (0, 0, 0))
        self.assertEqual(self.state.strip.getpixel((60, 60)), (0, 128, 0))
        self.assertEqual((self.state.display_observations()['keyCacheCount'],
                          self.state.display_observations()['keyCacheClears']), (1, 0))
        self.now = 2
        self.state.notify_input(self.now, True, lambda: None)
        self.assertFalse(self.state.idle)
        self.assertFalse(self.state.state()['screensaverActive'])
        self.assertEqual(keys[0].getpixel((60, 60)), (0, 0, 255))
        self.assertEqual(panels[11].getpixel((60, 60)), (0, 128, 0))
        self.assertEqual(self.state.display_observations()['keys'][0]['lastWriter'], 'restore')

    def test_updates_while_overlay_is_idle_are_cached_and_host_clear_restores_latest_images(self):
        keys = self.capture_keys()
        self.output('key:2', 'blue')
        self.output('lcd', 'black', (800, 480))
        writes = self.device.set_key_image.call_count
        self.output('key:2', 'yellow')
        self.output('touch', 'blue', (20, 10), (199, 5, 20, 10))
        self.assertEqual(self.device.set_key_image.call_count, writes)
        result = self.control('clear')
        self.assertEqual(result, {'normalStateEffect': 'background-only'})
        self.assertFalse(self.state.idle)
        self.assertEqual(keys[2].getpixel((60, 60)), (255, 255, 0))
        self.assertEqual(self.state.strip.getpixel((200, 6)), (0, 0, 255))
        self.assertEqual(self.state.display_observations()['keyCacheClears'], 0)

    def test_late_stream_frame_after_input_wake_is_ignored_until_host_fade_finishes(self):
        keys = self.capture_keys()
        self.output('key:0', 'blue')
        self.output('lcd', 'red', (800, 480))
        self.state.notify_input(self.now, True, lambda: None)
        writes = self.device.set_key_image.call_count
        self.now = .2
        self.output('lcd', 'black', (800, 480))
        self.assertFalse(self.state.idle)
        self.assertEqual(self.device.set_key_image.call_count, writes)
        self.assertEqual(keys[0].getpixel((60, 60)), (0, 0, 255))
        self.now = .4
        self.output('lcd', 'red', (800, 480))
        self.assertTrue(self.state.idle)
        self.assertEqual(self.device.set_key_image.call_count, writes + 10)

    def test_full_fill_replaces_normal_caches_and_key_fill_during_stream_is_restored_on_wake(self):
        keys = self.capture_keys()
        self.output('key:3', 'blue')
        self.output('lcd', 'red', (800, 480))
        self.control('fill', surfaceId='key:3', color=[255, 255, 0])
        self.control('wake')
        self.assertEqual(keys[3].getpixel((60, 60)), (255, 255, 0))
        self.now = 1
        self.output('lcd', 'black', (800, 480))
        result = self.control('fill', color=[0, 128, 0])
        self.assertEqual(result, {'normalStateEffect': 'replace-all'})
        self.assertFalse(self.state.idle)
        self.assertFalse(self.state.state()['screensaverActive'])
        self.assertEqual(self.state.keys, {})
        self.assertEqual(keys[3].getpixel((60, 60)), (0, 128, 0))
        self.assertEqual(self.state.background.getpixel((60, 60)), (0, 128, 0))
        self.assertEqual(self.state.strip.getpixel((60, 60)), (0, 128, 0))
        self.assertEqual(self.state.display_observations()['keyCacheClears'], 1)

    def test_reset_sleep_discards_stale_stream_and_new_frame_wakes_sleep_without_dropping_actions(self):
        keys = self.capture_keys()
        self.output('lcd', 'red', (800, 480))
        self.output('key:0', 'blue')
        self.control('brightness', value=0)
        self.assertTrue(self.state.sleeping)
        self.assertIsNone(self.state.streamed_screensaver)
        self.output('lcd', 'black', (800, 480))
        self.assertFalse(self.state.sleeping)
        self.device.wakeScreen.assert_called_once()
        self.control('wake')
        self.assertEqual(keys[0].getpixel((60, 60)), (0, 0, 255))

    def test_generic_lcd_output_retains_normal_full_frame_contract_and_mode_is_explicit(self):
        state = DisplayState(self.device, self.root)
        state.output('key:0', Image.new('RGB', (120, 120), 'blue'), None, lambda: None)
        state.output('lcd', Image.new('RGB', (800, 480), 'red'), None, lambda: None)
        self.assertFalse(state.idle)
        self.assertEqual(state.keys, {})
        self.assertEqual(state.background.getpixel((60, 60)), (255, 0, 0))
        with self.assertRaises(ValueError):
            DisplayState(self.device, self.root, lcd_mode='unknown')

    def test_later_screensaver_control_reuses_overlay_without_replacing_updated_normal_cache(self):
        keys = self.capture_keys()
        self.output('key:0', 'blue')
        self.output('lcd', 'red', (800, 480))
        self.control('wake')
        self.output('key:0', 'yellow')
        self.now = 1
        self.control('screensaver')
        self.assertTrue(self.state.stream_active)
        self.assertEqual(keys[0].getpixel((60, 60)), (255, 0, 0))
        self.assertEqual(self.state.keys[0].getpixel((60, 60)), (255, 255, 0))
        self.control('wake')
        self.assertEqual(keys[0].getpixel((60, 60)), (255, 255, 0))
        self.assertEqual(self.state.display_observations()['keyCacheClears'], 0)

    def test_streamed_images_and_wake_restoration_match_legacy_png_and_frame_bytes(self):
        for strip_mode in ('frame', 'panels'):
            with self.subTest(strip_mode=strip_mode):
                device, legacy_device = Mock(), Mock()
                state = DisplayState(device, self.root, strip_mode=strip_mode,
                                     clock=lambda: self.now, lcd_mode='stream-screensaver')
                baseline = DisplayMirror(legacy_device, self.root, strip_mode=strip_mode, clock=lambda: self.now)
                observed, expected = [], []
                def capture(target, kind):
                    def record(index, path):
                        target.append((kind, index, Path(path).read_bytes()))
                        return 0
                    return record
                for target, record in ((device, observed), (legacy_device, expected)):
                    target.set_key_image.side_effect = capture(record, 'key')
                    target.set_seondscreen_image.side_effect = capture(record, 'panel')
                events = (
                    ('key:0', 'blue', (120, 120), None, 'key-0.jpg', {'event': 'keyImage', 'detail': {'keyIndex': 0}}),
                    ('touch', 'green', (800, 100), (0, 0, 800, 100), 'touch.jpg',
                     {'event': 'touchImage', 'detail': {'file': 'touch.jpg', 'region': {'x': 0, 'y': 0, 'w': 800, 'h': 100}}}),
                    ('lcd', 'red', (800, 480), None, 'lcd.jpg', {'event': 'lcdImage', 'detail': {'file': 'lcd.jpg'}}),
                    ('key:0', 'yellow', (120, 120), None, 'key-0.jpg', {'event': 'keyImage', 'detail': {'keyIndex': 0}}),
                )
                for surface, color, size, region, filename, event in events:
                    encoded = BytesIO()
                    Image.new('RGB', size, color).save(encoded, format='JPEG')
                    (self.root / filename).write_bytes(encoded.getvalue())
                    with Image.open(BytesIO(encoded.getvalue())) as source:
                        state.output(surface, source.convert('RGB'), region, lambda: None)
                    baseline.apply(event)
                state.notify_input(self.now, True, lambda: None)
                baseline.notify_input(self.now, True)
                self.assertEqual(observed, expected)
                self.assertEqual(device.transport.set_background_frame_stream.call_args_list,
                                 legacy_device.transport.set_background_frame_stream.call_args_list)


if __name__ == '__main__':
    unittest.main()

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from PIL import Image

from app.n4.screensaver import ScreensaverSource


def qstring(value):
    encoded = value.encode('utf-16-be')
    return struct.pack('>I', len(encoded)) + encoded


def variant(value):
    if isinstance(value, dict):
        return struct.pack('>II', 8, len(value)) + b''.join(
            qstring(key) + variant(item) for key, item in value.items())
    if isinstance(value, str):
        return struct.pack('>I', 10) + qstring(value)
    if isinstance(value, int):
        return struct.pack('>Ii', 2, value)
    raise TypeError(value)


def settings(devices):
    raw = variant({f'@(128)[4057/132/{serial}]': {
        'DeviceName': 'Stream Deck +',
        'map_dev_brightness': 35,
        'map_dev_sleep_wallpaper_path': 'wallpaper://image/?' + urlencode({'path': str(path)}) if path else '',
    } for serial, path in devices.items()})
    return ('@Variant(' + raw.decode('latin-1') + ')').encode('utf-16-le')


class ScreensaverSourceTests(unittest.TestCase):
    def test_reads_existing_animated_asset_for_matching_device_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'animated image.webp'
            frames = [Image.new('RGB', (800, 480), color) for color in ('red', 'blue')]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=[60, 140], lossless=True)
            before = path.read_bytes()
            source = ScreensaverSource('HGMOCK000007', lambda: settings({
                'HGMOCK000006': path.with_name('other.webp'), 'HGMOCK000007': path}))
            self.assertTrue(source.reload())
            self.assertEqual(source.path, path)
            self.assertIsNone(source.error)
            self.assertFalse(source.reload())
            source.close()
            self.assertEqual(path.read_bytes(), before)
            with Image.open(path) as image:
                self.assertEqual(image.n_frames, 2)
                image.load()
                self.assertEqual(image.info['duration'], 60)
                image.seek(1)
                image.load()
                self.assertEqual(image.info['duration'], 140)

    def test_reloads_setting_changes_file_changes_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'first.png'
            second = Path(directory) / 'second.png'
            Image.new('RGB', (16, 16), 'red').save(first)
            Image.new('RGB', (16, 16), 'blue').save(second)
            selected = {'device': first}
            source = ScreensaverSource('device', lambda: settings(selected))
            self.assertTrue(source.reload())
            selected['device'] = second
            self.assertTrue(source.reload())
            self.assertEqual(source.path, second)
            Image.new('RGB', (24, 24), 'blue').save(second)
            self.assertTrue(source.reload())
            selected['device'] = None
            self.assertTrue(source.reload())
            self.assertIsNone(source.path)
            self.assertFalse(source.reload())

    def test_missing_or_malformed_settings_do_not_crash(self):
        for payload in (None, b'bad', b'\x00\x00', '@Variant()', '@Variant(\0\0\0\x08\xff\xff\xff\xff)', settings({'other': ''})):
            with self.subTest(payload=payload):
                source = ScreensaverSource('device', lambda: payload)
                self.assertFalse(source.reload())
                self.assertIsNone(source.path)

    def test_invalid_file_and_resource_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            source = ScreensaverSource('device', lambda: settings({'device': path}))
            path.write_bytes(b'not an image')
            source.reload()
            self.assertIsNone(source.path)
            self.assertIsNotNone(source.error)
            Image.new('RGB', (16, 16), 'red').save(path)
            with patch('app.n4.screensaver.MAX_FRAME_PIXELS', 128):
                source.reload()
                self.assertIsNone(source.path)
            self.assertTrue(source.reload())
            self.assertEqual(source.path, path)

    def test_removed_asset_and_registry_error_clear_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            Image.new('RGB', (16, 16), 'red').save(path)
            source = ScreensaverSource('device', lambda: settings({'device': path}))
            source.reload()
            path.unlink()
            self.assertTrue(source.reload())
            self.assertIsNone(source.path)
            source.read_settings = lambda: (_ for _ in ()).throw(OSError('registry unavailable'))
            self.assertFalse(source.reload())
            self.assertIn('registry unavailable', source.error)
            source.read_settings = lambda: None
            self.assertFalse(source.reload())
            self.assertIsNone(source.error)

    def test_animation_limit_and_nonlocal_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'animation.webp'
            frames = [Image.new('RGB', (16, 16), color) for color in ('red', 'blue')]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, lossless=True)
            source = ScreensaverSource('device', lambda: settings({'device': path}))
            with patch('app.n4.screensaver.MAX_TOTAL_PIXELS', 300):
                self.assertFalse(source.reload())
                self.assertIsNone(source.path)
                self.assertIn('animation size', source.error)
            source.read_settings = lambda: settings({'device': 'relative.png'})
            self.assertFalse(source.reload())
            self.assertIsNone(source.path)
            self.assertIn('absolute', source.error)


if __name__ == '__main__':
    unittest.main()

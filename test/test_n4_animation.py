import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.n4.animation import NativeAnimation


def color(data, point=(400, 240)):
    with Image.open(BytesIO(data)) as frame:
        assert frame.size == (800, 480)
        return frame.getpixel(point)


class NativeAnimationTests(unittest.TestCase):
    def test_animated_webp_uses_saved_durations_and_skips_expired_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'saved.webp'
            frames = [Image.new('RGB', (800, 480), value)
                      for value in ('red', 'lime', 'blue')]
            frames[0].save(path, save_all=True, append_images=frames[1:],
                           duration=[10, 20, 40], lossless=True, loop=0)
            animation = NativeAnimation(path)
            path.unlink()
            self.assertGreater(color(animation.frame(10))[0], 240)
            self.assertIsNone(animation.frame(10.005))
            self.assertGreater(color(animation.frame(10.015))[1], 240)
            self.assertGreater(color(animation.frame(10.035))[2], 240)
            self.assertGreater(color(animation.frame(11.975))[1], 240)
            self.assertIsNone(animation.frame(11.976))
            self.assertGreater(color(animation.frame(20, restart=True))[0], 240)

    def test_gif_duration_boundaries_loop_without_reencoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'saved.gif'
            first = Image.new('RGB', (20, 20), 'red')
            first.save(path, save_all=True,
                       append_images=[Image.new('RGB', (20, 20), 'blue')],
                       duration=[100, 200], loop=0)
            animation = NativeAnimation(path)
            start = animation.frame(0)
            self.assertGreater(color(animation.frame(.1))[2], 240)
            self.assertIsNone(animation.frame(.299))
            self.assertIs(animation.frame(.3), start)

    def test_static_image_composites_alpha_pads_rotates_and_emits_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'saved.png'
            image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
            image.paste((255, 0, 0, 128), (0, 0, 50, 100))
            image.save(path)
            animation = NativeAnimation(path)
            frame = animation.frame(0)
            self.assertLess(max(color(frame, (100, 240))), 5)
            self.assertLess(max(color(frame, (300, 240))), 5)
            self.assertTrue(120 <= color(frame, (500, 240))[0] <= 135)
            self.assertIsNone(animation.frame(100))
            self.assertIs(animation.frame(101, restart=True), frame)
            animation.clear()
            self.assertIsNone(animation.frame(102, restart=True))

    def test_limits_reject_files_without_replacing_loaded_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'saved.png'
            Image.new('RGB', (800, 480), 'red').save(path)
            animation = NativeAnimation(path)
            first = animation.frame(0)
            for name in ('MAX_FILE_BYTES', 'MAX_FRAME_PIXELS', 'MAX_TOTAL_PIXELS',
                         'MAX_FRAMES', 'MAX_ENCODED_BYTES'):
                with self.subTest(limit=name), patch('app.n4.animation.' + name, 0):
                    with self.assertRaises(ValueError):
                        animation.load(path)
                self.assertIs(animation.frame(1, restart=True), first)


if __name__ == '__main__':
    unittest.main()

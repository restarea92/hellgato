import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from app.n4.display import DisplayMirror


class DisplayTests(unittest.TestCase):
    def test_brightness_validates_and_applies_percent(self):
        device = Mock()
        mirror = DisplayMirror(device, Path('.'))
        for level in (0, 35, 100, -1, 101, True, '50', None):
            mirror.apply({'event': 'brightness', 'detail': {'level': level}})
        self.assertEqual([call.args[0] for call in device.set_brightness.call_args_list],
                         [0, 35, 100])

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


if __name__ == '__main__':
    unittest.main()

from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch as mock_patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from display import DisplayMirror


class DisplayMirrorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.images = Path(self.temporary.name)
        self.device = Mock()
        self.mirror = DisplayMirror(self.device, self.images, 'frame')

    def patch(self, x, width, color, filename, region=True):
        Image.new('RGB', (width, 100), color).save(self.images / filename, format='PNG')
        detail = {'file': filename}
        if region:
            detail['region'] = {'x': x, 'y': 0, 'w': width, 'h': 100}
        return {'event': 'touchImage', 'detail': detail}

    def test_dial_burst_sends_one_frame_with_all_latest_regions(self):
        for index, color in enumerate(['red', 'green', 'blue', 'white']):
            self.mirror.stage(self.patch(index * 200, 200, color, f'{index}.png'))
        self.mirror.stage(self.patch(0, 200, 'yellow', 'latest.png'))
        self.device.transport.set_background_frame_stream.assert_not_called()
        self.mirror.flush()
        self.device.transport.set_background_frame_stream.assert_called_once()
        data, *geometry = self.device.transport.set_background_frame_stream.call_args.args
        self.assertEqual(geometry, [800, 130, 0, 0])
        self.assertEqual(self.mirror.strip.getpixel((100, 50)), (255, 255, 0))
        self.assertEqual(self.mirror.strip.getpixel((500, 50)), (0, 0, 255))
        with Image.open(BytesIO(data)) as output:
            self.assertEqual(output.size, (800, 130))
            self.assertGreater(output.getpixel((700, 70))[0], 240)
            self.assertLess(output.getpixel((300, 70))[0], 10)
        self.mirror.flush()
        self.device.transport.set_background_frame_stream.assert_called_once()

    def test_full_strip_without_region_is_displayed(self):
        self.mirror.apply(self.patch(0, 800, 'red', 'full.png', region=False))
        self.device.transport.set_background_frame_stream.assert_called_once()
        self.assertEqual(self.mirror.strip.getpixel((799, 99)), (255, 0, 0))

    @mock_patch('display.time.monotonic')
    def test_rate_limit_keeps_latest_frame_and_flushes_without_new_events(self, clock):
        clock.return_value = 10
        self.mirror.stage(self.patch(0, 200, 'red', 'first.png'))
        self.mirror.flush(minimum_interval=1 / 30)
        clock.return_value = 10.01
        self.mirror.stage(self.patch(0, 200, 'blue', 'second.png'))
        self.mirror.flush(minimum_interval=1 / 30)
        self.mirror.stage(self.patch(0, 200, 'yellow', 'latest.png'))
        self.mirror.flush(minimum_interval=1 / 30)
        self.device.transport.set_background_frame_stream.assert_called_once()
        clock.return_value = 10.04
        self.mirror.flush(minimum_interval=1 / 30)
        self.assertEqual(self.device.transport.set_background_frame_stream.call_count, 2)
        self.assertEqual(self.mirror.strip.getpixel((100, 50)), (255, 255, 0))
        self.assertEqual(self.mirror.dirty_panels, set())

    def test_failed_usb_transfer_keeps_final_frame_for_retry(self):
        self.mirror.stage(self.patch(0, 200, 'red', 'first.png'))
        self.device.transport.set_background_frame_stream.side_effect = [OSError('USB busy'), None]
        with self.assertRaises(OSError):
            self.mirror.flush()
        self.mirror.stage(self.patch(200, 200, 'blue', 'second.png'))
        self.mirror.flush()
        self.assertEqual(self.device.transport.set_background_frame_stream.call_count, 2)
        self.assertEqual(self.mirror.dirty_panels, set())
        self.assertEqual(self.mirror.strip.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(self.mirror.strip.getpixel((200, 0)), (0, 0, 255))

    def test_panel_mode_only_flushes_each_affected_panel_once(self):
        self.mirror.strip_mode = 'panels'
        self.mirror.stage(self.patch(190, 30, 'red', 'boundary.png'))
        self.mirror.stage(self.patch(200, 20, 'blue', 'latest.png'))
        self.mirror.flush()
        self.assertEqual([call.args[0] for call in self.device.set_seondscreen_image.call_args_list], [11, 12])
        self.assertEqual(list(self.images.glob('tmp*')), [])

    def test_invalid_bounds_and_mismatched_dimensions_do_not_send(self):
        invalid = self.patch(790, 20, 'red', 'invalid.png')
        self.mirror.apply(invalid)
        mismatch = self.patch(0, 200, 'red', 'mismatch.png')
        mismatch['detail']['region']['w'] = 199
        self.mirror.apply(mismatch)
        self.device.transport.set_background_frame_stream.assert_not_called()


if __name__ == '__main__':
    unittest.main()

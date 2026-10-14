"""Preserved N4 image writer transforms from app/n4/display.py.

Only vendor-required temporary PNGs use files. IPC always carries bounded bytes.
The caller serializes operations and guards each individual native write deadline.
"""
from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image


class DisplayWriter:
    def __init__(self, device, images_dir, strip_mode='panels'):
        if strip_mode not in ('frame', 'panels'):
            raise ValueError('Unknown N4 strip mode')
        self.strip_mode = strip_mode
        self.frame_initialized = False
        self.device = device
        self.images_dir = Path(images_dir)
        self.strip = Image.new('RGB', (800, 100), 'black')

    def key(self, index, image, check):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
            path = Path(temporary.name)
        try:
            image.save(path)
            check()
            result = self.device.set_key_image(index + 1, str(path))
            if type(result) is int and result < 0:
                raise OSError('The vendor SDK rejected the key image')
        finally:
            path.unlink(missing_ok=True)

    def frame(self, image, check, x=0, y=0):
        encoded = BytesIO()
        image.save(encoded, format='JPEG', quality=95)
        check()
        self.device.transport.set_background_frame_stream(encoded.getvalue(), image.width, image.height, x, y)

    def touch(self, image, region, check):
        x, y, width, height = region
        self.strip.paste(image, (x, y))
        if self.strip_mode == 'frame':
            # Preserve the legacy N4 framebuffer layout: 20px offset in 130px,
            # 180-degree rotation, then inverted partial-update coordinates.
            if not self.frame_initialized:
                frame = Image.new('RGB', (800, 130), 'black')
                frame.paste(self.strip.transpose(Image.Transpose.ROTATE_180), (0, 20))
                self.frame(frame, check)
                self.frame_initialized = True
            else:
                frame = self.strip.crop((x, y, x + width, y + height)).transpose(Image.Transpose.ROTATE_180)
                self.frame(frame, check, 800 - x - width, 120 - y - height)
            return
        for index in range(x // 200, (x + width - 1) // 200 + 1):
            panel = self.strip.crop((index * 200, 0, (index + 1) * 200, 100))
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
                path = Path(temporary.name)
            try:
                panel.save(path)
                check()
                result = self.device.set_seondscreen_image(11 + index, str(path))
                if type(result) is int and result < 0:
                    raise OSError('The vendor SDK rejected the touch-panel image')
            finally:
                path.unlink(missing_ok=True)

    def lcd(self, image, check):
        self.frame(image.transpose(Image.Transpose.ROTATE_180), check)
        for index in range(10):
            x, y = 13 + (index % 5) * 164, 12 + (index // 5) * 160
            self.key(index, image.crop((x, y, x + 120, y + 120)), check)
        self.frame_initialized = False
        self.touch(image.crop((0, 380, 800, 480)), (0, 0, 800, 100), check)

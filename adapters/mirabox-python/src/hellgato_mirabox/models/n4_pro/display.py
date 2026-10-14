"""Preserved N4 image writer transforms from app/n4/display.py.

Only vendor-required temporary PNGs use files. IPC always carries bounded bytes.
The caller serializes operations and guards each individual native write deadline.
"""
from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image


class DisplayWriter:
    def __init__(self, device, images_dir):
        self.device = device
        self.images_dir = Path(images_dir)
        self.strip = Image.new('RGB', (800, 100), 'black')

    def key(self, index, image, check):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
            path = Path(temporary.name)
        try:
            image.save(path)
            check()
            self.device.set_key_image(index + 1, str(path))
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
        for index in range(x // 200, (x + width - 1) // 200 + 1):
            panel = self.strip.crop((index * 200, 0, (index + 1) * 200, 100))
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
                path = Path(temporary.name)
            try:
                panel.save(path)
                check()
                self.device.set_seondscreen_image(11 + index, str(path))
            finally:
                path.unlink(missing_ok=True)

    def lcd(self, image, check):
        self.frame(image.transpose(Image.Transpose.ROTATE_180), check)
        for index in range(10):
            x, y = 13 + (index % 5) * 164, 12 + (index // 5) * 160
            self.key(index, image.crop((x, y, x + 120, y + 120)), check)
        self.touch(image.crop((0, 380, 800, 480)), (0, 0, 800, 100), check)

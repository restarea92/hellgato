"""Mirror CORA key and feedback images to N4 Pro displays."""

from pathlib import Path
from io import BytesIO
import tempfile
import time

from PIL import Image


class DisplayMirror:
    def __init__(self, device, images_dir: Path, strip_mode='panels'):
        self.device = device
        self.images_dir = images_dir
        self.strip = Image.new('RGB', (800, 100), 'black')
        self.strip_mode = strip_mode
        self.dirty_panels = set()
        self.next_flush = 0

    def apply(self, event):
        self.stage(event)
        self.flush()

    def stage(self, event):
        detail = event.get('detail', {})
        if event.get('event') == 'keyImage':
            index = detail.get('keyIndex')
            if type(index) is not int or not 0 <= index < 10:
                return
            image_path = self.images_dir / f'key-{index}.jpg'
            if image_path.is_file():
                self.device.set_key_image(index + 1, str(image_path))
        elif event.get('event') == 'touchImage':
            region = detail.get('region') or {'x': 0, 'y': 0, 'w': 800, 'h': 100}
            x, y, width, height = (region.get(key) for key in ('x', 'y', 'w', 'h'))
            if not all(type(value) is int for value in (x, y, width, height)):
                return
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 800 or y + height > 100:
                return
            image_path = self.images_dir / detail['file']
            with Image.open(image_path) as image:
                if image.size != (width, height):
                    return
                self.strip.paste(image.convert('RGB'), (x, y))
            self.dirty_panels.update(range(x // 200, (x + width - 1) // 200 + 1))

    def flush(self, minimum_interval=0):
        if not self.dirty_panels or time.monotonic() < self.next_flush:
            return
        self.next_flush = time.monotonic() + minimum_interval
        if self.strip_mode == 'frame':
            frame = Image.new('RGB', (800, 130), 'black')
            frame.paste(self.strip.transpose(Image.Transpose.ROTATE_180), (0, 20))
            encoded = BytesIO()
            frame.save(encoded, format='JPEG', quality=95)
            self.device.transport.set_background_frame_stream(encoded.getvalue(), 800, 130, 0, 0)
            self.dirty_panels.clear()
            return
        for index in sorted(self.dirty_panels):
            panel = self.strip.crop((index * 200, 0, (index + 1) * 200, 100))
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
                temporary_path = Path(temporary.name)
            try:
                panel.save(temporary_path)
                self.device.set_seondscreen_image(11 + index, str(temporary_path))
                self.dirty_panels.remove(index)
            finally:
                temporary_path.unlink(missing_ok=True)

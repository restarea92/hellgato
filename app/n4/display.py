"""Mirror CORA key and feedback images to N4 Pro displays."""

from pathlib import Path
from io import BytesIO
import tempfile

from PIL import Image


class DisplayMirror:
    def __init__(self, device, images_dir: Path, strip_mode='panels'):
        self.device = device
        self.images_dir = images_dir
        self.strip = Image.new('RGB', (800, 100), 'black')
        self.strip_mode = strip_mode
        self.frame_initialized = False

    def apply_batch(self, events):
        latest = {}
        for event in events:
            detail = event.get('detail', {})
            kind = event.get('event')
            if kind == 'touchImage':
                region = detail.get('region', {})
                key = ('touchImage', *(region.get(name) for name in ('x', 'y', 'w', 'h')))
            else:
                key = (kind, detail.get('keyIndex'))
            latest.pop(key, None)
            latest[key] = event
        for event in latest.values():
            self.apply(event)
        return len(latest)

    def apply(self, event):
        detail = event.get('detail', {})
        if event.get('event') == 'brightness':
            level = detail.get('level')
            if type(level) is int and 0 <= level <= 100:
                self.device.set_brightness(level)
        elif event.get('event') == 'keyImage':
            index = detail.get('keyIndex')
            if type(index) is not int or not 0 <= index < 10:
                return
            image_path = self.images_dir / f'key-{index}.jpg'
            if image_path.is_file():
                self.device.set_key_image(index + 1, str(image_path))
        elif event.get('event') == 'touchImage':
            region = detail.get('region', {})
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
            if self.strip_mode == 'frame':
                if not self.frame_initialized:
                    frame = Image.new('RGB', (800, 130), 'black')
                    frame.paste(self.strip.transpose(Image.Transpose.ROTATE_180), (0, 20))
                    frame_x, frame_y = 0, 0
                else:
                    frame = self.strip.crop((x, y, x + width, y + height))
                    frame = frame.transpose(Image.Transpose.ROTATE_180)
                    frame_x, frame_y = 800 - x - width, 120 - y - height
                encoded = BytesIO()
                frame.save(encoded, format='JPEG', quality=95)
                self.device.transport.set_background_frame_stream(
                    encoded.getvalue(), frame.width, frame.height, frame_x, frame_y)
                self.frame_initialized = True
                return

            for index in range(x // 200, (x + width - 1) // 200 + 1):
                panel = self.strip.crop((index * 200, 0, (index + 1) * 200, 100))
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
                    temporary_path = Path(temporary.name)
                try:
                    panel.save(temporary_path)
                    self.device.set_seondscreen_image(11 + index, str(temporary_path))
                finally:
                    temporary_path.unlink(missing_ok=True)

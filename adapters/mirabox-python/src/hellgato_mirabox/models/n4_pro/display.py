"""Preserved N4 image writer transforms from app/n4/display.py.

Only vendor-required temporary PNGs use files. IPC always carries bounded bytes.
The caller serializes operations and guards each individual native write deadline.
"""
from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image, ImageStat


MAX_OBSERVATION_COUNT = 9007199254740991
WRITER_TYPES = frozenset(('none', 'initialize', 'key', 'lcd', 'clear', 'fill', 'restore', 'screensaver', 'logo'))
VENDOR_RETURN_KINDS = ('none', 'zero', 'negative', 'positive', 'other')


def increment(value):
    return min(MAX_OBSERVATION_COUNT, value + 1)


def vendor_return_kind(value):
    if value is None:
        return 'none'
    if type(value) is int:
        return 'zero' if value == 0 else 'negative' if value < 0 else 'positive'
    return 'other'


class DisplayWriter:
    def __init__(self, device, images_dir, strip_mode='panels'):
        if strip_mode not in ('frame', 'panels'):
            raise ValueError('Unknown N4 strip mode')
        self.strip_mode = strip_mode
        self.frame_initialized = False
        self.device = device
        self.images_dir = Path(images_dir)
        self.strip = Image.new('RGB', (800, 100), 'black')
        self.key_observations = [{
            'attempted': 0, 'submitted': 0, 'failed': 0, 'lastWriter': 'none',
            'width': None, 'height': None, 'meanBrightness': None, 'black': None,
            'lastVendorReturnKind': None, 'vendorReturnKinds': dict.fromkeys(VENDOR_RETURN_KINDS, 0),
        } for _ in range(10)]

    def observations(self):
        return [{**item, 'vendorReturnKinds': dict(item['vendorReturnKinds'])} for item in self.key_observations]

    def _attempt(self, index, writer_type, width, height, mean):
        if writer_type not in WRITER_TYPES or writer_type == 'none':
            raise ValueError('Unknown display writer type')
        item = self.key_observations[index]
        item.update(attempted=increment(item['attempted']), lastWriter=writer_type,
                    width=width, height=height, meanBrightness=mean, black=mean == 0,
                    lastVendorReturnKind=None)
        return item

    def _vendor_return(self, item, result):
        kind = vendor_return_kind(result)
        item['lastVendorReturnKind'] = kind
        item['vendorReturnKinds'][kind] = increment(item['vendorReturnKinds'][kind])

    def clear_keys(self, check, writer_type):
        check()
        items = [self._attempt(index, writer_type, 120, 120, 0) for index in range(10)]
        try:
            result = self.device.clearAllIcon()
            for item in items:
                self._vendor_return(item, result)
                item['submitted'] = increment(item['submitted'])
        except Exception:
            for item in items:
                item['failed'] = increment(item['failed'])
            raise

    def key(self, index, image, check, writer_type='key'):
        mean = sum(ImageStat.Stat(image.convert('RGB')).mean) / 3
        item = self._attempt(index, writer_type, image.width, image.height, mean)
        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
                path = Path(temporary.name)
            try:
                image.save(path)
                check()
                result = self.device.set_key_image(index + 1, str(path))
                self._vendor_return(item, result)
                if type(result) is int and result < 0:
                    raise OSError('The vendor SDK rejected the key image')
                # Submission records vendor-call intent, never physical readback.
                item['submitted'] = increment(item['submitted'])
            finally:
                path.unlink(missing_ok=True)
        except Exception:
            item['failed'] = increment(item['failed'])
            raise

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

    def lcd(self, image, check, writer_type='lcd'):
        self.frame(image.transpose(Image.Transpose.ROTATE_180), check)
        if image.getbbox() is None:
            # Remove black key overlays so later action images can become visible.
            self.clear_keys(check, writer_type)
        else:
            for index in range(10):
                x, y = 13 + (index % 5) * 164, 12 + (index // 5) * 160
                self.key(index, image.crop((x, y, x + 120, y + 120)), check, writer_type)
        self.frame_initialized = False
        self.touch(image.crop((0, 380, 800, 480)), (0, 0, 800, 100), check)
        # Finish composite writes like open and wake restoration, after every surface.
        check()
        self.device.refresh()

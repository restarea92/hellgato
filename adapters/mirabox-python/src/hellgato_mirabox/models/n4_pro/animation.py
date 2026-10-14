"""Bounded saved screensaver preparation, adapted from app/n4/animation.py."""

from bisect import bisect_right
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence


MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FRAME_PIXELS = 4_000_000
MAX_TOTAL_PIXELS = 256_000_000
MAX_FRAMES = 1000
MAX_ENCODED_BYTES = 128 * 1024 * 1024
FRAME_SIZE = (800, 480)


class NativeAnimation:
    def __init__(self, path=None):
        self.clear()
        if path is not None:
            self.load(path)

    def clear(self):
        self._frames = []
        self._ends = []
        self._started = None
        self._last_index = None

    def load(self, path, check=lambda: None):
        check()
        path = Path(path)
        if not path.is_absolute() or str(path).startswith(('\\\\', '//')) or not path.is_file():
            raise ValueError("Screensaver must be an explicit absolute local file")
        if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError('Screensaver file is too large')
        frames = []
        ends = []
        elapsed = 0
        encoded_bytes = 0
        total_pixels = 0
        # Read bounded bytes once: a growing/replaced file cannot bypass the stat limit.
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if not 0 < len(data) <= MAX_FILE_BYTES:
            raise ValueError('Screensaver file is too large or empty')
        check()
        with Image.open(BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP'):
                raise ValueError('Unsupported screensaver format')
            for frame in ImageSequence.Iterator(image):
                check()
                pixels = frame.width * frame.height
                total_pixels += max(pixels, FRAME_SIZE[0] * FRAME_SIZE[1])
                if (pixels > MAX_FRAME_PIXELS or total_pixels > MAX_TOTAL_PIXELS
                        or len(frames) >= MAX_FRAMES):
                    raise ValueError('Screensaver has too many image pixels')
                # Animated WebP publishes this frame's duration when it is loaded.
                frame.load()
                duration = max(1, int(frame.info.get('duration') or 100))
                rgba = frame.convert('RGBA')
                if rgba.size != FRAME_SIZE:
                    rgba = ImageOps.contain(rgba, FRAME_SIZE, Image.Resampling.LANCZOS)
                canvas = Image.new('RGB', FRAME_SIZE, 'black')
                canvas.paste(rgba, ((800 - rgba.width) // 2, (480 - rgba.height) // 2), rgba)
                encoded = BytesIO()
                canvas.transpose(Image.Transpose.ROTATE_180).save(
                    encoded, format='JPEG', quality=95, subsampling=0)
                data = encoded.getvalue()
                encoded_bytes += len(data)
                if encoded_bytes > MAX_ENCODED_BYTES:
                    raise ValueError('Screensaver frames need too much memory')
                frames.append(data)
                elapsed += duration
                ends.append(elapsed)
        if not frames:
            raise ValueError('Screensaver has no frames')
        check()
        self._frames = frames
        self._ends = ends
        self._started = None
        self._last_index = None
        return self

    def frame(self, now, restart=False):
        if not self._frames:
            return None
        if restart or self._started is None:
            self._started = now
            self._last_index = 0
            return self._frames[0]
        if len(self._frames) == 1:
            return None
        elapsed = max(0, round((now - self._started) * 1000))
        index = bisect_right(self._ends, elapsed % self._ends[-1])
        if index == self._last_index:
            return None
        self._last_index = index
        return self._frames[index]

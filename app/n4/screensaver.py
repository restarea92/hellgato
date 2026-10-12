"""Read the current device's saved screen saver from Stream Deck settings."""

from pathlib import Path
import struct
from urllib.parse import parse_qs, urlsplit

from PIL import Image


MAX_SETTINGS_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_FRAME_PIXELS = 4 * 1024 * 1024
MAX_TOTAL_PIXELS = 128 * 1024 * 1024
MAX_FRAMES = 1000


def _read_registry_value():
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Elgato Systems GmbH\StreamDeck') as key:
            return winreg.QueryValueEx(key, 'Devices')[0]
    except FileNotFoundError:
        return None


class _VariantReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.offset:
            raise ValueError('Incomplete Stream Deck settings')
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def uint(self):
        return struct.unpack('>I', self.take(4))[0]

    def count(self):
        count = self.uint()
        if count > len(self.data) - self.offset:
            raise ValueError('Invalid Stream Deck collection size')
        return count

    def string(self):
        size = self.uint()
        return None if size == 0xffffffff else self.take(size).decode('utf-16-be')

    def variant(self, depth=0):
        if depth > 32:
            raise ValueError('Stream Deck settings nesting limit exceeded')
        kind = self.uint()
        if kind == 0:
            return None
        if kind == 8:
            result = {}
            for _ in range(self.count()):
                name = self.string()
                result[name] = self.variant(depth + 1)
            return result
        if kind == 9:
            return [self.variant(depth + 1) for _ in range(self.count())]
        if kind == 10:
            return self.string()
        if kind == 11:
            return [self.string() for _ in range(self.count())]
        if kind == 1:
            return bool(self.take(1)[0])
        if kind in (2, 3, 4, 5, 6):
            format = {2: '>i', 3: '>I', 4: '>q', 5: '>Q', 6: '>d'}[kind]
            return struct.unpack(format, self.take(struct.calcsize(format)))[0]
        if kind == 12:
            size = self.uint()
            return None if size == 0xffffffff else self.take(size)
        if kind in (19, 20, 21, 22, 25, 26):
            count = 4 if kind in (19, 20) else 2
            format = '>' + ('d' if kind in (20, 22, 26) else 'i') * count
            return struct.unpack(format, self.take(struct.calcsize(format)))
        raise ValueError(f'Unsupported Stream Deck setting type: {kind}')


def _saved_path(serial, payload):
    if payload is None:
        return None
    if not isinstance(payload, (str, bytes)) or len(payload) > MAX_SETTINGS_BYTES:
        raise ValueError('Invalid Stream Deck settings value')
    text = payload.decode('utf-16-le') if isinstance(payload, bytes) else payload
    text = text.rstrip('\0')
    if not text.startswith('@Variant(') or not text.endswith(')'):
        raise ValueError('Invalid Stream Deck settings envelope')
    reader = _VariantReader(text[len('@Variant('):-1].encode('latin-1'))
    devices = reader.variant()
    if not isinstance(devices, dict):
        raise ValueError('Invalid Stream Deck device map')
    device = next((value for key, value in devices.items()
                   if isinstance(key, str) and key.startswith('@(')
                   and key.endswith(f'[4057/132/{serial}]')), None)
    if not isinstance(device, dict):
        return None
    uri = device.get('map_dev_sleep_wallpaper_path')
    if not uri:
        return None
    if not isinstance(uri, str):
        raise ValueError('Invalid Stream Deck screen saver path')
    parsed = urlsplit(uri)
    paths = parse_qs(parsed.query).get('path', [])
    if parsed.scheme != 'wallpaper' or parsed.netloc != 'image' or len(paths) != 1:
        raise ValueError('Invalid Stream Deck screen saver URL')
    path = Path(paths[0])
    if '\0' in paths[0] or not path.is_absolute():
        raise ValueError('Screen saver path must be an absolute local file')
    return path


class ScreensaverSource:
    def __init__(self, serial, read_settings=_read_registry_value):
        self.serial = serial
        self.read_settings = read_settings
        self.path = None
        self.error = None
        self._signature = None

    def reload(self):
        previous = self._signature
        try:
            path = _saved_path(self.serial, self.read_settings())
            if path is None:
                signature = None
            else:
                stat = path.stat()
                signature = (str(path), stat.st_mtime_ns, stat.st_size)
            if signature == previous:
                if signature is None:
                    self.error = None
                return False
            self.path = None
            self.error = None
            self._signature = signature
            if path is not None:
                if not 0 < stat.st_size <= MAX_IMAGE_BYTES:
                    raise ValueError('Screen saver file size limit exceeded')
                with Image.open(path) as image:
                    pixels = image.width * image.height
                    if pixels > MAX_FRAME_PIXELS:
                        raise ValueError('Screen saver frame size limit exceeded')
                    frames = getattr(image, 'n_frames', 1)
                    if frames > MAX_FRAMES or pixels * frames > MAX_TOTAL_PIXELS:
                        raise ValueError('Screen saver animation size limit exceeded')
                    if image.format not in ('WEBP', 'GIF', 'PNG', 'JPEG', 'BMP'):
                        raise ValueError('Unsupported screen saver image format')
                self.path = path
        except (OSError, ValueError, UnicodeError, Image.DecompressionBombError) as error:
            self.path = None
            self.error = str(error)
            self._signature = None
        return self._signature != previous

    def close(self):
        pass

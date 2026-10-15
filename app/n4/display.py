"""Mirror CORA display commands and idle state to N4 Pro hardware."""

from pathlib import Path
from io import BytesIO
import tempfile
import time

from PIL import Image

if __package__:
    from .animation import NativeAnimation
else:
    from animation import NativeAnimation


DISPLAY_EVENTS = {'brightness', 'sleepDuration', 'displayCommand', 'keyImage', 'touchImage', 'lcdImage', 'screensaver'}


class DisplayMirror:
    def __init__(self, device, images_dir: Path, strip_mode='panels', columns=5, clock=time.monotonic,
                 screensaver_source=None, on_native_source=None, led_writer=None):
        self.device = device
        self.images_dir = images_dir
        self.strip = Image.new('RGB', (800, 100), 'black')
        self.strip_mode = strip_mode
        self.columns = columns
        self.frame_initialized = False
        self.keys = {}
        self.background = Image.new('RGB', (800, 480), 'black')
        self.screensaver = None
        self.overlay = False
        self.idle = False
        self.sleeping = False
        self.brightness = 100
        self.sleep_seconds = 0
        self.clock = clock
        self.last_activity = clock()
        self.source = screensaver_source
        self.on_native_source = on_native_source
        self.led_writer = led_writer
        self.animation = NativeAnimation()
        self.native_ready = False
        self.native_active = False
        self.next_source_check = 0
        self.screen_level = 100
        self.led_level = None
        self.fade = None
        self.next_fade_update = 0
        self.last_key_release = None
        self.sleep_candidate = None
        self.ignore_saver_until = 0
        self.wake_clear_until = 0

    def _reload_screensaver(self, force=False):
        now = self.clock()
        if self.source is None or (not force and now < self.next_source_check):
            return
        self.next_source_check = now + 1
        if not self.source.reload():
            return
        self.animation.clear()
        self.native_ready = False
        self.screensaver = None
        if self.source.path is not None:
            try:
                self.animation.load(self.source.path)
                self.native_ready = True
            except (OSError, ValueError) as error:
                print(f'Saved screensaver could not be prepared: {error}', flush=True)
        if self.on_native_source is not None:
            self.on_native_source(self.native_ready)
        if self.idle:
            self.native_active = False
            self._enter_idle(reload=False)

    def apply_batch(self, events):
        pending = {}
        applied = 0

        def flush():
            nonlocal applied
            for event in pending.values():
                self.apply(event)
                applied += 1
            pending.clear()

        try:
            for event in events:
                detail = event.get('detail', {})
                kind = event.get('event')
                if kind not in ('keyImage', 'touchImage', 'lcdImage'):
                    flush()
                    self.apply(event)
                    applied += 1
                    continue
                if pending and (kind == 'lcdImage') != (next(iter(pending))[0] == 'lcdImage'):
                    flush()
                if kind == 'touchImage':
                    region = detail.get('region', {})
                    key = (kind, *(region.get(name) for name in ('x', 'y', 'w', 'h')))
                elif kind == 'keyImage':
                    key = (kind, detail.get('keyIndex'))
                else:
                    key = (kind,)
                pending.pop(key, None)
                pending[key] = event
            flush()
        finally:
            for event in events:
                if event.get('event') in ('touchImage', 'lcdImage'):
                    path = self._image_path(event.get('detail', {}).get('file'))
                    if path is not None:
                        path.unlink(missing_ok=True)
        return applied

    def _image_path(self, filename):
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            return None
        return self.images_dir / filename

    def _read_image(self, filename, size):
        path = self._image_path(filename)
        if path is None or not path.is_file():
            return None
        with Image.open(path) as image:
            if image.size != size:
                return None
            return image.convert('RGB')

    def _write_key(self, index, image):
        self.images_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
            path = Path(temporary.name)
        try:
            image.save(path)
            self.device.set_key_image(index + 1, str(path))
        finally:
            path.unlink(missing_ok=True)

    def _write_frame(self, image, x=0, y=0):
        encoded = BytesIO()
        image.save(encoded, format='JPEG', quality=95)
        self.device.transport.set_background_frame_stream(encoded.getvalue(), image.width, image.height, x, y)

    def _write_strip(self, strip, region=None):
        x, y, width, height = region or (0, 0, 800, 100)
        if self.strip_mode == 'frame':
            if not self.frame_initialized:
                frame = Image.new('RGB', (800, 130), 'black')
                frame.paste(strip.transpose(Image.Transpose.ROTATE_180), (0, 20))
                self._write_frame(frame)
                self.frame_initialized = True
            else:
                frame = strip.crop((x, y, x + width, y + height)).transpose(Image.Transpose.ROTATE_180)
                self._write_frame(frame, 800 - x - width, 120 - y - height)
            return
        self.images_dir.mkdir(parents=True, exist_ok=True)
        for index in range(x // 200, (x + width - 1) // 200 + 1):
            panel = strip.crop((index * 200, 0, (index + 1) * 200, 100))
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=self.images_dir) as temporary:
                path = Path(temporary.name)
            try:
                panel.save(path)
                self.device.set_seondscreen_image(11 + index, str(path))
            finally:
                path.unlink(missing_ok=True)

    def _show_lcd(self, image):
        # Match Stream Deck's verified background crop grid, including the two extra keys.
        step = 164 if self.columns == 5 else 219
        self._write_frame(image.transpose(Image.Transpose.ROTATE_180))
        for index in range(self.columns * 2):
            x = 13 + (index % self.columns) * step
            y = 12 + (index // self.columns) * 160
            self._write_key(index, image.crop((x, y, x + 120, y + 120)))
        self.frame_initialized = False
        self._write_strip(image.crop((0, 380, 800, 480)))

    def _wake(self):
        if self.sleeping:
            self.device.wakeScreen()
            self.sleeping = False

    def _set_screen_level(self, level):
        if level != self.screen_level:
            self.device.set_brightness(level)
            self.screen_level = level

    def _set_led_level(self, level):
        if self.led_writer is not None:
            self.led_level = self.led_writer(level, self.brightness, self.idle or self.sleeping)
        elif level != self.led_level:
            self.device.set_led_brightness(level)
            self.led_level = level

    def _start_fade(self, target, delay=0):
        self.fade = (self.clock() + delay, self.screen_level, target)
        self.next_fade_update = 0

    def _fade_tick(self, now):
        if self.fade is None:
            return
        if now < self.next_fade_update:
            return
        self.next_fade_update = now + .02
        start, initial, target = self.fade
        fraction = max(0, min(1, (now - start) / .3))
        # Smoothstep keeps the first and last brightness changes gentle.
        level = round(initial + (target - initial) * fraction * fraction * (3 - 2 * fraction))
        self._set_screen_level(level)
        if not self.idle:
            self._set_led_level(level)
        if fraction == 1:
            self.fade = None

    def _sleep(self):
        self.native_active = False
        self.fade = None
        self._set_led_level(0)
        if not self.sleeping:
            # N4 Pro brightness 0 still leaves the LCD backlight on.
            self.device.transport.sleep()
            self.sleeping = True

    def _native_frame(self, restart=False):
        frame = self.animation.frame(self.clock(), restart=restart)
        if frame is not None:
            self.device.transport.set_background_frame_stream(frame, 800, 480, 0, 0)
            self.device.refresh()

    def _show_screensaver_frame(self, image, *, clear_keys):
        if clear_keys:
            self.device.clearAllIcon()
        self._write_frame(image.transpose(Image.Transpose.ROTATE_180))
        self.device.refresh()

    def _enter_idle(self, reload=True):
        if reload:
            self._reload_screensaver(force=not self.idle)
        first = not self.idle or not self.overlay
        self.idle = True
        self.sleep_candidate = None
        self._set_led_level(0)
        if self.native_ready:
            if not self.native_active:
                # Key overlays otherwise hide the SDK's full-screen background layer.
                self.device.clearAllIcon()
                self._native_frame(restart=True)
                self.native_active = True
                self._wake()
                self._start_fade(0)
            self.overlay = True
        elif self.screensaver is not None:
            self._show_screensaver_frame(self.screensaver, clear_keys=first)
            self._wake()
            self._start_fade(0)
            self.overlay = True
        else:
            self._sleep()

    def _restore(self):
        was_idle = self.idle or self.sleeping or self.native_active
        wake_fade = self.fade if not was_idle else None
        self.native_active = False
        self.fade = None
        if was_idle:
            self._set_screen_level(0)
        # Restore each surface once while dim/off, before bringing the backlight up.
        self._write_frame(self.background.transpose(Image.Transpose.ROTATE_180))
        step = 164 if self.columns == 5 else 219
        for index in range(self.columns * 2):
            x, y = 13 + (index % self.columns) * step, 12 + (index // self.columns) * 160
            image = self.keys.get(index)
            if image is None:
                image = self.background.crop((x, y, x + 120, y + 120))
            self._write_key(index, image)
        self.frame_initialized = False
        self._write_strip(self.strip)
        self.device.refresh()
        self._wake()
        self.idle = False
        self.overlay = False
        if was_idle:
            now = self.clock()
            self.ignore_saver_until = now + .3
            self.wake_clear_until = now + 1
            self._start_fade(self.brightness, delay=.08)
        elif wake_fade is not None:
            self.fade = wake_fade

    def notify_input(self, now=None, key_release=False):
        now = self.clock() if now is None else now
        self.last_activity = max(self.last_activity, now)
        self.last_key_release = now if key_release else None
        self.sleep_candidate = None
        if self.idle or self.overlay:
            self._restore()

    def tick(self):
        now = self.clock()
        self._reload_screensaver()
        if self.idle:
            if self.native_active:
                self._native_frame()
            self._fade_tick(now)
            return
        self._fade_tick(now)
        if not self.sleep_seconds or now - self.last_activity < self.sleep_seconds:
            return
        self._enter_idle()

    def apply(self, event):
        detail = event.get('detail', {})
        kind = event.get('event')
        if kind == 'brightness':
            level = detail.get('level')
            if type(level) is int and 0 <= level <= 100:
                now = self.clock()
                if (self.sleep_candidate is not None and now <= self.sleep_candidate
                        and level < self.brightness):
                    self._enter_idle()
                    return
                self.sleep_candidate = None
                if level == 0:
                    if now < self.ignore_saver_until:
                        return
                    if self.source is None:
                        self.screensaver = None
                    self._enter_idle()
                else:
                    # Ignore the rest of Stream Deck's sleep fade after native sleep starts.
                    if self.idle and level < self.brightness:
                        return
                    if now < self.ignore_saver_until and level < self.brightness:
                        return
                    self.brightness = level
                    if self.idle or self.overlay:
                        self.notify_input()
                    if self.fade is not None:
                        start, initial, _ = self.fade
                        self.fade = (start, initial, level)
                    else:
                        self._set_screen_level(level)
                        self._set_led_level(level)
        elif kind == 'sleepDuration':
            seconds = detail.get('seconds')
            if type(seconds) is int and 0 <= seconds <= 0x7fffffff:
                now = self.clock()
                # Observed manual Sleep: key release, timeout disabled, then brightness fade.
                # Disabling the timer alone or ordinary brightness adjustments never sleep.
                if (seconds == 0 and self.last_key_release is not None
                        and 0 <= now - self.last_key_release <= .5 and not self.idle):
                    self.sleep_candidate = now + .1
                else:
                    self.sleep_candidate = None
                self.sleep_seconds = seconds
                self.last_activity = now
        elif kind == 'keyImage':
            index = detail.get('keyIndex')
            if type(index) is not int or not 0 <= index < self.columns * 2:
                return
            image = self._read_image(f'key-{index}.jpg', (120, 120))
            if image is None:
                return
            self.keys[index] = image
            if self.idle:
                return
            if self.overlay:
                self._restore()
            else:
                self._write_key(index, image)
        elif kind == 'touchImage':
            region = detail.get('region', {})
            x, y, width, height = (region.get(key) for key in ('x', 'y', 'w', 'h'))
            if not all(type(value) is int for value in (x, y, width, height)):
                return
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 800 or y + height > 100:
                return
            image = self._read_image(detail.get('file'), (width, height))
            if image is None:
                return
            self.strip.paste(image, (x, y))
            if self.idle:
                return
            if self.overlay:
                self._restore()
            else:
                self._write_strip(self.strip, (x, y, width, height))
        elif kind == 'screensaver':
            if self.clock() >= self.ignore_saver_until:
                self._enter_idle()
        elif kind == 'lcdImage':
            if self.clock() < self.ignore_saver_until:
                return
            self._reload_screensaver(force=not self.idle)
            if self.native_ready:
                self._enter_idle(reload=False)
                return
            image = self._read_image(detail.get('file'), (800, 480))
            if image is not None:
                first = not self.idle or not self.overlay
                self.screensaver = image
                self._show_screensaver_frame(image, clear_keys=first)
                self._wake()
                self._set_led_level(0)
                if first:
                    self._start_fade(0)
                self.overlay = True
                self.idle = True
        elif kind == 'displayCommand':
            self.sleep_candidate = None
            if detail.get('type') == 'showLogo':
                was_idle = self.idle
                self.native_active = False
                self.fade = None
                if was_idle:
                    self._set_screen_level(0)
                self.idle = False
                image = Image.new('RGB', (800, 480), 'black')
                logo_path = Path(__file__).resolve().parents[1] / 'assets/icons/hellgato-front-64.png'
                with Image.open(logo_path) as logo:
                    logo = logo.convert('RGBA')
                    image.paste(logo, ((800 - logo.width) // 2, (480 - logo.height) // 2), logo)
                self._show_lcd(image)
                self._wake()
                if was_idle:
                    self._start_fade(self.brightness)
                self.overlay = True
            elif detail.get('type') == 'fill':
                color = detail.get('color')
                if not isinstance(color, list) or len(color) != 3 or not all(type(c) is int and 0 <= c <= 255 for c in color):
                    return
                index = detail.get('keyIndex')
                if 'keyIndex' in detail:
                    if type(index) is not int or not 0 <= index < self.columns * 2:
                        return
                    image = self.keys[index] = Image.new('RGB', (120, 120), tuple(color))
                    if not self.idle:
                        if self.overlay:
                            self._restore()
                        else:
                            self._write_key(index, image)
                else:
                    # Stream Deck clears the LCD when leaving its screensaver.
                    self.last_activity = self.clock()
                    if color == [0, 0, 0] and (self.idle or self.clock() < self.wake_clear_until):
                        if self.idle:
                            self._restore()
                        self.wake_clear_until = 0
                        self.background = Image.new('RGB', (800, 480), 'black')
                        return
                    was_idle = self.idle
                    self.native_active = False
                    if was_idle:
                        self._set_screen_level(0)
                    self.idle = False
                    self.background = Image.new('RGB', (800, 480), tuple(color))
                    self.strip = Image.new('RGB', (800, 100), tuple(color))
                    self.keys.clear()
                    self._show_lcd(self.background)
                    self._wake()
                    self.overlay = False
                    if was_idle:
                        self._start_fade(self.brightness)

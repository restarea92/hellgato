"""N4 display/idle state adapted from the legacy display and device-controls code.

All methods run on the worker's serialized writer, never the SDK input callback.
Values describe submitted intent, not physical readback. No user files are written.
"""
import time
from io import BytesIO
from pathlib import Path
from PIL import Image
from ...protocol import ProtocolError, require
from .animation import NativeAnimation
from .display import DisplayWriter, increment


class DisplayState:
    def __init__(self, device, images_dir, *, screensaver_path=None, logo_path=None, strip_mode='panels', led_writer=None,
                 clock=time.monotonic, check=lambda: None, lcd_mode='normal'):
        if lcd_mode not in ('normal', 'stream-screensaver'):
            raise ValueError('Unknown N4 LCD output mode')
        self.device = device
        self.lcd_mode = lcd_mode
        self.writer = DisplayWriter(device, images_dir, strip_mode)
        self.clock = clock
        self.led_writer = led_writer
        self.animation = NativeAnimation()
        if screensaver_path is not None:
            try:
                self.animation.load(screensaver_path, check)
            except ProtocolError:
                raise
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                raise ProtocolError('Saved screensaver asset is invalid or exceeds its bounds', 'ERR_ASSET_INVALID') from error
        self.screensaver_ready = screensaver_path is not None
        self.logo = None
        if logo_path is not None:
            check()
            path = Path(logo_path)
            require(path.is_absolute() and not str(path).startswith(('\\\\', '//')) and path.is_file(),
                    'Logo must be an explicit absolute local PNG', 'ERR_CONTRACT')
            with path.open('rb') as stream:
                data = stream.read(1_048_577)
            require(0 < len(data) <= 1_048_576, 'Logo byte bound', 'ERR_CONTRACT')
            with Image.open(BytesIO(data)) as image:
                require(image.format == 'PNG' and 0 < image.width <= 800 and 0 < image.height <= 480
                        and getattr(image, 'n_frames', 1) == 1, 'Invalid logo dimensions or format', 'ERR_CONTRACT')
                self.logo = image.convert('RGBA')
            check()
        self.logo_active = False
        self.background = Image.new('RGB', (800, 480), 'black')
        self.keys = {}
        self.key_cache_clears = 0
        self.strip = Image.new('RGB', (800, 100), 'black')
        self.brightness = 100
        # No device readback is available. None means nothing submitted this session.
        self.screen_level = None
        self.led_level = None
        self.knob_brightness = None
        self.sleep_seconds = 0
        self.last_activity = clock()
        self.last_key_release = None
        self.sleep_candidate = None
        self.ignore_saver_until = 0
        self.wake_clear_until = 0
        self.idle = False
        self.sleeping = False
        self.native_active = False
        self.stream_active = False
        self.streamed_screensaver = None
        self.fade = None
        self.next_fade_update = 0

    def initialize(self, check):
        """Reset volatile display state after an admitted open, like legacy init.

        Firmware discovery never calls this. Each native operation has its own
        cancellation guard, and waking is explicit because sleep has no readback.
        """
        self._screen(0, check)
        self.writer.clear_keys(check, 'initialize')
        self.writer.lcd(self.background, check, 'initialize')
        check()
        self.device.wakeScreen()
        self._screen(self.brightness, check)
        self._led(self.brightness, check)

    def _screen(self, level, check):
        if level != self.screen_level:
            check()
            self.device.set_brightness(level)
            self.screen_level = level

    def _led(self, level, check, *, knob=None, follow=None):
        if self.led_writer is None:
            return
        chosen = self.knob_brightness if knob is None else knob
        if follow is True:
            chosen = None
        if self.idle or self.sleeping:
            applied = 0
        elif chosen is None:
            applied = level
        else:
            factor = 1 if self.brightness == 0 else min(1, level / self.brightness)
            applied = round(chosen * factor)
        if applied != self.led_level:
            check()
            self.led_writer(applied)
            self.led_level = applied

    def _wake(self, check):
        if self.sleeping:
            check()
            self.device.wakeScreen()
            self.sleeping = False

    def _fade(self, target, delay=0):
        initial = self.brightness if self.screen_level is None else self.screen_level
        self.fade = (self.clock() + delay, initial, target)
        self.next_fade_update = 0

    def _fade_tick(self, now, check):
        if self.fade is None or now < self.next_fade_update:
            return
        self.next_fade_update = now + .02
        start, initial, target = self.fade
        fraction = max(0, min(1, (now - start) / .3))
        level = round(initial + (target - initial) * fraction * fraction * (3 - 2 * fraction))
        self._screen(level, check)
        if not self.idle:
            self._led(level, check)
        if fraction == 1:
            self.fade = None

    def _native_frame(self, check, restart=False):
        frame = self.animation.frame(self.clock(), restart=restart)
        if frame is not None:
            check()
            self.device.transport.set_background_frame_stream(frame, 800, 480, 0, 0)
            check()
            self.device.refresh()

    def enter_idle(self, check, *, force_sleep=False):
        check()
        self.idle = True
        self.logo_active = False
        self.stream_active = False
        self.sleep_candidate = None
        self._led(0, check)
        if self.screensaver_ready and not force_sleep:
            if not self.native_active:
                self.writer.clear_keys(check, 'screensaver')
                self._native_frame(check, restart=True)
                self._wake(check)
                self.native_active = True
                self._fade(0)
        elif self.streamed_screensaver is not None and not force_sleep:
            self.native_active = False
            self.writer.lcd(self.streamed_screensaver, check, 'screensaver')
            self._wake(check)
            self._fade(0)
            self.stream_active = True
        else:
            self.native_active = False
            self.fade = None
            if not self.sleeping:
                check()
                self.device.transport.sleep()
                self.sleeping = True

    def restore(self, check):
        check()
        self.sleep_candidate = None
        was_idle = self.idle or self.sleeping or self.native_active
        wake_fade = self.fade if not was_idle else None
        restore_surfaces = was_idle or self.logo_active or self.stream_active
        self.logo_active = False
        self.stream_active = False
        self.native_active = False
        self.fade = None
        if was_idle:
            self._screen(0, check)
        if restore_surfaces:
            self.writer.frame(self.background.transpose(Image.Transpose.ROTATE_180), check)
            for index in range(10):
                x, y = 13 + (index % 5) * 164, 12 + (index // 5) * 160
                image = self.keys.get(index)
                if image is None:
                    image = self.background.crop((x, y, x + 120, y + 120))
                self.writer.key(index, image, check, 'restore')
            self.writer.frame_initialized = False
            self.writer.touch(self.strip, (0, 0, 800, 100), check)
            check()
            self.device.refresh()
        self._wake(check)
        self.idle = False
        self.last_activity = self.clock()
        if was_idle:
            self.ignore_saver_until = self.last_activity + .3
            self.wake_clear_until = self.last_activity + 1
            self._fade(self.brightness, delay=.08)
        elif wake_fade is not None:
            self.fade = wake_fade
        else:
            self._screen(self.brightness, check)
            self._led(self.brightness, check)

    def notify_input(self, now, key_release, check):
        self.last_activity = max(self.last_activity, now)
        self.last_key_release = now if key_release else None
        self.sleep_candidate = None
        if self.idle or self.sleeping or self.logo_active or self.stream_active:
            self.restore(check)

    def tick(self, check):
        now = self.clock()
        if self.idle:
            if self.native_active:
                self._native_frame(check)
            self._fade_tick(now, check)
            return
        self._fade_tick(now, check)
        if self.sleep_seconds and now - self.last_activity >= self.sleep_seconds:
            self.enter_idle(check)

    def _stream_lcd(self, image, check):
        if self.clock() < self.ignore_saver_until:
            return
        if self.screensaver_ready:
            self.enter_idle(check)
            return
        first = not self.idle or not self.stream_active
        self.streamed_screensaver = image
        self.writer.lcd(image, check, 'screensaver')
        self._wake(check)
        self.idle = True
        self.logo_active = False
        self.native_active = False
        self.stream_active = True
        self._led(0, check)
        if first:
            self._fade(0)

    def output(self, surface, image, region, check, writer_type=None):
        check()
        if surface == 'lcd' and self.lcd_mode == 'stream-screensaver' and writer_type is None:
            self._stream_lcd(image, check)
            return
        if surface.startswith('key:'):
            index = int(surface[4:])
            self.keys[index] = image
            if self.logo_active:
                self.restore(check)
            elif not self.idle:
                self.writer.key(index, image, check, writer_type or 'key')
        elif surface == 'touch':
            self.strip.paste(image, (region[0], region[1]))
            if self.logo_active:
                self.restore(check)
            elif not self.idle:
                self.writer.touch(image, region, check)
        elif surface == 'lcd':
            self.background = image
            self.keys.clear()
            self.key_cache_clears = increment(self.key_cache_clears)
            self.strip = image.crop((0, 380, 800, 480))
            if self.logo_active:
                self.restore(check)
            elif not self.idle:
                self.writer.lcd(image, check, writer_type or 'lcd')

    def control(self, control, check):
        kind = control['control']
        check()
        now = self.clock()
        if kind in ('showLogo', 'clear', 'fill'):
            self.sleep_candidate = None
        if kind == 'brightness':
            level = control['value']
            if (self.sleep_candidate is not None and now <= self.sleep_candidate and level < self.brightness):
                self.enter_idle(check)
                return
            self.sleep_candidate = None
            if level == 0:
                if now >= self.ignore_saver_until:
                    if self.lcd_mode == 'stream-screensaver' and not self.screensaver_ready:
                        self.streamed_screensaver = None
                    self.enter_idle(check)
            else:
                if (self.idle or now < self.ignore_saver_until) and level < self.brightness:
                    return  # Remaining host fade must not wake a saved screensaver.
                self.brightness = level
                if self.idle or self.sleeping or self.logo_active or self.stream_active:
                    self.restore(check)
                if self.fade is not None:
                    start, initial, _ = self.fade
                    self.fade = (start, initial, level)
                else:
                    self._screen(level, check)
                    self._led(level, check)
        elif kind == 'sleepDuration':
            seconds = control['value']
            self.sleep_candidate = (now + .1 if seconds == 0 and self.last_key_release is not None
                and 0 <= now - self.last_key_release <= .5 and not self.idle else None)
            self.sleep_seconds = seconds
            self.last_activity = now
        elif kind in ('knobBrightness', 'followDisplayBrightness'):
            require(self.led_writer is not None, 'Checked knob brightness unavailable in this runtime', 'ERR_UNSUPPORTED_OPERATION', -32601)
            level = self.brightness if self.screen_level is None else self.screen_level
            if kind == 'knobBrightness':
                self._led(level, check, knob=control['value'])
                self.knob_brightness = control['value']
            elif control['value']:
                self._led(level, check, follow=True)
                self.knob_brightness = None
            else:
                chosen = self.brightness if self.knob_brightness is None else self.knob_brightness
                self._led(level, check, knob=chosen)
                self.knob_brightness = chosen
        elif kind == 'sleep':
            self.enter_idle(check, force_sleep=True)
        elif kind == 'screensaver':
            if now >= self.ignore_saver_until:
                self.enter_idle(check)
        elif kind == 'wake':
            self.restore(check)
        elif kind == 'showLogo':
            require(self.logo is not None, 'No explicit software display logo was supplied', 'ERR_UNSUPPORTED_OPERATION', -32601)
            was_idle = self.idle or self.sleeping
            self.native_active = False
            self.stream_active = False
            self.fade = None
            if was_idle:
                self._screen(0, check)
            self.idle = False
            canvas = Image.new('RGB', (800, 480), 'black')
            canvas.paste(self.logo, ((800 - self.logo.width) // 2, (480 - self.logo.height) // 2), self.logo)
            self.writer.lcd(canvas, check, 'logo')
            self._wake(check)
            self.logo_active = True
            if was_idle:
                self._fade(self.brightness)
        elif kind in ('clear', 'fill'):
            color = tuple(control.get('color', [0, 0, 0]))
            surface = control.get('surfaceId', 'lcd')
            if surface == 'lcd':
                self.last_activity = now
                # The host clears its saver on wake: preserve the cached keys/strip.
                if color == (0, 0, 0) and (self.idle or now < self.wake_clear_until):
                    if self.idle:
                        self.restore(check)
                    self.wake_clear_until = 0
                    self.background = Image.new('RGB', (800, 480), 'black')
                    return {'normalStateEffect': 'background-only'}
                was_idle = self.idle
                self.native_active = False
                self.stream_active = False
                self.logo_active = False
                self.fade = None
                if was_idle:
                    self._screen(0, check)
                self.idle = False
                self.output(surface, Image.new('RGB', (800, 480), color), None, check, kind)
                self._wake(check)
                self.last_activity = now
                if was_idle:
                    self._fade(self.brightness)
            elif surface == 'touch':
                self.output(surface, Image.new('RGB', (800, 100), color), (0, 0, 800, 100), check, kind)
            else:
                self.output(surface, Image.new('RGB', (120, 120), color), None, check, kind)
            return {'normalStateEffect': 'replace-all' if surface == 'lcd' else 'surface'}
        else:
            require(False, 'Control is not implemented by the candidate worker', 'ERR_UNSUPPORTED_OPERATION', -32601)

    def display_observations(self):
        return {'keyCacheCount': len(self.keys), 'keyCacheClears': self.key_cache_clears,
                'keys': self.writer.observations()}

    def state(self):
        return {'stripMode': self.writer.strip_mode, 'lcdMode': self.lcd_mode,
                'brightness': self.brightness, 'screenBrightness': self.screen_level,
                'sleepDuration': self.sleep_seconds, 'idle': self.idle, 'sleeping': self.sleeping,
                'screensaverReady': self.screensaver_ready, 'screensaverActive': self.native_active or self.stream_active,
                'logoActive': self.logo_active,
                'knobBrightness': self.brightness if self.knob_brightness is None else self.knob_brightness,
                'followDisplayBrightness': self.knob_brightness is None, 'knobBrightnessLevel': self.led_level,
                'stateVerification': 'unconfirmed'}

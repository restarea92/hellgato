import json
from dataclasses import make_dataclass
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from app.n4 import bridge
from app.n4.device_controls import DeviceControls
from app.n4.display import DisplayMirror
from device_registry import DeviceRegistry


def device(path, serial='N4-A'):
    result = Mock()
    result.path = path
    result.serial_number = serial
    result.firmware_version = '1.0'
    result.vendor_id, result.product_id = 1, 2
    result.open.return_value = True
    result.transport.enumerate_devices.return_value = [{'path': path}]
    result.key_pixels = {}
    result.set_key_image.side_effect = lambda index, filename: result.key_pixels.update(
        {index: Image.open(filename).getpixel((60, 60))})
    return result


class UsbReconnectTests(unittest.TestCase):
    def attachment(self, directory, devices, registry=None):
        display = DisplayMirror(bridge.DisconnectedDisplay(), Path(directory), 'frame')
        writes = []
        controls = DeviceControls(None, directory, 'HGMOCK000003', led_writer=writes.append)

        def led(level, normal, sleeping):
            if controls.device is None:
                return controls.led_level(level, normal, sleeping)
            return controls.apply_led_level(level, normal, sleeping=sleeping)

        display.led_writer = led
        events, closed = [], Mock(return_value=True)
        attachment = bridge.UsbAttachment(display, controls, lambda: devices[:], Mock(),
                                         initialize=True, report=events.append, close=closed, registry=registry)
        return attachment, display, controls, writes, events, closed

    def test_one_missed_observation_preserves_handle_and_restores_pending_display(self):
        with TemporaryDirectory() as directory:
            first = device(b'first')
            attachment, display, controls, _, events, closed = self.attachment(directory, [first])
            attachment.poll()
            first.transport.enumerate_devices.return_value = []
            attachment.poll()
            display.apply({'event': 'brightness', 'detail': {'level': 37}})
            first.transport.enumerate_devices.return_value = [{'path': first.path}]
            attachment.poll()
            closed.assert_not_called()
            self.assertIs(attachment.device, first)
            self.assertIs(controls.device, first)
            self.assertEqual(first.set_brightness.call_args.args, (37,))
            self.assertNotIn('deviceDisconnected', [event['event'] for event in events])

    def test_usb_reopen_replays_latest_images_brightness_and_persisted_knob_preference(self):
        with TemporaryDirectory() as directory:
            first, second = device(b'first'), device(b'second')
            devices = [first]
            attachment, display, controls, writes, events, closed = self.attachment(directory, devices)
            attachment.poll()
            controls.set_knob_brightness(63)
            first.transport.enumerate_devices.return_value = []
            attachment.poll()
            attachment.poll()
            self.assertIsNone(controls.device)
            closed.assert_called_once_with(first, notify=False, report=events.append)
            first.reset_mock()
            for index, color in ((4, [255, 0, 0]), (9, [0, 0, 255])):
                display.apply({'event': 'displayCommand', 'detail': {
                    'type': 'fill', 'keyIndex': index, 'color': color}})
            display.apply({'event': 'brightness', 'detail': {'level': 27}})
            first.set_key_image.assert_not_called()
            first.set_brightness.assert_not_called()
            devices[:] = [second]
            attachment.poll()
            self.assertEqual(second.key_pixels[5], (255, 0, 0))
            self.assertEqual(second.key_pixels[10], (0, 0, 255))
            self.assertEqual(second.set_brightness.call_args.args, (27,))
            self.assertEqual(writes[-1], 63)
            self.assertEqual(controls.knob_brightness, 63)
            self.assertIs(controls.device, second)
            self.assertEqual([event['event'] for event in events],
                             ['deviceReady', 'deviceDisconnected', 'deviceReconnected', 'deviceReady'])

    def test_ambiguous_or_replacement_device_is_not_opened_and_sleep_survives_reopen(self):
        with TemporaryDirectory() as directory:
            first, second, other = device(b'first'), device(b'second'), device(b'other', 'N4-B')
            devices = [first, other]
            attachment, display, _, _, events, _ = self.attachment(directory, devices)
            attachment.poll()
            first.open.assert_not_called()
            other.open.assert_not_called()
            devices[:] = [first]
            attachment.poll()
            first.transport.enumerate_devices.return_value = []
            attachment.poll()
            attachment.poll()
            display.apply({'event': 'brightness', 'detail': {'level': 0}})
            devices[:] = [other]
            attachment.poll()
            other.open.assert_not_called()
            devices[:] = [second]
            attachment.poll()
            self.assertTrue(display.idle)
            self.assertTrue(display.sleeping)
            second.transport.sleep.assert_called_once()
            sleeping_at = next(index for index, call in enumerate(second.mock_calls)
                               if call[0] == 'transport.sleep')
            self.assertTrue(all(index < sleeping_at for index, call in enumerate(second.mock_calls)
                                if call[0] == 'set_brightness'))
            self.assertEqual([event['detail']['reason'] for event in events if event['event'] == 'deviceWaiting'],
                             ['multiple-devices', 'different-device'])

    def test_main_keeps_one_cora_process_across_initial_absence_and_usb_reopen(self):
        with TemporaryDirectory() as directory:
            first, second = device(b'first'), device(b'second')
            first.transport.enumerate_devices.return_value = []
            ticks = [0]
            callbacks = []
            first.set_key_callback.side_effect = callbacks.append
            Input = make_dataclass('Input', ['event_type', 'key', 'state'])

            def sleep(seconds):
                ticks[0] += 1
                if ticks[0] == 2:
                    callbacks[0](first, Input('button', 5, 1))

            process = Mock()
            process.poll.side_effect = lambda: None if ticks[0] < 5 else 0
            manager = Mock()
            manager.enumerate.side_effect = [[], [first], [second]]
            original_type = type

            def named_type(value):
                if value is first or value is second:
                    return SimpleNamespace(__name__='StreamDockN4Pro')
                return original_type(value)

            modules = {
                'StreamDock.DeviceManager': SimpleNamespace(DeviceManager=lambda: manager),
                'StreamDock.InputTypes': SimpleNamespace(ButtonKey=lambda value: value),
            }
            with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['bridge.py', '--init']), \
                    patch.object(sys, 'platform', 'linux'), patch.dict(bridge.os.environ, {}, clear=True), \
                    patch.object(bridge, 'state_root', Path(directory)), \
                    patch.object(bridge, 'settings_path', return_value=Path(directory) / 'settings.json'), \
                    patch.object(bridge, 'load_settings', return_value={'identity': 3, 'dockIdentity': 1, 'stripMode': 'frame'}), \
                    patch.object(bridge, 'ScreensaverSource', return_value=None), \
                    patch.object(bridge, 'DeviceControlServer'), patch.object(bridge, 'type', named_type, create=True), \
                    patch.object(bridge, 'install_interruptible_heartbeat'), \
                    patch.object(bridge, 'close_device', return_value=True), \
                    patch.object(bridge, 'DeviceControls', side_effect=lambda *args, **kwargs: DeviceControls(
                        *args, **kwargs, led_writer=lambda level: None)), \
                    patch.object(bridge.subprocess, 'Popen', return_value=process) as start, \
                    patch.object(bridge.time, 'monotonic', side_effect=lambda: ticks[0]), \
                    patch.object(bridge.time, 'sleep', side_effect=sleep), \
                    patch('builtins.print') as output:
                bridge.main()
            start.assert_called_once()
            events = [json.loads(call.args[0])['event'] for call in output.call_args_list
                      if call.args and isinstance(call.args[0], str) and call.args[0].startswith('{')]
            self.assertEqual(events.count('deviceReady'), 2)
            self.assertEqual(events.count('deviceDisconnected'), 1)
            self.assertEqual(events.count('deviceReconnected'), 1)
            self.assertEqual(events.count('bridgeReady'), 1)
            self.assertEqual([call.args[0] for call in process.stdin.write.call_args_list],
                             ['key 4 down\n', 'key 4 up\n'])
            self.assertEqual(DeviceRegistry(directory, 3).devices()[0]['virtualSerial'], 'HGMOCK000003')
            self.assertEqual(start.call_args.args[0][-2:], ['1', '3'])

    def test_registry_identity_is_reported_on_usb_ready_disconnect_and_reconnect(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            first, second = device(b'first'), device(b'second')
            devices = [first]
            attachment, _, _, _, events, _ = self.attachment(directory, devices, registry)
            attachment.poll()
            first.transport.enumerate_devices.return_value = []
            attachment.poll()
            attachment.poll()
            devices[:] = [second]
            attachment.poll()
            row = registry.devices()[0]
            self.assertEqual([event['event'] for event in events],
                             ['deviceReady', 'deviceDisconnected', 'deviceReconnected', 'deviceReady'])
            self.assertTrue(all(event['detail']['deviceId'] == row['id'] for event in events))
            self.assertTrue(all(event['detail']['virtualSerial'] == 'HGMOCK000003' for event in events))
            self.assertEqual(len(registry.devices()), 1)

    def test_registry_rejects_different_device_before_open_after_bridge_restart(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            registry.remember(registry.prepare(device(b'first')))
            other = device(b'other', 'N4-B')
            restarted = DeviceRegistry(directory, 3)
            attachment, _, _, _, events, _ = self.attachment(directory, [other], restarted)
            attachment.poll()
            other.open.assert_not_called()
            attachment.configure.assert_not_called()
            self.assertEqual(events[0]['event'], 'deviceWaiting')
            self.assertEqual(events[0]['detail']['reason'], 'registered-device-mismatch')
            self.assertIsNone(attachment.device)

    def test_failed_usb_open_does_not_record_a_previously_connected_device(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 3)
            unavailable = device(b'first')
            unavailable.open.return_value = False
            attachment, _, _, _, events, closed = self.attachment(directory, [unavailable], registry)
            attachment.poll()
            self.assertFalse(registry.path.exists())
            self.assertEqual(registry.devices(), [])
            self.assertEqual(events[0]['event'], 'deviceError')
            closed.assert_called_once()


if __name__ == '__main__':
    unittest.main()

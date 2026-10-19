import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.n4.device_controls import DeviceControls, checked_led_brightness
from app.n4.device_control_server import ControlCancelled, ControlRequest, DeviceControlServer


SERIAL = 'HGMOCK000042'


class DeviceControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write = Mock()
        self.controls = DeviceControls(Mock(), self.root, SERIAL, led_writer=self.write)

    def test_independent_brightness_survives_restart_and_scales_sleep_fade(self):
        self.assertTrue(self.controls.state()['followDisplayBrightness'])
        self.assertEqual(self.controls.led_level(40, 40), 40)
        self.controls.set_knob_brightness(75)
        self.write.assert_called_once_with(75)
        self.assertEqual(self.controls.led_level(20, 40), 38)
        self.assertEqual(self.controls.led_level(0, 40), 0)
        other = DeviceControls(Mock(), self.root, SERIAL, led_writer=self.write)
        self.assertEqual(other.knob_brightness, 75)
        self.assertEqual(other.led_level(30, 30), 75)
        self.assertFalse(other.state()['followDisplayBrightness'])
        unrelated = DeviceControls(Mock(), self.root, 'HGMOCK000043', led_writer=self.write)
        self.assertTrue(unrelated.state()['followDisplayBrightness'])

    def test_invalid_failed_and_cancelled_writes_do_not_persist(self):
        for value in (-1, 101, True, '50', None):
            with self.assertRaises(ValueError):
                self.controls.set_knob_brightness(value)
        self.write.assert_not_called()
        self.write.side_effect = OSError('USB failed')
        with self.assertRaises(OSError):
            self.controls.set_knob_brightness(60)
        self.assertFalse(self.controls.path.exists())
        self.assertIsNone(self.controls.knob_brightness)
        self.write.reset_mock()
        with self.assertRaises(ControlCancelled):
            self.controls.set_knob_brightness(60, Mock(side_effect=ControlCancelled()))
        self.write.assert_not_called()

    def test_linkage_can_be_restored_during_sleep_fade_and_persists(self):
        self.controls.led_level(40, 40)
        self.controls.set_knob_brightness(80)
        self.controls.led_level(10, 40)
        self.write.reset_mock()
        result = self.controls.apply_control({'serial': SERIAL, 'action': 'followDisplayBrightness',
                                              'value': True})
        self.write.assert_called_once_with(10)
        self.assertTrue(result['state']['followDisplayBrightness'])
        restarted = DeviceControls(Mock(), self.root, SERIAL, led_writer=self.write)
        self.assertIsNone(restarted.knob_brightness)
        self.controls.set_follow_display_brightness(False)
        self.assertEqual(self.controls.knob_brightness, 40)
        self.assertFalse(self.controls.state()['followDisplayBrightness'])
        with self.assertRaises(ValueError):
            self.controls.set_follow_display_brightness(1)

    def test_independent_led_at_lcd_zero_is_distinct_from_actual_sleep(self):
        self.controls.apply_led_level(0, 0)
        self.controls.set_knob_brightness(65)
        self.write.assert_called_with(65)
        self.assertEqual(self.controls.apply_led_level(0, 0, sleeping=True), 0)
        self.controls.set_knob_brightness(80)
        self.write.assert_called_with(0)
        self.assertEqual(self.controls.apply_led_level(0, 0, sleeping=False), 80)
        self.write.assert_called_with(80)

    def test_native_transport_failure_is_not_reported_as_success(self):
        transport = Mock(_handle=123)
        library = Mock()
        library.transport_set_led_brightness.return_value = 0x04000201
        module = Mock(_transport_lib=library)
        with patch('app.n4.device_controls.sys.modules', {type(transport).__module__: module}):
            with self.assertRaisesRegex(OSError, '0x04000201'):
                checked_led_brightness(Mock(transport=transport), 50)
            library.transport_set_led_brightness.return_value = 0
            checked_led_brightness(Mock(transport=transport), 50)
            self.assertEqual(library.transport_set_led_brightness.call_args.args, (123, 50))

    def test_only_supported_device_control_actions_can_write(self):
        self.assertEqual(self.controls.state()['capabilities'], {'knobBrightness': True})
        with self.assertRaises(ValueError):
            self.controls.apply_control({'serial': SERIAL, 'action': 'unsupported'})
        self.write.assert_not_called()

    def test_settings_with_wrong_serial_are_preserved(self):
        content = json.dumps({'version': 1, 'serial': 'HGMOCK000099', 'knobBrightness': 20})
        self.controls.path.write_text(content)
        with self.assertRaises(ValueError):
            DeviceControls(Mock(), self.root, SERIAL)
        self.assertEqual(self.controls.path.read_text(), content)

    def test_realtime_previews_preserve_saved_linkage_until_one_final_commit(self):
        self.controls.set_follow_display_brightness(True)
        original = self.controls.path.read_text()
        self.write.reset_mock()
        with patch.object(self.controls, '_save', wraps=self.controls._save) as save:
            for value in (20, 40, 60):
                result = self.controls.apply_control({'serial': SERIAL, 'action': 'knobBrightness',
                                                      'value': value, 'persist': False})
                self.assertEqual(result['state']['knobBrightness'], value)
                self.assertFalse(result['state']['followDisplayBrightness'])
                self.assertEqual(self.controls.path.read_text(), original)
            save.assert_not_called()
            restarted = DeviceControls(Mock(), self.root, SERIAL, led_writer=self.write)
            self.assertIsNone(restarted.knob_brightness)
            self.controls.apply_control({'serial': SERIAL, 'action': 'knobBrightness',
                                          'value': 60, 'persist': True})
            save.assert_called_once_with(60)
        self.assertEqual([item.args[0] for item in self.write.call_args_list], [20, 40, 60])
        restarted = DeviceControls(Mock(), self.root, SERIAL, led_writer=self.write)
        self.assertEqual(restarted.knob_brightness, 60)

    def test_preview_persist_validation_and_deadline_prevent_writes_or_save(self):
        for invalid in (0, 1, 'false', None, []):
            with self.assertRaisesRegex(ValueError, 'persist must be a boolean'):
                self.controls.apply_control({'serial': SERIAL, 'action': 'knobBrightness',
                                              'value': 30, 'persist': invalid})
        self.write.assert_not_called()
        self.assertIsNone(self.controls.knob_brightness)
        with self.assertRaises(ControlCancelled):
            self.controls.set_knob_brightness(30, Mock(side_effect=ControlCancelled()), persist=False)
        self.write.assert_not_called()
        self.controls.set_knob_brightness(30, persist=False)
        with patch.object(self.controls, '_save', wraps=self.controls._save) as save:
            guard = Mock(side_effect=[None, ControlCancelled()])
            with self.assertRaises(ControlCancelled):
                self.controls.set_knob_brightness(30, guard, persist=True)
            save.assert_not_called()
            self.assertFalse(self.controls.path.exists())
            # Omission remains a persisted update for existing callers.
            self.controls.apply_control({'serial': SERIAL, 'action': 'knobBrightness', 'value': 30})
            save.assert_called_once_with(30)
        self.write.assert_called_once_with(30)


class DeviceControlServerTests(unittest.TestCase):
    def test_native_automatic_reconnect_is_disabled_unless_the_product_enables_it(self):
        config = json.loads(self.server.config_path.read_text())
        self.assertFalse(config['autoReconnect'])
        with tempfile.TemporaryDirectory() as directory:
            server = DeviceControlServer(directory, SERIAL, lambda: {}, Mock(), port=0, auto_reconnect=True)
            try:
                self.assertTrue(json.loads(server.config_path.read_text())['autoReconnect'])
            finally:
                server.close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.apply = Mock(return_value={'ok': True, 'applied': True})
        self.server = DeviceControlServer(self.temporary.name, SERIAL,
                                          lambda: {'serial': SERIAL, 'knobBrightness': 50},
                                          self.apply, port=0, timeout=.1)
        self.addCleanup(self.server.close)

    def request(self, method, path, payload=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=2)
        headers = {'Authorization': 'Bearer ' + (token or self.server.token)}
        body = None if payload is None else json.dumps(payload)
        if body is not None:
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        return response.status, value

    def test_state_requires_token_and_does_not_call_hardware(self):
        self.assertEqual(self.request('GET', '/state', token='wrong')[0], 403)
        status, value = self.request('GET', '/state')
        self.assertEqual(status, 200)
        self.assertEqual(value['serial'], SERIAL)
        self.apply.assert_not_called()
        config = json.loads(self.server.config_path.read_text())
        self.assertEqual(config['token'], self.server.token)
        self.assertEqual(config['url'], f'http://127.0.0.1:{self.server.port}')
        self.assertEqual(config['endpoint'], config['url'])

    def test_request_runs_only_in_drain_thread_and_returns_actual_failure(self):
        result = []
        self.apply.side_effect = OSError('USB write failed')
        worker = threading.Thread(target=lambda: result.append(self.request(
            'POST', '/control', {'serial': SERIAL, 'action': 'knobBrightness', 'value': 30})))
        worker.start()
        deadline = time.monotonic() + 1
        while self.server.requests.empty() and time.monotonic() < deadline:
            time.sleep(.005)
        self.apply.assert_not_called()
        self.server.drain()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0][0], 503)
        self.assertFalse(result[0][1]['ok'])
        self.assertIn('USB write failed', result[0][1]['error'])

    def test_timed_out_request_cannot_write_when_bridge_later_drains(self):
        status, value = self.request('POST', '/control',
                                     {'serial': SERIAL, 'action': 'knobBrightness', 'value': 30})
        self.assertEqual(status, 504)
        self.assertFalse(value['ok'])
        self.server.drain()
        self.apply.assert_not_called()
        now = [10]
        request = ControlRequest({}, 11, clock=lambda: now[0])
        request.guard()
        now[0] = 11
        with self.assertRaises(ControlCancelled):
            request.guard()

    def test_wrong_serial_and_unknown_action_never_enter_queue(self):
        for payload in ({'serial': 'HGMOCK000099', 'action': 'knobBrightness', 'value': 50},
                        {'serial': SERIAL, 'action': 'other'}):
            self.assertEqual(self.request('POST', '/control', payload)[0], 400)
        self.assertTrue(self.server.requests.empty())
        self.apply.assert_not_called()

    def test_close_cancels_queued_requests_without_hardware_writes(self):
        request = ControlRequest({'serial': SERIAL, 'action': 'knobBrightness', 'value': 20},
                                 time.monotonic() + 10)
        self.server.requests.put_nowait(request)
        self.server.close()
        self.assertTrue(request.completed.is_set())
        self.assertTrue(request.cancelled.is_set())
        self.assertEqual(request.status, 504)
        self.assertFalse(self.server.thread.is_alive())
        self.assertTrue(self.server.requests.empty())
        self.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()

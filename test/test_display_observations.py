"""Native-free display evidence tests; submission is never hardware readback."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mirabox-python/src'))
from hellgato_mirabox.models.n4_pro.display import MAX_OBSERVATION_COUNT, VENDOR_RETURN_KINDS
from hellgato_mirabox.models.n4_pro.state import DisplayState
from hellgato_mirabox.models.n4_pro.model import descriptor
from hellgato_mirabox.protocol import ProtocolError
from hellgato_mirabox.transport.fake import FakeBackend, DEVICE_ID
from hellgato_mirabox.transport.mirabox import MiraboxBackend
from hellgato_mirabox.worker import Worker


class DisplayObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.device = Mock()
        self.device.set_key_image.return_value = 0
        self.device.clearAllIcon.return_value = None
        self.state = DisplayState(self.device, self.temporary.name)

    def output(self, surface, color, size=(120, 120)):
        self.state.output(surface, Image.new('RGB', size, color), None, lambda: None)

    def control(self, kind, **options):
        self.state.control({'control': kind, **options}, lambda: None)

    def test_pristine_snapshot_has_ten_slots_and_does_not_expose_internal_storage(self):
        snapshot = self.state.display_observations()
        self.assertEqual((snapshot['keyCacheCount'], snapshot['keyCacheClears']), (0, 0))
        self.assertEqual(len(snapshot['keys']), 10)
        self.assertEqual(snapshot['keys'][0], {
            'attempted': 0, 'submitted': 0, 'failed': 0, 'lastWriter': 'none',
            'width': None, 'height': None, 'meanBrightness': None, 'black': None,
            'lastVendorReturnKind': None, 'vendorReturnKinds': dict.fromkeys(VENDOR_RETURN_KINDS, 0),
        })
        snapshot['keys'][0]['vendorReturnKinds']['zero'] = 99
        self.assertEqual(self.state.display_observations()['keys'][0]['vendorReturnKinds']['zero'], 0)

    def test_lcd_overwrite_exposes_cache_clear_and_replaces_each_key_intent(self):
        observed = []
        def capture(index, path):
            with Image.open(path) as image:
                observed.append((index, image.size, image.getpixel((0, 0))))
            return 0
        self.device.set_key_image.side_effect = capture
        self.output('key:2', (9, 18, 30))
        key = self.state.display_observations()['keys'][2]
        self.assertEqual((key['meanBrightness'], key['black'], key['lastWriter']), (19, False, 'key'))
        self.assertEqual(self.state.display_observations()['keyCacheCount'], 1)
        self.output('lcd', 'black', (800, 480))
        snapshot = self.state.display_observations()
        self.assertEqual((snapshot['keyCacheCount'], snapshot['keyCacheClears']), (0, 1))
        self.assertTrue(all(key['black'] and key['lastWriter'] == 'lcd' for key in snapshot['keys']))
        self.assertEqual(snapshot['keys'][2]['submitted'], 2)
        self.assertEqual(observed[0], (3, (120, 120), (9, 18, 30)))
        self.assertEqual([item[0] for item in observed[1:]], list(range(1, 11)))
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_idle_frames_only_change_cache_then_wake_records_restoration(self):
        self.control('sleep')
        before = self.state.display_observations()['keys']
        self.output('key:4', 'red')
        self.assertEqual(self.state.display_observations()['keys'], before)
        self.assertEqual(self.state.display_observations()['keyCacheCount'], 1)
        self.control('wake')
        keys = self.state.display_observations()['keys']
        self.assertTrue(all(key['submitted'] == 1 and key['lastWriter'] == 'restore' for key in keys))
        self.assertEqual((keys[4]['meanBrightness'], keys[4]['black']), (85, False))
        self.assertTrue(keys[3]['black'])

    def test_idle_lcd_cache_clear_records_no_physical_submission(self):
        self.output('key:1', 'white')
        self.control('sleep')
        before = self.state.display_observations()['keys']
        self.output('lcd', 'black', (800, 480))
        snapshot = self.state.display_observations()
        self.assertEqual((snapshot['keyCacheCount'], snapshot['keyCacheClears']), (0, 1))
        self.assertEqual(snapshot['keys'], before)

    def test_initialize_clear_fill_logo_and_screensaver_origins_remain_distinct(self):
        self.state.initialize(lambda: None)
        keys = self.state.display_observations()['keys']
        self.assertTrue(all(key['submitted'] == 2 and key['lastWriter'] == 'initialize' for key in keys))
        self.assertEqual(keys[0]['vendorReturnKinds'], {'none': 1, 'zero': 1, 'negative': 0, 'positive': 0, 'other': 0})
        self.control('clear')
        self.assertTrue(all(key['lastWriter'] == 'clear' for key in self.state.display_observations()['keys']))
        self.control('fill', surfaceId='key:6', color=[20, 30, 40])
        self.assertEqual(self.state.display_observations()['keys'][6]['lastWriter'], 'fill')
        self.state.logo = Image.new('RGBA', (800, 480), 'white')
        self.control('showLogo')
        self.assertTrue(all(key['lastWriter'] == 'logo' for key in self.state.display_observations()['keys']))
        self.state.screensaver_ready = True
        self.state.animation.frame = Mock(return_value=b'bounded-test-frame')
        self.control('screensaver')
        self.assertTrue(all(key['lastWriter'] == 'screensaver' and key['black']
                            for key in self.state.display_observations()['keys']))

    def test_partial_lcd_failure_preserves_preceding_submissions_and_does_not_log_native_error(self):
        def fail(index, path):
            if index == 4:
                raise OSError('PRIVATE device/path/native detail')
            return 0
        self.device.set_key_image.side_effect = fail
        with self.assertRaises(OSError):
            self.output('lcd', 'white', (800, 480))
        snapshot = self.state.display_observations()
        self.assertEqual([(key['attempted'], key['submitted'], key['failed']) for key in snapshot['keys']],
                         [(1, 1, 0)] * 3 + [(1, 0, 1)] + [(0, 0, 0)] * 6)
        self.assertEqual(snapshot['keys'][3]['lastWriter'], 'lcd')
        self.assertIsNone(snapshot['keys'][3]['lastVendorReturnKind'])
        self.assertNotIn('PRIVATE', json.dumps(snapshot))
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_vendor_return_categories_preserve_existing_rejection_policy(self):
        for result, kind in ((None, 'none'), (0, 'zero'), (-1, 'negative'), (0x80000001, 'positive'), (True, 'other')):
            with self.subTest(kind=kind):
                self.device.set_key_image.return_value = result
                if kind == 'negative':
                    with self.assertRaises(OSError):
                        self.output('key:0', 'white')
                else:
                    self.output('key:0', 'white')
                key = self.state.display_observations()['keys'][0]
                self.assertEqual(key['lastVendorReturnKind'], kind)
                self.assertEqual(key['vendorReturnKinds'][kind], 1)
        key = self.state.display_observations()['keys'][0]
        self.assertEqual((key['attempted'], key['submitted'], key['failed']), (5, 4, 1))

    def test_fixed_counts_saturate_and_all_key_clear_failure_remains_visible(self):
        key = self.state.writer.key_observations[0]
        key.update(attempted=MAX_OBSERVATION_COUNT, submitted=MAX_OBSERVATION_COUNT, failed=MAX_OBSERVATION_COUNT)
        key['vendorReturnKinds']['zero'] = MAX_OBSERVATION_COUNT
        self.state.key_cache_clears = MAX_OBSERVATION_COUNT
        self.output('lcd', 'white', (800, 480))
        snapshot = self.state.display_observations()
        self.assertEqual(snapshot['keyCacheClears'], MAX_OBSERVATION_COUNT)
        self.assertEqual(snapshot['keys'][0]['submitted'], MAX_OBSERVATION_COUNT)
        self.assertEqual(snapshot['keys'][0]['vendorReturnKinds']['zero'], MAX_OBSERVATION_COUNT)
        self.device.clearAllIcon.side_effect = OSError('PRIVATE')
        with self.assertRaises(OSError):
            self.state.writer.clear_keys(lambda: None, 'screensaver')
        self.assertTrue(all(key['failed'] >= 1 and key['lastWriter'] == 'screensaver'
                            for key in self.state.display_observations()['keys']))
        self.assertEqual(self.state.display_observations()['keys'][0]['failed'], MAX_OBSERVATION_COUNT)

    def test_physical_success_replies_and_get_state_include_the_same_cheap_snapshot(self):
        backend = object.__new__(MiraboxBackend)
        backend.device, backend.display, backend.lock = self.device, self.state, threading.RLock()
        data = io.BytesIO()
        Image.new('RGB', (120, 120), 'white').save(data, format='JPEG')
        frame = {'surfaceId': 'key:3', 'width': 120, 'height': 120,
                 'data': base64.b64encode(data.getvalue()).decode()}
        result = backend.set_output(frame, lambda: None)
        self.assertEqual((result['status'], result['verification']), ('submitted', 'unconfirmed'))
        self.assertEqual(result['displayObservations']['keyCacheCount'], 1)
        result = backend.set_control({'control': 'clear', 'surfaceId': 'key:3'}, lambda: None)
        self.assertEqual(result['displayObservations']['keys'][3]['lastWriter'], 'clear')
        self.assertEqual(backend.get_state()['displayObservations'], result['displayObservations'])
        backend.display = None
        self.assertIsNone(backend.get_state()['displayObservations'])

    def test_worker_error_reply_carries_failed_snapshot_without_raw_exception_text(self):
        backend = FakeBackend()
        worker = Worker(backend, io.BytesIO(), io.BytesIO(), 'tests')
        deadline = int(time.time() * 1000) + 3000
        worker.dispatch({'jsonrpc': '2.0', 'id': 'hello', 'method': 'hello',
                         'params': {'protocol': {'major': 1, 'minor': 0}, 'deadlineAt': deadline}})
        worker.dispatch({'jsonrpc': '2.0', 'id': 'open', 'method': 'open',
                         'params': {'deviceId': DEVICE_ID, 'generation': 1, 'deadlineAt': deadline}})
        snapshot = self.state.display_observations()
        backend.get_display_observations = lambda: snapshot
        backend.set_control = Mock(side_effect=OSError('PRIVATE native error'))
        message = {'jsonrpc': '2.0', 'id': 'control', 'method': 'setControl',
                   'params': {'deviceId': DEVICE_ID, 'generation': 1, 'type': 'control',
                              'control': 'brightness', 'value': 40, 'deadlineAt': int(time.time() * 1000) + 3000}}
        data = json.dumps(message).encode()
        worker.requests.put(data)
        worker.queued_bytes = len(data)
        replies = []
        def finish(reply):
            replies.append(reply)
            worker.stopped.set()
        with patch.object(worker, '_read'), patch.object(worker, 'send', side_effect=finish):
            self.assertEqual(worker.run(), 0)
        self.assertEqual(replies[0]['error']['data']['displayObservations'], snapshot)
        self.assertNotIn('PRIVATE', json.dumps(replies))

    def test_worker_deadline_reply_exposes_mid_lcd_submissions_and_failed_key(self):
        backend = object.__new__(MiraboxBackend)
        backend.device, backend.display, backend.lock = self.device, self.state, threading.RLock()
        backend.poll, backend.close = Mock(), Mock()
        worker = Worker(backend, io.BytesIO(), io.BytesIO(), 'tests')
        worker.negotiated, worker.current, worker.generation = True, 'test', 1
        worker.descriptor = descriptor('test')
        message = {'jsonrpc': '2.0', 'id': 'control', 'method': 'setControl',
                   'params': {'deviceId': 'test', 'generation': 1, 'type': 'control',
                              'control': 'fill', 'color': [255, 255, 255]}}
        data = json.dumps(message).encode()
        worker.requests.put(data)
        worker.queued_bytes = len(data)
        guards, replies = [], []
        def deadline(params):
            guards.append(params)
            if len(guards) == 8:
                raise ProtocolError('Request deadline exceeded', 'ERR_REQUEST_TIMEOUT')
        def finish(reply):
            replies.append(reply)
            worker.stopped.set()
        with patch.object(worker, '_read'), patch.object(worker, '_deadline', side_effect=deadline), \
                patch.object(worker, 'send', side_effect=finish):
            self.assertEqual(worker.run(), 0)
        error = replies[0]['error']
        self.assertEqual(error['message'], 'Request deadline exceeded')
        self.assertEqual(error['data']['code'], 'ERR_REQUEST_TIMEOUT')
        self.assertEqual([(key['attempted'], key['submitted'], key['failed'])
                          for key in error['data']['displayObservations']['keys']],
                         [(1, 1, 0)] * 3 + [(1, 0, 1)] + [(0, 0, 0)] * 6)
        self.assertEqual(self.device.set_key_image.call_count, 3)


if __name__ == '__main__':
    unittest.main()

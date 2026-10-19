"""Product GUI, native preferences and process supervision use the separated worker."""

from io import BytesIO, StringIO
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/stream-dog/python'))
from hellgato_stream_dog.device_registry import DeviceIdentityConflict, DeviceRegistry
from hellgato_stream_dog.ipc_bridge import ProductBridge, ProductControls, ProductProcess, configuration, initial_controls


def descriptor(identifier='a'):
    return {'id': 'n4-' + identifier * 64, 'modelId': 'n4-pro', 'firmware': 'checked',
            'capabilities': {'features': {'knobBrightness': {'status': 'native'}}}}


class ProductIpcTest(unittest.TestCase):
    def test_primary_network_loss_does_not_claim_usb_was_disconnected(self):
        with TemporaryDirectory() as directory:
            bridge = self.make_bridge(Path(directory))
            bridge.selected = bridge.select_device([descriptor()])
            bridge.on_event({'event': 'deviceReady', 'deviceId': descriptor()['id'],
                             'capabilities': descriptor()['capabilities']})
            bridge.on_event({'event': 'started', 'ports': {'primary': 5343, 'child': 5344}})
            bridge.on_event({'event': 'child.clientConnected', 'hostConnected': True})
            self.assertTrue(bridge.controls.state()['hostConnected'])
            bridge.on_event({'event': 'primary.clientDisconnected', 'hostConnected': False})
            state = bridge.controls.state()
            self.assertTrue(state['connected'])
            self.assertTrue(state['networkListening'])
            self.assertFalse(state['hostConnected'])
            self.assertFalse(bridge.emit.call_args.args[0]['detail']['hostConnected'])
            self.assertTrue(state['connectionDesired'])
            bridge.stop_file.touch()
            self.assertFalse(bridge.controls.state()['connectionDesired'])

    def test_gui_requires_both_network_channels_after_usb_recovers(self):
        from hellgato_stream_dog.launcher import Session
        session = Session(queue.Queue())
        session.device_ready.set()
        session.connected.set()
        for event, connected in (('primary.clientDisconnected', False), ('child.clientConnected', False),
                                  ('primary.clientConnected', True)):
            session.log_bridge(StringIO(json.dumps({'event': event, 'detail': {'hostConnected': connected}}) + '\n'), StringIO())
            self.assertEqual(session.connected.is_set(), connected)
            self.assertTrue(session.device_ready.is_set())
            self.assertEqual(session.phase, 'connected' if connected else 'pairing')

    def test_worker_descriptor_registration_reuses_identity_and_refuses_replacement(self):
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 23, 7)
            row = registry.remember(registry.prepare_descriptor(descriptor()))
            self.assertEqual(row['virtualSerial'], 'HGMOCK000023')
            self.assertEqual(row['dockIdentity'], 7)
            self.assertEqual(row['identitySource'], 'sdk')
            self.assertNotIn('vendorId', row)
            second = DeviceRegistry(directory, 23, 7)
            self.assertEqual(second.prepare_descriptor(descriptor()), row)
            with self.assertRaises(DeviceIdentityConflict):
                second.prepare_descriptor(descriptor('b'))
            with self.assertRaises(DeviceIdentityConflict):
                DeviceRegistry(directory, 23, 8).prepare_descriptor(descriptor())

    def test_migration_retains_legacy_registry_metadata(self):
        from types import SimpleNamespace
        with TemporaryDirectory() as directory:
            registry = DeviceRegistry(directory, 23, 7)
            legacy = registry.remember(registry.prepare(SimpleNamespace(serial_number='old', path=b'usb', vendor_id=1, product_id=2)))
            discovered = {**descriptor(), 'id': legacy['id']}
            self.assertEqual(registry.prepare_descriptor(discovered), legacy)

    def test_product_preferences_are_loaded_and_saved_in_existing_files(self):
        with TemporaryDirectory() as directory:
            state, serial = Path(directory), 'HGMOCK000023'
            (state / f'display-{serial}.json').write_text('{"sleepDuration":2147483647}', encoding='utf-8')
            (state / f'device-controls-{serial}.json').write_text(json.dumps({'version': 1, 'serial': serial, 'knobBrightness': 35}), encoding='utf-8')
            controls = ProductControls(state, serial, initial_controls(state, serial))
            self.assertEqual(controls.settings, {'sleepDuration': 2147483647, 'knobBrightness': 35, 'followDisplayBrightness': False})
            controls.save({'sleepDuration': 20, 'knobBrightness': 70, 'followDisplayBrightness': False})
            self.assertEqual(initial_controls(state, serial)['knobBrightness'], 70)
            controls.save({'followDisplayBrightness': True})
            self.assertTrue(initial_controls(state, serial)['followDisplayBrightness'])
            self.assertEqual(json.loads((state / f'display-{serial}.json').read_text())['sleepDuration'], 20)
            self.assertEqual(list(state.glob('*.tmp')), [])

    def test_native_preview_uses_correlated_sdk_commands_without_persisting(self):
        with TemporaryDirectory() as directory:
            state, serial = Path(directory), 'HGMOCK000023'
            controls = ProductControls(state, serial, {'knobBrightness': 30, 'followDisplayBrightness': False})
            controls.connected = controls.knob_supported = True
            controls.channel = Mock()
            controls.channel.request.side_effect = [
                {'event': 'controlSubmitted', 'status': 'submitted'},
                {'event': 'workerState', 'state': {'knobBrightness': 60, 'followDisplayBrightness': False}}]
            result = controls.apply({'serial': serial, 'action': 'knobBrightness', 'value': 60, 'persist': False})
            self.assertTrue(result['ok'])
            self.assertEqual(result['state']['knobBrightness'], 60)
            self.assertEqual(controls.settings['knobBrightness'], 30)
            self.assertFalse((state / f'device-controls-{serial}.json').exists())
            self.assertEqual(controls.channel.request.call_args_list[0].kwargs['control'],
                             {'control': 'knobBrightness', 'value': 60, 'persist': False})
            self.assertEqual(controls.channel.request.call_args_list[1].args, ('workerState',))

    def test_unavailable_native_controls_do_not_send_or_persist(self):
        with TemporaryDirectory() as directory:
            controls = ProductControls(Path(directory), 'HGMOCK000023', {})
            controls.channel = Mock()
            with self.assertRaises(OSError):
                controls.apply({'serial': controls.serial, 'action': 'knobBrightness', 'value': 20})
            controls.channel.request.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_discovery_waits_for_saved_device_and_only_remembers_after_open(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            settings = '{"version":1,"identity":23,"dockIdentity":7,"stripMode":"frame"}'
            (state / 'settings.json').write_text(settings, encoding='utf-8')
            events = []
            bridge = ProductBridge(state, state / 'stop', emit=events.append)
            self.assertIsNone(bridge.select_device([]))
            self.assertFalse((state / 'devices.json').exists())
            bridge.selected = bridge.select_device([descriptor()])
            self.assertFalse((state / 'devices.json').exists())
            bridge.on_event({'event': 'deviceReady', 'deviceId': descriptor()['id'], 'firmware': 'checked',
                             'capabilities': descriptor()['capabilities']})
            self.assertTrue(bridge.controls.state()['available'])
            self.assertEqual(events[-1]['detail']['deviceId'], descriptor()['id'])
            self.assertEqual(events[-1]['detail']['firmware'], 'checked')
            self.assertIsNone(bridge.select_device([descriptor('b')]))
            self.assertEqual(events[-1]['detail']['reason'], 'registered-device-mismatch')
            self.assertEqual((state / 'settings.json').read_text(), settings)

    def test_configuration_uses_normal_product_runtime_and_readiness_entrypoint(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ('runtime/node.exe', 'runtime/mirabox/HellgatoMiraboxWorker.exe',
                         'work/stream-dog-sdk/candidate-runner.mjs'):
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'fixture')
            (root / 'runtime/mirabox/runtime-manifest.json').write_text('{"buildId":"verified-source"}', encoding='utf-8')
            settings = {'identity': 23, 'dockIdentity': 7, 'stripMode': 'frame'}
            with patch.dict(os.environ, {'ProgramFiles': str(root)}):
                config, command = configuration(root, root, settings, {}, descriptor()['id'])
            self.assertTrue(config['productMode'])
            self.assertEqual(config['identity']['childSerial'], 'HGMOCK000023')
            self.assertEqual(config['runtime']['command'], str((root / 'runtime/mirabox/HellgatoMiraboxWorker.exe').resolve()))
            self.assertIn('--inspect-host', config['readiness']['args'])
            self.assertEqual(config['screensaverMode'], 'stream')
            self.assertEqual(command[1], str((root / 'work/stream-dog-sdk/candidate-runner.mjs').resolve()))
            saver = root / 'saved animation.webp'
            reload_config = root / 'screensaver-HGMOCK000023.json'
            with patch.dict(os.environ, {'ProgramFiles': str(root)}):
                config, _ = configuration(root, root, settings, {}, descriptor()['id'], saver, reload_config)
            args = config['runtime']['args']
            self.assertEqual(args[args.index('--screensaver-config') + 1], str(reload_config))
            self.assertEqual(args[args.index('--screensaver-path') + 1], str(saver))
            self.assertEqual(config['screensaverMode'], 'native')

    def test_process_job_assignment_precedes_start_and_correlates_requests(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'assigned'
            helper = root / 'helper.py'
            helper.write_text('''import json,sys
from pathlib import Path
for line in sys.stdin:
    value=json.loads(line)
    if value['command']=='start':
        print(json.dumps({'event':'ready','assigned':Path(sys.argv[1]).exists()}),flush=True)
    elif value['command']=='stop':
        break
    else:
        print(json.dumps({'event':'workerState','requestId':value['requestId'],'state':{'knobBrightness':42}}),flush=True)
''', encoding='utf-8')
            class Job:
                def __enter__(self):
                    return self

                def assign(self, process):
                    marker.touch()

                def __exit__(self, *_):
                    pass
            ready = threading.Event()
            events = []
            def received(value):
                events.append(value)
                ready.set()
            channel = ProductProcess([sys.executable, str(helper), str(marker)], root / 'config', received, job_factory=Job)
            try:
                channel.start()
                self.assertTrue(ready.wait(3))
                self.assertTrue(events[0]['assigned'])
                result = channel.request('workerState')
                self.assertEqual(result['state']['knobBrightness'], 42)
                self.assertEqual(len(events), 1)
            finally:
                channel.close()
            self.assertEqual(channel.process.returncode, 0)

    def test_fatal_stderr_preserves_error_details_without_unrelated_fields(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'helper.py'
            helper.write_text("import sys,json\nsys.stdin.readline()\nprint(json.dumps({'state':'failed','code':'ERR_FIRMWARE_UNVERIFIED','message':'Missing firmware','stack':'worker trace','details':{'operation':'discover'},'private':'hidden'}),file=sys.stderr,flush=True)\n", encoding='utf-8')
            job = Mock()
            job.__enter__ = Mock(return_value=job)
            job.__exit__ = Mock()
            events = []
            channel = ProductProcess([sys.executable, str(helper)], root / 'config', events.append, job_factory=lambda: job)
            try:
                channel.start()
                channel.process.wait(timeout=3)
            finally:
                channel.close()
            self.assertEqual(channel.failure, 'ERR_FIRMWARE_UNVERIFIED')
            self.assertEqual(events, [{'event': 'diagnostic', 'detail': {
                'code': 'ERR_FIRMWARE_UNVERIFIED', 'message': 'Missing firmware', 'stack': 'worker trace',
                'details': {'operation': 'discover'}}}, {'state': 'failed', 'code': 'ERR_FIRMWARE_UNVERIFIED'}])

    def test_gui_consumes_private_device_event_but_redacts_identity_from_log(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(ROOT), 'APPDATA': str(ROOT)}):
            from hellgato_stream_dog.launcher import Session
        session = Session(queue.Queue())
        log = StringIO()
        session.log_bridge(StringIO('[1]\n{"detail":null}\n' + json.dumps(
            {'event': 'deviceReady', 'detail': {'deviceId': descriptor()['id']}}) + '\n'), log)
        self.assertEqual(session.active_device_id, descriptor()['id'])
        self.assertNotIn(descriptor()['id'], log.getvalue())

    def make_bridge(self, state):
        (state / 'settings.json').write_text('{"version":1,"identity":23,"dockIdentity":7,"stripMode":"frame"}', encoding='utf-8')
        return ProductBridge(state, state / 'stop', emit=Mock())

    def test_initial_no_usb_waits_and_keeps_existing_preferences_authoritative(self):
        import device_control_server
        import screensaver
        with TemporaryDirectory() as directory:
            state = Path(directory)
            bridge = self.make_bridge(state)
            generated = state / f'ipc-state-{bridge.serial}.json'
            generated.write_text('{"old":"generated"}', encoding='utf-8')
            original = (state / 'settings.json').read_bytes()
            def absent(server):
                bridge.stop_file.touch()
                return None
            with patch.object(bridge, 'discover', side_effect=absent), \
                    patch.object(device_control_server, 'DeviceControlServer'), \
                    patch.object(screensaver, 'ScreensaverSource'):
                bridge.run()
            self.assertFalse(generated.exists())
            self.assertEqual((state / 'settings.json').read_bytes(), original)
            self.assertFalse((state / 'devices.json').exists())

    def test_disconnected_query_race_preserves_supervisor_recovery_loop(self):
        import device_control_server
        import screensaver
        with TemporaryDirectory() as directory:
            state = Path(directory)
            bridge = self.make_bridge(state)
            selected = bridge.registry.prepare_descriptor(descriptor())
            channel = Mock(failure=None)
            channel.process.poll.return_value = None
            count = 0
            def query(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 1:
                    return {'event': 'workerStateFailed', 'code': 'ERR_DEVICE_CLOSED'}
                bridge.stop_file.touch()
                return {'event': 'workerState', 'state': {'connected': False}}
            channel.request.side_effect = query
            @contextmanager
            def active(**kwargs):
                bridge.on_event({'event': 'deviceReady', **{'deviceId': selected['id'], 'capabilities': descriptor()['capabilities']}})
                yield channel
            timer = iter(range(100))
            with patch.object(bridge, 'discover', return_value=selected), \
                    patch.object(bridge, 'channel', side_effect=active), \
                    patch.object(device_control_server, 'DeviceControlServer'), \
                    patch.object(screensaver, 'ScreensaverSource') as source, \
                    patch('hellgato_stream_dog.ipc_bridge.time.monotonic', side_effect=lambda: next(timer)), \
                    patch('hellgato_stream_dog.ipc_bridge.time.sleep'):
                source.return_value.reload.return_value = False
                bridge.run()
            self.assertEqual(count, 2)
            self.assertFalse(bridge.controls.connected)

    def test_host_commands_are_bounded_and_relay_correlated_physical_transitions(self):
        with TemporaryDirectory() as directory:
            bridge = self.make_bridge(Path(directory))
            bridge.read_host_commands(BytesIO(b'{"command":"hostUnavailable"}\n{"command":"hostAvailable"}\n'))
            channel = Mock()
            channel.request.side_effect = [{'event': 'hostUnavailable', 'status': 'paused'},
                                           {'event': 'hostAvailable', 'status': 'starting'}]
            bridge.parent_closed = False
            bridge.pump_host_commands(channel)
            self.assertEqual([call.args[0] for call in channel.request.call_args_list],
                             ['hostUnavailable', 'hostAvailable'])
            self.assertTrue(bridge.host_available)
            self.assertFalse(bridge.controls.connected)
            bridge.read_host_commands(BytesIO(b'{"command":"hostUnavailable","reason":"untrusted"}\n'))
            with self.assertRaises(OSError):
                bridge.pump_host_commands(channel)

    def test_screensaver_changes_keep_the_same_usb_and_network_session(self):
        import device_control_server
        import screensaver
        with TemporaryDirectory() as directory:
            state = Path(directory)
            bridge = self.make_bridge(state)
            selected = bridge.registry.prepare_descriptor(descriptor())
            channel = Mock(failure=None)
            channel.process.poll.return_value = None
            channel.request.return_value = {'event': 'workerState', 'state': {'connected': True}}
            source = Mock()
            paths = iter([state / 'original.webp', state / 'changed.webp', None, state / 'changed.webp'])
            def reload_source():
                try:
                    source.path = next(paths)
                    return True
                except StopIteration:
                    bridge.stop_file.touch()
                    return False
            source.reload.side_effect = reload_source
            updates = []
            original_update = bridge.update_screensaver
            def update(path, **kwargs):
                original_update(path, **kwargs)
                updates.append(json.loads((state / f'screensaver-{bridge.serial}.json').read_text(encoding='utf-8')))
                self.assertTrue(bridge.controls.connected)
                self.assertTrue(bridge.controls.host_connected)
            @contextmanager
            def active(**kwargs):
                bridge.on_event({'event': 'deviceReady', 'deviceId': selected['id'],
                                 'capabilities': descriptor()['capabilities']})
                bridge.on_event({'event': 'child.clientConnected', 'hostConnected': True})
                bridge.update_screensaver(kwargs['saver'], initial=True)
                yield channel
                self.assertTrue(bridge.stop_file.exists())
            timer = iter(range(100))
            with patch.object(bridge, 'discover', return_value=selected) as discover, \
                    patch.object(bridge, 'channel', side_effect=active) as channels, \
                    patch.object(bridge, 'update_screensaver', side_effect=update), \
                    patch.object(device_control_server, 'DeviceControlServer'), \
                    patch.object(screensaver, 'ScreensaverSource', return_value=source), \
                    patch('hellgato_stream_dog.ipc_bridge.time.monotonic', side_effect=lambda: next(timer)), \
                    patch('hellgato_stream_dog.ipc_bridge.time.sleep'):
                bridge.run()
            self.assertEqual(discover.call_count, 1)
            self.assertEqual(channels.call_count, 1)
            self.assertEqual([item['revision'] for item in updates], [0, 1, 2, 3])
            self.assertEqual([item['path'] for item in updates],
                             [str(state / 'original.webp'), str(state / 'changed.webp'), None, str(state / 'changed.webp')])
            self.assertFalse(list(state.glob('*.tmp')))

    def test_screensaver_write_failure_retries_only_the_latest_selection(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            bridge = self.make_bridge(state)
            bridge.update_screensaver(state / 'initial.png', initial=True)
            target = state / f'screensaver-{bridge.serial}.json'
            with patch('hellgato_stream_dog.ipc_bridge.write_json', side_effect=PermissionError('Sharing violation')):
                bridge.update_screensaver(state / 'stale.png')
                bridge.update_screensaver(None)
                self.assertEqual(json.loads(target.read_text())['revision'], 0)
            bridge.flush_screensaver()
            self.assertEqual(json.loads(target.read_text()), {'version': 1, 'revision': 2, 'path': None})
            self.assertIsNone(bridge.pending_screensaver)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows file sharing contract')
    def test_open_config_reader_does_not_interrupt_screensaver_updates(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            bridge = self.make_bridge(state)
            bridge.update_screensaver(state / 'initial.png', initial=True)
            target = state / f'screensaver-{bridge.serial}.json'
            with target.open('rb') as reader:
                bridge.update_screensaver(state / 'replacement.png')
                self.assertEqual(json.load(reader)['revision'], 0)
                self.assertIsNotNone(bridge.pending_screensaver)
            bridge.flush_screensaver()
            self.assertEqual(json.loads(target.read_text())['revision'], 1)
            self.assertFalse(list(state.glob('*.tmp')))

    def test_confirmed_host_absence_does_not_start_discovery_worker(self):
        import device_control_server
        import screensaver
        with TemporaryDirectory() as directory:
            bridge = self.make_bridge(Path(directory))
            bridge.host_commands.put_nowait('hostUnavailable')
            def pause(_):
                bridge.stop_file.touch()
            with patch.object(bridge, 'discover') as discover, \
                    patch.object(device_control_server, 'DeviceControlServer'), \
                    patch.object(screensaver, 'ScreensaverSource'), \
                    patch('hellgato_stream_dog.ipc_bridge.time.sleep', side_effect=pause):
                bridge.run()
            discover.assert_not_called()
            self.assertFalse(bridge.host_available)


if __name__ == '__main__':
    unittest.main()

"""No-hardware verification of candidate isolation, snapshots and restore."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/stream-dog/python'))
from hellgato_candidate.files import (digest, existing_identity, inventory, make_snapshot,
    read_json, restore_snapshot, safe_relative, verify_bundle, verify_inventory, verify_snapshot)


class CandidateFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.profiles = self.root / 'ProfilesV3'
        self.backup = self.root / 'backup'
        self.state.mkdir()
        self.profiles.mkdir()
        self.settings = {'version': 1, 'identity': 42, 'dockIdentity': 99, 'stripMode': 'frame'}
        serial = 'PRIVATE-PHYSICAL-SERIAL'
        fingerprint = json.dumps(['StreamDockN4Pro', 1, 2, 'serial', serial], ensure_ascii=True, separators=(',', ':'))
        self.record = {'id': 'n4-' + hashlib.sha256(fingerprint.encode()).hexdigest(), 'identity': 42,
                       'dockIdentity': 99, 'model': 'StreamDockN4Pro', 'virtualSerial': 'HGMOCK000042',
                       'identitySource': 'serial', 'physicalSerial': serial, 'vendorId': 1, 'productId': 2}
        self.write('settings.json', self.settings)
        self.write('devices.json', {'version': 1, 'devices': [self.record]})
        (self.profiles / 'profile.sdProfile').mkdir()
        (self.profiles / 'profile.sdProfile/manifest.json').write_text('{"Device":"private"}')
        (self.profiles / 'profile.sdProfile/image.png').write_bytes(b'private-profile-image')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def snapshot(self):
        return make_snapshot(self.state, self.profiles, self.backup, 42, 'a' * 40)

    def test_saved_independent_identity_is_preserved_without_writes(self):
        before = inventory(self.state)
        settings, record = existing_identity(self.state)
        self.assertEqual((settings['dockIdentity'], record['virtualSerial']), (99, 'HGMOCK000042'))
        self.assertEqual(inventory(self.state), before)

    def test_missing_registry_does_not_create_identity(self):
        (self.state / 'devices.json').unlink()
        before = inventory(self.state)
        with self.assertRaises(FileNotFoundError):
            existing_identity(self.state)
        self.assertEqual(inventory(self.state), before)

    def test_registry_mismatch_and_fingerprint_tampering_fail(self):
        for change in ({'dockIdentity': 12}, {'id': 'n4-' + 'b' * 64}, {'virtualSerial': 'HGMOCK000012'}, {'physicalSerial': 'wrong'}):
            self.write('devices.json', {'version': 1, 'devices': [{**self.record, **change}]})
            with self.subTest(change=change), self.assertRaises(ValueError):
                existing_identity(self.state)

    def test_duplicate_selected_records_fail(self):
        self.write('devices.json', {'version': 1, 'devices': [self.record, self.record]})
        with self.assertRaises(ValueError):
            existing_identity(self.state)

    def test_boolean_identity_rejected(self):
        self.write('settings.json', {**self.settings, 'identity': True})
        with self.assertRaises(ValueError):
            existing_identity(self.state)

    def test_snapshot_copies_exact_bytes_and_never_touches_sources(self):
        before = inventory(self.state), inventory(self.profiles)
        value = self.snapshot()
        self.assertEqual(value['sourceSha'], 'a' * 40)
        self.assertEqual(verify_snapshot(self.backup), value)
        self.assertEqual((inventory(self.state), inventory(self.profiles)), before)
        self.assertIn('device-controls-HGMOCK000042.json', value['settingsAbsent'])

    def test_snapshot_tampering_or_extra_files_refused(self):
        self.snapshot()
        (self.backup / 'injected.exe').write_bytes(b'not backed up')
        with self.assertRaises(ValueError):
            verify_snapshot(self.backup)
        (self.backup / 'injected.exe').unlink()
        (self.backup / 'settings/settings.json').write_text('{}')
        with self.assertRaises(ValueError):
            verify_snapshot(self.backup)

    def test_snapshot_failure_never_publishes_partial_backup(self):
        before = inventory(self.state), inventory(self.profiles)
        with patch('hellgato_candidate.files.copy_verified', side_effect=OSError('Copy failed')):
            with self.assertRaisesRegex(OSError, 'Copy failed'):
                self.snapshot()
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.root.glob('.ipc-backup-stage-*')), [])
        self.assertEqual((inventory(self.state), inventory(self.profiles)), before)

    def test_snapshot_rejects_destination_within_any_source(self):
        before = inventory(self.state), inventory(self.profiles)
        for destination in (self.state / 'backup', self.profiles / 'backup', self.state / '..' / 'ProfilesV3/backup'):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'locations must be separate'):
                make_snapshot(self.state, self.profiles, destination, 42, 'a' * 40)
        self.assertEqual((inventory(self.state), inventory(self.profiles)), before)

    def test_snapshot_rechecks_earlier_settings_after_later_copies(self):
        from hellgato_candidate.files import copy_verified
        def copy_and_change(source, target, expected):
            copy_verified(source, target, expected)
            if source.name == 'devices.json':
                self.write('settings.json', {**self.settings, 'stripMode': 'panels'})
        with patch('hellgato_candidate.files.copy_verified', side_effect=copy_and_change):
            with self.assertRaisesRegex(ValueError, 'settings changed'):
                self.snapshot()
        self.assertFalse(self.backup.exists())

    def test_json_and_backup_metadata_are_strict(self):
        value = self.snapshot()
        path = self.backup / 'backup-manifest.json'
        for change in ({'schemaVersion': True}, {'sourceSha': None}, {'createdAt': None},
                       {'createdAt': True}, {'createdAt': -1}, {'settingsAbsent': [{}]},
                       {'profilesExisted': False}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**value, **change}))
                with self.assertRaises(ValueError):
                    verify_snapshot(self.backup)
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[' * 1100 + ']' * 1100):
            path.write_text(text)
            with self.subTest(text=text[:60]), self.assertRaises(ValueError):
                read_json(path)
    def test_restore_preserves_displaced_data_and_keeps_identity(self):
        self.snapshot()
        changed = {**self.settings, 'stripMode': 'panels'}
        self.write('settings.json', changed)
        (self.profiles / 'new-private-profile.json').write_text('new work')
        preserved = restore_snapshot(self.backup, self.state, self.profiles)
        self.assertEqual(read_json(self.state / 'settings.json'), self.settings)
        self.assertEqual(read_json(preserved / 'settings.json'), changed)
        self.assertFalse((self.profiles / 'new-private-profile.json').exists())
        old = list(self.root.glob('ProfilesV3.before-ipc-rollback-*'))
        self.assertEqual(len(old), 1)
        self.assertEqual((old[0] / 'new-private-profile.json').read_text(), 'new work')
        self.assertEqual((self.profiles / 'profile.sdProfile/image.png').read_bytes(), b'private-profile-image')

    def test_restore_verifies_before_mutating(self):
        self.snapshot()
        (self.backup / 'profiles/profile.sdProfile/image.png').write_bytes(b'bad')
        before = inventory(self.state), inventory(self.profiles)
        with self.assertRaises(ValueError):
            restore_snapshot(self.backup, self.state, self.profiles)
        self.assertEqual((inventory(self.state), inventory(self.profiles)), before)

    def test_restore_linked_profile_preflight_never_changes_settings(self):
        self.snapshot()
        self.write('settings.json', {**self.settings, 'stripMode': 'panels'})
        original = self.root / 'original-profiles'
        self.profiles.rename(original)
        try:
            self.profiles.symlink_to(original, target_is_directory=True)
        except OSError:
            original.rename(self.profiles)
            self.skipTest('Symlink creation unavailable')
        before = inventory(self.state), inventory(original)
        with self.assertRaises(ValueError):
            restore_snapshot(self.backup, self.state, self.profiles)
        self.assertEqual((inventory(self.state), inventory(original)), before)

    def test_restore_mid_transaction_failure_recovers_every_active_file(self):
        self.snapshot()
        self.write('settings.json', {**self.settings, 'stripMode': 'panels'})
        (self.profiles / 'new.json').write_text('after test')
        before_settings = {p.name: p.read_bytes() for p in self.state.iterdir()}
        before_profiles = inventory(self.profiles)
        real_replace = os.replace
        fired = False
        def fail_once(source, target):
            nonlocal fired
            if Path(target) == self.state / 'settings.json' and not fired:
                fired = True
                raise OSError('Simulated locked setting file')
            return real_replace(source, target)
        with patch('hellgato_candidate.files.os.replace', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'Simulated'):
                restore_snapshot(self.backup, self.state, self.profiles)
        self.assertTrue(fired)
        self.assertEqual({name: (self.state / name).read_bytes() for name in before_settings}, before_settings)
        self.assertEqual(inventory(self.profiles), before_profiles)

    def test_restore_interrupt_recovers_profiles_and_prior_settings(self):
        self.snapshot()
        self.write('settings.json', {**self.settings, 'stripMode': 'panels'})
        self.write('devices.json', {'changed': True})
        (self.profiles / 'new.json').write_text('after test')
        before_settings = {p.name: p.read_bytes() for p in self.state.iterdir()}
        before_profiles = inventory(self.profiles)
        real_replace = os.replace
        def interrupt(source, target):
            if Path(source).parent.name == 'replacement' and Path(target).name == 'settings.json':
                raise KeyboardInterrupt()
            return real_replace(source, target)
        with patch('hellgato_candidate.files.os.replace', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                restore_snapshot(self.backup, self.state, self.profiles)
        self.assertEqual({name: (self.state / name).read_bytes() for name in before_settings}, before_settings)
        self.assertEqual(inventory(self.profiles), before_profiles)

    def test_failed_settings_undo_still_recovers_profiles_and_keeps_staging(self):
        self.snapshot()
        self.write('settings.json', {**self.settings, 'stripMode': 'panels'})
        self.write('devices.json', {'changed': True})
        (self.profiles / 'new.json').write_text('after test')
        before_profiles = inventory(self.profiles)
        real_replace = os.replace
        def fail_setting_and_undo(source, target):
            if Path(source).name == 'undo-devices.json':
                raise OSError('Cannot undo locked registry')
            if Path(source).parent.name == 'replacement' and Path(target).name == 'settings.json':
                raise OSError('Cannot replace locked settings')
            return real_replace(source, target)
        with patch('hellgato_candidate.files.os.replace', side_effect=fail_setting_and_undo):
            with self.assertRaisesRegex(RuntimeError, 'recovery could not finish'):
                restore_snapshot(self.backup, self.state, self.profiles)
        self.assertEqual(inventory(self.profiles), before_profiles)
        preserved = list(self.state.glob('before-ipc-rollback-*'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(read_json(preserved[0] / 'devices.json'), {'changed': True})
        stage = list(self.root.glob('.ipc-restore-stage-*'))
        self.assertEqual(len(stage), 1)
        self.assertEqual(read_json(stage[0] / 'undo-devices.json'), {'changed': True})

    def test_restore_rejects_backup_inside_target_before_mutating(self):
        self.backup = self.profiles / 'nested-backup'
        # Construct a valid backup elsewhere, then simulate the caller selecting
        # a copy nested within a target that the transaction would displace.
        outside = self.root / 'outside'
        make_snapshot(self.state, self.profiles, outside, 42, 'a' * 40)
        outside.rename(self.backup)
        before = inventory(self.state), inventory(self.profiles)
        with self.assertRaisesRegex(ValueError, 'locations must be separate'):
            restore_snapshot(self.backup, self.state, self.profiles)
        self.assertEqual((inventory(self.state), inventory(self.profiles)), before)

    def test_no_profile_directory_is_safe(self):
        shutil.rmtree(self.profiles)
        value = self.snapshot()
        self.assertFalse(value['profilesExisted'])
        restore_snapshot(self.backup, self.state, self.profiles)
        self.assertFalse(self.profiles.exists())

    def test_inventory_escape_and_duplicate_paths_rejected(self):
        for path in ('../bad', '/bad', 'C:/bad', 'x\\bad', 'x//bad', 'x/./bad', 'x/../bad'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_relative(path)
        entries = inventory(self.state)
        with self.assertRaises(ValueError):
            verify_inventory(self.state, entries + entries)

    def test_inventory_refuses_unreadable_tree_and_regular_file_root(self):
        with self.assertRaises(ValueError):
            inventory(self.state / 'settings.json')
        with patch('hellgato_candidate.files.os.scandir', side_effect=PermissionError('Unreadable profile tree')):
            with self.assertRaises(PermissionError):
                inventory(self.profiles)

    def test_links_are_not_followed(self):
        link = self.profiles / 'unsafe'
        try:
            link.symlink_to(self.state, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_runtime_and_bundle_bind_exact_bytes(self):
        root = self.root / 'bundle'
        worker = root / 'worker'
        worker.mkdir(parents=True)
        (root / 'HellgatoIpcTest.exe').write_bytes(b'supervisor')
        (worker / 'HellgatoMiraboxWorker.exe').write_bytes(b'worker')
        (worker / 'dependency.dll').write_bytes(b'dependency')
        build_id = 'windows-ipc-' + 'a' * 40
        runtime = {'schemaVersion': 1, 'platform': 'win32', 'arch': 'x64', 'buildId': build_id,
                   'executableSha256': digest(worker / 'HellgatoMiraboxWorker.exe'), 'files': inventory(worker)}
        (worker / 'runtime-manifest.json').write_text(json.dumps(runtime))
        manifest = {'schemaVersion': 1, 'component': 'stream-dog-ipc-candidate', 'channel': 'experimental-ci',
                    'sourceSha': 'a' * 40, 'buildId': build_id, 'physicalHardwareVerified': False,
                    'entrypoint': 'HellgatoIpcTest.exe', 'files': inventory(root)}
        (root / 'candidate-manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(verify_bundle(root)['buildId'], build_id)
        (worker / 'dependency.dll').write_bytes(b'replaced')
        with self.assertRaises(ValueError):
            verify_bundle(root)


class CandidateSupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ipc_candidate_supervisor', ROOT / 'apps/stream-dog/python/candidate.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_packaged_fake_selftest_has_no_physical_only_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / 'output'
            def completed(*args, **kwargs):
                value = {'schemaVersion': 1, 'engine': 'ipc-candidate', 'state': 'stopped',
                    'simulation': True, 'physicalHardwareVerified': False, 'generation': 2,
                    'checks': sorted(self.module.SELF_TEST_CHECKS)}
                (output / 'self-test-receipt.json').write_text(json.dumps(value))
                return 0
            with patch.object(self.module, 'supervised', side_effect=completed):
                self.module.self_test(root, {'sourceSha': 'a' * 40, 'buildId': 'windows-ipc-' + 'a' * 40}, output)
            config = read_json(output / 'config.json')
            self.assertEqual(config['deviceId'], 'fake:buttons-dial')
            self.assertEqual(config['runtime']['args'], ['--build-id', 'windows-ipc-' + 'a' * 40])
            self.assertNotIn('readiness', config)

    def test_zero_exit_without_selftest_receipt_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / 'output'
            with patch.object(self.module, 'supervised', return_value=0):
                with self.assertRaises(FileNotFoundError):
                    self.module.self_test(root, {'sourceSha': 'a' * 40, 'buildId': 'windows-ipc-' + 'a' * 40}, output)
            self.assertFalse((output / 'result.json').exists())

    def test_selftest_receipt_requires_all_checks_and_simulation(self):
        value = {'schemaVersion': 1, 'engine': 'ipc-candidate', 'state': 'stopped',
            'simulation': True, 'physicalHardwareVerified': False, 'generation': 2,
            'checks': sorted(self.module.SELF_TEST_CHECKS)}
        self.assertEqual(self.module.validate_self_test_receipt(value)['state'], 'stopped')
        for change in ({'checks': []}, {'state': 'ready'}, {'physicalHardwareVerified': True}, {'simulation': False}, {'generation': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.module.validate_self_test_receipt({**value, **change})

    def test_console_commands_are_bounded_and_have_no_arbitrary_operations(self):
        self.assertEqual(self.module.console_command('knob 50'), {'command': 'control', 'control': {'control': 'knobBrightness', 'value': 50}})
        self.assertEqual(self.module.console_command('follow off')['control']['value'], False)
        self.assertEqual(self.module.console_command(''), {'command': 'stop'})
        for value in ('knob 101', 'brightness -1', 'sleep-time 86401', 'firmware x', 'run shell', 'logo path'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.console_command(value)

    def test_sanitized_events_omit_serials_paths_messages_and_images(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'safe.jsonl'
            logger = self.module.SanitizedEvents(path)
            logger.line(json.dumps({'event': 'candidate-ready', 'state': 'connected', 'message': 'SECRET',
                'serial': 'SECRET', 'path': 'C:\\SECRET', 'image': 'SECRET', 'inputCount': 2}).encode(), 'node')
            logger.line(b'Error raw SECRET', 'node-stderr')
            logger.close()
            text = path.read_text()
            self.assertNotIn('SECRET', text)
            self.assertIn('candidate-ready', text)
            self.assertIn('"inputCount": 2', text)

    def test_diagnostic_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'safe.jsonl'
            logger = self.module.SanitizedEvents(path)
            for _ in range(20000):
                logger.line(b'{"event":"state","state":"ready"}', 'node')
            logger.close()
            self.assertLessEqual(path.stat().st_size, 1024 * 1024)

    def test_settings_controls_are_checked_and_readonly(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            path = state / 'device-controls-HGMOCK000042.json'
            path.write_text('{"version":1,"serial":"HGMOCK000042","knobBrightness":12}')
            self.assertEqual(self.module.initial_controls(state, 'HGMOCK000042'), {'followDisplayBrightness': False, 'knobBrightness': 12})
            path.write_text('{"version":1,"serial":"HGMOCK000042","knobBrightness":101}')
            with self.assertRaises(ValueError):
                self.module.initial_controls(state, 'HGMOCK000042')


class CandidateStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ipc_candidate_stage', ROOT / 'scripts/stage-ipc-candidate.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_immutable_candidate_requires_committed_clean_source(self):
        with patch.object(self.module.subprocess, 'check_output', side_effect=['a' * 40, '']):
            self.assertEqual(self.module.source_identity(ROOT), 'a' * 40)
        for changes in (' M apps/stream-dog/python/candidate.py\n', 'A  new-runtime.py\n', '?? new-policy.json\n'):
            with patch.object(self.module.subprocess, 'check_output', side_effect=['a' * 40, changes]):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'clean, committed'):
                    self.module.source_identity(ROOT)

    def test_sdk_stage_revalidates_inventory_before_candidate_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'runtime.mjs').write_text('exact packed runtime')
            value = {'schemaVersion': 1, 'component': 'stream-dog-sdk', 'engine': 'legacy-compatibility',
                     'releaseSha': 'a' * 40, 'files': inventory(root)}
            (root / 'product-build-manifest.json').write_text(json.dumps(value))
            self.assertEqual(self.module.verify_sdk_stage(root, 'a' * 40), value)
            (root / 'runtime.mjs').write_text('mutated after staging')
            with self.assertRaisesRegex(ValueError, 'inventory'):
                self.module.verify_sdk_stage(root, 'a' * 40)
            with self.assertRaisesRegex(ValueError, 'source'):
                self.module.verify_sdk_stage(root, 'b' * 40)

    def test_candidate_notices_reference_its_own_flat_license_files(self):
        notices = self.module.CANDIDATE_NOTICES
        references = {line.removeprefix('License: ') for line in notices.splitlines() if line.startswith('License: ')}
        self.assertEqual(references, {'licenses/' + name + '.txt' for name in
                                     ('Hellgato', 'Mirabox', 'DeckBridge', 'Node', 'Python', 'Pillow', 'pefile', 'six', 'PyInstaller')})
        self.assertNotIn('licenses/Tk/', notices)
        self.assertNotIn('licenses/pystray/', notices)
        self.assertNotIn('licenses/NODE-LICENSE.txt', notices)
        self.assertIn('bootloader distribution exception', notices)


if __name__ == '__main__':
    unittest.main()

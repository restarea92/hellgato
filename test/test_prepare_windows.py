"""Offline supply-chain checks: no SDK/Node download or hardware access."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare_windows', ROOT / 'scripts/prepare-windows.py')
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PinnedSdkPreparation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sdk = self.root / 'sdk'
        names = ['LICENSE', 'Python-SDK/src/StreamDock/DeviceManager.py',
                 'Python-SDK/src/StreamDock/Devices/StreamDockN4Pro.py',
                 'Python-SDK/src/StreamDock/Transport/TransportDLL/transport.dll']
        self.content = {name: ('fixture bytes: ' + name).encode() for name in names}
        self.inventory = {'schemaVersion': 1, 'repository': prepare.SDK_REPOSITORY,
                          'revision': prepare.SDK_REVISION, 'files': []}
        for name, data in self.content.items():
            self.inventory['files'].append({'path': name, 'size': len(data),
                'gitBlobSha1': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()})
        self.manifest = self.root / 'manifest.json'
        self.save()
        self.calls = []

    def save(self):
        self.manifest.write_text(json.dumps(self.inventory), encoding='utf-8')

    def fetch(self, url):
        self.calls.append(url)
        prefix = f'https://raw.githubusercontent.com/{prepare.SDK_REPOSITORY}/{prepare.SDK_REVISION}/'
        self.assertTrue(url.startswith(prefix))
        self.assertNotIn('api.github.com', url)
        return self.content[url[len(prefix):]]

    def run_prepare(self, **kwargs):
        return prepare.prepare_sdk(self.sdk, manifest_path=self.manifest, fetcher=self.fetch, **kwargs)

    def test_checked_in_inventory_is_complete_and_pinned(self):
        inventory = prepare.sdk_manifest()
        self.assertEqual(len(inventory['files']), 38)
        self.assertEqual(inventory['revision'], prepare.SDK_REVISION)

    def test_cold_download_is_verified_and_warm_cache_never_uses_network(self):
        result = self.run_prepare()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(result['sdkInventorySha256'], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.calls.clear()
        self.assertEqual(self.run_prepare(offline=True), result)
        self.assertEqual(self.run_prepare(), result)
        self.assertEqual(self.calls, [])
        self.assertEqual((self.sdk / 'revision.txt').read_text(), prepare.SDK_REVISION)

    def test_missing_cache_fetches_only_missing_exact_file(self):
        self.run_prepare()
        (self.sdk / self.inventory['files'][1]['path']).unlink()
        self.calls.clear()
        self.run_prepare()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0].endswith('/DeviceManager.py'))

    def test_local_corruption_is_preserved_and_not_repaired_or_downloaded(self):
        self.run_prepare()
        target = self.sdk / 'LICENSE'
        target.write_bytes(b'corrupt')
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'differs from pinned'):
            self.run_prepare()
        self.assertEqual(target.read_bytes(), b'corrupt')
        self.assertEqual(self.calls, [])

    def test_wrong_download_size_or_digest_never_enters_cache(self):
        for bad in [b'', b'x' * len(self.content['LICENSE'])]:
            with self.assertRaisesRegex(RuntimeError, 'differs from pinned'):
                prepare.prepare_sdk(self.sdk, manifest_path=self.manifest, fetcher=lambda _: bad)
            self.assertFalse((self.sdk / 'LICENSE').exists())
            self.assertFalse((self.sdk / 'revision.txt').exists())

    def test_offline_mode_fails_closed_on_missing_file_without_fetch(self):
        with self.assertRaisesRegex(RuntimeError, 'Missing pinned SDK'):
            self.run_prepare(offline=True)
        self.assertEqual(self.calls, [])

    def test_rejects_wrong_identity_or_incomplete_inventory(self):
        original = copy.deepcopy(self.inventory)
        for field, value in [('schemaVersion', 2), ('revision', 'a' * 40), ('repository', 'attacker/sdk'), ('files', [])]:
            self.inventory = {**original, field: value}; self.save()
            with self.assertRaises(RuntimeError):
                self.run_prepare()
        self.inventory = copy.deepcopy(original); self.inventory['files'].pop(); self.save()
        with self.assertRaisesRegex(RuntimeError, 'omits required'):
            self.run_prepare()
        self.assertEqual(self.calls, [])

    def test_rejects_duplicate_case_collisions_and_unsafe_paths(self):
        original = copy.deepcopy(self.inventory)
        for name in ['LICENSE', 'license', '../escape.py', '/absolute.py', 'Python-SDK/src/StreamDock/../escape.py', 'Python-SDK/src/StreamDock/evil.exe', 'Python-SDK\\evil.py']:
            self.inventory = copy.deepcopy(original)
            self.inventory['files'].append({**self.inventory['files'][0], 'path': name}); self.save()
            with self.assertRaisesRegex(RuntimeError, 'Unsafe or duplicate'):
                self.run_prepare()
        self.assertEqual(self.calls, [])

    def test_rejects_unexpected_cached_runtime_files(self):
        self.run_prepare()
        extra = self.sdk / 'Python-SDK/src/StreamDock/unreviewed.py'
        extra.write_text('print("unreviewed")')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected SDK cache'):
            self.run_prepare()
        self.assertTrue(extra.exists())

    def test_rejects_symlink_root_or_cached_file(self):
        outside = self.root / 'outside'; outside.mkdir()
        try:
            self.sdk.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows account cannot create symbolic links')
        with self.assertRaisesRegex(RuntimeError, 'real directory'):
            self.run_prepare()
        self.sdk.unlink(); self.run_prepare()
        target = self.sdk / 'LICENSE'; target.unlink()
        target.symlink_to(outside / 'foreign')
        with self.assertRaisesRegex(RuntimeError, 'link is forbidden'):
            self.run_prepare()
        self.assertFalse((outside / 'foreign').exists())

    def test_checks_and_packaging_prepare_the_pinned_runtime(self):
        checks = (ROOT / '.github/workflows/checks.yml').read_text()
        self.assertIn('scripts/prepare-windows.py', checks)
        self.assertIn('test_installer_migration.ps1', checks)
        self.assertIn('scripts/prepare-windows.py', (ROOT / 'scripts/build-windows.ps1').read_text())
        source = (ROOT / 'scripts/prepare-windows.py').read_text()
        self.assertNotIn('api.github.com', source)


if __name__ == '__main__':
    unittest.main()

"""Canonical product sources keep legacy launch/import and frozen layouts stable."""

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / 'apps/stream-dog/python'
CANONICAL = PRODUCT / 'hellgato_stream_dog'
ALIASES = {'hellgato': 'launcher', 'ui': 'ui', 'i18n': 'i18n',
           'settings': 'settings', 'device_registry': 'device_registry',
           'profile_transfer': 'profile_transfer'}


class ProductBoundaryTest(unittest.TestCase):
    def run_python(self, arguments, *, state=None, cwd=None):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(state or temporary)
            environment = {**os.environ, 'LOCALAPPDATA': str(state), 'APPDATA': str(state),
                           'HELLGATO_STATE_DIR': str(state / 'Hellgato')}
            # The child gets its paths from the entrypoint/alias, not a developer's PYTHONPATH.
            environment.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, *arguments], cwd=cwd or temporary,
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

    def test_legacy_and_package_imports_share_one_patchable_module(self):
        self.run_python(['-c', '''
import importlib
from pathlib import Path
import sys
from unittest.mock import patch
root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / 'app'), str(root / 'app/n4')]
aliases = {'hellgato': 'launcher', 'ui': 'ui', 'i18n': 'i18n',
           'settings': 'settings', 'device_registry': 'device_registry',
           'profile_transfer': 'profile_transfer'}
for old, new in aliases.items():
    legacy = importlib.import_module(old)
    canonical = importlib.import_module('hellgato_stream_dog.' + new)
    prefix = 'app.' if old in ('hellgato', 'ui', 'i18n') else 'app.n4.'
    qualified = importlib.import_module(prefix + old)
    assert legacy is canonical is qualified, old
    assert Path(canonical.__file__).parent == root / 'apps/stream-dog/python/hellgato_stream_dog'
with patch('hellgato.STATE', 'patched'):
    assert importlib.import_module('hellgato_stream_dog.launcher').STATE == 'patched'
with patch('device_registry._timestamp', return_value='patched'):
    assert importlib.import_module('hellgato_stream_dog.device_registry')._timestamp() == 'patched'
with patch('settings.load_settings', return_value='patched'):
    assert importlib.import_module('hellgato_stream_dog.settings').load_settings() == 'patched'
''', str(ROOT)])

    def test_canonical_first_import_keeps_same_legacy_object(self):
        self.run_python(['-c', '''
import importlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'apps/stream-dog/python'))
canonical = importlib.import_module('hellgato_stream_dog.launcher')
sys.path.insert(0, str(root / 'app'))
assert importlib.import_module('hellgato') is canonical
assert canonical.ROOT == root
assert canonical.Message is importlib.import_module('i18n').Message
''', str(ROOT)])

    def test_both_entrypoints_read_same_version_away_from_checkout(self):
        expected = json.loads((ROOT / 'app/release.json').read_text(encoding='utf-8'))['version']
        for entry in ('app/hellgato.py', 'apps/stream-dog/python/stream-dog.py'):
            with self.subTest(entry=entry):
                result = self.run_python([str(ROOT / entry), '--version'])
                self.assertEqual(result.stdout.strip(), expected)

    def test_runtime_metadata_accepts_only_exact_stable_or_beta_windows_versions(self):
        self.run_python(['-c', '''
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'apps/stream-dog/python'))
from hellgato_stream_dog.launcher import read_release_metadata
fixture = Path.cwd() / 'metadata'
(fixture / 'app').mkdir(parents=True)
def check(value, accepted):
    (fixture / 'app/release.json').write_text(json.dumps(value), encoding='utf-8')
    try:
        actual = read_release_metadata(fixture)
    except ValueError:
        assert not accepted, value
    else:
        assert accepted and actual == value, value
for version, channel in [('0.1.0', 'stable'), ('65535.65535.65535', 'stable'),
                         ('0.2.0-beta.0', 'beta'), ('1.2.3-beta.65533', 'beta')]:
    check({'version': version, 'channel': channel}, True)
for version, channel in [('0.2.0-beta.0', 'stable'), ('0.2.0', 'beta'),
                         ('01.2.3', 'stable'), ('1.02.3', 'stable'),
                         ('1.2.03', 'stable'), ('1.2.3-beta.01', 'beta'),
                         ('1.2.3-alpha.0', 'beta'), ('1.2.3-beta.0+build', 'beta'),
                         ('65536.1.0', 'stable'), ('1.2.3-beta.65534', 'beta'),
                         ('9' * 5000 + '.1.0', 'stable'), ('1.2.3', 'public')]:
    check({'version': version, 'channel': channel}, False)
for value in [{}, {'version': '1.2.3', 'channel': 'stable', 'approved': True},
              {'version': True, 'channel': 'stable'}, [], None]:
    check(value, False)
''', str(ROOT)])

    def test_both_entrypoints_preserve_stop_dispatch_and_state_directory(self):
        for entry in ('app/hellgato.py', 'apps/stream-dog/python/stream-dog.py'):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as temporary:
                state = Path(temporary)
                self.run_python([str(ROOT / entry), '--stop'], state=state)
                self.assertEqual(list((state / 'Hellgato').iterdir()), [state / 'Hellgato/launcher.stop'])

    def test_both_entrypoints_preserve_legacy_worker_dispatch(self):
        for entry in ('app/hellgato.py', 'apps/stream-dog/python/stream-dog.py'):
            with self.subTest(entry=entry):
                result = self.run_python([str(ROOT / entry), '--run-script',
                                          str(ROOT / 'app/n4/bridge.py'), '--help'])
                self.assertIn('usage:', result.stdout)
                self.assertIn('--init', result.stdout)

    def test_frozen_aliases_resolve_only_from_bundle_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'bundle'
            for relative in ('app', 'apps/stream-dog/python', 'host-integrations/streamdeck/windows/python'):
                shutil.copytree(ROOT / relative, root / relative,
                                ignore=shutil.ignore_patterns('__pycache__'))
            self.run_python(['-c', '''
import importlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys._MEIPASS = str(root)
sys.frozen = True
sys.path[:0] = [str(root / 'app'), str(root / 'app/n4')]
for name in ('hellgato', 'i18n', 'ui', 'settings', 'device_registry', 'profile_transfer'):
    module = importlib.import_module(name)
    assert Path(module.__file__).is_relative_to(root), name
import hellgato
assert hellgato.ROOT == root
assert hellgato.command('app/n4/bridge.py', '--init') == [
    str(Path(sys.executable).with_name('HellgatoWorker.exe')),
    '--run-script', str(root / 'app/n4/bridge.py'), '--init']
import i18n
assert i18n.ROOT == root
assert i18n.Translator(sys.argv[2], 'en').text('app.subtitle')
''', str(root), str(Path(temporary) / 'state')])

    def test_frozen_root_does_not_evaluate_source_parent_depth(self):
        namespace = {'__file__': str(Path(Path.cwd().anchor) / 'paths.py')}
        with patch.object(sys, '_MEIPASS', str(ROOT), create=True):
            exec(compile((CANONICAL / 'paths.py').read_text(encoding='utf-8'), '<frozen paths>', 'exec'), namespace)
        self.assertEqual(namespace['ROOT'], ROOT)

    def test_legacy_device_engine_imports_canonical_product_state(self):
        self.run_python(['-c', '''
import importlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'app/n4'))
import bridge
from hellgato_stream_dog.settings import load_settings
from hellgato_stream_dog.device_registry import DeviceRegistry
assert bridge.load_settings is load_settings
assert bridge.DeviceRegistry is DeviceRegistry
assert bridge.root == root
''', str(ROOT)])

    def test_installer_collects_canonical_product_and_legacy_alias_helper(self):
        spec = (ROOT / 'installer/hellgato.spec').read_text(encoding='utf-8')
        self.assertIn("Analysis([str(root / 'apps/stream-dog/python/stream-dog.py')]", spec)
        self.assertIn("include_tree('apps/stream-dog/python/hellgato_stream_dog', {'.py'})", spec)
        self.assertIn("(str(root / 'app/_product_compat.py'), 'app')", spec)
        self.assertIn("include_tree('app/n4', {'.py'})", spec)
        self.assertIn("str(root / 'host-integrations/streamdeck/windows/python')", spec)

    def test_frozen_worker_inventories_policy_and_requires_pinned_windows_vendor(self):
        spec = (ROOT / 'installer/mirabox-worker.spec').read_text(encoding='utf-8')
        self.assertIn("'hellgato_mirabox/models/n4_pro/firmware-policy.json'", spec)
        self.assertIn("if sys.platform == 'win32':", spec)
        self.assertIn("prepare['prepare_sdk'](root / 'work/references/mirabox', offline=True)", spec)
        stage = (ROOT / 'scripts/stage-ipc-candidate.py').read_text(encoding='utf-8')
        self.assertIn("digest(worker / '_internal' / firmware_policy)", stage)

    def test_wrapper_sources_do_not_duplicate_product_implementation(self):
        for relative in ('app/hellgato.py', 'app/ui.py', 'app/i18n.py',
                         'app/n4/settings.py', 'app/n4/device_registry.py', 'app/n4/profile_transfer.py'):
            with self.subTest(path=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
                self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in ast.walk(tree)))
        for name in ALIASES.values():
            tree = ast.parse((CANONICAL / f'{name}.py').read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ALIASES:
                    self.assertEqual(node.level, 1, f'{name}: product import must use its own package')
        self.assertTrue((ROOT / 'app/n4/bridge.py').is_file(), 'Compatibility runtime remains until hardware approval')

    def test_source_boundaries_read_utf8_with_windows_locale_defaults(self):
        read_text = Path.read_text
        for default_encoding in ('cp949', 'cp1252'):
            def locale_read(path, encoding=None, errors=None, newline=None):
                return read_text(path, encoding=encoding or default_encoding, errors=errors, newline=newline)
            with self.subTest(encoding=default_encoding), patch.object(Path, 'read_text', locale_read):
                self.test_wrapper_sources_do_not_duplicate_product_implementation()


class PythonLockTest(unittest.TestCase):
    def setUp(self):
        self.project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))

    def test_environment_is_private_python_313_and_has_no_release_owner(self):
        self.assertEqual(self.project['project']['requires-python'], '>=3.13,<3.14')
        self.assertEqual(self.lock['requires-python'], '==3.13.*')
        self.assertFalse(self.project['tool']['uv']['package'])
        self.assertIn('Private :: Do Not Upload', self.project['project']['classifiers'])
        self.assertEqual(self.project['tool']['uv']['workspace']['members'], ['adapters/mirabox-python'])
        self.assertEqual(self.project['tool']['uv']['required-version'], '==0.12.23')

    def test_existing_runtime_and_build_pins_are_unchanged(self):
        expected = {'PyInstaller==6.16.0', 'Pillow==12.0.0', 'pefile==2023.2.7',
                    'pystray==0.19.5', 'six==1.17.0', 'unicorn==2.1.4'}
        self.assertEqual(set(self.project['dependency-groups']['build']), expected)
        packages = {package['name']: package for package in self.lock['package']}
        for requirement in expected:
            name, version = requirement.lower().split('==')
            self.assertEqual(packages[name]['version'], version)

    def test_all_registry_artifacts_are_content_pinned_and_export_is_derived(self):
        requirements = (ROOT / 'installer/requirements-build.txt').read_text(encoding='utf-8')
        self.assertIn('autogenerated by uv', requirements)
        self.assertIn('uv export --locked --only-group build --no-emit-workspace', requirements)
        self.assertNotIn('-e ', requirements)
        self.assertNotIn('hellgato-', '\n'.join(requirements.splitlines()[2:]))
        # Each exported pin/hash comes from the canonical universal lock, including platform markers.
        locked = {p['name']: p for p in self.lock['package'] if 'registry' in p['source']}
        for package in locked.values():
            self.assertEqual(package['source']['registry'], 'https://pypi.org/simple')
            for artifact in ([package['sdist']] if 'sdist' in package else []) + package.get('wheels', []):
                self.assertRegex(artifact['hash'], r'^sha256:[0-9a-f]{64}$')
        for block in requirements.replace('\\\n', '').splitlines()[2:]:
            if not block.strip():
                continue
            pin, *hashes = block.split(' --hash=')
            name, version = pin.split(';')[0].strip().split('==')
            self.assertEqual(locked[name]['version'], version)
            package = locked[name]
            allowed = {a['hash'] for a in ([package['sdist']] if 'sdist' in package else []) + package.get('wheels', [])}
            self.assertTrue(hashes, name)
            self.assertTrue({item.strip() for item in hashes} <= allowed, name)


if __name__ == '__main__':
    unittest.main()

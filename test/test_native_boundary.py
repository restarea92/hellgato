"""Native build relocation preserves canonical source and compatibility commands."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / 'host-integrations/streamdeck/windows/native'


def load(path):
    spec = importlib.util.spec_from_file_location('native_boundary_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeBoundaryTests(unittest.TestCase):
    def test_build_root_and_sources_are_canonical(self):
        startup = load(CANONICAL / 'startup/build.py')
        ui = load(CANONICAL / 'ui/build.py')
        prepare = load(CANONICAL / 'ui/prepare.py')
        self.assertEqual(startup.ROOT, ROOT)
        self.assertEqual(ui.ROOT, ROOT)
        self.assertEqual(prepare.ROOT, ROOT)
        self.assertEqual(startup.SOURCE, CANONICAL / 'startup')
        for relative in ('startup/startup.cpp', 'startup/fixture.cpp', 'startup/fixture.asm',
                         'startup/version.def', 'ui/native_ui.cpp', 'ui/test_host.cpp'):
            self.assertTrue((CANONICAL / relative).is_file())
        self.assertFalse((ROOT / 'native/hellgato-startup/startup.cpp').exists())
        self.assertFalse((ROOT / 'native/hellgato-ui/native_ui.cpp').exists())

    def test_legacy_python_entry_uses_same_code_and_defaults(self):
        for old, new in (('hellgato-startup/build.py', 'startup/build.py'),
                         ('hellgato-ui/build.py', 'ui/build.py'), ('hellgato-ui/prepare.py', 'ui/prepare.py')):
            legacy = load(ROOT / 'native' / old)
            canonical = load(CANONICAL / new)
            self.assertEqual(legacy.ROOT, canonical.ROOT)
            self.assertEqual(legacy.main.__code__.co_filename, str(CANONICAL / new))

    def test_windows_pipeline_builds_canonical_fixtures(self):
        build = (ROOT / 'scripts/build-windows.ps1').read_text()
        self.assertIn('host-integrations/streamdeck/windows/native/startup/build.py --test', build)
        self.assertIn('host-integrations/streamdeck/windows/native/ui/build.py --test-host', build)
        self.assertNotIn('native/hellgato-ui/build.py', build)
        spec = (ROOT / 'installer/hellgato.spec').read_text()
        self.assertNotIn('fixture.cpp', spec)
        self.assertNotIn('build-native-ui-test', spec)


if __name__ == '__main__':
    unittest.main()

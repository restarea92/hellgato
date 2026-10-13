from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
import startup_install as setup


class StartupInstallTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.exe = self.root / 'StreamDeck.exe'
        self.exe.write_bytes(b'host unchanged')
        self.source = self.root / 'source.dll'
        self.source.write_bytes(b'owned dll')

    def install(self):
        with patch.object(setup, 'validate_dll'), patch.object(setup, 'build_plan', return_value=(
                b'bound plan', {'sha256': setup.digest(self.exe.read_bytes())})):
            return setup.install(self.exe, self.source)

    def test_foreign_dll_and_modified_owned_file_are_preserved(self):
        target = self.root / setup.DLL
        target.write_bytes(b'other application')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(target.read_bytes(), b'other application')
        target.unlink()
        self.install()
        target.write_bytes(b'modified later')
        with self.assertRaises(ValueError):
            setup.remove(self.exe)
        self.assertEqual(target.read_bytes(), b'modified later')

    def test_owned_install_refresh_and_remove_leave_host_and_unrelated_files(self):
        self.install()
        self.assertTrue(setup.installed(self.exe, self.source))
        self.exe.write_bytes(b'host updated by Elgato')
        self.assertFalse(setup.installed(self.exe, self.source))
        self.install()
        self.assertTrue(setup.installed(self.exe, self.source))
        extra = self.root / 'unrelated.txt'
        extra.write_text('keep')
        self.assertTrue(setup.remove(self.exe))
        self.assertFalse(setup.remove(self.exe))
        self.assertEqual(self.exe.read_bytes(), b'host updated by Elgato')
        self.assertEqual(extra.read_text(), 'keep')

    def test_second_file_failure_rolls_back_first_write(self):
        self.install()
        old = {name: (self.root / name).read_bytes() for name in (setup.DLL, setup.PLAN, setup.MANIFEST)}
        actual_replace = setup.os.replace
        def replace(source, target):
            if Path(target).name == setup.PLAN:
                raise PermissionError('locked')
            return actual_replace(source, target)
        with patch.object(setup.os, 'replace', side_effect=replace), self.assertRaises(PermissionError):
            setup.replace_files(self.root, {setup.DLL: b'new dll', setup.PLAN: b'new plan'})
        self.assertEqual(old, {name: (self.root / name).read_bytes() for name in old})
        self.assertEqual(list(self.root.glob('.hellgato-*')), [])

    def test_locked_dll_removal_retains_plan_and_ownership(self):
        self.install()
        actual_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.name == 'HellgatoStartup.removing.dll':
                raise PermissionError('loaded')
            return actual_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink), self.assertRaises(PermissionError):
            setup.remove(self.exe)
        self.assertTrue(setup.installed(self.exe, self.source))


if __name__ == '__main__':
    unittest.main()

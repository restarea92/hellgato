"""Exercise the launcher's transfer callback without Windows or a display server."""

import ast
import json
from pathlib import Path
import queue
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'app/n4'))
from i18n import LANGUAGES, Message, Translator
from profile_transfer import ProfileError, device_uuid, export_profiles


def launcher_transfer(namespace):
    # Compile the actual nested callback with its closure dependencies injected.
    # This avoids executing main's Windows mutex, tray, and device setup.
    source = ast.parse((ROOT / 'app/hellgato.py').read_text(encoding='utf-8'))
    main = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    transfer = next(node for node in main.body if isinstance(node, ast.FunctionDef) and node.name == 'transfer')
    wrapper = ast.parse('''
def make_transfer():
    transfer_thread = None
    transfer_busy = False
    def state():
        return transfer_busy, transfer_thread
    return transfer, state
''')
    wrapper.body[0].body.insert(2, transfer)
    exec(compile(ast.fix_missing_locations(wrapper), str(ROOT / 'app/hellgato.py'), 'exec'), namespace)
    return namespace['make_transfer']()


class ProfileExportConfirmationTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'profiles' / '11111111-1111-1111-1111-111111111111.sdProfile'
        self.source.mkdir(parents=True)
        manifest = {'Version': '3.0', 'AppIdentifier': 'C:/Apps/editor.exe',
                    'Device': {'Model': '20GBD9901', 'UUID': device_uuid(7)},
                    'Actions': {'Settings': {'path': 'C:/Users/example/private', 'token': 'test-secret'}}}
        (self.source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (self.source / 'plugin.json').write_bytes(b'{"credentials":"test-secret","path":"C:/private"}\n')
        self.original = {path: path.read_bytes() for path in self.source.iterdir()}
        self.destination = self.root / 'export.hellgatoProfiles'
        self.dialogs = Mock()
        self.dialogs.asksaveasfilename.return_value = str(self.destination)
        self.messages = Mock(WARNING='warning', CANCEL='cancel')
        self.messages.askokcancel.return_value = False
        self.lifecycle = SimpleNamespace(closing=False)
        self.session = Mock(running=False, thread=None)
        self.session.stop.is_set.return_value = False
        self.view, self.refresh = Mock(), Mock()
        self.export = Mock(wraps=export_profiles)
        self.threads = Mock()
        self.threads.Thread.side_effect = lambda *, target, daemon: Mock(start=target)
        self.callbacks = queue.Queue()
        self.namespace = dict(
            filedialog=self.dialogs, messagebox=self.messages, lifecycle=self.lifecycle,
            session=self.session, view=self.view, refresh=self.refresh,
            window=Mock(), translator=Translator(self.root, 'en'), threading=self.threads,
            load_settings=Mock(return_value={'identity': 7}), export_profiles=self.export,
            profiles_root=self.source.parent, STATE=self.root, ROOT=ROOT, Path=Path,
            ProfileError=ProfileError, Message=Message, callbacks=self.callbacks,
            finish_transfer=Mock(), logging=Mock(), read_profiles=Mock(return_value={}),
            install_profiles=Mock(return_value=(1, None)), command=Mock(),
            subprocess=Mock(CREATE_NO_WINDOW=0))
        self.namespace['subprocess'].run.return_value.returncode = 0
        self.transfer, self.state = launcher_transfer(self.namespace)

    def assert_no_transfer(self):
        self.export.assert_not_called()
        self.threads.Thread.assert_not_called()
        self.view.render_transfer.assert_not_called()
        self.refresh.assert_not_called()
        self.session.request_stop.assert_not_called()
        self.assertEqual(self.state(), (False, None))
        self.assertTrue(self.callbacks.empty())
        for path, content in self.original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_cancel_creates_no_bundle_or_temporary_file(self):
        self.session.running = True
        before = set(self.root.rglob('*'))
        self.transfer(False)
        self.messages.askokcancel.assert_called_once()
        self.assert_no_transfer()
        self.assertEqual(set(self.root.rglob('*')), before)
        self.assertFalse(self.destination.exists())

    def test_cancel_leaves_existing_destination_unchanged(self):
        self.destination.write_bytes(b'existing bundle')
        self.transfer(False)
        self.assert_no_transfer()
        self.assertEqual(self.destination.read_bytes(), b'existing bundle')

    def test_save_dialog_cancel_skips_confirmation(self):
        self.dialogs.asksaveasfilename.return_value = ''
        self.transfer(False)
        self.messages.askokcancel.assert_not_called()
        self.assert_no_transfer()

    def test_confirmation_precedes_export_and_keeps_sensitive_settings(self):
        def confirm(*args, **kwargs):
            self.assert_no_transfer()
            self.assertFalse(self.destination.exists())
            return True
        self.messages.askokcancel.side_effect = confirm
        self.transfer(False)
        self.export.assert_called_once_with(str(self.destination), self.source.parent, 7)
        self.threads.Thread.assert_called_once()
        with zipfile.ZipFile(self.destination) as bundle:
            prefix = 'Profiles/' + self.source.name + '/'
            exported = json.loads(bundle.read(prefix + 'manifest.json'))
            expected = json.loads(self.original[self.source / 'manifest.json'])
            expected['AppIdentifier'] = '*'
            self.assertEqual(exported, expected)
            self.assertEqual(bundle.read(prefix + 'plugin.json'), self.original[self.source / 'plugin.json'])
        for path, content in self.original.items():
            self.assertEqual(path.read_bytes(), content)
        self.callbacks.get_nowait()()
        self.namespace['finish_transfer'].assert_called_once_with(
            Message('transfer.exported', {'count': 1, 'filename': self.destination.name}), False, False)

    def test_closing_during_confirmation_aborts(self):
        def confirm(*args, **kwargs):
            self.lifecycle.closing = True
            return True
        self.messages.askokcancel.side_effect = confirm
        self.transfer(False)
        self.assert_no_transfer()
        self.assertFalse(self.destination.exists())

    def test_import_does_not_show_export_warning(self):
        self.dialogs.askopenfilenames.return_value = ('import.streamDeckProfile',)
        self.transfer(True)
        self.messages.askokcancel.assert_not_called()
        self.export.assert_not_called()
        self.namespace['read_profiles'].assert_called_once_with(('import.streamDeckProfile',))
        self.namespace['install_profiles'].assert_called_once()

    def test_warning_uses_selected_language_parent_and_cancel_default(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                translator = self.namespace['translator']
                translator.select(language)
                self.messages.askokcancel.reset_mock()
                self.transfer(False)
                self.messages.askokcancel.assert_called_once_with(
                    translator.text('dialog.export_confirm_title'),
                    translator.text('dialog.export_confirm_message'),
                    parent=self.namespace['window'], icon='warning', default='cancel')
                self.assert_no_transfer()


if __name__ == '__main__':
    unittest.main()

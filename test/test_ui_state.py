from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from i18n import Message, ROOT, Translator
from ui import LauncherView, connection_presentation


class ConnectionControlTest(unittest.TestCase):
    def test_only_relevant_connection_action_is_available(self):
        for phase, running, expected in [('idle', False, 'start'), ('connected', True, 'stop'),
                                          ('waiting', True, 'cancel'), ('checking', True, 'cancel'),
                                          ('pairing', True, 'cancel'), ('error', False, 'retry')]:
            with self.subTest(phase=phase):
                state = connection_presentation(phase, running)
                self.assertEqual(state.action, f'connection.{expected}')
                self.assertTrue(state.enabled)
        self.assertFalse(connection_presentation('connected', True, stopping=True).enabled)
        self.assertFalse(connection_presentation('idle', False, busy=True).enabled)
        self.assertFalse(connection_presentation('idle', False, closing=True).enabled)


class ProfilePresentationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.window = tk.Tk()
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.translator = Translator(self.temporary.name, 'en')
        self.export = Mock()
        self.import_profiles = Mock()
        def change_language(language):
            self.translator.select(language, persist=True)
            self.view.retranslate()
        self.view = LauncherView(self.window, ROOT, '0.0.1-beta', 'HGMOCK000042',
                                 Mock(), self.export, self.import_profiles, Mock(), self.translator,
                                 change_language, Mock(), Mock())

    def test_main_window_only_shows_transfer_notice_after_a_task(self):
        self.assertEqual(self.view.notice.winfo_manager(), '')
        self.view.render_transfer(Message('transfer.exporting'), busy=True)
        self.assertEqual(self.view.notice.winfo_manager(), 'pack')
        self.view.dismiss_transfer()
        self.assertEqual(self.view.notice.winfo_manager(), 'pack')
        self.view.render_transfer(Message('transfer.exported', {'count': 1, 'filename': 'test.hellgatoProfiles'}))
        self.view.dismiss_transfer()
        self.view.retranslate()
        self.assertEqual(self.view.notice.winfo_manager(), '')

    def test_export_completion_clears_progress_and_preserves_connection(self):
        self.view.render_transfer(Message('transfer.exporting'), busy=True)
        self.view.render_connection('connected', Message('detail.connected'), True, busy=True)
        self.assertEqual(self.view.connection_button.cget('state'), 'disabled')
        self.view.invoke_command('ui.export')
        self.export.assert_not_called()
        self.assertEqual(self.view.progress.winfo_manager(), 'place')
        self.view.render_transfer(Message('transfer.exported', {'count': 2, 'filename': 'backup.hellgatoProfiles'}))
        self.view.render_connection('connected', Message('detail.connected'), True)
        self.assertEqual(self.view.progress.winfo_manager(), '')
        self.assertEqual(self.view.connection_title.cget('text'), 'Connected')
        self.assertEqual(self.view.connection_button.cget('text'), 'Disconnect')
        self.view.invoke_command('ui.export')
        self.export.assert_called_once()
        self.assertIn('Export complete', self.view.transfer_status.cget('text'))
        self.translator.select('ko')
        self.view.retranslate()
        self.view.render_connection('connected', Message('detail.connected'), True)
        self.assertNotIn('Export complete', self.view.transfer_status.cget('text'))
        self.assertIn('backup.hellgatoProfiles', self.view.transfer_status.cget('text'))

    def test_transfer_failure_clears_progress_and_keeps_retry_available(self):
        self.view.render_transfer(Message('transfer.importing'), busy=True)
        self.view.render_transfer(Message('transfer.failed', {'reason': Message('profile.format')}), failed=True)
        self.view.render_connection('idle', Message('detail.stopped'), False)
        self.assertEqual(self.view.progress.winfo_manager(), '')
        self.assertIn('not supported', self.view.transfer_status.cget('text'))
        self.view.invoke_command('ui.import')
        self.import_profiles.assert_called_once()
        self.assertEqual(self.view.connection_button.cget('text'), 'Connect')

    def test_standard_menu_preserves_disabled_actions_and_closing(self):
        self.window.deiconify()
        self.window.update()
        self.view.render_connection('connected', Message('detail.connected'), True, busy=True)
        menu, index = self.view._menu_items['ui.export']
        self.assertIsInstance(menu, tk.Menu)
        self.assertEqual(menu.entrycget(index, 'state'), 'disabled')
        menu.invoke(index)
        self.export.assert_not_called()
        self.view.close_menu()
        self.assertIsNone(self.window.grab_current())

    def test_menu_uses_current_anchor_after_window_moves(self):
        self.window.geometry('+100+100')
        self.window.deiconify()
        self.window.update()
        menu = self.view._native_menus['menu.file']
        with patch.object(menu, 'post') as post:
            self.view.open_menu('menu.file')
            first = post.call_args.args
            self.window.geometry('+250+200')
            self.window.update()
            self.assertIsNone(self.view._popup)
            self.view.open_menu('menu.file')
            self.assertEqual(post.call_args.args, (first[0] + 150, first[1] + 100))
            self.view.close_menu()
            self.window.geometry('+-200+100')
            self.window.update()
            self.view.open_menu('menu.file')
            self.assertLess(post.call_args.args[0], 0)

    def test_preferences_translate_immediately_and_close_releases_grab(self):
        self.window.deiconify()
        self.window.update()
        self.view.open_preferences()
        self.window.update()
        self.view.language_choice.current(1)
        self.view.language_choice.event_generate('<<ComboboxSelected>>')
        self.window.update()
        self.assertEqual(Translator(self.temporary.name).language, 'ko')
        self.assertEqual(self.view._dialog.title(), self.translator.text('preferences.title'))
        self.assertEqual(self.view.menu_buttons['menu.file'].cget('text'), self.translator.text('menu.file'))
        self.view._dialog.event_generate('<Escape>')
        self.window.update()
        self.assertIsNone(self.window.grab_current())
        self.assertIsNone(self.view._dialog)

    def test_native_menu_selection_runs_once_and_hiding_releases_dialog(self):
        self.window.deiconify()
        self.window.update()
        menu, index = self.view._menu_items['ui.import']
        menu.invoke(index)
        self.window.update()
        self.import_profiles.assert_called_once()
        self.export.assert_not_called()
        self.assertIsNone(self.window.grab_current())
        self.view.open_preferences()
        self.window.update()
        self.window.withdraw()
        self.window.update()
        self.assertIsNone(self.window.grab_current())
        self.assertIsNone(self.view._dialog)


if __name__ == '__main__':
    unittest.main()

import json
from pathlib import Path
from string import Formatter
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from i18n import LANGUAGES, Message, ROOT, Translator


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate translation key: {key}')
        result[key] = value
    return result


def placeholders(text):
    return {field for _, field, _, _ in Formatter().parse(text) if field is not None}


class TranslationTest(unittest.TestCase):
    def test_catalogs_match_source_and_preserve_placeholders(self):
        directory = ROOT / 'app/locales'
        catalogs = {path.stem: json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
                    for path in directory.glob('*.json')}
        self.assertEqual(set(catalogs), set(LANGUAGES))
        source = catalogs['en']
        for language, catalog in catalogs.items():
            with self.subTest(language=language):
                self.assertEqual(set(catalog), set(source))
                for key, value in catalog.items():
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip(), key)
                    self.assertEqual(placeholders(value), placeholders(source[key]), key)
                    value.format(count=2, filename='Profiles.hellgatoProfiles', reason='Details')

    def test_language_persists_without_changing_device_identity(self):
        with TemporaryDirectory() as temporary:
            state = Path(temporary)
            identity = state / 'settings.json'
            identity.write_text('{"identity": 42}', encoding='utf-8')
            translator = Translator(state, 'en')
            translator.select('ja', persist=True)
            restored = Translator(state)
            self.assertEqual(restored.language, 'ja')
            self.assertEqual(identity.read_text(encoding='utf-8'), '{"identity": 42}')

    def test_missing_empty_and_invalid_translation_fall_back_to_english(self):
        with TemporaryDirectory() as temporary:
            translator = Translator(temporary, 'ko')
            for value in (None, '', '{wrong}', 42):
                translator.strings['transfer.exported'] = value
                self.assertIn('Profiles: 2', translator.text(Message('transfer.exported',
                    {'count': 2, 'filename': 'test.hellgatoProfiles'})))
            del translator.strings['connection.start']
            self.assertEqual(translator.text('connection.start'), 'Connect')

    def test_in_flight_messages_follow_language_switch(self):
        with TemporaryDirectory() as temporary:
            translator = Translator(temporary, 'en')
            message = Message('transfer.failed', {'reason': Message('profile.no_profiles')})
            english = translator.text(message)
            translator.select('ko')
            self.assertNotEqual(translator.text(message), english)
            self.assertIn(translator.text('profile.no_profiles'), translator.text(message))


if __name__ == '__main__':
    unittest.main()

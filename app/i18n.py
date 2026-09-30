"""JSON-backed interface translations and per-user language preference."""

from dataclasses import dataclass, field
import ctypes
import json
import locale
import os
from pathlib import Path
import sys


LANGUAGES = {'en': 'English', 'ko': '한국어', 'es': 'Español', 'ja': '日本語',
             'fr': 'Français', 'de': 'Deutsch', 'ru': 'Русский', 'it': 'Italiano', 'pt-BR': 'Português (Brasil)',
             'zh-Hans': '简体中文', 'zh-Hant': '繁體中文'}
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


@dataclass(frozen=True)
class Message:
    key: str
    values: dict = field(default_factory=dict)


def system_language():
    try:
        if sys.platform == 'win32':
            code = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), 'en')
        else:
            code = locale.getlocale()[0] or 'en'
    except (AttributeError, OSError, ValueError):
        code = 'en'
    normalized = code.replace('-', '_').lower()
    if normalized.startswith('zh'):
        return 'zh-Hant' if any(region in normalized for region in ('tw', 'hk', 'mo', 'hant')) else 'zh-Hans'
    language = normalized.split('_')[0]
    if language == 'pt':
        return 'pt-BR'
    return language if language in LANGUAGES else 'en'


class Translator:
    def __init__(self, state, language=None, directory=None):
        self.path = Path(state) / 'preferences.json'
        self.directory = directory or ROOT / 'app/locales'
        self.english = self._read('en')
        if language is None:
            try:
                preferences = json.loads(self.path.read_text(encoding='utf-8'))
                language = preferences.get('language') if isinstance(preferences, dict) else None
            except (OSError, ValueError):
                pass
        self.select(language if language in LANGUAGES else system_language())

    def _read(self, language):
        return json.loads((self.directory / f'{language}.json').read_text(encoding='utf-8'))

    def select(self, language, persist=False):
        if language not in LANGUAGES:
            raise ValueError(f'Unsupported language: {language}')
        strings = self._read(language)
        if persist:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'language': language}, indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, self.path)
        self.language, self.strings = language, strings

    def text(self, message, **values):
        if isinstance(message, Message):
            key, values = message.key, message.values
        else:
            key = message
        values = {name: self.text(value) if isinstance(value, Message) else value for name, value in values.items()}
        fallback = self.english[key]
        translated = self.strings.get(key) or fallback
        if not isinstance(translated, str):
            translated = fallback
        try:
            return translated.format(**values)
        except (KeyError, ValueError, IndexError):
            return fallback.format(**values)

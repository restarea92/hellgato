import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from settings import load_settings


class SettingsTest(unittest.TestCase):
    def test_reuses_existing_profile_and_retains_identity_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / 'Elgato/StreamDeck/ProfilesV3/example/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'Device': {'UUID': 'network[/HGMOCK000007]'}}))
            settings = root / 'state/settings.json'
            with patch.dict(os.environ, {'APPDATA': folder}):
                first = load_settings(settings)
                self.assertEqual(first['identity'], 7)
                manifest.unlink()
                self.assertEqual(load_settings(settings), first)
                with self.assertRaises(ValueError):
                    load_settings(settings, identity=8)
                self.assertEqual(load_settings(settings), first)

    def test_invalid_saved_identity_is_preserved_for_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            original = '{"version": 1, "identity": 0, "dockIdentity": 1, "stripMode": "frame"}'
            path.write_text(original)
            with self.assertRaises(ValueError):
                load_settings(path)
            self.assertEqual(path.read_text(), original)


if __name__ == '__main__':
    unittest.main()

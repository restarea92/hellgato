import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from profile_transfer import device_uuid, export_profiles, read_profiles, install_profiles


class ProfileTransferTest(unittest.TestCase):
    def test_roundtrip_keeps_images_and_retargets_without_changing_other_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = '11111111-1111-1111-1111-111111111111.sdProfile'
            source = root / 'source' / name
            source.mkdir(parents=True)
            manifest = {'Version': '3.0', 'Name': 'Test', 'AppIdentifier': 'C:/Old/Photoshop.exe',
                        'Device': {'Model': '20GBD9901', 'UUID': device_uuid(7)}, 'Pages': {}}
            (source / 'manifest.json').write_text(json.dumps(manifest))
            (source / 'image.png').write_bytes(b'image-bytes')
            archive = root / 'profiles.hellgatoProfiles'
            self.assertEqual(export_profiles(archive, source.parent, 7), 1)
            destination = root / 'destination'
            (destination / name).mkdir(parents=True)
            (destination / name / 'old.txt').write_text('previous')
            (destination / 'another-profile').mkdir()
            count, backup = install_profiles(read_profiles([archive]), destination, 42, root / 'state')
            actual = json.loads((destination / name / 'manifest.json').read_text())
            self.assertEqual(count, 1)
            self.assertEqual(actual['Device']['UUID'], device_uuid(42))
            self.assertEqual(actual['AppIdentifier'], '*')
            self.assertEqual((destination / name / 'image.png').read_bytes(), b'image-bytes')
            self.assertEqual((backup / name / 'old.txt').read_text(), 'previous')
            self.assertTrue((destination / 'another-profile').is_dir())

    def test_rejects_path_traversal_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'bad.streamDeckProfile'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('package.json', json.dumps({'FormatVersion': 1, 'DeviceModel': '20GBD9901'}))
                output.writestr('Profiles/../outside.json', '{}')
            with self.assertRaises(ValueError):
                read_profiles([archive])


if __name__ == '__main__':
    unittest.main()

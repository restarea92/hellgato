"""Fetch the pinned Qt development kit used only to compile the UI extension."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request


ROOT = Path(__file__).resolve().parents[5]
ARCHIVE = '6.5.3-0-202309260341qtbase-Windows-Windows_10_22H2-MSVC2019-Windows-Windows_10_22H2-X86_64.7z'
URL = 'https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_653/qt.qt6.653.win64_msvc2019_64/' + ARCHIVE
SHA256 = 'f2b9a947ffb4f4d0515ca2fa39915161d8431b35e0dd2f04605ce952c2309b54'


def main():
    folder = ROOT / 'work/toolchain/qt-6.5.3'
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'qtbase.7z'
    if not archive.exists():
        request = urllib.request.Request(URL, headers={'User-Agent': 'Hellgato-build'})
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != SHA256:
            raise RuntimeError('Qt development archive checksum mismatch')
        archive.write_bytes(content)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError('Qt development archive differs from the pinned official package')
    kit = folder / '6.5.3/msvc2019_64'
    if not (kit / 'include/QtWidgets/QApplication').is_file():
        seven_zip = shutil.which('7z')
        if seven_zip:
            subprocess.run([seven_zip, 'x', '-y', f'-o{folder}', str(archive)], check=True)
        else:
            subprocess.run(['tar', '-xf', str(archive), '-C', str(folder)], check=True)
    (folder / 'provenance.json').write_text(json.dumps({'qtVersion': '6.5.3', 'url': URL, 'sha256': SHA256}, indent=2), encoding='utf-8')
    print(kit)


if __name__ == '__main__':
    main()

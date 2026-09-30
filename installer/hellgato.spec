from pathlib import Path
import importlib.metadata
import sys

root = Path(SPECPATH).parent
icon = root / 'app/assets/icons/hellgato-front.ico'
datas = []

def include_tree(relative, suffixes):
    for source in (root / relative).rglob('*'):
        if source.is_file() and source.suffix in suffixes and '__pycache__' not in source.parts:
            datas.append((str(source), str(source.parent.relative_to(root))))

include_tree('app/n4', {'.py'})
include_tree('app/cora', {'.mjs', '.json'})
include_tree('app/locales', {'.json'})
include_tree('work/cora-runtime', {'.js', '.json'})
include_tree('work/references/mirabox/Python-SDK/src/StreamDock', {'.py', '.dll'})
for script in ('start-streamdeck.py', 'patch-device-geometry.py', 'patch-background-grid.py'):
    datas.append((str(root / 'scripts' / script), 'scripts'))
datas += [
    (str(root / 'LICENSE'), '.'),
    (str(icon), 'app/assets/icons'),
    (str(root / 'app/assets/icons/hellgato-64.png'), 'app/assets/icons'),
    (str(root / 'app/assets/icons/hellgato-front-64.png'), 'app/assets/icons'),
    (str(root / 'work/build-runtime/node.exe'), 'runtime'),
    (str(root / 'work/build-runtime/provenance.json'), 'runtime'),
    (str(root / 'work/build-runtime/NODE-LICENSE.txt'), 'licenses'),
    (str(root / 'work/cora-runtime/LICENSE'), 'licenses/DeckBridge'),
    (str(root / 'work/references/mirabox/LICENSE'), 'licenses/Mirabox'),
    (str(Path(sys.base_prefix) / 'LICENSE.txt'), 'licenses/Python'),
    (str(Path(sys.base_prefix) / 'tcl/tk8.6/license.terms'), 'licenses/Tk'),
    (str(importlib.metadata.distribution('pillow').locate_file('pillow-12.0.0.dist-info/licenses/LICENSE')), 'licenses/Pillow'),
    (str(importlib.metadata.distribution('pystray').locate_file('pystray-0.19.5.dist-info/COPYING')), 'licenses/pystray'),
    (str(importlib.metadata.distribution('pystray').locate_file('pystray-0.19.5.dist-info/COPYING.LGPL')), 'licenses/pystray'),
    (str(importlib.metadata.distribution('pefile').locate_file('pefile-2023.2.7.dist-info/LICENSE')), 'licenses/pefile'),
    (str(importlib.metadata.distribution('six').locate_file('six-1.17.0.dist-info/LICENSE')), 'licenses/six'),
    (str(root / 'installer/THIRD-PARTY-NOTICES.txt'), '.'),
]
a = Analysis([str(root / 'app/hellgato.py')], pathex=[str(root / 'app/n4')],
             datas=datas, binaries=[],
             module_collection_mode={'pystray': 'py'},
             hiddenimports=['PIL.Image', 'PIL.ImageSequence', 'ctypes.util', 'ctypes.wintypes', 'csv', 'hashlib', 'struct',
                            'argparse', 'dataclasses', 'enum', 'gc', 'typing', 'platform', 'tempfile',
                            'copy', 'io', 'random', 'datetime', 'shutil', 'pefile', 'pystray._win32'],
             excludes=['numpy', 'matplotlib', 'scipy', 'pytest', 'IPython', 'wmi', 'pythoncom'])
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Hellgato', console=False, upx=False, icon=str(icon))
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HellgatoWorker', console=True, upx=False, icon=str(icon))
COLLECT(gui, worker, a.binaries, a.datas, name='Hellgato', upx=False)

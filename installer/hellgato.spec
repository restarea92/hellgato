from pathlib import Path
import importlib.metadata
import sys

root = Path(SPECPATH).parent
icon = root / 'app/assets/icons/stream-dog.ico'
datas = [(str(root / 'app/_product_compat.py'), 'app')]

def include_tree(relative, suffixes):
    for source in (root / relative).rglob('*'):
        if source.is_file() and source.suffix in suffixes and '__pycache__' not in source.parts:
            datas.append((str(source), str(source.parent.relative_to(root))))

include_tree('app/n4', {'.py'})
include_tree('apps/stream-dog/python/hellgato_stream_dog', {'.py'})
include_tree('apps/stream-dog/python/hellgato_candidate', {'.py'})
include_tree('host-integrations/streamdeck/windows/python', {'.py'})
# Only the compatibility launcher remains here; package runtime is packed below.
datas.append((str(root / 'app/cora/server.mjs'), 'app/cora'))
include_tree('app/locales', {'.json'})
# Consume the verified npm installation, never app/core sources or workspace links.
for source in (root / 'work/stream-dog-sdk').rglob('*'):
    if source.is_symlink():
        raise ValueError('Links are forbidden in the packaged SDK tree')
    if source.is_file():
        datas.append((str(source), str(source.parent.relative_to(root))))
# The USB runtime is an independent executable, including its own Python and vendor DLLs.
worker_root = root / 'dist/HellgatoMiraboxWorker'
if not (worker_root / 'runtime-manifest.json').is_file():
    raise ValueError('Build and stage the Mirabox worker before the desktop app')
for source in worker_root.rglob('*'):
    if source.is_symlink():
        raise ValueError('Links are forbidden in the packaged worker tree')
    if source.is_file():
        datas.append((str(source), str(Path('runtime/mirabox') / source.parent.relative_to(worker_root))))
for script in ('setup-startup.py', 'start-streamdeck.py', 'patch-device-geometry.py', 'patch-background-grid.py', 'load-native-ui.py'):
    datas.append((str(root / 'scripts' / script), 'scripts'))
datas += [
    (str(root / 'app/release.json'), 'app'),
    (str(root / 'work/build-native-startup/version.dll'), 'native'),
    (str(root / 'work/build-native-ui-package/HellgatoNativeUiV2.dll'), 'native'),
    (str(root / 'LICENSE'), '.'),
    (str(icon), 'app/assets/icons'),
    (str(root / 'app/assets/icons/stream-dog-64.png'), 'app/assets/icons'),
    (str(root / 'app/assets/icons/hellgato-front-64.png'), 'app/assets/icons'),
    (str(root / 'work/build-runtime/node.exe'), 'runtime'),
    (str(root / 'work/build-runtime/provenance.json'), 'runtime'),
    (str(root / 'work/build-runtime/NODE-LICENSE.txt'), 'licenses'),
    (str(root / 'work/stream-dog-sdk/node_modules/@hellgato/core/dist/vendor/LICENSE'), 'licenses/DeckBridge'),
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
a = Analysis([str(root / 'apps/stream-dog/python/stream-dog.py')], pathex=[str(root / 'apps/stream-dog/python'), str(root / 'app/n4'), str(root / 'host-integrations/streamdeck/windows/python')],
             datas=datas, binaries=[],
             module_collection_mode={'pystray': 'py'},
             hiddenimports=['PIL.Image', 'PIL.ImageSequence', 'PIL.ImageOps', 'bisect', 'urllib.parse', 'winreg',
                            'ctypes.util', 'ctypes.wintypes', 'csv', 'hashlib', 'struct',
                            'argparse', 'dataclasses', 'enum', 'gc', 'typing', 'platform', 'tempfile',
                            'copy', 'io', 'random', 'datetime', 'shutil', 'pefile', 'pystray._win32',
                            'http.server', 'secrets', 'hmac'],
             excludes=['numpy', 'matplotlib', 'scipy', 'pytest', 'IPython', 'wmi', 'pythoncom'])
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Hellgato', console=False, upx=False, icon=str(icon))
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HellgatoWorker', console=True, upx=False, icon=str(icon))
COLLECT(gui, worker, a.binaries, a.datas, name='Hellgato', upx=False)

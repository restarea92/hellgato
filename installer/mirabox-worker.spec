from pathlib import Path
import runpy
import sys
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'adapters/mirabox-python/src'))
source = root / 'adapters/mirabox-python/src'
vendor = root / 'work/references/mirabox/Python-SDK/src'
# Windows distribution builds must not pass the fake smoke test with a missing
# or stale vendor runtime. Validation is offline here; preparation is explicit.
if sys.platform == 'win32':
    prepare = runpy.run_path(str(root / 'scripts/prepare-windows.py'))
    prepare['prepare_sdk'](root / 'work/references/mirabox', offline=True)
datas = [(str(source / 'hellgato_mirabox/schemas/contracts.schema.json'), 'hellgato_mirabox/schemas'),
         (str(source / 'hellgato_mirabox/models/n4_pro/firmware-policy.json'), 'hellgato_mirabox/models/n4_pro')]
for file in vendor.rglob('*'):
    if file.is_file() and '__pycache__' not in file.parts and file.suffix in ('.py', '.dll'):
        datas.append((str(file), str(Path('vendor/mirabox/src') / file.parent.relative_to(vendor))))
a = Analysis([str(root / 'installer/mirabox-worker-entry.py')], pathex=[str(source)],
             datas=datas, binaries=[], hiddenimports=collect_submodules('hellgato_mirabox') + [
                 'PIL.Image', 'PIL.ImageOps', 'PIL.ImageSequence', 'PIL.GifImagePlugin',
                 'PIL.WebPImagePlugin', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
                 'ctypes.util', 'ctypes.wintypes', 'platform', 'csv', 'enum', 'typing'],
             excludes=['tkinter', 'numpy', 'matplotlib', 'scipy', 'pytest', 'IPython'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HellgatoMiraboxWorker', console=True, upx=False)
COLLECT(exe, a.binaries, a.datas, name='HellgatoMiraboxWorker', upx=False)

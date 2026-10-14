from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'apps/stream-dog/python'))
sys.path.insert(0, str(root / 'host-integrations/streamdeck/windows/python'))
sys.path.insert(0, str(root / 'app/n4'))
source = root / 'apps/stream-dog/python'
host = root / 'host-integrations/streamdeck/windows/python'
a = Analysis([str(source / 'candidate.py')], pathex=[str(source), str(host), str(root / 'app/n4')],
             datas=[], binaries=[],
             hiddenimports=collect_submodules('hellgato_candidate') + collect_submodules('hellgato_host') + [
                 'screensaver', 'PIL.Image', 'PIL.ImageSequence', 'PIL.ImageOps',
                 'PIL.GifImagePlugin', 'PIL.WebPImagePlugin', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
                 'pefile', 'ctypes.wintypes', 'ctypes.util', 'winreg'],
             excludes=['tkinter', 'pystray', 'numpy', 'matplotlib', 'scipy', 'pytest', 'IPython'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HellgatoIpcTest', console=True, upx=False)
COLLECT(exe, a.binaries, a.datas, name='HellgatoIpcTest', upx=False)

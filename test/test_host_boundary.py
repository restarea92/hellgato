import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = ROOT / 'host-integrations/streamdeck/windows/python'


class HostBoundaryTest(unittest.TestCase):
    def test_legacy_imports_share_canonical_module_and_patch_targets(self):
        script = """
import importlib
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
for name in ('compatibility', 'feedback', 'startup_plan', 'startup_install', 'startup_status', 'presence', 'native_ui'):
    old = importlib.import_module(name)
    canonical = importlib.import_module('hellgato_host.' + name)
    assert old is canonical, name
    assert 'host-integrations' in str(Path(old.__file__)), name
with patch('compatibility.Executable', return_value='patched'):
    from hellgato_host import compatibility
    assert compatibility.Executable(b'') == 'patched'
with patch('startup_install.build_plan', return_value='patched'):
    from hellgato_host import startup_install
    assert startup_install.build_plan(b'') == 'patched'
"""
        subprocess.run([sys.executable, '-c', script, str(ROOT / 'app/n4')], check=True, capture_output=True, text=True)

    def test_frozen_legacy_import_uses_bundle_root_without_current_directory(self):
        script = """
from pathlib import Path
import sys
sys._MEIPASS = sys.argv[1]
sys.frozen = True
sys.path.insert(0, str(Path(sys._MEIPASS) / 'app/n4'))
import startup_install
assert Path(startup_install.__file__).is_relative_to(Path(sys._MEIPASS))
assert startup_install.__name__ == 'hellgato_host.startup_install'
"""
        with tempfile.TemporaryDirectory() as folder:
            import shutil
            root = Path(folder)
            shutil.copytree(ROOT / 'app/n4', root / 'app/n4', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(HOST_ROOT, root / 'host-integrations/streamdeck/windows/python',
                            ignore=shutil.ignore_patterns('__pycache__'))
            subprocess.run([sys.executable, '-c', script, str(root)], cwd='/', check=True, capture_output=True, text=True)

    def test_inspection_module_declares_no_mutating_windows_apis(self):
        import ast
        source = (HOST_ROOT / 'hellgato_host/inspection.py').read_text()
        tree = ast.parse(source)
        functions = {node.func.attr for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(functions & {'WriteProcessMemory', 'CreateRemoteThread', 'VirtualAllocEx',
                                      'LoadLibraryW', 'LoadLibraryExW', 'ShellExecuteW'})
        readiness_source = (HOST_ROOT / 'hellgato_host/readiness.py').read_text()
        tree = ast.parse(readiness_source)
        functions = {node.func.id for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertFalse(functions & {'install', 'remove', 'load_native_ui', 'replace_files'})


if __name__ == '__main__':
    unittest.main()

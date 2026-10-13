import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_build', ROOT / 'native/hellgato-ui/build.py')
native_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_build)


class NativeBuildEnvironmentTest(unittest.TestCase):
    def test_activated_compiler_path_replaces_inherited_path_in_any_case(self):
        for spelling in ('Path', 'PATH', 'path'):
            with self.subTest(spelling=spelling):
                result = subprocess.CompletedProcess([], 0, stdout=f'{spelling}=C:\\MSVC\\bin;C:\\Windows\nLIB=C:\\MSVC\\lib\n')
                with patch.dict(native_build.os.environ, {'PATH': 'C:\\Windows'}, clear=True), \
                     patch.object(native_build.subprocess, 'check_output', return_value='C:\\Visual Studio'), \
                     patch.object(native_build.subprocess, 'run', return_value=result):
                    environment = native_build.msvc_environment()
                self.assertEqual(environment['Path'], 'C:\\MSVC\\bin;C:\\Windows')
                self.assertEqual([key for key in environment if key.upper() == 'PATH'], ['Path'])
                self.assertEqual(environment['LIB'], 'C:\\MSVC\\lib')


if __name__ == '__main__':
    unittest.main()

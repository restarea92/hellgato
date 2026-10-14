"""Enforce Python dependency direction and keep lint/type gates non-vacuous."""

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_python', ROOT / 'scripts/check-python.py')
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class PythonQualityTests(unittest.TestCase):
    def test_quality_tools_are_pinned_and_part_of_locked_default_environment(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(set(project['dependency-groups']['check']), {'mypy==1.18.2', 'ruff==0.14.0'})
        self.assertIn('check', project['tool']['uv']['default-groups'])
        self.assertEqual(project['tool']['ruff']['required-version'], '==0.14.0')
        lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))
        packages = {package['name']: package for package in lock['package']}
        for requirement in project['dependency-groups']['check']:
            name, version = requirement.split('==')
            self.assertEqual(packages[name]['version'], version)
            self.assertEqual(packages[name]['source']['registry'], 'https://pypi.org/simple')
        self.assertEqual(project['tool']['uv']['required-version'], '==0.12.23')

    def test_strict_types_cover_real_boundary_code_without_blanket_exceptions(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        settings = project['tool']['mypy']
        self.assertTrue(settings['strict'])
        self.assertFalse(settings.get('ignore_errors', False))
        self.assertFalse(settings.get('ignore_missing_imports', False))
        self.assertNotEqual(settings.get('follow_imports'), 'skip')
        required = {'adapters/mirabox-python/src/hellgato_mirabox/protocol.py',
                    'apps/stream-dog/python/hellgato_candidate/files.py',
                    'host-integrations/streamdeck/windows/python/hellgato_host/startup_status.py',
                    'test/typechecks/python_boundaries.py'}
        self.assertTrue(required <= set(settings['files']))
        for name in settings['files']:
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertEqual(set(project['tool']['ruff']['lint']['select']), {'E4', 'E7', 'E9', 'F', 'B006'})
        for codes in project['tool']['ruff']['lint']['per-file-ignores'].values():
            self.assertEqual(codes, ['E402'], 'Only documented entrypoint bootstrap ordering may be excluded')

    def test_workflow_runs_locked_quality_checks_on_windows(self):
        workflow = (ROOT / '.github/workflows/checks.yml').read_text(encoding='utf-8')
        self.assertIn('runs-on: windows-latest', workflow)
        install = workflow.split('- name: Prepare dependencies')[1].split('- name:')[0]
        self.assertNotIn('if:', install)
        self.assertIn('uv==0.12.23', install)
        self.assertIn('uv sync --locked --no-install-workspace --python python', install)
        self.assertIn('pnpm install --frozen-lockfile', install)
        self.assertIn('pnpm run build:npm', install)
        gate = workflow.split('- name: Python quality')[1].split('- name:')[0]
        self.assertNotIn('if:', gate)
        self.assertIn('uv run --frozen --no-sync python scripts/check-python.py', gate)

    def test_python_layers_do_not_import_reverse_dependencies(self):
        # Architectural source checks, not a security sandbox. The stable app/n4
        # bridge remains a named migration exception outside these new layers.
        rules = {
            'adapters/mirabox-python/src/hellgato_mirabox': {
                'hellgato_host', 'hellgato_stream_dog', 'hellgato_candidate',
                'app', 'apps', 'tkinter', 'subprocess'},
            'host-integrations/streamdeck/windows/python/hellgato_host': {
                'hellgato_mirabox', 'hellgato_stream_dog', 'hellgato_candidate',
                'app', 'apps', 'StreamDock'},
            'apps/stream-dog/python': {'hellgato_mirabox', 'StreamDock'},
        }
        for directory, forbidden in rules.items():
            sources = list((ROOT / directory).rglob('*.py'))
            self.assertTrue(sources, directory)
            for source in sources:
                tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                        modules = [node.module]
                    elif (isinstance(node, ast.Call) and node.args
                          and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                          and ((isinstance(node.func, ast.Name) and node.func.id == '__import__')
                               or (isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'))):
                        modules = [node.args[0].value]
                    for module in modules:
                        self.assertNotIn(module.split('.')[0], forbidden,
                                         f'{source.relative_to(ROOT)}:{node.lineno}: forbidden dependency {module}')


class PythonQualityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hellgato-quality-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        fixture = self.root / 'test/typechecks/python_boundaries_invalid.py.txt'
        fixture.parent.mkdir(parents=True)
        fixture.write_text("bad_call()  # expect: arg-type\nwrong_type()  # expect: assignment\n", encoding='utf-8')
        self.root_patch = patch.object(quality, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def windows_short_path(self, path):
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        api.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        api.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        if not api(str(path), buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        alias = Path(buffer.value)
        if alias == path.resolve():
            self.skipTest('This Windows volume does not provide a distinct short path alias')
        self.assertEqual(alias.resolve(), path.resolve())
        return alias

    def lint_sources(self):
        paths = [self.root / tree / 'boundary.py' for tree in quality.SOURCE_TREES]
        paths.extend(self.root / name for name in (
            'scripts/check-python.py', 'test/test_python_quality.py', 'test/typechecks/python_boundaries.py'))
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('pass\n', encoding='utf-8')
        return paths

    def run_consumer(self, *, code=1, first='arg-type', extra='', repeats=1):
        def result(command, **kwargs):
            path = command[-1]
            output = (f'{path}:1: error: Bad call  [{first}]\n' * repeats
                      + f'{path}:2: error: Bad assignment  [assignment]\n' + extra)
            return subprocess.CompletedProcess(command, code, stdout=output, stderr='')
        with patch.object(quality.subprocess, 'run', side_effect=result):
            quality.check_rejected_consumers()

    def test_negative_fixture_accepts_exact_expected_errors(self):
        self.run_consumer()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows short path aliases')
    def test_negative_fixture_accepts_short_alias_for_the_same_diagnostic_file(self):
        def result(command, **kwargs):
            alias = self.windows_short_path(Path(command[-1]))
            output = (f'{alias}:1: error: Bad call  [arg-type]\n'
                      + f'{alias}:2: error: Bad assignment  [assignment]\n')
            return subprocess.CompletedProcess(command, 1, stdout=output, stderr='')
        with patch.object(quality.subprocess, 'run', side_effect=result):
            quality.check_rejected_consumers()

    def test_negative_fixture_rejects_false_success_or_wrong_error(self):
        for arguments in ({'code': 0}, {'code': 2}, {'first': 'name-defined'}, {'repeats': 2}):
            with self.subTest(arguments=arguments), self.assertRaises(RuntimeError):
                self.run_consumer(**arguments)

    def test_negative_fixture_rejects_errors_in_imported_source(self):
        with self.assertRaises(RuntimeError):
            self.run_consumer(extra='actual_boundary.py:9: error: Real defect  [return-value]\n')

    def test_negative_fixture_requires_declared_errors(self):
        (self.root / 'test/typechecks/python_boundaries_invalid.py.txt').write_text('pass\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            quality.check_rejected_consumers()

    def test_lint_cannot_silently_exclude_a_canonical_module(self):
        for tree in quality.SOURCE_TREES:
            directory = self.root / tree
            directory.mkdir(parents=True)
            (directory / 'boundary.py').write_text('pass\n', encoding='utf-8')
        result = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with patch.object(quality.subprocess, 'run', return_value=result), self.assertRaises(RuntimeError):
            quality.check_lint_coverage()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows short path aliases')
    def test_lint_short_root_still_reports_an_omitted_canonical_module(self):
        paths = self.lint_sources()
        omitted = paths[1]
        output = '\n'.join(str(path.resolve()) for path in paths if path != omitted)
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
        with patch.object(quality, 'ROOT', self.windows_short_path(self.root)), \
                patch.object(quality.subprocess, 'run', return_value=result), \
                self.assertRaisesRegex(RuntimeError, 'apps[/\\\\]stream-dog[/\\\\]python[/\\\\]boundary.py'):
            quality.check_lint_coverage()

    def test_lint_cannot_silently_check_an_empty_source_tree(self):
        result = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with patch.object(quality.subprocess, 'run', return_value=result), self.assertRaises(RuntimeError):
            quality.check_lint_coverage()


if __name__ == '__main__':
    unittest.main()

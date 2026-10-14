"""Run canonical lint, strict boundary types, and non-vacuous checker fixtures.

Use the pinned environment: uv sync --locked --no-install-workspace, followed by
uv run --frozen --no-sync python scripts/check-python.py (Linux or Windows).
"""

from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TREES = (
    'adapters/mirabox-python/src/hellgato_mirabox',
    'apps/stream-dog/python',
    'host-integrations/streamdeck/windows/python',
)


def check_tool_versions() -> None:
    """Direct invocation must not silently run different, unpinned checkers."""
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError('Python quality gates require the locked Python 3.13 environment')
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    for requirement in config['dependency-groups']['check']:
        name, expected = requirement.split('==')
        actual = version(name)
        if actual != expected:
            raise RuntimeError(f'{name} {actual} is installed; run uv sync --locked for {expected}')


def check_lint_coverage() -> None:
    """Fail closed if include/exclude changes silently omit a canonical module."""
    result = subprocess.run([sys.executable, '-m', 'ruff', 'check', '--show-files'],
                            cwd=ROOT, check=True, capture_output=True, text=True, timeout=120)
    actual = {Path(name).resolve() for name in result.stdout.splitlines()}
    required: set[Path] = set()
    for tree in SOURCE_TREES:
        sources = {source.resolve() for source in (ROOT / tree).rglob('*.py')}
        if not sources:
            raise RuntimeError('Canonical Python source tree is missing or empty: ' + tree)
        required.update(sources)
    required.update((ROOT / name).resolve() for name in (
        'scripts/check-python.py', 'test/test_python_quality.py', 'test/typechecks/python_boundaries.py'))
    missing = sorted(str(name.relative_to(ROOT)) for name in required - actual)
    if missing:
        raise RuntimeError('Canonical Python modules escaped lint coverage: ' + ', '.join(missing))
    print(f'Canonical Python lint coverage: {len(required)} files', flush=True)


def check_rejected_lint() -> None:
    """Verify the selected lint rules really reject undefined and unused names."""
    result = subprocess.run([sys.executable, '-m', 'ruff', 'check', '--output-format', 'json',
                             '--stdin-filename', 'scripts/check-python.py', '-'], cwd=ROOT,
                            input='import os\nprint(unknown_name)\n', capture_output=True,
                            text=True, timeout=120)
    errors = json.loads(result.stdout)
    actual = {(error['location']['row'], error['code']) for error in errors}
    if result.returncode != 1 or actual != {(1, 'F401'), (2, 'F821')} or len(errors) != 2:
        raise RuntimeError('Lint checker did not reject the invalid fixture:\n'
                           + result.stdout + result.stderr)
    print('Negative lint consumer rejected: 2 expected lint errors', flush=True)


def check_rejected_consumers() -> None:
    """A green checker must reject bad consumers at each real boundary."""
    fixture = ROOT / 'test/typechecks/python_boundaries_invalid.py.txt'
    source = fixture.read_text(encoding='utf-8')
    expected = {(line, match.group(1)) for line, text in enumerate(source.splitlines(), 1)
                if (match := re.search(r'# expect: ([a-z-]+)', text))}
    if not expected:
        raise ValueError('Negative boundary fixture must contain expected type errors')
    with tempfile.TemporaryDirectory(prefix='hellgato-type-boundary-') as directory:
        path = Path(directory) / 'invalid_consumer.py'
        path.write_text(source, encoding='utf-8')
        result = subprocess.run([sys.executable, '-m', 'mypy', '--config-file', 'pyproject.toml',
                                 '--no-pretty', '--no-color-output', str(path)], cwd=ROOT,
                                capture_output=True, text=True, timeout=120)
        # Count every diagnostic, including unexpected errors in imported source.
        diagnostics = re.findall(r'^(.+):(\d+): error: .*?\[([a-z-]+)\]$',
                                 result.stdout, re.MULTILINE)
        actual = {(int(line), code) for name, line, code in diagnostics if Path(name) == path}
        unexpected = any(Path(name) != path for name, _, _ in diagnostics)
    if (result.returncode != 1 or actual != expected or unexpected
            or len(diagnostics) != len(expected)):
        raise RuntimeError('Type checker did not reject exactly the expected invalid boundary consumers:\n'
                           + result.stdout + result.stderr)
    print(f'Negative boundary consumers rejected: {len(expected)} expected type errors', flush=True)


def main() -> None:
    check_tool_versions()
    check_lint_coverage()
    for module, arguments in (('ruff', ['check']), ('mypy', ['--config-file', 'pyproject.toml'])):
        subprocess.run([sys.executable, '-m', module, *arguments], cwd=ROOT, check=True, timeout=120)
    check_rejected_lint()
    check_rejected_consumers()
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'test',
                    '-p', 'test_python_quality.py'], cwd=ROOT, check=True, timeout=120)


if __name__ == '__main__':
    main()

"""Compatibility entry for the canonical Windows host-integration build."""
import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[2] / 'host-integrations/streamdeck/windows/native/ui/prepare.py'
_spec = importlib.util.spec_from_file_location('hellgato_native_ui_prepare', _source)
_implementation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_implementation)
globals().update({name: value for name, value in vars(_implementation).items() if not name.startswith('__')})

if __name__ == '__main__':
    main()

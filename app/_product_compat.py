"""Keep legacy imports bound to the single, patchable product module object."""

import importlib
from pathlib import Path
import sys


def alias(legacy_name, canonical_name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    location = str(root / 'apps/stream-dog/python')
    if location not in sys.path:
        sys.path.insert(0, location)
    module = importlib.import_module('hellgato_stream_dog.' + canonical_name)
    if legacy_name != '__main__':
        sys.modules[legacy_name] = module
    return module

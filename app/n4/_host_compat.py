"""Resolve legacy module names to one canonical, patchable module object."""

import importlib
from pathlib import Path
import sys


def alias(legacy_name, canonical_name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    location = str(root / 'host-integrations/streamdeck/windows/python')
    if location not in sys.path:
        sys.path.insert(0, location)
    module = importlib.import_module('hellgato_host.' + canonical_name)
    sys.modules[legacy_name] = module
    return module

"""Attach Hellgato controls to an existing, user-launched Stream Deck."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/n4'))
from native_ui import load_native_ui


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--dll', type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--exe', type=Path)
    args = parser.parse_args()
    dll = args.dll
    if dll is None:
        candidates = [ROOT / 'native/HellgatoNativeUiV2.dll',
                      ROOT / 'work/build-native-ui-package/HellgatoNativeUiV2.dll',
                      ROOT / 'work/build-native-ui/HellgatoNativeUiV2.dll']
        dll = next((path for path in candidates if path.is_file()), candidates[0])
    print(json.dumps(load_native_ui(args.pid, dll, args.config, args.exe)), flush=True)


if __name__ == '__main__':
    main()

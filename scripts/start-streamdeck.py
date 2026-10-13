"""Prepare a running Stream Deck application without starting or stopping it."""

import argparse
import csv
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/n4'))
from compatibility import Executable, INCOMPATIBLE, MESSAGE, IncompatibleError, require, resolve
from feedback import resolve_feedback
from live_preparation import RETRY, TransientPreparationError, WinProcess, inspect_layout, prepare_live, verify_live_code

STATE = Path(os.environ.get('HELLGATO_STATE_DIR', ROOT / 'work'))
RESULTS = STATE / 'results'


def streamdeck_pids():
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq StreamDeck.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return [int(row[1]) for row in csv.reader(io.StringIO(result.stdout))
            if len(row) > 1 and row[0].lower() == 'streamdeck.exe']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=Path(os.environ['ProgramFiles']) / 'Elgato/StreamDeck/StreamDeck.exe')
    parser.add_argument('--pid', type=int, help='Attach only to this existing Stream Deck process')
    parser.add_argument('--check-only', action='store_true', help='Inspect compatibility without process writes')
    parser.add_argument('--inspect-only', action='store_true', help='Read the live layout and existing hooks without writes')
    parser.add_argument('--require-closed', action='store_true', help='Fail unless Stream Deck is already closed')
    parser.add_argument('--ready-timeout', type=float, default=15, help='Seconds to retry transient preparation readiness')
    args = parser.parse_args()
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        parser.error('64-bit Python is required')
    if args.pid is not None and args.pid <= 0:
        parser.error('pid must be positive')
    if not 0 <= args.ready_timeout <= 30:
        parser.error('ready-timeout must be between 0 and 30 seconds')
    stop_file = os.environ.get('HELLGATO_STOP_FILE')
    stopped = lambda: bool(stop_file and Path(stop_file).exists())
    if stopped():
        raise TransientPreparationError('Preparation cancelled')
    if args.require_closed:
        require(not streamdeck_pids(), 'Close Stream Deck manually before importing profiles')
        print(json.dumps({'mode': 'require-closed', 'closed': True}), flush=True)
        return
    executable = args.exe.read_bytes()
    spec = resolve(executable)
    spec['feedback'] = resolve_feedback(Executable(executable))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / 'compatibility-spec.json').write_text(json.dumps(spec, indent=2), encoding='utf-8')
    if args.check_only:
        print(json.dumps({'event': 'staticCompatibilityPassed', 'mode': 'check', 'sha256': spec['sha256']}), flush=True)
        return
    pids = [args.pid] if args.pid is not None else streamdeck_pids()
    if not pids:
        raise TransientPreparationError('Open Stream Deck before connecting')
    require(len(pids) == 1, f'Open one Stream Deck application before connecting; found {len(pids)}')
    deadline = time.monotonic() + args.ready_timeout
    while True:
        try:
            with WinProcess(pids[0], args.exe, writable=not args.inspect_only) as process:
                process.verify_executable(spec)
                if args.inspect_only:
                    _, feedback = verify_live_code(process, spec)
                    layout = inspect_layout(process, spec)
                    report = {'pid': process.pid, 'mode': 'inspect', 'feedback': feedback,
                              'inspection': {key: value for key, value in layout.items() if not key.startswith('_')}}
                else:
                    report = prepare_live(process, spec, stop=stopped)
                    (RESULTS / 'startup-ready.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps(report), flush=True)
                return
        except TransientPreparationError:
            if stopped() or args.inspect_only or time.monotonic() >= deadline:
                raise
            time.sleep(.2)


if __name__ == '__main__':
    try:
        main()
    except IncompatibleError as error:
        print(json.dumps({'event': 'incompatible', 'message': MESSAGE, 'detail': str(error)}, ensure_ascii=False), flush=True)
        raise SystemExit(INCOMPATIBLE)
    except TransientPreparationError as error:
        print(json.dumps({'event': 'preparationPending', 'detail': str(error)}), flush=True)
        raise SystemExit(RETRY)

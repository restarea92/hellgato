"""Inspect or prepare verified CORA Plus geometry in an existing process."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import INCOMPATIBLE, IncompatibleError, validate_controllers
from live_preparation import RETRY, TransientPreparationError, WinProcess, inspect_layout, prepare_live, verify_live_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--mode', choices=['inspect', 'apply'], default='inspect')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--objects', action='store_true')
    parser.add_argument('--final-layout', action='store_true')
    parser.add_argument('--controller-width', type=int, choices=[5])
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding='utf-8'))
    with WinProcess(args.pid, writable=args.mode == 'apply') as process:
        process.verify_executable(spec)
        verify_live_code(process, spec)
        if args.mode == 'apply':
            prepared = prepare_live(process, spec, include_feedback=False)
            result = prepared['inspection']
            result['preparation'] = {key: value for key, value in prepared.items() if key != 'inspection'}
        else:
            result = {key: value for key, value in inspect_layout(process, spec).items() if not key.startswith('_')}
        if args.final_layout:
            validate_controllers(result['plusModelControllers'], final=True)
        result['mode'] = args.mode
        result['rva'] = hex(spec['rva'])
        result['after'] = process.read(process.base + spec['rva'], len(bytes.fromhex(spec['original']))).hex()
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except IncompatibleError as error:
        print(f'Incompatible layout: {error}', file=sys.stderr, flush=True)
        raise SystemExit(INCOMPATIBLE)
    except TransientPreparationError as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(RETRY)

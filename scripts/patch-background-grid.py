"""Prepare the live Plus background grid with a suspended coherent commit."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import INCOMPATIBLE, IncompatibleError
from live_preparation import RETRY, TransientPreparationError, WinProcess, prepare_live


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    args = parser.parse_args()
    prior = json.loads(args.inspection.read_text(encoding='utf-8'))
    spec = json.loads(args.spec.read_text(encoding='utf-8'))
    with WinProcess(prior['pid'], prior['executable']) as process:
        process.verify_executable(spec)
        report = prepare_live(process, spec, include_feedback=False)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except IncompatibleError as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(INCOMPATIBLE)
    except TransientPreparationError as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(RETRY)

"""Frozen physical-worker entry. Fake mode must be explicitly selected."""
from hellgato_mirabox.worker import main

if __name__ == '__main__':
    raise SystemExit(main())

"""Source and PyInstaller entry point for the read-only host readiness inspector."""

from hellgato_host.readiness import main


if __name__ == '__main__':
    raise SystemExit(main())

"""Stable source/bundle root; independent of the process working directory."""

from pathlib import Path
import sys

ROOT = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(__file__).resolve().parents[4]

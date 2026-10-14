"""Compatibility alias for the canonical Stream Dog product module."""

if __package__:
    from .._product_compat import alias
else:
    from pathlib import Path
    import sys
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(root / 'app'))
    from _product_compat import alias

alias(__name__, 'profile_transfer')

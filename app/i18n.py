"""Compatibility alias for the canonical Stream Dog product module."""

if __package__:
    from ._product_compat import alias
else:
    from _product_compat import alias

_module = alias(__name__, 'i18n')

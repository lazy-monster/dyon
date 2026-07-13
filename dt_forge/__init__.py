"""Backward-compatibility shim: ``dt_forge`` was renamed to ``dyon``.

Importing ``dt_forge`` (or any ``dt_forge.*`` submodule) transparently resolves
to the matching ``dyon`` module and emits a one-time ``DeprecationWarning``. The
redirect machinery lives in :mod:`dyon._compat`. Migrate by replacing ``dt_forge``
with ``dyon`` in your imports; this shim will be removed in a future major release.
"""

from dyon._compat import install_alias

install_alias()

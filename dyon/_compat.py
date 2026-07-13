"""Compatibility support for the legacy ``dt_forge`` import name.

``dt_forge`` was renamed to ``dyon``. :func:`install_alias` registers a meta-path
hook so that every ``dt_forge`` / ``dt_forge.*`` import is served by the matching
``dyon`` module — the *same* module object, so identity, ``isinstance`` and
module-level singletons stay consistent across both names.

The logic lives here (shipped with ``dyon``) so the ``dt_forge`` shim package is a
trivial two-line forwarder with nothing to drift out of sync.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

_OLD = "dt_forge"
_NEW = "dyon"


def require(module: str, extra: str):
    """Import ``module`` or raise with the pip extra that provides it.

    Heavy dependencies live in optional extras (see ``pyproject.toml``); the
    features that use them import lazily and route the import through this helper
    so a missing dependency fails with an actionable message instead of a bare
    ``ModuleNotFoundError``.
    """
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise ImportError(
            f"'{module}' is required for this feature. "
            f"Install it with: pip install 'dyon[{extra}]'"
        ) from e


class _AliasLoader(Loader):
    """Loads an old-name module by handing back the real ``dyon`` module object."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: ModuleSpec):
        module = importlib.import_module(self._target)
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module) -> None:  # already initialised as ``dyon.*``
        pass


class _AliasFinder(MetaPathFinder):
    """Redirects ``dt_forge`` / ``dt_forge.<sub>`` to ``dyon`` / ``dyon.<sub>``."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == _OLD or fullname.startswith(_OLD + "."):
            return ModuleSpec(fullname, _AliasLoader(_NEW + fullname[len(_OLD):]))
        return None


def install_alias(*, warn: bool = True) -> None:
    """Make ``dt_forge`` resolve to ``dyon``. Idempotent; safe to call repeatedly."""
    if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        # Insert ahead of the default path finder so submodules alias rather than
        # re-import under a second name.
        sys.meta_path.insert(0, _AliasFinder())

    if warn:
        warnings.warn(
            "'dt_forge' has been renamed to 'dyon'. Update your imports to 'dyon' "
            "(e.g. 'import dyon', 'from dyon.core.config import TwinConfig'). The "
            "'dt_forge' compatibility alias will be removed in a future major release.",
            DeprecationWarning,
            stacklevel=3,
        )

    # Serve ``import dt_forge`` itself from the real package, so attribute access
    # (``dt_forge.AbstractDigitalTwin``) resolves identically to ``dyon``.
    sys.modules[_OLD] = importlib.import_module(_NEW)

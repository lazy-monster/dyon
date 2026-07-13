"""The ``dt_forge`` → ``dyon`` rename must not break existing code.

These guard the compatibility shim: old-name imports resolve to the very same
``dyon`` module objects (so identity, ``isinstance`` and singletons hold), and
importing the old name emits a single ``DeprecationWarning``.
"""

from __future__ import annotations

import subprocess
import sys


def test_old_imports_alias_to_the_same_dyon_objects() -> None:
    from dt_forge.core.config import TwinConfig
    from dt_forge.core.events import EventBus

    import dyon.core.config
    import dyon.core.events

    # The shim hands back the real module/class, not a re-executed copy.
    assert TwinConfig is dyon.core.config.TwinConfig
    assert EventBus is dyon.core.events.EventBus

    import dt_forge.core.events as old_events

    import dt_forge

    assert dt_forge is dyon
    assert old_events is dyon.core.events


def test_old_package_import_emits_deprecation_warning() -> None:
    # Run in a fresh interpreter: the warning fires once per process, so a
    # subprocess is the reliable way to observe it regardless of import order.
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as w:\n"
        "    warnings.simplefilter('always')\n"
        "    import dt_forge\n"
        "assert any(issubclass(x.category, DeprecationWarning) and 'dyon' in str(x.message)\n"
        "           for x in w), 'no deprecation warning naming dyon'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

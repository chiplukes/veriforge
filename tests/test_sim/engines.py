"""Shared engine-list helper for sim tests."""

import shutil

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def available_engines() -> list[str]:
    """Return list of available engine names."""
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        try:
            import Cython  # noqa: F401, PLC0415

            engines.append("compiled")
        except ImportError:
            pass
    return engines


ENGINES = available_engines()
"""All available engines for parameterisation."""

STEPPED_ENGINES = [e for e in ENGINES if e in {"vm", "compiled"}]
"""Engines that support stepped execution (no reference-engine-only constructs)."""

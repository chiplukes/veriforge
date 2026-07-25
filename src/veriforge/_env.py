"""Environment-variable access with legacy-prefix fallback."""

import os
import warnings

_LEGACY_PREFIX = "VERILOG_TOOLS_"
_PREFIX = "VERIFORGE_"


def get_env(suffix: str, default: str | None = None) -> str | None:
    """Read VERIFORGE_<suffix>, falling back to VERILOG_TOOLS_<suffix> with a DeprecationWarning."""
    val = os.environ.get(_PREFIX + suffix)
    if val is not None:
        return val
    legacy = os.environ.get(_LEGACY_PREFIX + suffix)
    if legacy is not None:
        warnings.warn(
            f"{_LEGACY_PREFIX}{suffix} is deprecated; use {_PREFIX}{suffix}",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy
    return default

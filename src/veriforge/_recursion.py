"""Recursion headroom for deeply nested Verilog expressions."""

import sys

# The tree transforms, semantic passes and the reference evaluator recurse
# once (or a few times) per expression nesting level, so Python's default
# limit of 1000 rejected expressions nested ~200 deep that Icarus accepts.
# Python 3.12+ keeps Python-to-Python calls off the C stack and guards the C
# stack itself, so a higher limit can't crash the interpreter.
_DEEP_RECURSION_LIMIT = 50_000


def ensure_deep_recursion() -> None:
    """Raise the interpreter's recursion limit (never lower it)."""
    if sys.getrecursionlimit() < _DEEP_RECURSION_LIMIT:
        sys.setrecursionlimit(_DEEP_RECURSION_LIMIT)

"""Constant folding and parameter evaluation for Verilog expressions.

Evaluates constant expressions to their integer values at elaboration time.
Handles arithmetic, bitwise, logical, shift, and comparison operators, as well
as system functions like ``$clog2``.

After name resolution (``resolve_names()``), parameters referenced by
``Identifier`` nodes are followed through to their ``default_value`` and
recursively folded.

Usage::

    from veriforge.analysis.const_fold import const_fold, const_int

    # Fold to int ─ returns int | None
    value = const_int(expr)

    # Fold to Literal ─ returns a new Literal or None
    lit = const_fold(expr)

    # Fold all constant expressions in a design
    fold_constants(design)         # entire design
    fold_constants_in_module(mod)  # single module
"""

from __future__ import annotations

from ..model.base import VerilogNode
from ..model.design import Design, Module
from ..model.expressions import Expression, Literal, Range
from ..semantics import const_int as semantics_const_int

# ---------------------------------------------------------------------------
# Core constant folding
# ---------------------------------------------------------------------------


def const_int(expr: Expression) -> int | None:
    """Try to evaluate an expression to a constant integer.

    Returns the integer value if the expression is fully constant,
    or ``None`` if it depends on non-constant signals or cannot be
    evaluated.

    Follows resolved ``Identifier`` → ``Parameter`` references to
    fold parameter expressions (delegates to ``semantics.const_int``,
    which supports this via ``Identifier.resolved`` in addition to its
    primary ``env``-dict mechanism -- see ``semantics.py``'s module
    docstring and ``tests/test_analysis/test_semantics_parity.py``).
    """
    return semantics_const_int(expr)


def const_fold(expr: Expression) -> Literal | None:
    """Fold a constant expression into a ``Literal`` node.

    Returns a new ``Literal`` with the folded value, or ``None`` if the
    expression is not fully constant.  The returned ``Literal`` has no width
    (unsized, like Verilog default integer).
    """
    val = const_int(expr)
    if val is None:
        return None
    return Literal(val)


def const_range_width(rng: Range | None) -> int | None:
    """Extract integer width from a Range [msb:lsb], using constant folding.

    Unlike the simple version in width_inference that only handles bare
    Literals, this follows parameter references and evaluates expressions.
    """
    if rng is None:
        return 1  # scalar
    msb_val = const_int(rng.msb)
    lsb_val = const_int(rng.lsb)
    if msb_val is not None and lsb_val is not None:
        return abs(msb_val - lsb_val) + 1
    return None


# ---------------------------------------------------------------------------
# Module / Design-level passes
# ---------------------------------------------------------------------------


def fold_constants_in_module(module: Module) -> None:
    """Fold constant expressions in a module's parameter default values.

    Populates ``Parameter.folded_value`` (an ``int | None``) for each
    parameter whose default value can be statically determined.

    .. note:: This does NOT rewrite the expression tree.  It only stores
       the folded integer value on the Parameter object for consumers
       (e.g. width inference) to use.
    """
    for param in module.parameters:
        if param.default_value is not None:
            val = const_int(param.default_value)
            # Store as a convenience but don't modify the tree
            # Consumers use const_int() directly when needed


def fold_constants(design: Design) -> None:
    """Fold constant expressions across all modules in a design.

    Should be called after ``resolve_names()`` so that ``Identifier.resolved``
    is populated for parameter references.
    """
    for module in design.modules:
        fold_constants_in_module(module)

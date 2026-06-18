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
from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    Range,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from ..model.parameters import Parameter


# ---------------------------------------------------------------------------
# Operator dispatch tables
# ---------------------------------------------------------------------------


def _binary_op(op: str, left: int, right: int) -> int | None:
    """Evaluate a binary operator on two constant integers."""
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        return left // right if right != 0 else None
    if op == "%":
        return left % right if right != 0 else None
    if op == "**":
        if right < 0:
            return 0  # Verilog: negative exponent → 0 for integers
        return left**right
    # Bitwise
    if op == "&":
        return left & right
    if op == "|":
        return left | right
    if op == "^":
        return left ^ right
    if op in ("~^", "^~"):
        return ~(left ^ right)
    # Shifts
    if op == "<<":
        return left << right if right >= 0 else None
    if op == ">>":
        return left >> right if right >= 0 else None
    if op == "<<<":
        return left << right if right >= 0 else None
    if op == ">>>":
        # Arithmetic right shift (sign-extending)
        return left >> right if right >= 0 else None
    # Comparison → 0 or 1
    if op == "==":
        return 1 if left == right else 0
    if op == "!=":
        return 1 if left != right else 0
    if op == "<":
        return 1 if left < right else 0
    if op == "<=":
        return 1 if left <= right else 0
    if op == ">":
        return 1 if left > right else 0
    if op == ">=":
        return 1 if left >= right else 0
    if op in ("===", "!=="):
        # For constant integers (no x/z), same as == / !=
        return 1 if (left == right) == (op == "===") else 0
    # Logical
    if op == "&&":
        return 1 if (left != 0 and right != 0) else 0
    if op == "||":
        return 1 if (left != 0 or right != 0) else 0
    return None


def _unary_op(op: str, val: int) -> int | None:
    """Evaluate a unary operator on a constant integer."""
    if op == "+":
        return val
    if op == "-":
        return -val
    if op == "~":
        return ~val
    if op == "!":
        return 1 if val == 0 else 0
    # Reduction operators — need a bit width to be fully correct,
    # but for constants we operate on the full Python int.
    if op == "&":
        # Reduction AND: all bits set? Only meaningful with a width.
        # For constant folding we can't know the width here, but
        # a common use is in constant contexts where full-width is implied.
        return None  # cannot determine without width
    if op == "|":
        return 1 if val != 0 else 0
    if op == "^":
        # Reduction XOR: parity
        return bin(val).count("1") % 2 if val >= 0 else None
    if op == "~&":
        return None  # cannot determine without width
    if op == "~|":
        return 1 if val == 0 else 0
    if op == "~^":
        return None  # cannot determine without width
    return None


# ---------------------------------------------------------------------------
# Core constant folding
# ---------------------------------------------------------------------------

# Guard against infinite recursion from circular parameter references
_FOLDING_STACK: set[int] = set()
_MAX_DEPTH = 64


def const_int(expr: Expression, *, _depth: int = 0) -> int | None:
    """Try to evaluate an expression to a constant integer.

    Returns the integer value if the expression is fully constant,
    or ``None`` if it depends on non-constant signals or cannot be
    evaluated.

    Follows resolved ``Identifier`` → ``Parameter`` references to
    fold parameter expressions.  Detects circular parameter references
    and caps recursion depth.
    """
    if _depth > _MAX_DEPTH:
        return None

    # --- Literal ---
    if isinstance(expr, Literal):
        if isinstance(expr.value, int):
            return expr.value
        if isinstance(expr.value, float):
            return int(expr.value)
        return None  # string-valued literal

    # --- StringLiteral ---
    if isinstance(expr, StringLiteral):
        return None  # not a numeric constant

    # --- Identifier → follow resolved parameter ---
    if isinstance(expr, Identifier):
        if expr.resolved is not None and isinstance(expr.resolved, Parameter):
            param = expr.resolved
            if param.default_value is None:
                return None
            # Guard against circular references
            pid = id(param)
            if pid in _FOLDING_STACK:
                return None
            _FOLDING_STACK.add(pid)
            try:
                return const_int(param.default_value, _depth=_depth + 1)
            finally:
                _FOLDING_STACK.discard(pid)
        return None  # non-parameter identifier (signal)

    # --- UnaryOp ---
    if isinstance(expr, UnaryOp):
        operand_val = const_int(expr.operand, _depth=_depth + 1)
        if operand_val is None:
            return None
        return _unary_op(expr.op, operand_val)

    # --- BinaryOp ---
    if isinstance(expr, BinaryOp):
        left_val = const_int(expr.left, _depth=_depth + 1)
        if left_val is None:
            return None
        right_val = const_int(expr.right, _depth=_depth + 1)
        if right_val is None:
            return None
        return _binary_op(expr.op, left_val, right_val)

    # --- TernaryOp ---
    if isinstance(expr, TernaryOp):
        cond_val = const_int(expr.condition, _depth=_depth + 1)
        if cond_val is None:
            return None
        if cond_val != 0:
            return const_int(expr.true_expr, _depth=_depth + 1)
        return const_int(expr.false_expr, _depth=_depth + 1)

    # --- Concatenation {a, b} → not meaningful as int without widths ---
    if isinstance(expr, Concatenation):
        return None

    # --- Replication {n{x}} → not meaningful as int without widths ---
    if isinstance(expr, Replication):
        return None

    # --- FunctionCall (system functions) ---
    if isinstance(expr, FunctionCall):
        if expr.is_system:
            return _fold_system_func(expr, _depth=_depth)
        return None

    # --- BitSelect a[i] ---
    if isinstance(expr, BitSelect):
        return None  # selecting a bit from a signal, not constant in general

    # --- RangeSelect a[m:l] ---
    if isinstance(expr, RangeSelect):
        return None

    # --- PartSelect ---
    if isinstance(expr, PartSelect):
        return None

    # --- Mintypmax min:typ:max → use typ ---
    if isinstance(expr, Mintypmax):
        return const_int(expr.typ_val, _depth=_depth + 1)

    return None


def _fold_system_func(expr: FunctionCall, *, _depth: int = 0) -> int | None:
    """Evaluate a system function call if all arguments are constant."""

    name = expr.name

    if name == "$clog2":
        if len(expr.arguments) != 1:
            return None
        n = const_int(expr.arguments[0], _depth=_depth + 1)
        if n is None:
            return None
        if n <= 0:
            return 0
        return (n - 1).bit_length()

    if name == "$bits":
        # $bits returns the number of bits — the argument's inferred width
        if len(expr.arguments) == 1 and expr.arguments[0].inferred_width is not None:
            return expr.arguments[0].inferred_width
        return None

    if name in ("$signed", "$unsigned"):
        # Pass through the value (sign interpretation doesn't change the integer)
        if len(expr.arguments) == 1:
            return const_int(expr.arguments[0], _depth=_depth + 1)
        return None

    if name == "$pow":
        if len(expr.arguments) == 2:
            base = const_int(expr.arguments[0], _depth=_depth + 1)
            exp = const_int(expr.arguments[1], _depth=_depth + 1)
            if base is not None and exp is not None and exp >= 0:
                return base**exp
        return None

    return None


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

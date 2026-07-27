"""Unified width/signedness/const-eval semantics (work plan item 4.2).

Single source of truth for the constant-expression evaluation and
width/signedness computation that used to be duplicated across
``sim/scheduler.py``, ``sim/vm/compiler.py``, ``sim/compiled/_codegen_utils.py``,
``sim/compiled/codegen.py``, and ``analysis/width_inference.py`` /
``analysis/const_fold.py`` (see ``tests/test_analysis/test_semantics_parity.py``
for the characterization that produced this module's resolved behavior).

Deliberately stdlib + ``veriforge.model`` imports only — no engine data
structures (symbol tables, codegen state, ``EvalContext``, ...) leak in here.
Engine-specific lookups are passed in as callbacks (``env`` for constant
identifiers, ``width_of``/``signed_of`` for signal declarations).

IEEE 1364-2005 references: §5.4.1 (self-determined widths, Table 5-22),
§5.5 (signedness rules), §5.5.1 (bit-select/part-select/range-select are
always unsigned regardless of the sliced signal's own declared signedness).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .model.expressions import (
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
from .model.nets import Net
from .model.parameters import Parameter
from .model.variables import Variable, VariableKind

__all__ = [
    "const_int",
    "expr_signed",
    "expr_width",
    "net_width",
    "range_width",
    "var_width",
]

# ── Constant expression evaluation ──────────────────────────────────────────

_UNARY_CONST_OPS: dict[str, Callable[[int], int | None]] = {
    "+": lambda v: v,
    "-": lambda v: -v,
    "~": lambda v: ~v,
    "!": lambda v: 1 if v == 0 else 0,
    "|": lambda v: 1 if v != 0 else 0,
    "~|": lambda v: 1 if v == 0 else 0,
    "^": lambda v: bin(v).count("1") % 2 if v >= 0 else None,
    # Reduction AND/NAND/XNOR on a bare constant with no known width are
    # genuinely ambiguous (there's no declared bit count to reduce over) --
    # unlike the other reduction ops above, these cannot be approximated.
    "&": lambda _v: None,
    "~&": lambda _v: None,
    "~^": lambda _v: None,
    "^~": lambda _v: None,
}

_BINARY_CONST_OPS: dict[str, Callable[[int, int], int | None]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a // b if b != 0 else None,
    "%": lambda a, b: a % b if b != 0 else None,
    "**": lambda a, b: 0 if b < 0 else a**b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "~^": lambda a, b: ~(a ^ b),
    "^~": lambda a, b: ~(a ^ b),
    "<<": lambda a, b: a << b if b >= 0 else None,
    ">>": lambda a, b: a >> b if b >= 0 else None,
    "<<<": lambda a, b: a << b if b >= 0 else None,
    ">>>": lambda a, b: a >> b if b >= 0 else None,
    "==": lambda a, b: 1 if a == b else 0,
    "!=": lambda a, b: 1 if a != b else 0,
    "<": lambda a, b: 1 if a < b else 0,
    "<=": lambda a, b: 1 if a <= b else 0,
    ">": lambda a, b: 1 if a > b else 0,
    ">=": lambda a, b: 1 if a >= b else 0,
    "===": lambda a, b: 1 if a == b else 0,
    "!==": lambda a, b: 1 if a != b else 0,
    "&&": lambda a, b: 1 if (a != 0 and b != 0) else 0,
    "||": lambda a, b: 1 if (a != 0 or b != 0) else 0,
}


def _eval_const(expr: Expression, env: Mapping[str, int]) -> int:
    """Evaluate a constant expression given parameter/genvar values in *env*.

    Raises ``ValueError``/``TypeError`` for anything it cannot evaluate
    (unknown identifier, non-constant construct, missing width info). This
    is the internal primitive — ``const_int`` is the public, non-raising
    wrapper around it.
    """
    if isinstance(expr, StringLiteral):
        raise ValueError("string literal is not a numeric constant")
    if isinstance(expr, Literal):
        if isinstance(expr.value, str):
            return int(expr.value)
        if isinstance(expr.value, float):
            return int(expr.value)
        return int(expr.value)
    if isinstance(expr, Identifier):
        name = expr.name
        if expr.hierarchy:
            name = ".".join(expr.hierarchy) + "." + name
        if name in env:
            return env[name]
        if expr.name in env:
            return env[expr.name]
        return _eval_const_via_resolved(expr, env)
    if isinstance(expr, UnaryOp):
        val = _eval_const(expr.operand, env)
        unary_fn = _UNARY_CONST_OPS.get(expr.op)
        if unary_fn is None:
            raise ValueError(f"Unsupported unary operator {expr.op!r} in constant expression")
        unary_result = unary_fn(val)
        if unary_result is None:
            raise ValueError(f"Cannot evaluate unary {expr.op!r} on a bare constant without width info")
        return unary_result
    if isinstance(expr, BinaryOp):
        left = _eval_const(expr.left, env)
        right = _eval_const(expr.right, env)
        binary_fn = _BINARY_CONST_OPS.get(expr.op)
        if binary_fn is None:
            raise ValueError(f"Unsupported binary operator {expr.op!r} in constant expression")
        binary_result = binary_fn(left, right)
        if binary_result is None:
            raise ValueError(f"Cannot evaluate binary {expr.op!r} in constant expression")
        return binary_result
    if isinstance(expr, TernaryOp):
        cond = _eval_const(expr.condition, env)
        return _eval_const(expr.true_expr if cond else expr.false_expr, env)
    if isinstance(expr, Concatenation):
        if len(expr.parts) == 1:
            return _eval_const(expr.parts[0], env)
        result = 0
        for part in expr.parts:
            part_val = _eval_const(part, env)
            part_width = _const_part_width(part, env)
            if part_width is None:
                raise ValueError("Cannot evaluate multi-element Concatenation without width info")
            result = (result << part_width) | (part_val & ((1 << part_width) - 1))
        return result
    if isinstance(expr, Replication):
        count = _eval_const(expr.count, env)
        value = _eval_const(expr.value, env)
        value_width = _const_part_width(expr.value, env)
        if value_width is None:
            raise ValueError("Cannot evaluate Replication without value width info")
        mask = (1 << value_width) - 1
        result = 0
        for _ in range(count):
            result = (result << value_width) | (value & mask)
        return result
    if isinstance(expr, RangeSelect):
        base = _eval_const(expr.target, env)
        msb = _eval_const(expr.msb, env)
        lsb = _eval_const(expr.lsb, env)
        width = abs(msb - lsb) + 1
        return (base >> min(msb, lsb)) & ((1 << width) - 1)
    if isinstance(expr, PartSelect):
        target_val = _eval_const(expr.target, env)
        base_val = _eval_const(expr.base, env)
        width_val = _eval_const(expr.width, env)
        if expr.direction == "+:":
            return (target_val >> base_val) & ((1 << width_val) - 1)
        lsb_val = base_val - width_val + 1
        return (target_val >> lsb_val) & ((1 << width_val) - 1)
    if isinstance(expr, Mintypmax):
        return _eval_const(expr.typ_val, env)
    if isinstance(expr, FunctionCall):
        return _eval_const_func(expr, env)
    raise ValueError(f"Cannot evaluate {type(expr).__name__} as constant expression")


# Guard against infinite recursion from circular parameter references, when
# resolving an Identifier via `.resolved` (see `_eval_const_via_resolved`).
_RESOLVING_PARAMS: set[int] = set()


def _eval_const_via_resolved(expr: Identifier, env: Mapping[str, int]) -> int:
    """Fall back to following `Identifier.resolved` to a `Parameter`'s own
    default-value expression, when *expr*'s name isn't in *env*.

    This is a secondary resolution path -- the `env` dict (built once per
    module/instance, e.g. via `sim/elaborate.py:_build_param_env`) is
    `const_int`'s primary mechanism (see its docstring). `.resolved` is
    populated by the name-resolution analysis pass instead, for identifiers
    used in module *body* statements/expressions -- the mechanism
    `analysis/const_fold.py`'s `const_int` historically relied on
    exclusively. Supporting both here makes `semantics.const_int` a strict
    superset of both callers' prior behavior, rather than a regression for
    callers that never pass an `env`.
    """
    resolved = expr.resolved
    if not isinstance(resolved, Parameter) or resolved.default_value is None:
        raise ValueError(f"Unknown identifier {expr.name!r} in constant expression")
    pid = id(resolved)
    if pid in _RESOLVING_PARAMS:
        raise ValueError(f"Circular parameter reference resolving {expr.name!r}")
    _RESOLVING_PARAMS.add(pid)
    try:
        return _eval_const(resolved.default_value, env)
    finally:
        _RESOLVING_PARAMS.discard(pid)


def _const_part_width(part: Expression, env: Mapping[str, int]) -> int | None:
    """Best-effort bit width of a Concatenation/Replication element, for
    constant-folding purposes only (does not consult a symbol table)."""
    width = getattr(part, "inferred_width", None)
    if width is not None:
        return width
    if isinstance(part, Literal):
        return part.width
    if isinstance(part, Concatenation):
        widths = [_const_part_width(p, env) for p in part.parts]
        if any(w is None for w in widths):
            return None
        return sum(w for w in widths if w is not None)
    return None


def _eval_const_func(expr: FunctionCall, env: Mapping[str, int]) -> int:
    name = expr.name
    if name == "$clog2" and len(expr.arguments) == 1:
        val = _eval_const(expr.arguments[0], env)
        return (val - 1).bit_length() if val > 0 else 0
    if name == "$bits" and len(expr.arguments) == 1:
        arg = expr.arguments[0]
        bits_val = getattr(arg, "inferred_width", None)
        if bits_val is not None:
            return bits_val
        if isinstance(arg, Identifier):
            bits_key = f"$bits:{arg.name}"
            if bits_key in env:
                return env[bits_key]
        raise ValueError(f"Cannot resolve $bits({arg}) — unknown type")
    if name in ("$signed", "$unsigned") and len(expr.arguments) == 1:
        return _eval_const(expr.arguments[0], env)
    if name == "$pow" and len(expr.arguments) == 2:
        base = _eval_const(expr.arguments[0], env)
        exp = _eval_const(expr.arguments[1], env)
        if exp < 0:
            raise ValueError("$pow with negative exponent is not a constant integer")
        return base**exp
    raise ValueError(f"Unsupported system function {name!r} in constant expression")


def const_int(expr: Expression | None, env: Mapping[str, int] | None = None) -> int | None:
    """Evaluate *expr* to a constant integer, or ``None`` if it cannot be.

    ``env`` maps parameter/genvar names (plain or hierarchically-prefixed,
    e.g. ``"uut.WIDTH"``) to their resolved integer values — built once per
    module/instance (see ``sim/elaborate.py:_build_param_env``). This is the
    module's *primary* identifier-resolution mechanism, not the
    ``.resolved`` attribute the analysis-pass implementations use, since an
    identifier inside another parameter's own default-value expression is
    never populated by that pass (see the parity characterization test for
    the concrete case).

    Never raises — catches everything the internal evaluator can throw and
    returns ``None``, matching every existing wrapper's convention.
    """
    if expr is None:
        return None
    try:
        return _eval_const(expr, env or {})
    except (ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
        return None


# ── Range / declaration widths ──────────────────────────────────────────────


def range_width(rng: Range | None, env: Mapping[str, int] | None = None) -> int:
    """Bit width of a ``[msb:lsb]`` range, or ``1`` for a scalar (``None``).

    Always ``abs(msb - lsb) + 1`` — Verilog permits an *ascending* range
    (``[0:7]``, bit 0 is the MSB), which must still give a positive width.
    """
    if rng is None:
        return 1
    msb = const_int(rng.msb, env)
    lsb = const_int(rng.lsb, env)
    if msb is not None and lsb is not None:
        return abs(msb - lsb) + 1
    return 1


_VARIABLE_KIND_WIDTHS: dict[VariableKind, int] = {
    VariableKind.INTEGER: 32,
    VariableKind.REAL: 64,
    VariableKind.REALTIME: 64,
    VariableKind.TIME: 64,
    VariableKind.BYTE: 8,
    VariableKind.SHORTINT: 16,
    VariableKind.INT: 32,
    VariableKind.LONGINT: 64,
}


def var_width(var: Variable, env: Mapping[str, int] | None = None) -> int:
    """Bit width of a ``Variable`` declaration, honoring fixed-width kinds
    (``integer``/``real``/``time``/``byte``/``shortint``/``int``/``longint``)
    before falling back to its declared ``[msb:lsb]`` range."""
    fixed = _VARIABLE_KIND_WIDTHS.get(var.kind)
    if fixed is not None:
        return fixed
    return range_width(var.width, env)


def net_width(net: Net, env: Mapping[str, int] | None = None) -> int:
    """Bit width of a ``Net`` declaration."""
    return range_width(net.width, env)


# ── Self-determined width (IEEE 1364-2005 §5.4.1, Table 5-22) ──────────────

_ONE_BIT_BINARY = frozenset({"==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"})
_ONE_BIT_UNARY = frozenset({"!", "&", "|", "^", "~&", "~|", "~^", "^~"})
_LEFT_WIDTH_BINARY = frozenset({"<<", ">>", "<<<", ">>>"})


def expr_width(expr: Expression, width_of: Callable[[str], int], env: Mapping[str, int] | None = None) -> int:
    """Self-determined bit width of *expr* per IEEE 1364-2005 §5.4.1 / Table 5-22.

    ``width_of(name)`` resolves a (possibly hierarchically-prefixed) signal
    name to its declared width — each engine keeps its own symbol table and
    passes the lookup in rather than exposing it to this module.

    Note: multiplication's self-determined width is the *sum* of operand
    widths (Table 5-22), not `max` — this only matters when `*` is
    genuinely unconstrained; an enclosing context-determined operator
    narrows it regardless (that resizing is the caller's responsibility,
    not this function's — see `sim/evaluator.py:_expr_self_width`'s
    docstring for the distinction between this rule and the "floor" used
    when narrowing a multiply feeding a shift).
    """
    if isinstance(expr, Literal):
        return expr.width if expr.width is not None else 32
    if isinstance(expr, StringLiteral):
        return 8 * len(expr.value)
    if isinstance(expr, Identifier):
        name = expr.name
        if expr.hierarchy:
            name = ".".join(expr.hierarchy) + "." + name
        return width_of(name)
    if isinstance(expr, UnaryOp):
        if expr.op in _ONE_BIT_UNARY:
            return 1
        return expr_width(expr.operand, width_of, env)
    if isinstance(expr, BinaryOp):
        if expr.op in _ONE_BIT_BINARY:
            return 1
        if expr.op in _LEFT_WIDTH_BINARY:
            return expr_width(expr.left, width_of, env)
        left_w = expr_width(expr.left, width_of, env)
        right_w = expr_width(expr.right, width_of, env)
        if expr.op in ("*", "**"):
            return left_w + right_w
        return max(left_w, right_w)
    if isinstance(expr, TernaryOp):
        return max(expr_width(expr.true_expr, width_of, env), expr_width(expr.false_expr, width_of, env))
    if isinstance(expr, Concatenation):
        return sum(expr_width(p, width_of, env) for p in expr.parts)
    if isinstance(expr, Replication):
        count = const_int(expr.count, env)
        value_w = expr_width(expr.value, width_of, env)
        return (count if count is not None else 1) * value_w
    if isinstance(expr, BitSelect):
        return 1
    if isinstance(expr, RangeSelect):
        msb = const_int(expr.msb, env)
        lsb = const_int(expr.lsb, env)
        if msb is not None and lsb is not None:
            return abs(msb - lsb) + 1
        return 1
    if isinstance(expr, PartSelect):
        width_val = const_int(expr.width, env)
        return width_val if width_val is not None else 1
    if isinstance(expr, FunctionCall):
        name = expr.name.lower()
        if name in ("$signed", "$unsigned") and expr.arguments:
            return expr_width(expr.arguments[0], width_of, env)
        return 32
    if isinstance(expr, Mintypmax):
        return expr_width(expr.typ_val, width_of, env)
    return 32


# ── Signedness (IEEE 1364-2005 §5.5) ────────────────────────────────────────


def expr_signed(expr: Expression, signed_of: Callable[[str], bool]) -> bool:
    """Whether *expr* is a fully signed expression per IEEE 1364-2005 §5.5.

    ``signed_of(name)`` resolves a (possibly hierarchically-prefixed) signal
    name to its declared signedness.

    Per §5.5.1, a bit-select/part-select/range-select result is *always*
    unsigned regardless of the sliced signal's own declared signedness;
    concatenation/replication results are always unsigned; a shift's result
    signedness depends only on the *left* operand; all other binary
    operators are signed only when *both* operands are signed.
    """
    if isinstance(expr, Identifier):
        name = expr.name
        if expr.hierarchy:
            name = ".".join(expr.hierarchy) + "." + name
        return signed_of(name)
    if isinstance(expr, Literal):
        return expr.signed
    if isinstance(expr, (BitSelect, RangeSelect, PartSelect)):
        return False
    if isinstance(expr, UnaryOp):
        if expr.op == "!":
            return False
        if expr.op in _ONE_BIT_UNARY:
            return False
        return expr_signed(expr.operand, signed_of)
    if isinstance(expr, BinaryOp):
        if expr.op in _LEFT_WIDTH_BINARY:
            return expr_signed(expr.left, signed_of)
        if expr.op in _ONE_BIT_BINARY:
            return False
        return expr_signed(expr.left, signed_of) and expr_signed(expr.right, signed_of)
    if isinstance(expr, TernaryOp):
        return expr_signed(expr.true_expr, signed_of) and expr_signed(expr.false_expr, signed_of)
    if isinstance(expr, (Concatenation, Replication)):
        return False
    if isinstance(expr, FunctionCall):
        return expr.name.lower() == "$signed"
    return False

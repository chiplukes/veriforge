"""Width inference for Verilog expression trees.

Infers and populates ``Expression.inferred_width`` for every expression
in a design, following IEEE 1364-2005 width rules.

Usage::

    from veriforge.analysis import analyze_design
    from veriforge.analysis.width_inference import infer_widths

    analyze_design(design)   # resolve names first
    infer_widths(design)     # then infer widths

    # After inference, every expression has inferred_width set:
    expr = module.continuous_assigns[0].rhs
    print(expr.inferred_width)  # e.g. 8

Width rules (IEEE 1364-2005, Section 5.4.1):

  Unsized integer literal        → 32 bits
  Sized literal  (e.g. 8'hFF)   → explicit width
  Identifier                     → resolved declaration width (or 32)
  Unary  ~, -                    → operand width
  Unary  !, &, |, ^, ~&, ~|, ~^ → 1 (reduction / logical)
  Binary +, -, *, /, %, **       → max(left, right)
  Binary &, |, ^                 → max(left, right)
  Binary <<, >>                  → left width
  Binary ==, !=, <, >, <=, >=   → 1
  Binary &&, ||                  → 1
  Binary ===, !==               → 1
  Ternary  ? :                   → max(true, false)
  Concatenation {a, b}           → sum of parts
  Replication  {n{x}}            → n * value_width  (n must be constant)
  BitSelect    a[i]              → 1
  RangeSelect  a[m:l]            → m - l + 1  (constants)
  PartSelect   a[b +: w]        → w  (constant)
  FunctionCall                   → return width if known, else 32
  StringLiteral                  → 8 * len(string)
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
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port
from ..model.variables import Variable, VariableKind

# Operators that always produce a 1-bit result
_ONE_BIT_BINARY = frozenset({"==", "!=", "<", "<=", ">", ">=", "&&", "||", "===", "!=="})
_ONE_BIT_UNARY = frozenset({"!", "&", "|", "^", "~&", "~|", "~^"})

# Operators where result width = max(left, right)
_MAX_WIDTH_BINARY = frozenset({"+", "-", "*", "/", "%", "**", "&", "|", "^", "^~", "~^"})

# Operators where result width = left operand width
_LEFT_WIDTH_BINARY = frozenset({"<<", ">>", "<<<", ">>>"})

# System functions with known return widths
_SYSTEM_FUNC_WIDTHS: dict[str, int] = {
    "$clog2": 32,
    "$bits": 32,
    "$size": 32,
    "$countones": 32,
    "$onehot": 1,
    "$onehot0": 1,
    "$isunknown": 1,
    "$signed": 0,  # same as operand (handled specially)
    "$unsigned": 0,  # same as operand (handled specially)
    "$time": 64,
    "$stime": 32,
    "$realtime": 64,
    "$random": 32,
}


def _range_width(rng: Range | None) -> int | None:
    """Extract integer width from a Range [msb:lsb], or None if not constant.

    Uses constant folding so parameterized ranges like ``[WIDTH-1:0]``
    are resolved when the parameter value is known.
    """
    from .const_fold import const_range_width

    return const_range_width(rng)


def _declaration_width(decl: VerilogNode) -> int | None:
    """Get the bit width from a Port, Net, Variable, or Parameter declaration."""
    if isinstance(decl, Port):
        if decl.data_type == "integer":
            return 32
        return _range_width(decl.width)

    if isinstance(decl, Net):
        return _range_width(decl.width)

    if isinstance(decl, Variable):
        if decl.kind == VariableKind.INTEGER:
            return 32
        if decl.kind == VariableKind.TIME:
            return 64
        if decl.kind in (VariableKind.REAL, VariableKind.REALTIME):
            return 64  # double-precision
        if decl.kind == VariableKind.BYTE:
            return 8
        if decl.kind == VariableKind.SHORTINT:
            return 16
        if decl.kind == VariableKind.INT:
            return 32
        if decl.kind == VariableKind.LONGINT:
            return 64
        return _range_width(decl.width)

    if isinstance(decl, Parameter):
        if decl.param_type == "integer":
            return 32
        if decl.param_type == "real":
            return 64
        if decl.width is not None:
            return _range_width(decl.width)
        # Unranged parameter — try to infer from default value
        return 32  # Verilog default for unsized parameters

    return None


def _const_int(expr: Expression) -> int | None:
    """Try to extract a constant integer value from an expression.

    Delegates to ``const_fold.const_int()`` which handles parameter
    references, arithmetic, and system functions like ``$clog2``.
    """
    from .const_fold import const_int

    return const_int(expr)


def infer_expr_width(expr: Expression) -> int | None:
    """Infer the bit width of a single expression (recursive).

    Sets ``expr.inferred_width`` and returns the width, or None if it
    cannot be determined (e.g. unresolved identifiers, parametric widths).
    """
    if expr.inferred_width is not None:
        return expr.inferred_width  # already computed

    width = _infer_width_impl(expr)
    if width is not None and width > 0:
        expr.inferred_width = width
    return width


def _infer_width_impl(expr: Expression) -> int | None:  # noqa: PLR0911, PLR0912
    """Internal width inference implementation."""

    # --- Literal ---
    if isinstance(expr, Literal):
        if expr.width is not None:
            return expr.width
        # Unsized Verilog literal defaults to 32 bits
        return 32

    # --- StringLiteral ---
    if isinstance(expr, StringLiteral):
        return 8 * len(expr.value)

    # --- Identifier ---
    if isinstance(expr, Identifier):
        if expr.resolved is not None:
            return _declaration_width(expr.resolved)
        # Unresolved — assume 32 (Verilog default for unranged)
        return None

    # --- UnaryOp ---
    if isinstance(expr, UnaryOp):
        operand_w = infer_expr_width(expr.operand)
        if expr.op in _ONE_BIT_UNARY:
            return 1
        # ~ and - preserve width
        return operand_w

    # --- BinaryOp ---
    if isinstance(expr, BinaryOp):
        left_w = infer_expr_width(expr.left)
        right_w = infer_expr_width(expr.right)

        if expr.op in _ONE_BIT_BINARY:
            return 1

        if expr.op in _LEFT_WIDTH_BINARY:
            return left_w

        if expr.op in _MAX_WIDTH_BINARY:
            if left_w is not None and right_w is not None:
                return max(left_w, right_w)
            return left_w or right_w  # best effort

        # Unknown op — best effort
        if left_w is not None and right_w is not None:
            return max(left_w, right_w)
        return left_w or right_w

    # --- TernaryOp ---
    if isinstance(expr, TernaryOp):
        infer_expr_width(expr.condition)
        true_w = infer_expr_width(expr.true_expr)
        false_w = infer_expr_width(expr.false_expr)
        if true_w is not None and false_w is not None:
            return max(true_w, false_w)
        return true_w or false_w

    # --- Concatenation ---
    if isinstance(expr, Concatenation):
        total = 0
        for part in expr.parts:
            pw = infer_expr_width(part)
            if pw is None:
                return None
            total += pw
        return total

    # --- Replication ---
    if isinstance(expr, Replication):
        count = _const_int(expr.count)
        value_w = infer_expr_width(expr.value)
        if count is not None and value_w is not None:
            return count * value_w
        return None

    # --- BitSelect ---
    if isinstance(expr, BitSelect):
        infer_expr_width(expr.target)
        infer_expr_width(expr.index)
        return 1

    # --- RangeSelect ---
    if isinstance(expr, RangeSelect):
        infer_expr_width(expr.target)
        msb_val = _const_int(expr.msb)
        lsb_val = _const_int(expr.lsb)
        if msb_val is not None and lsb_val is not None:
            return abs(msb_val - lsb_val) + 1
        return None

    # --- PartSelect ---
    if isinstance(expr, PartSelect):
        infer_expr_width(expr.target)
        infer_expr_width(expr.base)
        width_val = _const_int(expr.width)
        if width_val is not None:
            return width_val
        return None

    # --- FunctionCall ---
    if isinstance(expr, FunctionCall):
        for arg in expr.arguments:
            infer_expr_width(arg)
        if expr.is_system and expr.name in _SYSTEM_FUNC_WIDTHS:
            w = _SYSTEM_FUNC_WIDTHS[expr.name]
            if w == 0 and expr.arguments:
                # $signed/$unsigned — same width as argument
                return infer_expr_width(expr.arguments[0])
            return w
        # User function — width not determinable without function decl
        return None

    # --- Mintypmax ---
    if isinstance(expr, Mintypmax):
        return infer_expr_width(expr.typ_val)

    return None


def infer_widths_in_module(module: Module) -> None:
    """Infer widths for all expressions in a module.

    Should be called after ``resolve_names()`` so that ``Identifier.resolved``
    is populated.  Walks every expression in:
      - continuous assigns
      - always blocks (statements)
      - initial blocks (statements)
      - port default values
      - net/variable initial values
      - parameter default values
    """
    # Port default values
    for port in module.ports:
        if port.default_value:
            infer_expr_width(port.default_value)

    # Parameter defaults
    for param in module.parameters:
        if param.default_value:
            infer_expr_width(param.default_value)

    # Net initial values
    for net in module.nets:
        if net.initial_value:
            infer_expr_width(net.initial_value)

    # Variable initial values
    for var in module.variables:
        if var.initial_value:
            infer_expr_width(var.initial_value)

    # Walk all expressions in the module tree
    for node in module.walk():
        if isinstance(node, Expression):
            infer_expr_width(node)


def infer_widths(design: Design) -> None:  # cm:4f2e9b
    """Infer expression widths for all modules in a design.

    Should be called after ``resolve_names()`` (or ``analyze_design()``)
    so that ``Identifier.resolved`` is populated.

    After calling, every ``Expression`` node in the design tree will have
    its ``inferred_width`` attribute set (where determinable).
    """
    for module in design.modules:
        infer_widths_in_module(module)

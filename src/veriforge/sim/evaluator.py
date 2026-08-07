"""Expression evaluator for the Verilog simulation engine.

Walks the model's Expression tree and returns a simulated Value.
Uses flat if/elif dispatch with ``type(expr) is X`` for fast exact-type
matching (single pointer compare, no MRO walk).

The evaluator does NOT own signal state — it reads from an EvalContext
that the caller provides. This keeps the evaluator pure and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)

from .elaborate import match_assignment_pattern_layout
from .value import Value, _verilog_pow

if TYPE_CHECKING:
    pass


class EvalContext:  # cm:1f4c6a
    """Interface for reading signal values during expression evaluation.

    Subclass or duck-type with:
        read_signal(name: str) -> Value
        read_signal_node(node) -> Value
    """

    __slots__ = (
        "_dirty",
        "_functions",
        "_memories",
        "_memory_bases",
        "_memory_names",
        "_originals",
        "_signal_bases",
        "_signal_signed",
        "_signals",
        "_struct_type_map",
        "_struct_types",
        "time",
    )

    def __init__(self, signals: dict[str, Value] | None = None) -> None:
        self._signals = signals or {}
        # When not None, collects names of signals written during execution.
        # The scheduler sets this before running processes and reads it after.
        self._dirty: set[str] | None = None
        # Snapshot of signal values at region start (before first write).
        # Used to compute the TRUE dirty set after all processes finish.
        self._originals: dict[str, Value | None] | None = None
        # Current simulation time (set by scheduler/executor for $time etc.)
        self.time: int = 0
        # Signedness: signal_name -> True if declared signed (e.g. reg signed)
        # Populated during elaboration.  Consulted by _expr_signed().
        self._signal_signed: dict[str, bool] = {}
        # Struct type registry: signal_name -> StructLayout
        # Populated during elaboration for struct-typed variables.
        self._struct_types: dict[str, object] = {}
        # Typedef registry: struct type name -> StructLayout
        self._struct_type_map: dict[str, object] = {}
        # Memory arrays: name -> (list[Value], elem_width)
        # For `reg [7:0] mem [0:255]`, stores 256 Value elements.
        self._memories: dict[str, tuple[list[Value], int]] = {}
        self._memory_names: set[str] = set()
        # Non-zero packed base offsets on memory elements: memory_name -> lsb_offset
        self._memory_bases: dict[str, int] = {}
        # Non-zero base offsets: signal_name -> lsb_offset
        # For signals declared with non-zero LSB (e.g. logic [31:1] foo),
        # stores the LSB so bit/range selects can adjust indices.
        self._signal_bases: dict[str, int] = {}
        # User-defined function registry: function_name -> FunctionDecl.
        # Populated during elaboration (mirrors the executor's own
        # `_function_map`, populated alongside it in `scheduler.py`) so
        # `_expr_self_width` can look up a user-defined function CALL's
        # own real return width instead of a hardcoded fallback -- see
        # that function's FunctionCall case for the concrete Icarus-
        # confirmed repro this fixes.
        self._functions: dict[str, object] = {}

    def read_signal(self, name: str) -> Value:
        """Read a signal by name.  Returns x(1) if unknown.

        Supports memory array element access via ``"MEM[idx]"`` syntax.
        """
        v = self._signals.get(name)
        if v is not None:
            return v
        # Try memory array element: "MEM[idx]"
        if "[" in name:
            bracket = name.index("[")
            mem_name = name[:bracket]
            mem = self._memories.get(mem_name)
            if mem is not None and name.endswith("]"):
                data, _ew = mem
                idx = int(name[bracket + 1 : -1])
                if 0 <= idx < len(data):
                    return data[idx]
        struct_val = _resolve_struct_field_value(name, self)
        if struct_val is not None:
            return struct_val
        return Value.x(1)

    def write_signal(self, name: str, value: Value) -> None:
        """Write a signal by name (for blocking assigns)."""
        if "[" in name and name.endswith("]"):
            bracket = name.index("[")
            mem_name = name[:bracket]
            mem = self._memories.get(mem_name)
            if mem is not None:
                data, elem_width = mem
                idx = int(name[bracket + 1 : -1])
                if 0 <= idx < len(data):
                    data[idx] = value.resize(elem_width) if value.width != elem_width else value
                    originals = self._originals
                    if originals is not None and mem_name not in originals:
                        originals[mem_name] = Value(0)
                    return
        old = self._signals.get(name)
        self._signals[name] = value
        # Record the original value (before ANY write in this region)
        # so the scheduler can compute the true dirty set by comparing
        # final values against these originals.  This correctly handles
        # combinational blocks that write A=0 then A=1 — the net effect
        # is compared against the pre-region value, not intermediate ones.
        originals = self._originals
        if originals is not None and name not in originals:
            originals[name] = old


def _resolve_memory_index(index_spec: int | str, ctx: EvalContext) -> int | None:
    """Resolve a memory index from a literal or simple signal name."""
    if isinstance(index_spec, int):
        return index_spec
    try:
        return int(index_spec, 0)
    except ValueError:
        pass
    idx_val = ctx.read_signal(index_spec)
    if idx_val.is_defined:
        return int(idx_val)
    return None


def _identifier_name(expr: Identifier) -> str:
    """Return the fully qualified identifier name."""
    if expr.hierarchy:
        return ".".join(expr.hierarchy) + "." + expr.name
    return expr.name


def _select_base(target: Expression, ctx: EvalContext) -> int:
    """Return the packed-range LSB base for scalar or memory-element selects."""
    if type(target) is Identifier:
        return ctx._signal_bases.get(_identifier_name(target), 0)
    if type(target) is BitSelect and type(target.target) is Identifier:
        tname = _identifier_name(target.target)
        if tname in ctx._memory_names:
            return ctx._memory_bases.get(tname, 0)
    return 0


def _resolve_struct_field_value(name: str, ctx: EvalContext) -> Value | None:
    """Resolve nested struct field access from a signal or memory-backed base."""
    from .elaborate import resolve_struct_storage_access  # noqa: PLC0415

    access = resolve_struct_storage_access(name, ctx._struct_types, ctx._signals, ctx._memory_names)
    if access is None:
        return None
    storage_name, storage_index_spec, offset, width = access
    if storage_index_spec is None:
        base_val = ctx._signals.get(storage_name)
    else:
        mem = ctx._memories.get(storage_name)
        if mem is None:
            return None
        mem_data, _elem_width = mem
        storage_index = _resolve_memory_index(storage_index_spec, ctx)
        if storage_index is None:
            return None
        if storage_index < 0 or storage_index >= len(mem_data):
            return None
        base_val = mem_data[storage_index]
    if base_val is None:
        return None
    return base_val[offset + width - 1 : offset]


def _resolve_struct_write_target(name: str, ctx: EvalContext) -> tuple[str, int | None, int, int, Value] | None:
    """Resolve nested struct field writes against a signal or memory-backed base."""
    from .elaborate import resolve_struct_storage_access  # noqa: PLC0415

    access = resolve_struct_storage_access(name, ctx._struct_types, ctx._signals, ctx._memory_names)
    if access is None:
        return None
    storage_name, storage_index_spec, offset, width = access
    if storage_index_spec is None:
        base_val = ctx._signals.get(storage_name)
        storage_index = None
    else:
        mem = ctx._memories.get(storage_name)
        if mem is None:
            return None
        mem_data, _elem_width = mem
        storage_index = _resolve_memory_index(storage_index_spec, ctx)
        if storage_index is None:
            return None
        if storage_index < 0 or storage_index >= len(mem_data):
            return None
        base_val = mem_data[storage_index]
    if base_val is not None:
        return storage_name, storage_index, offset + width - 1, offset, base_val
    return None


def _struct_layout_for_type(type_name: str | None, ctx: EvalContext):
    """Resolve a struct typedef name to its layout."""
    if not type_name:
        return None
    bare = type_name.rsplit("::", 1)[-1] if "::" in type_name else type_name
    return ctx._struct_type_map.get(bare)


def _concat_values(parts: list[Value]) -> Value:
    """Concatenate MSB-first values into a single packed Value."""
    if not parts:
        return Value(0, width=0)
    result = parts[0]
    for part in parts[1:]:
        result = result.concat(part)
    return result


class ExpressionEvaluator:  # cm:7e8b5d
    """Walk an Expression tree and compute a Value.

    Uses flat if/elif with ``type(expr) is X`` for exact-type dispatch.
    ``type()`` is a single C-level pointer deref; ``is`` is a pointer
    compare — together they avoid the MRO walk that ``isinstance()``
    performs.  The hot-path types (Identifier, Literal, BinaryOp) are
    tested first.
    """

    __slots__ = ("_executor", "_literal_cache")

    def __init__(self) -> None:
        # Cache: Literal object id -> Value.  Literals are constants; their
        # Value never changes, so we can compute it once and reuse it.
        self._literal_cache: dict[int, Value] = {}
        # Back-reference to StatementExecutor for user-defined function calls.
        # Set by StatementExecutor.__init__.
        self._executor: object | None = None

    def eval(  # noqa: PLR0911, PLR0912, PLR0915
        self, expr: Expression, ctx: EvalContext, width: int = 0, signed_override: bool | None = None
    ) -> Value:
        """Evaluate an expression tree and return its Value.

        *width* is the context-determined bit-width (e.g. from an
        assignment target).  When non-zero it widens operands of
        context-determined operators (arithmetic, bitwise, shift-left-
        operand) before evaluation — matching IEEE 1364-2005 §5.4.1.

        *signed_override*, when not None, replaces `_expr_signed()` for
        every extension decision made while evaluating *expr* (and
        propagates unchanged through nested context-determined operators).
        This models the conditional (ternary) operator's IEEE 1364-2005
        §5.5.1 rule: both of ITS branches are extended using ONE combined
        signedness (signed only if BOTH branches are signed) — not each
        branch's own individual signedness — and this combined signedness
        governs every extension nested within either branch, not just the
        branch's own immediate value. A ternary establishes a *fresh*
        override for its own branches (see the TernaryOp case below),
        discarding whatever override may have been active from further out.
        """
        etype = type(expr)

        # -- Hot path: Identifier (most frequent) ------------------
        if etype is Identifier:
            # Inlined read_signal: skip method-call overhead.
            name = expr.name
            # Hierarchical identifier: reconstruct full dotted name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            v = ctx._signals.get(name)
            if v is not None:
                if width and v.width < width:
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr, ctx)
                    if eff_signed:
                        return v.sign_extend(width)
                    return v.resize(width)
                return v
            # Check for struct field access: "base.field"
            struct_val = _resolve_struct_field_value(name, ctx)
            if struct_val is not None:
                return struct_val
            return Value.x(1)

        # -- Hot path: Literal (cached) ----------------------------
        if etype is Literal:
            lit_id = id(expr)
            v = self._literal_cache.get(lit_id)
            if v is None:
                v = self._eval_literal(expr)
                self._literal_cache[lit_id] = v
            # A declared-signed literal (e.g. `4'sb1000` = -8) whose own
            # top bit is 1 needs sign-extension when nested inside a wider
            # context (a $signed()-wrapped/ternary-forced-signed operand)
            # -- same gap as BitSelect/RangeSelect/PartSelect above, and
            # the identical bug already fixed in the compiled engine's
            # wide emitter (`_wide_emitter.py`'s Literal case). The cache
            # only ever stores the self-determined value, never the
            # extended one, so different call sites requesting different
            # widths for the same literal AST node stay correct.
            if width and v.width < width:
                eff_signed = signed_override if signed_override is not None else expr.signed
                return v.sign_extend(width) if eff_signed else v.resize(width)
            return v

        # -- Hot path: BinaryOp ------------------------------------
        if etype is BinaryOp:
            op = expr.op
            # `&&`/`||` produce a 1-bit result and their operands are each
            # independently self-determined -- truthiness doesn't need a
            # shared width between the two sides (unlike comparisons
            # below), so no extension/propagation is needed here.
            if op in ("&&", "||"):
                left = self.eval(expr.left, ctx)
                right = self.eval(expr.right, ctx)
            # Comparison/equality ops produce a 1-bit RESULT (self-
            # determined outward), but their TWO OPERANDS are mutually
            # context-determined between each other (IEEE 1364-2005 §5.5.2:
            # "The relational and equality operators have operands that
            # are neither fully self-determined nor fully context-
            # determined. The operands shall affect each other as if they
            # were context-determined operands with a result type and
            # size ... determined from them" -- i.e. via §5.5.1's normal
            # combining rule, "if any operand is unsigned, the result is
            # unsigned" -- NOT each operand's own individual signedness).
            # This is exactly `/ %`'s "combined signedness governs BOTH
            # operands uniformly" model, not `+ - *`'s "each operand uses
            # its own signedness" model: `both_signed` is computed once
            # and forced as `signed_override` into BOTH recursive `eval()`
            # calls, propagating into whatever nested operator either
            # operand is (so e.g. a nested unary `-` sees the comparison's
            # OWN combined decision, not its operand's individual type,
            # exactly like a ternary's combined signedness overrides its
            # branches) -- and each operand is evaluated directly AT the
            # shared target width (not its own self-width, resized
            # afterward) so that width propagates all the way down for
            # the SAME "unary `-` must negate at its final width, not
            # self-width-then-extend" reason established for the
            # arithmetic operand-extension redesign above. Confirmed
            # wrong (individual-signedness version) against Icarus for
            # `(a5[5:2] < a0)` (a5[5:2] an unsigned part-select, a0 a
            # signed 1-bit register): sign-extending `a0` by its OWN
            # signedness gave a different comparison outcome than zero-
            # extending it per the comparison's COMBINED (unsigned, since
            # not both signed) decision.
            elif op in ("==", "!=", "===", "!==", "<", "<=", ">", ">="):
                target = max(_expr_self_width(expr.left, ctx), _expr_self_width(expr.right, ctx))
                both_signed = _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx)
                left = self.eval(expr.left, ctx, target, both_signed)
                right = self.eval(expr.right, ctx, target, both_signed)
                # `_expr_self_width` is a STATIC, AST-shape-based estimate
                # (e.g. it falls back to a bare `1` for a RangeSelect whose
                # msb/lsb aren't literal constants -- common for a
                # parameter-expression bit range like
                # `[MSB:GRANULARITY+LSB]` in generated/parameterized RTL)
                # -- it can UNDER-estimate an operand's true width relative
                # to what that operand's own `eval()` call actually
                # computes (which derives the real width dynamically from
                # the msb/lsb VALUES, always correct). Only ever WIDEN here
                # (mirrors the `if result.width < width` guard used by
                # every other "extend to width" tail in this file) --
                # never narrow a genuinely-wider-than-`target` operand back
                # down: `.resize()`/`.sign_extend()` to a width smaller
                # than the operand's own already-correct width would
                # actively mask away real bits, not just "relabel" it,
                # corrupting the comparison. Confirmed wrong (cross-
                # engine, against Icarus) for the ibex PMP TOR address
                # comparator (`pmp_req_addr_i[c][PMP_ADDR_MSB:
                # PMPGranularity+PMP_ADDR_LSB] > ...`), whose non-literal
                # LSB expression made `_expr_self_width` underestimate the
                # slice at 1 bit and then truncate the real ~30-bit
                # extracted value down to 1 bit before comparing.
                if left.width < target:
                    left = left.sign_extend(target) if both_signed else left.resize(target)
                if right.width < target:
                    right = right.sign_extend(target) if both_signed else right.resize(target)
            # Shift operators: only LEFT operand is context-determined
            elif op in ("<<", ">>", "<<<", ">>>"):
                left = self.eval(expr.left, ctx, width, signed_override)
                right = self.eval(expr.right, ctx)  # self-determined
                if width and left.width != width:
                    target = max(width, _expr_self_width(expr.left, ctx))
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.left, ctx)
                    left = left.sign_extend(target) if eff_signed else left.resize(target)
            # Power operator: grouped with the SHIFT row in IEEE 1364-2005
            # Table 5-22 (`>> << ** >>> <<<` -> `L(i)`), not the generic
            # `max(L(i),L(j))` arithmetic row `+ - * / %` etc. share --
            # confirmed directly against the primary spec text ("In all
            # cases, the second operand of the power operator shall be
            # treated as self-determined"). The BASE (left operand) is
            # context-determined exactly like a shift's left operand; the
            # EXPONENT (right operand) is always self-determined at its
            # own natural width, never the outer context, and (unlike a
            # shift amount, which only needs its raw magnitude) evaluated
            # at `_expr_self_width` rather than bare width=0 so a nested
            # context-determined operator within it still resizes
            # correctly before `**` runs (the same leaf-width bug already
            # fixed for every other self-determined position this
            # session). Confirmed against Icarus.
            elif op == "**":
                left = self.eval(expr.left, ctx, width, signed_override)
                if width and left.width != width:
                    target = max(width, _expr_self_width(expr.left, ctx))
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.left, ctx)
                    left = left.sign_extend(target) if eff_signed else left.resize(target)
                right = self.eval(expr.right, ctx, _expr_self_width(expr.right, ctx))
            # Bitwise ops: combine at the OPERATOR's own natural width
            # (max of the two operands' self-determined widths) first, each
            # operand extended using its OWN individual signedness only far
            # enough to align with the other -- NOT extended straight to
            # some wider outer `width` using its own signedness, which
            # would let one signed operand's sign-extension (e.g. of an X
            # value) smear across the whole outer width even when the
            # OPERATOR's own combined signedness (both operands signed) --
            # which is what should govern the outer extension -- is
            # unsigned. The result is then extended separately to `width`
            # using the whole BinaryOp's own combined signedness. Confirmed
            # against a from-scratch IEEE 1364-2005 SS5.5.2 derivation (see
            # notes/known_issues.md); mirrors the identical fix already
            # applied to the compiled engine's wide emitter.
            elif op in ("&", "|", "^", "~^", "^~"):
                # Evaluate each operand AT op_width (not the outer `width`):
                # a nested context-determined operator (e.g. a shift) needs
                # to see this operator's own op_width as ITS context in
                # order to extend correctly BEFORE running (a shift's own
                # "extend left operand" step is gated on a nonzero width
                # being passed in -- self-determined (width=0) evaluation
                # would skip it entirely, e.g. `lo | (hi << 32)` would
                # shift `hi` out completely since 32 >= hi's own un-extended
                # width). op_width itself must still come from each
                # operand's OWN self-determined width (not the outer
                # `width`), per the docstring above.
                #
                # Each operand's OWN extension (both the recursive `eval()`
                # call and the subsequent "widen to op_width" step) must
                # NEVER consult the INCOMING `signed_override` -- a bitwise
                # op's own combined signedness is entirely SELF-CONTAINED
                # (determined solely by its own two operands' individual
                # types), unlike `>>`'s left operand or a ternary branch,
                # which genuinely need an outer decision to reach in. An
                # incoming override here is meant for a DIFFERENT purpose
                # -- e.g. `%`'s divisor-widening forcing `signed_override=
                # False` because the DIVIDEND happens to be unsigned -- and
                # must only govern how THIS bitwise op's already-computed
                # RESULT gets read/extended by that outer context (the
                # `if width and result.width != width` step below), not
                # leak into a NESTED, unrelated sub-expression's (e.g. a
                # `-` operand's) own independent signed/unsigned
                # computation. Confirmed against Icarus for `(|a3[45]) %
                # (($signed(a4[23]) - a0) | 1)`: the dividend's unsigned
                # reduction forces `%`'s combined decision unsigned,
                # correctly governing how the FINAL divisor value gets
                # read, but forcing that same `False` into the `-` node
                # (both of whose own operands are genuinely, individually
                # signed) wrongly zero-extended `a0` instead of sign-
                # extending it, corrupting the divisor's own computed
                # value from 1 to a huge wraparound magnitude.
                # `width` (the OUTER destination context, when known) is
                # folded in as a FLOOR on op_width, not a replacement for
                # the max-of-operands computation above it -- needed for a
                # `~`/unary-`-` operand specifically: those must extend
                # their OWN operand to the width they're evaluated AT
                # before complementing/negating (zero-extending a narrow
                # `~` RESULT afterward is wrong -- see the UnaryOp branch's
                # own extensive comment on this). When such an operand's
                # own self-width already happens to equal the OTHER
                # operand's self-width (so op_width alone doesn't widen it
                # at all), it would otherwise complement/negate at that
                # narrow width and only get zero-extended afterward at the
                # tail below -- computing the wrong padding bits. Widening
                # op_width to the outer context up front instead lets `~`
                # (or `-`) see and extend to the TRUE final width itself,
                # before it runs. Confirmed against Icarus for `o5 |
                # ~i3[6:3]` (o5 unsigned 3 bits, `i3[6:3]` an unsigned
                # 4-bit part-select, destination 8 bits): Icarus gives
                # `11111111` (i3[6:3] zero-extended to 8 bits, THEN
                # complemented); the old op_width=4 computed `~i3[6:3]`
                # at 4 bits first (`1111`), then zero-extended the already-
                # complemented result to `00001111`.
                op_width = max(_expr_self_width(expr.left, ctx), _expr_self_width(expr.right, ctx), width or 0)
                left = self.eval(expr.left, ctx, op_width)
                right = self.eval(expr.right, ctx, op_width)
                if left.width != op_width:
                    eff_signed = _expr_signed(expr.left, ctx)
                    left = left.sign_extend(op_width) if eff_signed else left.resize(op_width)
                if right.width != op_width:
                    eff_signed = _expr_signed(expr.right, ctx)
                    right = right.sign_extend(op_width) if eff_signed else right.resize(op_width)
                result = _eval_binary_op(op, left, right)
                if width and result.width != width:
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr, ctx)
                    result = result.sign_extend(width) if eff_signed else result.resize(width)
                return result
            else:
                # Context-determined arithmetic (+,-,*,/,%): each operand
                # is computed FIRST at its OWN self-determined width
                # (respecting whatever internal signedness/cast decisions
                # it has for THAT computation, without letting the outer
                # `width` reach down into it), then SEPARATELY extended to
                # a common target width using the OPERATOR's own COMBINED
                # signedness (IEEE 1364-2005 SS5.5.2: "any context-
                # determined operand shall be the SAME TYPE AND SIZE as
                # the RESULT of the operator") -- NOT the operand's own
                # individual signedness, which would let e.g. a
                # $signed(...)-cast operand's sign-extension leak into the
                # sum even when the OPERATOR's combined signedness
                # (governed by the OTHER, unsigned operand) says the whole
                # expression should be unsigned.
                #
                # Extending to the COMMON target width BEFORE combining
                # (rather than combining at a narrower natural width and
                # extending the RESULT afterward, which works fine for
                # bitwise ops -- see above) matters specifically for
                # +/-/*//: they have a carry/borrow chain that can't be
                # computed with partial unknowns, so `Value.__add__` etc.
                # already implement "any x bit ANYWHERE in either operand
                # makes the ENTIRE result x" -- that rule must see the
                # FULL target width's worth of operand bits to correctly
                # taint the whole result; combining narrower and widening
                # the (by-then-already-resolved) result afterward would
                # incorrectly leave the destination's extended bits
                # looking definite even when a genuine x exists elsewhere
                # in a wider-context operand.
                #
                # Confirmed against Icarus for `($signed($unsigned({a1,
                # a0, a1[5]})) + (~|((-a4) ^ a1)))`: with `a0` defined,
                # the unsigned reduction operand forces the whole
                # addition's combined signedness unsigned, so the sum
                # zero-extends into the 96-bit destination regardless of
                # the cast's own sign-extend decision; with `a0` x, the
                # ENTIRE 96-bit result must be x, not just the cast
                # operand's own narrow 10-bit self-width.
                # Each operand is evaluated directly AT the full target
                # width (not its own self-width, then resized after) so
                # that a nested context-determined operator (unary `-`, a
                # further `+`/`-`, a `$signed`/`$unsigned` cast) sees that
                # target width propagate all the way down through the
                # recursive `eval()` call and can apply its OWN
                # sign/zero-extension decision (e.g. negating an unsigned
                # operand AFTER zero-extending it to the target width, not
                # before) rather than being computed at a narrower
                # self-width and passively resized afterward -- those two
                # are only equivalent for width-commuting operators, and
                # unary `-` is not one of them. Confirmed wrong against
                # Icarus for `(-a5) - {(~&(~|a7)), a2, a6[63]}` (a5
                # unsigned, 65 bits): computing `-a5` at self-width 65 then
                # zero-extending the negation result to 96 gives a
                # different (wrong) 96-bit value than zero-extending `a5`
                # to 96 bits and negating at that width, because two's-
                # complement negation does not commute with zero-extension.
                #
                # `signed_override` is intentionally NOT forwarded to the
                # per-operand `eval()` calls: it describes how the WHOLE
                # binary expression's *result* should be interpreted by an
                # even-further-out cast (handled by the shared tail below),
                # not how each individual operand's own extension should be
                # decided -- each operand always uses its own natural
                # signedness for that (IEEE 1364-2005 §5.5.2), exactly like
                # `$signed`/`$unsigned` already override only themselves.
                target = max(width, _expr_self_width(expr.left, ctx), _expr_self_width(expr.right, ctx))
                # `+ - * / %` ALL need the OPERATOR's combined signedness
                # (signed only if BOTH operands are signed, IEEE 1364-2005
                # §5.5.1) to govern EVERY operand's own extension here --
                # not each operand's own individual declared type. This
                # file used to special-case `/`/`%` alone for this,
                # reasoning that `+`/`-`/`*` are "residue-safe" (their
                # modular arithmetic gives the same answer regardless of
                # HOW each operand was individually extended, as long as
                # each operand's bit pattern is "correct" at the target
                # width) -- but that reasoning is simply wrong: sign- vs
                # zero-extending a signed operand produces a DIFFERENT
                # integer value (e.g. a 1-bit signed `1` means -1 sign-
                # extended but +1 zero-extended), and `(a - b) mod N`
                # genuinely differs depending on WHICH of those two
                # different values `a` is taken to be -- "residue-safe"
                # only holds once each operand's value is ALREADY fixed,
                # it says nothing about which extension choice fixes it
                # correctly in the first place. Per §5.5.1, when a signed
                # operand is paired with an unsigned one in `+`/`-`/`*`,
                # the WHOLE operation reads as unsigned, and the signed
                # operand's bit pattern must be read as its RAW unsigned
                # magnitude (zero-extended), not resurrected as negative
                # via its own declared type. Confirmed against Icarus for
                # `(sa - ub)` with `sa` a signed 1-bit register holding `1`
                # (i.e. -1) and `ub` an unsigned 2-bit `0`: Icarus gives
                # `1` (zero-extending `sa` to +1 first, per the pair's
                # combined-unsigned type), not `-1`/`3` (sign-extending
                # `sa` on its own, what individual-signedness extension
                # computed here before this fix) -- the same bug shape
                # already fixed for `/`/`%` above, comparisons, and
                # bitwise ops, just missed for the other three arithmetic
                # operators specifically because of this flawed argument.
                # `signed_override=both_signed` is forced into both
                # recursive `eval()` calls so this combined decision --
                # not each operand's own type -- governs every extension
                # nested within either operand too (mirrors how a
                # ternary's combined signedness overrides its branches); a
                # `$signed`/`$unsigned` cast reached within either operand
                # still wins over this override, exactly like every other
                # signed_override use in this function.
                both_signed = _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx)
                left = self.eval(expr.left, ctx, target, both_signed)
                right = self.eval(expr.right, ctx, target, both_signed)
                # Only ever WIDEN here, never narrow -- `target` is a
                # floor computed partly from `_expr_self_width`, a
                # STATIC AST-shape-based estimate that can UNDER-
                # estimate an operand's true width (e.g. a RangeSelect
                # whose msb/lsb aren't literal constants falls back to
                # a bare `1`); the operand's own `eval()` call above
                # already computed its real, correct width dynamically
                # and may legitimately be wider than this floor.
                # `.resize()`/`.sign_extend()` to a narrower width
                # would actively mask away real bits, not just
                # relabel it. Mirrors the identical fix in the
                # comparison branch above; see its docstring for the
                # concrete Icarus-confirmed repro (ibex PMP TOR
                # address comparator).
                if left.width < target:
                    left = left.sign_extend(target) if both_signed else left.resize(target)
                if right.width < target:
                    right = right.sign_extend(target) if both_signed else right.resize(target)
            # Detect signed comparison: both operands must be signed
            if op in ("<", "<=", ">", ">=") and _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx):
                result = _eval_signed_cmp(op, left, right)
            # Signed division / modulus: interpret operands as 2's-complement
            elif op in ("/", "%") and _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx):
                result = _eval_signed_divmod(op, left, right)
            # Signed power: IEEE 1364-2005 §5.5.1 ("if all operands are
            # signed, the result will be signed") plus Table 5-6's
            # negative-base/negative-exponent special values only apply
            # under a genuinely signed interpretation -- `Value.__pow__`
            # (used by the `else` branch below) always treats both
            # operands as unsigned raw bit patterns, which is correct
            # for the unsigned case (an unsigned exponent can never be
            # negative, so Table 5-6's special cells never trigger) but
            # wrong once either operand's two's-complement bit pattern is
            # meant to be read as negative.
            elif op == "**" and _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx):
                result = _eval_signed_pow(left, right)
            else:
                result = _eval_binary_op(op, left, right)
            # The RESULT must end up at exactly the caller's requested
            # `width` (mirroring the already-correct bitwise-op branch
            # above), not just whatever width `_eval_binary_op` happened
            # to produce -- two distinct gaps, both silently corrupting
            # `.concat()`'s bit-packing whenever this BinaryOp lands as a
            # ternary branch or concat member (no wider top-level
            # assignment step around to paper over it):
            #  - Comparisons/&&/|| are self-determined-always-1-bit (IEEE
            #    1364-2005 Table 5-22) -- their 1-bit result was simply
            #    never extended to a wider requested `width` at all.
            #    Confirmed wrong against Icarus for `{8'hAA, (cond ? (a ==
            #    b) : c)}` with `c` 64 bits.
            #  - Per Table 5-22, `+ - * / % &   ^ ^~ ~^` (this includes
            #    `*`, despite this codebase's `Value.__mul__` deliberately
            #    computing at the SUM of operand widths as an internal-
            #    precision detail, verified directly against the IEEE
            #    1364-2005 primary text) share the SAME self-determined
            #    `max(L(i),L(j))` rule already correctly implemented by
            #    the bitwise-op branch above -- but this branch's operand-
            #    resize step only widens each operand up to `max(width,
            #    that operand's own self-width)` and never narrows the
            #    RESULT back down afterward, so `Value.__mul__`'s wider-
            #    than-requested sum-width result leaked straight through
            #    uncorrected. Confirmed wrong against Icarus for `(-
            #    {$signed({2{a7}}), ((a3 ? a1[0] : a6[64]) * (a1[6] <
            #    a1[2:0]))})` -- both operands are 1-bit self-determined,
            #    so `_expr_self_width` (already correctly max-based for
            #    `*`, matching Icarus/Verilator) requests width=1 for the
            #    whole multiplication, but `Value.__mul__` returns a
            #    2-bit-wide result (1+1) that was never narrowed back to
            #    the 1 bit the concat member actually needed, corrupting
            #    every subsequent bit position in the concat.
            # Shifts (whose own self-determined width is `L(i)`, i.e. the
            # already-resized left operand's width, which itself can
            # exceed `width` the identical way) and signed comparison/
            # divmod above share this same tail and need the identical
            # correction. Always unsigned in its own right unless
            # signed_override or the whole expression's own combined
            # signedness (§5.5.1) says otherwise.
            if width and result.width != width:
                eff_signed = signed_override if signed_override is not None else _expr_signed(expr, ctx)
                return result.sign_extend(width) if eff_signed else result.resize(width)
            return result

        # -- UnaryOp -----------------------------------------------
        if etype is UnaryOp:
            # ~/+/- are context-determined (IEEE 1364-2005 Table 5-22):
            # resize the operand to the surrounding context width BEFORE
            # applying the operator (see the BinaryOp arithmetic branch
            # above for why this can narrow, not just widen, the operand).
            # `~` used to be treated as self-determined here (evaluate at
            # the operand's own width, then let zero-extension happen at
            # the assignment site) -- that's wrong for unsigned operands,
            # since zero-extension doesn't commute with bitwise complement
            # (only sign-extension does), confirmed against Icarus/Verilator
            # (see notes/known_issues.md).
            if expr.op in ("~", "+", "-"):
                # This "compute at the operand's own fixed width, THEN
                # extend the RESULT" special case applies to `~` ONLY, not
                # unary `-` (despite both being grouped together above as
                # "context-determined") -- the two behave differently
                # under width-extension precisely BECAUSE `~` is a
                # bitwise, per-bit-independent operation while `-` is a
                # genuine two's-complement ARITHMETIC negation:
                # zero-extending a 1-bit value and THEN complementing
                # flips all the newly-added padding bits too (wrong --
                # `~` must run at the fixed width first, confirmed against
                # Icarus for `$signed(~({a0, a6, a0} && a7))`), but
                # zero-extending a value and THEN negating gives exactly
                # the modular two's-complement wraparound representation
                # of "minus that value" at the wider width -- which is
                # what real hardware (and Icarus) actually computes,
                # confirmed wrong the other way (compute-at-1-bit-then-
                # extend-result gives `1`, not Icarus's `all-ones`/-1) for
                # `-(~&{2{(a5[5:2] < a0)}})` widened into a 96-bit
                # destination. So unary `-` (like `+`, a no-op either way)
                # always falls through to the normal context-determined
                # path below -- it must NEVER take this fixed-width
                # shortcut, even when its operand is itself a comparison/
                # reduction/&&/||/! result.
                if expr.op == "~" and _is_fixed_self_determined(expr.operand):
                    operand = self.eval(expr.operand, ctx)
                    result = _eval_unary_op(expr.op, operand)
                    if width and result.width < width:
                        eff_signed = signed_override if signed_override is not None else _expr_signed(expr, ctx)
                        return result.sign_extend(width) if eff_signed else result.resize(width)
                    return result
                operand = self.eval(expr.operand, ctx, width, signed_override)
                if width and operand.width != width:
                    target = max(width, _expr_self_width(expr.operand, ctx))
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.operand, ctx)
                    operand = operand.sign_extend(target) if eff_signed else operand.resize(target)
            else:
                # `!` and the reduction ops (&, |, ^, ~&, ~|, ~^, ^~) are
                # themselves self-determined (always 1 bit), but their
                # OPERAND is still self-determined too (IEEE 1364-2005
                # §5.4.1) -- its OWN natural width is the context that
                # resizes any nested context-determined operator (~,
                # arithmetic, a ternary) within it. Evaluating with
                # width=0 here (as opposed to the operand's self-width)
                # leaves such a nested operator entirely unresized -- the
                # SAME bug already fixed for Concatenation/Replication
                # members above, just one operator type over. Confirmed
                # wrong against Icarus for
                # `!(~(a2[2] ? a6[49] : a6[38:9]))`, where `~`'s operand
                # (the ternary, self-width 30) must be evaluated at that
                # full 30-bit width before `~` runs, not at whichever
                # branch's own (possibly narrower, e.g. 1-bit) width the
                # ternary happens to return when given no context.
                operand = self.eval(expr.operand, ctx, _expr_self_width(expr.operand, ctx))
                result = _eval_unary_op(expr.op, operand)
                # The OPERATOR's own 1-bit RESULT must still be extended
                # to the caller's requested `width` when this UnaryOp is
                # itself embedded in a wider context (a ternary branch
                # whose other branch is wider, a concat member wrapped in
                # a further context-determined operator, etc.) -- this was
                # simply never done here, unlike every sibling
                # self-determined-fixed-width case (`~`/unary `-` on such
                # an operand above, BinaryOp bitwise-op results) which all
                # resize their RESULT after computing it at its own fixed
                # width. Always unsigned in its own right (IEEE
                # 1364-2005 §5.5.1), except when signed_override forces
                # sign-extension (e.g. `$signed(~&a)` embedded wider).
                # Confirmed wrong against Icarus for `(a1[2:1] ? a3 :
                # (~&{2{a4}}))` used as a concat member alongside a wider
                # sibling -- the reduction's 1-bit result was silently
                # merged into the concat's bit-packing as if it were only
                # 1 bit wide instead of the ternary's own 63-bit width,
                # corrupting every bit position from there on.
                if width and result.width < width:
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr, ctx)
                    return result.sign_extend(width) if eff_signed else result.resize(width)
                return result
            return _eval_unary_op(expr.op, operand)

        # -- TernaryOp ---------------------------------------------
        if etype is TernaryOp:
            # IEEE 1364-2005 §5.5.1: the conditional operator's own combined
            # signedness (signed only if BOTH branches are signed) governs
            # every extension needed while evaluating whichever branch is
            # selected -- not that branch's own individual signedness. This
            # establishes a *fresh* override for both branches, replacing
            # whatever override (if any) was active from further out.
            own_signed = _expr_signed(expr, ctx)
            # The condition is reduced to a boolean the same way `!`/`&&`/
            # `||`/reduction-OR are (Value.reduce_or): a known-1 bit ANYWHERE
            # makes the condition definitely true regardless of unrelated x/z
            # bits elsewhere (e.g. a wide concat condition with one x bit and
            # other known-1 bits) -- only "definitely all-zero" is definitely
            # false, and only "no known 1, but some x/z" is truly ambiguous
            # (triggering the branch-merge below). Using raw `cond.is_defined`
            # here treated ANY x/z bit as ambiguous even when a known-1 bit
            # elsewhere already determined the outcome -- confirmed wrong
            # against Icarus for a multi-bit condition like `(|a6) ? {a6[14],
            # a2[12:6], a6} : a6[15]` where `a2[12:6]` is x but `a6`'s own
            # bits already make the concatenation condition definitely
            # nonzero.
            # The condition is self-determined (IEEE 1364-2005 Table
            # 5-22): its OWN natural width is the context that resizes any
            # nested context-determined operator (~, arithmetic, another
            # ternary) within it -- evaluating with width=0 here (as
            # opposed to the condition's self-width) leaves such a nested
            # operator entirely unresized, the SAME bug already fixed for
            # Concatenation/Replication members and the `!`/reduction-op
            # operand above, just one more leaf position. Confirmed wrong
            # against Icarus for `(($unsigned(a1[5]) ? (a4 ^ a4[1]) :
            # (~a0)) ? a0 : (^{2{a0}}))`, where `~a0`'s operand must be
            # zero-extended to the INNER ternary's own 64-bit self-width
            # (from its other branch `a4 ^ a4[1]`) before `~` runs, not
            # left at a0's own 1-bit width -- a 1-bit `~a0` (=0) makes the
            # inner ternary look falsy when the correctly-64-bit-wide
            # `~a0` (=0xFFFF...FFFE) is actually nonzero.
            cond = self.eval(expr.condition, ctx, _expr_self_width(expr.condition, ctx)).reduce_or()
            # The selected branch must be evaluated at (at least) the
            # ternary's OWN combined self-determined width -- max of both
            # branches' self-widths, per IEEE 1364-2005 Table 5-22's
            # `L(i) = L(j)` context-determined-among-each-other rule for
            # `?:` -- not whatever `width` this TernaryOp node itself
            # happened to be called with. Blindly forwarding a caller's
            # `width=0` (self-determined request, e.g. from `&&`/`||`,
            # which never propagate a shared width into their operands)
            # straight into `self.eval(branch, ctx, width, ...)` left a
            # nested context-determined operator WITHIN the selected
            # branch (e.g. `~a0`) unresized at its own narrow width (1
            # bit) instead of the ternary's true combined width (63 bits,
            # from its other branch) -- silently flipping the branch's
            # truthiness. Confirmed wrong against Icarus for
            # `(((-a4[17:9]) ? (~a0) : a3) && a4)`: `~a0` computed at
            # 1 bit (=0, falsy) instead of the ternary's own 63-bit width
            # (=0xFFF...FFE, truthy) made the whole `&&` wrongly false.
            own_width = max(width, _expr_self_width(expr.true_expr, ctx), _expr_self_width(expr.false_expr, ctx))
            if cond.is_defined:
                if cond.val:
                    return self.eval(expr.true_expr, ctx, own_width, own_signed)
                else:
                    return self.eval(expr.false_expr, ctx, own_width, own_signed)
            t = self.eval(expr.true_expr, ctx, own_width, own_signed)
            f = self.eval(expr.false_expr, ctx, own_width, own_signed)
            return _merge_xz(t, f)

        # -- Concatenation -----------------------------------------
        if etype is Concatenation:
            if not expr.parts:
                return Value(0, width=0)
            # Each member is self-determined (IEEE 1364-2005 §5.4.1): its
            # OWN natural width is the "context" that resizes any
            # context-determined operator (~, arithmetic, a nested ternary)
            # within it. Evaluating with width=0 (the eval() default) would
            # leave those nested operators entirely unresized -- confirmed
            # wrong against Icarus for e.g. `{a, (~(cond ? ~x : y))}` where
            # `~x` needs to be sign-extended to match `y`'s width *before*
            # the outer `~` runs (see notes/known_issues.md).
            parts = [self.eval(p, ctx, _expr_self_width(p, ctx)) for p in expr.parts]
            result = parts[0]
            for p in parts[1:]:
                result = result.concat(p)
            # A concatenation is always unsigned in its OWN right (IEEE
            # 1364-2005 §5.5.1), so its own aggregate result normally
            # needs no further extension -- but when the WHOLE
            # concatenation is wrapped in `$signed(...)` (signed_override
            # True, forced by an outer FunctionCall/ternary/bitwise-op)
            # and requested at a wider `width`, that aggregate needs
            # sign-extension, not zero-extension. Individual MEMBERS never
            # see signed_override (concat members are always
            # self-determined, per the self-width-only eval() calls
            # above) -- this only concerns the concat's own aggregate
            # value. Mirrors the identical fix in the compiled engine's
            # wide emitter; confirmed wrong against Icarus for
            # `{2{(a0 ? $signed({a1, a2}) : a3)}}`.
            if width and result.width < width:
                return result.sign_extend(width) if signed_override else result.resize(width)
            return result

        # -- BitSelect ---------------------------------------------
        if etype is BitSelect:
            # Memory element access: mem[addr]
            target_name = _identifier_name(expr.target) if type(expr.target) is Identifier else None
            if target_name is not None and target_name in ctx._memory_names:
                index = self.eval(expr.index, ctx)
                if index.is_defined:
                    mem_data, elem_w = ctx._memories[target_name]
                    idx = int(index)
                    result = mem_data[idx] if 0 <= idx < len(mem_data) else Value.x(elem_w)
                else:
                    _mem_data, elem_w = ctx._memories[target_name]
                    result = Value.x(elem_w)
            else:
                target = self.eval(expr.target, ctx)
                index = self.eval(expr.index, ctx)
                if index.is_defined:
                    idx = int(index)
                    idx -= _select_base(expr.target, ctx)
                    result = target[idx]
                else:
                    result = Value.x(1)
            # A bit-select is always unsigned in its own right (IEEE
            # 1364-2005 §5.5.1) -- but when nested inside a
            # $signed()-wrapped context (signed_override True, forced by an
            # outer FunctionCall/ternary/bitwise-op), a wider requested
            # `width` needs sign-, not zero-, extension. Without this, a
            # bit-select nested one level deeper than an assignment's own
            # top-level RHS (e.g. a ternary branch) never gets extended at
            # all -- confirmed wrong against Icarus.
            if width and result.width < width:
                return result.sign_extend(width) if signed_override else result.resize(width)
            return result

        # -- RangeSelect -------------------------------------------
        if etype is RangeSelect:
            target = self.eval(expr.target, ctx)
            msb = self.eval(expr.msb, ctx)
            lsb = self.eval(expr.lsb, ctx)
            if msb.is_defined and lsb.is_defined:
                m, l = int(msb), int(lsb)
                base = _select_base(expr.target, ctx)
                m -= base
                l -= base
                result = target[m:l]
            else:
                w = (int(msb) - int(lsb) + 1) if msb.is_defined and lsb.is_defined else 1
                result = Value.x(w)
            # Same signed_override reasoning as BitSelect above.
            if width and result.width < width:
                return result.sign_extend(width) if signed_override else result.resize(width)
            return result

        # -- Replication -------------------------------------------
        if etype is Replication:
            count_val = self.eval(expr.count, ctx)
            # Self-determined, same reasoning as Concatenation above.
            inner = self.eval(expr.value, ctx, _expr_self_width(expr.value, ctx))
            if count_val.is_defined:
                result = inner.replicate(int(count_val))
            else:
                result = Value.x(inner.width)
            # Same signed_override reasoning as Concatenation's own
            # aggregate result above -- a replication is always unsigned
            # in its own right, but `$signed({N{...}})` still needs its
            # aggregate sign-extended when requested at a wider width.
            if width and result.width < width:
                return result.sign_extend(width) if signed_override else result.resize(width)
            return result

        # -- AssignmentPattern -------------------------------------
        if etype is AssignmentPattern:
            if expr.named_pairs:
                layout = match_assignment_pattern_layout(expr, ctx._struct_type_map)
                if layout is None:
                    raise ValueError(f"Cannot find matching struct layout for assignment pattern: {expr!r}")
                named_values = {name: value_expr for name, value_expr in expr.named_pairs}
                parts: list[Value] = []
                for field_name, (_offset, field_width) in sorted(
                    layout.fields.items(), key=lambda item: item[1][0], reverse=True
                ):
                    field_expr = named_values.get(field_name, expr.default_value)
                    if field_expr is None:
                        parts.append(Value(0, width=field_width))
                        continue
                    field_val = self.eval(field_expr, ctx, width=field_width)
                    if field_val.width != field_width:
                        field_val = field_val.resize(field_width)
                    parts.append(field_val)
                result = _concat_values(parts)
                # An assignment pattern has no inherent sign of its own (IEEE
                # 1364-2005 -- its bits are just packed together); `signed_
                # override`, when set, comes from an enclosing `$signed()`
                # cast or forced-signed context (a ternary/bitwise-op's
                # combined signedness) reinterpreting the pattern's OWN
                # packed bit pattern as signed before extending to `width`.
                # Confirmed against cross-engine agreement (vm/vm-fast/
                # compiled all correctly sign-extend `$signed('{flag})`;
                # only this branch's unconditional `.resize()` zero-extended
                # instead) for `$signed('{flag})` with flag=1: extending an
                # 8-bit destination should give -1 (all 1s), not 1.
                if width and result.width != width:
                    return result.sign_extend(width) if signed_override else result.resize(width)
                return result

            if expr.positional:
                # Self-determined, same reasoning as Concatenation above.
                parts = [self.eval(part, ctx, _expr_self_width(part, ctx)) for part in expr.positional]
                result = _concat_values(parts)
                # Same signed_override reasoning as the named_pairs branch above.
                if width and result.width != width:
                    return result.sign_extend(width) if signed_override else result.resize(width)
                return result

            if expr.default_value is not None:
                default_width = width or self.eval(expr.default_value, ctx).width
                default_val = self.eval(expr.default_value, ctx, width=default_width)
                # Same signed_override reasoning as the named_pairs branch above.
                if width and default_val.width != width:
                    return default_val.sign_extend(width) if signed_override else default_val.resize(width)
                return default_val

            return Value(0, width=width or 1)

        # -- PartSelect --------------------------------------------
        if etype is PartSelect:
            target = self.eval(expr.target, ctx)
            base = self.eval(expr.base, ctx)
            part_w = self.eval(expr.width, ctx)
            if base.is_defined and part_w.is_defined:
                w = int(part_w)
                b = int(base)
                b -= _select_base(expr.target, ctx)
                result = target[b + w - 1 : b] if expr.direction == "+:" else target[b : b - w + 1]
            else:
                result = Value.x(1)
            # Same signed_override reasoning as BitSelect above.
            if width and result.width < width:
                return result.sign_extend(width) if signed_override else result.resize(width)
            return result

        # -- FunctionCall ------------------------------------------
        if etype is FunctionCall:
            fname = expr.name.lower()
            if fname in ("$signed", "$unsigned") and expr.arguments:
                # $signed/$unsigned are transparent to VALUE -- they only
                # mark the expression's signedness for the ENCLOSING
                # context's extension decision. `_eval_function_call`
                # (below) evaluates the argument self-determined with no
                # width, which only happens to work when $signed(...) is
                # the assignment's own top-level RHS (a SEPARATE post-hoc
                # `_maybe_sign_extend` step at the statement-executor
                # level covers for it there). Nested one level deeper --
                # e.g. a ternary branch, `$signed(a4[4:2])` inside
                # `cond ? $signed(a4[4:2]) : a3` -- that top-level cover
                # doesn't reach, and the cast's own argument never gets
                # extended to the ternary's combined width at all.
                # Confirmed wrong against Icarus for
                # `{3{(a0 ? $signed(a4[4:2]) : a3)}}`. `$signed`/
                # `$unsigned` ALWAYS force their own decision here,
                # discarding whatever signed_override was passed in from
                # further out (mirrors the compiled engine's
                # `_wide_emitter.py` FunctionCall case).
                #
                # Directly-nested casts (`$unsigned($signed(x))`) are the
                # ONE exception: the OUTERMOST cast is what should govern
                # -- unwrapping the chain here (rather than letting this
                # branch recurse and re-trigger on the inner cast) means
                # the inner cast never gets a chance to re-force its OWN
                # (now-overridden) decision. Found via statement-level
                # differential fuzzing (`test_differential_statements.py`,
                # phase 1): `$unsigned($signed((a < b)))` assigned into a
                # wide destination -- Icarus zero-extends (the outer
                # $unsigned wins), but this code previously recursed into
                # the inner $signed's own branch, which force-set
                # signed_override=True and sign-extended instead. Every
                # prior confirmation of "the cast always forces its own
                # decision" involved exactly one cast layer (a ternary or
                # bitwise-op supplying the override, never another
                # $signed/$unsigned) -- that rule is unaffected here.
                inner = expr.arguments[0]
                while (
                    isinstance(inner, FunctionCall)
                    and inner.name.lower() in ("$signed", "$unsigned")
                    and inner.arguments
                ):
                    inner = inner.arguments[0]
                # $signed/$unsigned are themselves SELF-DETERMINED (IEEE
                # 1364-2005 Table 5-22): the argument must be evaluated at
                # its OWN self-determined width, not the width requested by
                # whatever outer context-determined operator is asking for
                # this cast's value -- the cast's job is only to decide
                # sign- vs zero-extension when the (already self-width-
                # computed) result is later widened to that outer `width`.
                # Passing the outer `width` straight into evaluating
                # `inner` used to force nested context-determined operators
                # inside the cast (e.g. `%`) to propagate that OUTER width
                # into THEIR OWN operands too, which is wrong: `%`'s
                # operands should extend to `%`'s own context (bounded by
                # its own self-width when nothing external propagates in,
                # exactly as the self-determined cast wrapping it dictates)
                # not fall through to an even wider width the cast was
                # asked to eventually produce. Confirmed wrong against
                # Icarus for `$signed((a3 % (a0 | 1))) + a1` (a3 unsigned
                # 63 bits, `a0 | 1` a signed expression evaluating to -1):
                # propagating the outer 96-bit context into the modulus's
                # own operands changed the divisor's value from what the
                # modulus should see at its own (self-determined, un-
                # propagated) width.
                result = self.eval(inner, ctx, _expr_self_width(inner, ctx))
                if width and result.width != width:
                    return result.sign_extend(width) if fname == "$signed" else result.resize(width)
                return result
            return self._eval_function_call(expr, ctx)

        # -- StringLiteral -----------------------------------------
        if etype is StringLiteral:
            val = 0
            for ch in expr.value:
                val = (val << 8) | ord(ch)
            return Value(val, width=len(expr.value) * 8)

        # -- Mintypmax ---------------------------------------------
        if etype is Mintypmax:
            return self.eval(expr.typ_val, ctx)

        raise TypeError(f"Cannot evaluate expression type: {type(expr).__name__}")

    def _eval_literal(self, lit: Literal) -> Value:
        """Convert a model Literal to a simulation Value."""
        width = lit.width or 32

        # If original_text is available, it preserves per-bit x/z info
        # (e.g. 4'b1xxx -> val=8, mask=7). Check it first.
        if lit.original_text:
            try:
                return Value.from_verilog(lit.original_text)
            except ValueError:
                pass

        # All-x or all-z literal
        if lit.is_x or lit.is_z:
            return Value.x(width)

        # Numeric value
        if isinstance(lit.value, (int, float)):
            return Value(int(lit.value), width=width)

        # String value in Literal (rare — some parsed number strings)
        if isinstance(lit.value, str):
            text = lit.value.strip()
            if lit.original_text:
                try:
                    return Value.from_verilog(lit.original_text)
                except ValueError:
                    pass
            try:
                return Value(int(text, 0), width=width)
            except (ValueError, TypeError):
                return Value.x(width)

        return Value.x(width)

    def _eval_function_call(self, call: FunctionCall, ctx: EvalContext) -> Value:
        """Evaluate built-in system function calls."""
        name = call.name.lower()
        args = [self.eval(a, ctx) for a in call.arguments]

        if name == "$clog2":
            if args and args[0].is_defined:
                n = int(args[0])
                if n <= 0:
                    return Value(0, width=32)
                return Value((n - 1).bit_length(), width=32)
            return Value.x(32)

        if name == "$signed":
            if args:
                return args[0]  # type handling is at the operator level
            return Value.x(32)

        if name == "$unsigned":
            if args:
                return args[0]
            return Value.x(32)

        if name == "$bits":
            if args:
                return Value(args[0].width, width=32)
            return Value.x(32)

        if name in ("$time", "$realtime"):
            return Value(ctx.time, width=64)

        if name == "$stime":
            return Value(ctx.time & 0xFFFFFFFF, width=32)

        if name == "$random":
            import random

            return Value(random.getrandbits(32), width=32)

        # User-defined function call
        if self._executor is not None:
            func = self._executor.lookup_function(call.name)
            if func is not None:
                return self._eval_user_function(func, call, ctx)

        # Unknown function — return x
        return Value.x(32)

    def _eval_user_function(self, func, call: FunctionCall, ctx: EvalContext) -> Value:
        """Execute a user-defined function and return its result."""
        from veriforge.model.functions import FunctionDecl

        func: FunctionDecl

        # Determine return width
        ret_width = 32
        if func.return_range is not None:
            msb_v = self.eval(func.return_range.msb, ctx)
            lsb_v = self.eval(func.return_range.lsb, ctx)
            if msb_v.is_defined and lsb_v.is_defined:
                ret_width = abs(int(msb_v) - int(lsb_v)) + 1
        elif func.return_kind == "integer":
            ret_width = 32

        # Create local context: copy parent signals, add port bindings + return var
        local_signals = dict(ctx._signals)
        for i, port in enumerate(func.ports):
            if i < len(call.arguments):
                # A call's argument-to-port binding is really an implicit
                # assignment (`port = arg_expr;`), and just like every
                # other assignment in this file, the RHS must be
                # evaluated DIRECTLY at the target's own width
                # (`self.eval(expr, ctx, width=port_width)`, mirroring
                # `executor.py`'s BlockingAssign/NonblockingAssign
                # handling, `self.evaluator.eval(stmt.rhs, ctx,
                # width=lhs_w)`) -- NOT at the argument's own self-
                # determined width with a resize applied afterward (the
                # first version of this fix). Those are NOT equivalent
                # whenever the argument is itself a NESTED context-
                # determined operator (unary `-`, a further `+`/`-`/`*`,
                # a `$signed`/`$unsigned` cast): two's-complement
                # negation, in particular, does not commute with
                # extension, the same "unary `-` must negate at its
                # final width, not self-width-then-extend" principle
                # already established for ordinary context-determined
                # arithmetic elsewhere in this file. Confirmed against
                # Icarus for `fn_neg(-(!a7))` with `fn_neg(input signed
                # [15:0] a)` and `a7` = 0: negating `!a7`=1 at its own
                # 1-bit self-determined width first (mod-2 arithmetic)
                # gives 1, zero-extended to 16 bits = `1` -- wrong;
                # Icarus negates directly at the port's 16-bit context,
                # correctly giving `-1` (0xFFFF).
                #
                # `self.eval(..., ctx, port_width)` still never NARROWS a
                # result on its own for expression types whose own
                # dispatch ignores the width hint for narrowing (a
                # `RangeSelect`/`BitSelect`'s own dispatch only ever
                # WIDENS when asked, matching the "never narrow, only
                # widen" convention this file uses everywhere else) -- an
                # explicit post-hoc resize/sign_extend is therefore still
                # needed for THAT case, same as before. Confirmed against
                # Icarus for `fn_sel1(a4[27:8], a1)` with `fn_sel1(input
                # a, input [62:0] b)`: passing the 20-bit range-select
                # `a4[27:8]` into the 1-bit port `a` unchanged left it
                # nonzero (hence "truthy") regardless of its own low bit,
                # always taking the ternary body's TRUE branch -- Icarus
                # (and every other engine, none of which had this bug)
                # truncates to just `a4`'s bit 8 first, as required.
                port_width = 1
                if port.width is not None:
                    msb_v = self.eval(port.width.msb, ctx)
                    lsb_v = self.eval(port.width.lsb, ctx)
                    if msb_v.is_defined and lsb_v.is_defined:
                        port_width = abs(int(msb_v) - int(lsb_v)) + 1
                arg_val = self.eval(call.arguments[i], ctx, port_width)
                if arg_val.width != port_width:
                    eff_signed = _expr_signed(call.arguments[i], ctx)
                    arg_val = arg_val.sign_extend(port_width) if eff_signed else arg_val.resize(port_width)
                local_signals[port.name] = arg_val
            else:
                local_signals[port.name] = Value.x(1)
        # Initialize the return variable (same name as the function)
        local_signals[func.name] = Value(0, width=ret_width)

        local_ctx = EvalContext(local_signals)
        local_ctx.time = ctx.time
        local_ctx._struct_type_map.update(ctx._struct_type_map)
        local_ctx._struct_types.update(ctx._struct_types)
        local_ctx._signal_bases.update(ctx._signal_bases)
        local_ctx._signal_signed.update(ctx._signal_signed)
        local_ctx._memory_bases.update(ctx._memory_bases)
        local_ctx._memory_names.update(ctx._memory_names)
        local_ctx._memories.update(ctx._memories)
        local_ctx._functions.update(ctx._functions)
        for port in func.ports:
            layout = _struct_layout_for_type(getattr(port, "data_type", None), ctx)
            if layout is not None:
                local_ctx._struct_types[port.name] = layout
        for local_var in func.locals:
            layout = _struct_layout_for_type(getattr(local_var, "type_name", None), ctx)
            if layout is not None:
                local_ctx._struct_types[local_var.name] = layout

        # Execute the function body
        if func.body:
            self._executor.execute(func.body, local_ctx)

        # Read the return value
        return local_ctx.read_signal(func.name)


_FIXED_SELF_DETERMINED_BINOPS = frozenset({"==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"})
_FIXED_SELF_DETERMINED_UNOPS = frozenset({"&", "|", "^", "~&", "~|", "~^", "^~", "!"})


def _is_fixed_self_determined(expr: Expression) -> bool:
    """True when *expr*'s own result width is ALWAYS fixed at 1 bit
    (IEEE 1364-2005 Table 5-22's self-determined operators: comparisons,
    &&/||, reduction ops, !) regardless of any enclosing context.

    Used by the `~`/unary `-` branch of `eval()`: such an operand must
    never be widened to match `~`/`-`'s enclosing context width BEFORE the
    operator runs -- unlike a regular signal or an arithmetic operand
    (where extension commutes with the operation), extending a bitwise-
    complement or negation's operand changes which bits get flipped/
    negated. Only `~`/`-`'s own RESULT should be extended, after the
    operator runs at the operand's fixed width.
    """
    etype = type(expr)
    if etype is BinaryOp:
        return expr.op in _FIXED_SELF_DETERMINED_BINOPS
    if etype is UnaryOp:
        return expr.op in _FIXED_SELF_DETERMINED_UNOPS
    return False


# ── Self-determined width (generic max-rule, incl. for '*') ───────────


def _expr_self_width(expr: Expression, ctx: EvalContext) -> int:
    """Self-determined width of *expr*, using the max-of-operands rule for
    every binary arithmetic operator, INCLUDING multiplication -- not the
    IEEE 1364-2005 §5.4.1 sum-of-operand-widths rule '*'/'**' otherwise get.

    Used only as a floor when resizing an operand of an enclosing
    context-determined operator (see the BinaryOp/UnaryOp context-determined
    branches in `eval()`): multiplication's own "widen to the sum of operand
    widths" rule only matters when '*' is genuinely unconstrained; when it
    is itself an operand of a further context-determined operator (e.g. the
    left side of '>>'), the ENCLOSING context wins and the multiply's result
    must be narrowed to match -- mirrors `sim/vm/compiler.py`'s
    `_expr_width`, which has the same generic-max treatment for this reason,
    and which the compiled/vm-fast/reference/vm engines must all agree with.
    """
    etype = type(expr)
    if etype is Identifier:
        name = expr.name
        if expr.hierarchy:
            name = ".".join(expr.hierarchy) + "." + name
        v = ctx._signals.get(name)
        if v is not None:
            return v.width
        # Struct field access ("base.field") is not a flat `ctx._signals`
        # entry -- eval()'s own Identifier hot path already falls back to
        # `_resolve_struct_field_value` for this case (see the "Check for
        # struct field access" comment there). This helper needs the same
        # fallback: without it, a struct field's self-determined width
        # silently defaulted to 32 regardless of its true (often narrower)
        # width, which is normally harmless UNTIL that wrong width gets
        # used to force-extend an enclosing self-determined construct's
        # OWN aggregate result (e.g. a single-field-struct's Concatenation
        # wrapper, sized via this exact function, then zero-extended from
        # 16 to a bogus 32 bits before a further Replication multiplies
        # that already-corrupted value) -- confirmed wrong (regression)
        # against a `{N{struct.field}}` repro for
        # `mst_w_data_int_r = {UpsizeFactor{wr_w_q.data}}` in the PULP AXI
        # lite DW converter example.
        struct_val = _resolve_struct_field_value(name, ctx)
        if struct_val is not None:
            return struct_val.width
        return 32
    if etype is Literal:
        return expr.width or 32
    if etype is BitSelect:
        # BitSelect also represents unpacked-array ELEMENT access (e.g.
        # `arr[4]` where `arr` is `logic [31:0] arr[5]`) -- that reads a
        # full element, not a single bit, so self-width must be the
        # element's own width, not 1 (mirrors `sim/vm/compiler.py`'s
        # `_expr_width`, which already distinguishes the two cases). This
        # was previously masked by every OTHER caller of
        # `_expr_self_width` using it only as a floor alongside an outer
        # context width that already dominated the wrong `1` -- exposed
        # once a caller (bitwise-op width propagation) relies on it alone.
        if type(expr.target) is Identifier:
            tname = _identifier_name(expr.target)
            if tname in ctx._memory_names:
                _mem_data, elem_w = ctx._memories[tname]
                return elem_w
        return 1
    if etype is RangeSelect:
        if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
            return int(expr.msb.value) - int(expr.lsb.value) + 1
        return 1
    if etype is PartSelect:
        if isinstance(expr.width, Literal):
            return int(expr.width.value)
        return 1
    if etype is Concatenation:
        return sum(_expr_self_width(p, ctx) for p in expr.parts)
    if etype is Replication:
        if isinstance(expr.count, Literal):
            return int(expr.count.value) * _expr_self_width(expr.value, ctx)
        return _expr_self_width(expr.value, ctx)
    if etype is BinaryOp:
        if expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"):
            return 1
        if expr.op in ("<<", "<<<"):
            # Left-shift's self-determined width needs left_width + shift
            # amount, not just max() -- otherwise `hi << 32` (hi 31 bits)
            # gets underestimated at 32 bits instead of 63, silently
            # truncating away the shifted-in bits (mirrors
            # `sim/vm/compiler.py`'s `_expr_width`, which has the identical
            # special case and the same rationale in its own docstring).
            lw = _expr_self_width(expr.left, ctx)
            if isinstance(expr.right, Literal) and not (expr.right.is_x or expr.right.is_z):
                return lw + int(expr.right.value)
            return max(lw, _expr_self_width(expr.right, ctx))
        if expr.op in (">>", ">>>", "**"):
            # A shift's self-determined width is its LEFT operand's width
            # only -- the shift amount never contributes bits to the
            # result (mirrors `sim/vm/compiler.py`'s `_expr_width`). `**`
            # (power) shares this SAME row in IEEE 1364-2005 Table 5-22
            # (`>> << ** >>> <<<` -> `L(i)`, with the exponent always
            # self-determined) -- verified directly against the primary
            # spec text; NOT the generic `max(L(i),L(j))` row the final
            # `return` below covers for `+ - * / % & | ^ ^~ ~^`.
            return _expr_self_width(expr.left, ctx)
        return max(_expr_self_width(expr.left, ctx), _expr_self_width(expr.right, ctx))
    if etype is UnaryOp:
        if expr.op in ("&", "|", "^", "~&", "~|", "~^", "^~", "!"):
            return 1
        return _expr_self_width(expr.operand, ctx)
    if etype is TernaryOp:
        return max(_expr_self_width(expr.true_expr, ctx), _expr_self_width(expr.false_expr, ctx))
    if etype is AssignmentPattern:
        # Previously unhandled: fell through to the generic `32` default,
        # silently wrong whenever an assignment pattern's true width isn't
        # 32 -- e.g. `$signed('{flag})` (a 1-bit pattern) evaluating `inner`
        # at a bogus self-width of 32 before the `$signed` cast's own
        # sign-extend-to-context-width step ever runs, corrupting the
        # result. Confirmed against cross-engine agreement (vm/vm-fast/
        # compiled all correctly give `-1` sign-extended to 8 bits for
        # `$signed('{flag})` with flag=1; only reference, via this gap,
        # gave `1` zero-extended instead).
        if expr.named_pairs:
            layout = match_assignment_pattern_layout(expr, ctx._struct_type_map)
            if layout is not None:
                return layout.total_width
        elif expr.positional:
            return sum(_expr_self_width(part, ctx) for part in expr.positional)
        elif expr.default_value is not None:
            return _expr_self_width(expr.default_value, ctx)
        return 32
    if etype is FunctionCall:
        name = expr.name.lower()
        if name in ("$signed", "$unsigned") and expr.arguments:
            return _expr_self_width(expr.arguments[0], ctx)
        # A user-defined function call's self-determined width is its
        # OWN declared return width, not a hardcoded fallback -- the
        # fallback below is only for genuinely unresolvable calls (e.g.
        # an unknown/unregistered function name). Mirrors
        # `_eval_user_function`'s own return-width computation (msb/lsb
        # evaluated as literals; a non-literal or missing return range
        # falls back to 32, the same approximation used everywhere else
        # in this STATIC, AST-shape-based helper). Confirmed against
        # Icarus for `($unsigned(fn_sel1(a4[27:8], a1)) ^ (~^(-a4)))`
        # with `fn_sel1` declared `function [62:0] fn_sel1(...)`: the
        # old hardcoded `32` here silently truncated the XOR's own
        # `target` width computation, corrupting the zero-extension of
        # the call's real 63-bit return value into the 64-bit result.
        func = ctx._functions.get(expr.name)
        if func is not None and func.return_range is not None:
            msb, lsb = func.return_range.msb, func.return_range.lsb
            if isinstance(msb, Literal) and isinstance(lsb, Literal):
                return abs(int(msb.value) - int(lsb.value)) + 1
        return 32
    if etype is StringLiteral:
        return len(expr.value) * 8
    return 32


# ── Signed comparison helpers ─────────────────────────────────────────


def _is_signed_call(expr) -> bool:
    """True when *expr* is ``$signed(...)``."""
    return isinstance(expr, FunctionCall) and expr.name.lower() == "$signed"


def _expr_signed(expr: Expression, ctx: EvalContext, cache: dict[int, bool] | None = None) -> bool:
    """Return True if *expr* is a fully signed expression per IEEE 1364-2005 §5.5.

    When *cache* is provided (an ``id(obj) → bool`` dict), intermediate
    results are memoised to avoid re-walking shared subtrees.
    """
    if cache is not None:
        key = id(expr)
        cached = cache.get(key)
        if cached is not None:
            return cached

    etype = type(expr)

    # -- Identifier: check declared signedness of the signal --------------
    if etype is Identifier:
        name = expr.name
        if expr.hierarchy:
            name = ".".join(expr.hierarchy) + "." + name
        result = ctx._signal_signed.get(name, False)
        if cache is not None:
            cache[id(expr)] = result
        return result

    # -- Literal: signed if base is 's' (e.g. 8'shFF) --------------------
    if etype is Literal:
        result = expr.signed
        if cache is not None:
            cache[id(expr)] = result
        return result

    # -- BitSelect / RangeSelect / PartSelect: always unsigned (§5.5.1) ---
    if etype in (BitSelect, RangeSelect, PartSelect):
        if cache is not None:
            cache[id(expr)] = False
        return False

    # -- UnaryOp: signed if operand is signed, EXCEPT reduction ops --------
    # `!` and all reduction ops (&, |, ^, ~&, ~|, ~^, ^~) always produce an
    # unsigned 1-bit result regardless of the operand's own signedness
    # (IEEE 1364-2005 SS5.5.1) -- only the context-determined pass-through
    # ops (~, +, -) inherit the operand's signedness.
    if etype is UnaryOp:
        if expr.op in ("!", "&", "|", "^", "~&", "~|", "~^", "^~"):
            result = False
            if cache is not None:
                cache[id(expr)] = result
            return result
        result = _expr_signed(expr.operand, ctx, cache)
        if cache is not None:
            cache[id(expr)] = result
        return result

    # -- BinaryOp: for shift, only left operand counts; comparisons and
    # logical ops always produce an unsigned 1-bit result regardless of
    # operand signedness (IEEE 1364-2005 SS5.5.1, Table 5-22); otherwise
    # both operands must be signed ------------------------------------
    if etype is BinaryOp:
        if expr.op in ("<<", ">>", "<<<", ">>>"):
            result = _expr_signed(expr.left, ctx, cache)
        elif expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"):
            result = False
        else:
            result = _expr_signed(expr.left, ctx, cache) and _expr_signed(expr.right, ctx, cache)
        if cache is not None:
            cache[id(expr)] = result
        return result

    # -- TernaryOp: both branches must be signed --------------------------
    if etype is TernaryOp:
        result = _expr_signed(expr.true_expr, ctx, cache) and _expr_signed(expr.false_expr, ctx, cache)
        if cache is not None:
            cache[id(expr)] = result
        return result

    # -- Concatenation / Replication → always unsigned (§5.5.1) -----------
    if etype in (Concatenation, Replication):
        if cache is not None:
            cache[id(expr)] = False
        return False

    # -- FunctionCall: $signed → True, $unsigned → False, else False -------
    if etype is FunctionCall:
        result = expr.name.lower() == "$signed"
        if cache is not None:
            cache[id(expr)] = result
        return result

    # -- All other expression types (Mintypmax, StringLiteral, etc.) → unsigned
    if cache is not None:
        cache[id(expr)] = False
    return False


def _eval_signed_cmp(op: str, left: Value, right: Value) -> Value:
    """Signed relational comparison, interpreting values as two's-complement."""
    if left.mask or right.mask:
        return Value.x(1)
    a = left.as_signed()
    b = right.as_signed()
    if op == "<":
        result = a < b
    elif op == "<=":
        result = a <= b
    elif op == ">":
        result = a > b
    elif op == ">=":
        result = a >= b
    else:
        raise ValueError(f"Unknown comparison operator: {op!r}")
    return Value(1 if result else 0, width=1)


def _eval_signed_divmod(op: str, left: Value, right: Value) -> Value:
    """Signed division or modulus, interpreting values as two's-complement.

    Verilog (like C) truncates toward zero; Python's // truncates toward
    negative infinity, so we use int(a / b) for truncating-toward-zero.
    """
    if left.mask or right.mask:
        return Value.x(left.width)
    if right.val == 0:
        return Value.x(left.width)
    a = left.as_signed()
    b = right.as_signed()
    w = max(left.width, right.width)
    if op == "/":
        return Value(int(a / b), width=w)
    if op == "%":
        # Verilog: a % b = a - b * int(a / b)  (remainder matches trunc-div)
        return Value(a - b * int(a / b), width=w)
    raise ValueError(f"Unknown div/mod operator: {op!r}")


def _eval_signed_pow(left: Value, right: Value) -> Value:
    """Signed power, interpreting both operands as two's-complement.

    Result width is the BASE's own width (IEEE 1364-2005 Table 5-22's
    `L(i)` rule for `**`, mirroring the unsigned `Value.__pow__`) --
    `left` has already been resized by the caller to whatever width is
    actually needed before this runs. See `_verilog_pow` (`sim/value.py`)
    for the Table 5-6 negative-base/negative-exponent special-value
    rules this delegates to.
    """
    w = left.width
    if left.mask or right.mask:
        return Value.x(w)
    result = _verilog_pow(left.as_signed(), right.as_signed())
    return Value.x(w) if result is None else Value(result, width=w)


# ── Binary operator dispatch ──────────────────────────────────────────


def _eval_binary_op(op: str, left: Value, right: Value) -> Value:
    """Evaluate a binary operator on two Values."""

    # Arithmetic
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        return left // right
    if op == "%":
        return left % right
    if op == "**":
        return left**right

    # Bitwise
    if op == "&":
        return left & right
    if op == "|":
        return left | right
    if op == "^":
        return left ^ right
    if op == "~^" or op == "^~":
        return ~(left ^ right)

    # Shift
    if op == "<<":
        return left << right
    if op == ">>":
        return left >> right
    if op == "<<<":
        return left << right  # arithmetic shift left = logical shift left
    if op == ">>>":
        # Arithmetic shift right (sign-extend). Only genuinely undetermined
        # bits become x (IEEE 1364/1800 semantics) -- not "any x in the
        # source -> entire result is x". Extending left to (width+shift)
        # bits sign-fills the top bits with correct x propagation from the
        # sign bit; a plain logical shift of that then reproduces
        # arithmetic-shift-right.
        if isinstance(right, Value):
            if right.mask:
                return Value.x(left.width)
            shift = right.val
        else:
            shift = right
        width = left.width
        if width == 0:
            return Value(0, width=0)
        if shift >= width:
            # Entire result is the sign bit (bit width-1) replicated -- avoid
            # constructing a `width + shift`-bit intermediate value, which
            # can raise OverflowError (CPython caps huge int shifts) when
            # `shift` comes from a wide self-determined operand.
            if (left.mask >> (width - 1)) & 1:
                return Value.x(width)
            if (left.val >> (width - 1)) & 1:
                return Value((1 << width) - 1, width=width)
            return Value(0, width=width)
        extended = left.sign_extend(width + shift)
        return (extended >> shift).resize(width)

    # Comparison — returns 1-bit Value
    if op == "==":
        return left.eq(right)
    if op == "!=":
        return left.ne(right)
    if op == "<":
        return left.lt(right)
    if op == "<=":
        return left.le(right)
    if op == ">":
        return left.gt(right)
    if op == ">=":
        return left.ge(right)

    # Case equality
    if op == "===":
        return left.case_eq(right)
    if op == "!==":
        return left.case_ne(right)

    # Logical
    if op == "&&":
        return left.logical_and(right)
    if op == "||":
        return left.logical_or(right)

    raise ValueError(f"Unknown binary operator: {op!r}")


# ── Unary operator dispatch ───────────────────────────────────────────


def _eval_unary_op(op: str, operand: Value) -> Value:
    """Evaluate a unary operator on a Value."""

    if op == "~":
        return ~operand
    if op == "!":
        return operand.logical_not()
    if op == "-":
        return -operand
    if op == "+":
        return operand

    # Reduction operators
    if op == "&":
        return operand.reduce_and()
    if op == "|":
        return operand.reduce_or()
    if op == "^":
        return operand.reduce_xor()
    if op == "~&":
        return operand.reduce_nand()
    if op == "~|":
        return operand.reduce_nor()
    if op == "~^" or op == "^~":
        return operand.reduce_xnor()

    raise ValueError(f"Unknown unary operator: {op!r}")


# ── Helpers ───────────────────────────────────────────────────────────


def _merge_xz(a: Value, b: Value) -> Value:
    """Merge two Values — bits that agree are kept, others become x.

    Used when a ternary condition is x/z: take the bitwise agreement
    of both branches (IEEE 1364-2005 Table 5-4). A bit only "agrees" when
    it is KNOWN (not x/z) in BOTH operands and has the same value -- two
    x/z bits do NOT agree just because their (val, mask) representation
    happens to match (mask=1 pairs with a placeholder val=0 in this
    codebase's Value encoding); confirmed against Icarus: merging two x/z
    branches must stay x, not collapse to a defined 0.
    """
    w = max(a.width, b.width)
    wmask = (1 << w) - 1
    both_known = ~a.mask & ~b.mask & wmask
    same_value = ~(a.val ^ b.val) & wmask
    agree = both_known & same_value
    new_mask = ~agree & wmask
    new_val = a.val & b.val & ~new_mask
    return Value(new_val, width=w, mask=new_mask)

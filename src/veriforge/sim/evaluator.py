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
from .value import Value

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
            cached = self._literal_cache.get(lit_id)
            if cached is not None:
                return cached
            v = self._eval_literal(expr)
            self._literal_cache[lit_id] = v
            return v

        # -- Hot path: BinaryOp ------------------------------------
        if etype is BinaryOp:
            op = expr.op
            # Comparison/logical ops produce 1-bit — operands are NOT
            # context-determined from the enclosing width; they are only
            # evaluated self-determined (matching the VM compiler, which
            # never propagates width into these operands either). Extending
            # each operand independently to some unrelated outer context
            # width (using each operand's own signedness) before comparing
            # would corrupt the comparison whenever the operands' own
            # signedness differs.
            if op in ("==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"):
                left = self.eval(expr.left, ctx)
                right = self.eval(expr.right, ctx)
            # Shift operators: only LEFT operand is context-determined
            elif op in ("<<", ">>", "<<<", ">>>"):
                left = self.eval(expr.left, ctx, width, signed_override)
                right = self.eval(expr.right, ctx)  # self-determined
                if width and left.width != width:
                    target = max(width, _expr_self_width(expr.left, ctx))
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.left, ctx)
                    left = left.sign_extend(target) if eff_signed else left.resize(target)
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
                op_width = max(_expr_self_width(expr.left, ctx), _expr_self_width(expr.right, ctx))
                left = self.eval(expr.left, ctx, op_width, signed_override)
                right = self.eval(expr.right, ctx, op_width, signed_override)
                if left.width != op_width:
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.left, ctx)
                    left = left.sign_extend(op_width) if eff_signed else left.resize(op_width)
                if right.width != op_width:
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.right, ctx)
                    right = right.sign_extend(op_width) if eff_signed else right.resize(op_width)
                result = _eval_binary_op(op, left, right)
                if width and result.width != width:
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr, ctx)
                    result = result.sign_extend(width) if eff_signed else result.resize(width)
                return result
            else:
                left = self.eval(expr.left, ctx, width, signed_override)
                right = self.eval(expr.right, ctx, width, signed_override)
                # Context-determined: resize both to max(context, operand's
                # own self-determined width) -- this can WIDEN (extend) or
                # NARROW (truncate) the operand. Truncation matters when the
                # operand is itself a further context-determined operator
                # whose own self-determined width can exceed the enclosing
                # context (e.g. multiplication's own width is the SUM of its
                # operand widths per IEEE 1364-2005 §5.4.1, which can easily
                # exceed an enclosing shift's context width): the enclosing
                # context wins and the multiply's result must be narrowed to
                # match before the shift runs, mirroring what real hardware
                # (and sim/vm/compiler.py, which already does this) does.
                # `_expr_self_width` is a floor so a genuinely-wide operand
                # whose width IS its meaning (e.g. a concatenation) is never
                # narrowed below its own exact width.
                if width:
                    if left.width != width:
                        target = max(width, _expr_self_width(expr.left, ctx))
                        eff_signed = signed_override if signed_override is not None else _expr_signed(expr.left, ctx)
                        left = left.sign_extend(target) if eff_signed else left.resize(target)
                    if right.width != width:
                        target = max(width, _expr_self_width(expr.right, ctx))
                        eff_signed = signed_override if signed_override is not None else _expr_signed(expr.right, ctx)
                        right = right.sign_extend(target) if eff_signed else right.resize(target)
            # Detect signed comparison: both operands must be signed
            if op in ("<", "<=", ">", ">=") and _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx):
                return _eval_signed_cmp(op, left, right)
            # Signed division / modulus: interpret operands as 2's-complement
            if op in ("/", "%") and _expr_signed(expr.left, ctx) and _expr_signed(expr.right, ctx):
                return _eval_signed_divmod(op, left, right)
            return _eval_binary_op(op, left, right)

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
                operand = self.eval(expr.operand, ctx, width, signed_override)
                if width and operand.width != width:
                    target = max(width, _expr_self_width(expr.operand, ctx))
                    eff_signed = signed_override if signed_override is not None else _expr_signed(expr.operand, ctx)
                    operand = operand.sign_extend(target) if eff_signed else operand.resize(target)
            else:
                operand = self.eval(expr.operand, ctx)
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
            cond = self.eval(expr.condition, ctx)
            if cond.is_defined:
                if cond.val:
                    return self.eval(expr.true_expr, ctx, width, own_signed)
                else:
                    return self.eval(expr.false_expr, ctx, width, own_signed)
            t = self.eval(expr.true_expr, ctx, width, own_signed)
            f = self.eval(expr.false_expr, ctx, width, own_signed)
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
                    if 0 <= idx < len(mem_data):
                        return mem_data[idx]
                    return Value.x(elem_w)
                mem_data, elem_w = ctx._memories[target_name]
                return Value.x(elem_w)
            target = self.eval(expr.target, ctx)
            index = self.eval(expr.index, ctx)
            if index.is_defined:
                idx = int(index)
                idx -= _select_base(expr.target, ctx)
                return target[idx]
            return Value.x(1)

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
                return target[m:l]
            w = (int(msb) - int(lsb) + 1) if msb.is_defined and lsb.is_defined else 1
            return Value.x(w)

        # -- Replication -------------------------------------------
        if etype is Replication:
            count_val = self.eval(expr.count, ctx)
            # Self-determined, same reasoning as Concatenation above.
            inner = self.eval(expr.value, ctx, _expr_self_width(expr.value, ctx))
            if count_val.is_defined:
                return inner.replicate(int(count_val))
            return Value.x(inner.width)

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
                return result.resize(width) if width and result.width != width else result

            if expr.positional:
                # Self-determined, same reasoning as Concatenation above.
                parts = [self.eval(part, ctx, _expr_self_width(part, ctx)) for part in expr.positional]
                result = _concat_values(parts)
                return result.resize(width) if width and result.width != width else result

            if expr.default_value is not None:
                default_width = width or self.eval(expr.default_value, ctx).width
                default_val = self.eval(expr.default_value, ctx, width=default_width)
                return default_val.resize(width) if width and default_val.width != width else default_val

            return Value(0, width=width or 1)

        # -- PartSelect --------------------------------------------
        if etype is PartSelect:
            target = self.eval(expr.target, ctx)
            base = self.eval(expr.base, ctx)
            width = self.eval(expr.width, ctx)
            if base.is_defined and width.is_defined:
                w = int(width)
                b = int(base)
                b -= _select_base(expr.target, ctx)
                if expr.direction == "+:":
                    return target[b + w - 1 : b]
                else:  # "-:"
                    return target[b : b - w + 1]
            return Value.x(1)

        # -- FunctionCall ------------------------------------------
        if etype is FunctionCall:
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
        args = [self.eval(a, ctx) for a in call.arguments]

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
            if i < len(args):
                local_signals[port.name] = args[i]
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
        return v.width if v is not None else 32
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
        if expr.op in (">>", ">>>"):
            # A shift's self-determined width is its LEFT operand's width
            # only -- the shift amount never contributes bits to the
            # result (mirrors `sim/vm/compiler.py`'s `_expr_width`).
            return _expr_self_width(expr.left, ctx)
        return max(_expr_self_width(expr.left, ctx), _expr_self_width(expr.right, ctx))
    if etype is UnaryOp:
        if expr.op in ("&", "|", "^", "~&", "~|", "~^", "^~", "!"):
            return 1
        return _expr_self_width(expr.operand, ctx)
    if etype is TernaryOp:
        return max(_expr_self_width(expr.true_expr, ctx), _expr_self_width(expr.false_expr, ctx))
    if etype is FunctionCall:
        name = expr.name.lower()
        if name in ("$signed", "$unsigned") and expr.arguments:
            return _expr_self_width(expr.arguments[0], ctx)
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

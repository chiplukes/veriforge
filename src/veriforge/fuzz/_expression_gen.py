"""Expression generator — produces Expression model objects randomly.

Mirrors test_differential.py's ``_gen_expr()`` but produces typed
``Expression`` model-object trees instead of Verilog text strings.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    RangeSelect,
    Replication,
    StreamingConcatenation,
    TernaryOp,
    UnaryOp,
    WidthCast,
)

from ._signal_context import SignalContext

# -- operator pools -------------------------------------------------------

_BINARY_OPS = [
    "+",
    "-",
    "*",
    "/",
    "%",
    "&",
    "|",
    "^",
    "~^",
    "<<",
    ">>",
    "<<<",
    ">>>",
    "**",
    "<",
    "<=",
    ">",
    ">=",
    "==",
    "!=",
    "===",
    "!==",
    "&&",
    "||",
]
_SHIFT_OPS = frozenset({"<<", ">>", "<<<", ">>>"})
_UNARY_OPS = ["~", "-", "!"]
_REDUCTION_OPS = ["&", "|", "^", "~&", "~|", "~^"]
_NODE_KINDS = (
    "binary",
    "unary",
    "reduction",
    "ternary",
    "concat",
    "replicate",
    "streaming_concat",
    "cast",
    "width_cast",
)
# Probability that a leaf is a literal rather than a signal. Literals were
# never generated as operands before 2026-10-09, so sized/unsized/signed/x
# constants -- and the width/signedness rules they trigger -- went untested.
_LITERAL_LEAF_PROBABILITY = 0.2
# Size-cast widths (`N'(e)`); kept <= 64 because the compiled engine
# doesn't support casts to wider (see known_issues.md).
_CAST_WIDTHS = (1, 2, 3, 4, 7, 8, 12, 16, 21, 31, 32, 33, 48, 63, 64)


class ExpressionGenerator:
    """Generates random ``Expression`` trees over a ``SignalContext``."""

    _BINARY_OPS = _BINARY_OPS
    _UNARY_OPS = _UNARY_OPS
    _REDUCTION_OPS = _REDUCTION_OPS
    _NODE_KINDS = _NODE_KINDS

    def __init__(self, ctx: SignalContext) -> None:
        self._ctx = ctx
        # > 0 while generating operands of a concatenation/replication/
        # streaming concat: an unsized literal is illegal there (IEEE 5.1.14).
        self._in_concat = 0

    # ------------------------------------------------------------------
    # Leaf / literal
    # ------------------------------------------------------------------

    def leaf(self, rng: random.Random, *, exclude: str | None = None) -> Expression:
        """Pick a signal from context, optionally bit/part selected.

        *exclude*: see ``expr()``'s docstring.
        """
        if rng.random() < _LITERAL_LEAF_PROBABILITY:
            return self.operand_literal(rng)
        sig = self._ctx.pick_readable(rng, exclude=exclude)
        if sig.width == 1:
            return sig.as_identifier()
        roll = rng.random()
        if roll < 0.5:
            return sig.as_identifier()
        if roll < 0.75:
            idx = rng.randrange(sig.width)
            return BitSelect(sig.as_identifier(), self._index_literal(idx))
        hi = rng.randrange(sig.width)
        lo = rng.randrange(hi + 1)
        if hi == lo:
            return BitSelect(sig.as_identifier(), self._index_literal(hi))
        return RangeSelect(sig.as_identifier(), self._index_literal(hi), self._index_literal(lo))

    @staticmethod
    def _index_literal(value: int) -> Literal:
        """A small explicitly-sized literal for a bit-select/range-select
        index. Bit-select/range-select indices don't themselves affect a
        concatenation operand's own size (a select's width comes from the
        select range, not the index), so leaving these unsized is likely
        harmless -- sized defensively anyway to close off any doubt, at
        zero cost. See `_make_nonzero` below for the literal that WAS
        directly confirmed (against Icarus) to cause "concatenation operand
        has indefinite width" errors.
        """
        return Literal(value, width=32, base="d", signed=False)

    def operand_literal(self, rng: random.Random) -> Expression:
        """A constant operand in one of the forms that exercise different
        width/signedness rules: sized unsigned (decimal/hex/binary), sized
        signed, unsized (32-bit signed, possibly >= 2**31), a negated signed
        literal, or a sized literal with x/z bits."""
        roll = rng.random()
        if roll < 0.25 or (self._in_concat and roll < 0.45):
            w = self._ctx.pick_width(rng)
            val = rng.getrandbits(w)
            base = rng.choice("dhb") if w <= 32 else rng.choice("dh")
            digits = {"d": str(val), "h": f"{val:x}", "b": f"{val:b}"}[base]
            return Literal(val, width=w, base=base, signed=False, original_text=f"{w}'{base}{digits}")
        if roll < 0.45:
            w = rng.choice((4, 8, 16, 32, 36, 64))
            val = rng.getrandbits(w - 1)
            text = f"{w}'sd{val}"
            lit = Literal(val, width=w, base="d", signed=True, original_text=text)
            return UnaryOp("-", lit) if rng.random() < 0.5 else lit
        if roll < 0.75 and not self._in_concat:
            val = rng.choice((0, 1, 2, 3, 7, 15, 16, 255, 256, 65535, 2**31 - 1, 2**31, 2**32 - 1, rng.getrandbits(31)))
            return Literal(val, base="d", signed=True, original_text=str(val))
        w = rng.choice((1, 2, 4, 8, 12, 33))
        bits = "".join(rng.choice("01xz") if rng.random() < 0.4 else rng.choice("01") for _ in range(w))
        return Literal(
            bits, width=w, base="b", signed=False, is_x="x" in bits, is_z="z" in bits, original_text=f"{w}'b{bits}"
        )

    def literal(self, rng: random.Random, *, width: int | None = None) -> Literal:
        """Generate a random literal with optional width constraint."""
        w = width if width is not None else rng.randint(1, 32)
        mask = (1 << w) - 1
        val = rng.randint(0, mask)
        return Literal(val, width=w, base="d", signed=False)

    # ------------------------------------------------------------------
    # Main generator
    # ------------------------------------------------------------------

    def expr(
        self,
        rng: random.Random,
        depth: int,
        *,
        extra_signals: Sequence = (),
        callables: Sequence = (),
        exclude: str | None = None,
    ) -> Expression:
        """Generate a random expression tree.

        Parameters
        ----------
        rng:
            Pre-seeded ``random.Random`` instance.
        depth:
            Remaining tree depth.  When ``depth <= 0`` or a random threshold
            is hit, a leaf is generated instead of a compound node.
        extra_signals:
            Reserved for future use (StatementGenerator inter-statement
            data dependencies).  Not yet wired through SignalContext.
        callables:
            ``_FuncSpec`` objects allowing ``FunctionCall`` generation.
        exclude:
            A signal name to never read anywhere in the generated tree --
            used by the statement generator to keep an assignment's RHS
            from directly reading the same signal it's writing (a same-
            statement self-reference forms a combinational loop with
            simulator-implementation-defined behavior, not a well-defined
            result -- confirmed as a real source of cross-engine/Icarus
            mismatches, not just a theoretical concern).
        """
        if depth <= 0 or rng.random() < 0.35:
            return self.leaf(rng, exclude=exclude)

        kinds = self._NODE_KINDS if not callables else (*self._NODE_KINDS, "call")
        kind = rng.choice(kinds)

        if kind == "binary":
            return self._binary(rng, depth, callables, exclude)
        if kind == "unary":
            return self._unary(rng, depth, callables, exclude)
        if kind == "reduction":
            return self._reduction(rng, depth, callables, exclude)
        if kind == "ternary":
            return self._ternary(rng, depth, callables, exclude)
        if kind == "concat":
            return self._concat(rng, depth, callables, exclude)
        if kind == "replicate":
            return self._replicate(rng, depth, callables, exclude)
        if kind == "streaming_concat":
            return self._streaming_concat(rng, depth, callables, exclude)
        if kind == "cast":
            return self._cast(rng, depth, callables, exclude)
        if kind == "width_cast":
            return self._width_cast(rng, depth, callables, exclude)
        if kind == "call":
            return self._call(rng, depth, callables, exclude)
        raise AssertionError(f"unknown kind: {kind}")

    # ------------------------------------------------------------------
    # Compound generators
    # ------------------------------------------------------------------

    def _binary(self, rng, depth, callables, exclude=None) -> BinaryOp:
        op = rng.choice(self._BINARY_OPS)
        lhs = self.expr(rng, depth - 1, callables=callables, exclude=exclude)
        if op in _SHIFT_OPS and rng.random() < 0.6:
            # Literal shift amounts, including ones at or past the operand's
            # width (a shift's result width is its left operand's).
            amount = rng.choice((0, 1, 2, 3, 7, 8, 15, 16, 31, 32, 33, 63, 64, 65, rng.randrange(130)))
            rhs: Expression = Literal(amount, base="d", signed=True, original_text=str(amount))
        elif op == "**":
            # Small constant exponent: results stay meaningful (not all
            # wrapped to 0) and no engine hits a pathological loop.
            exp = rng.choice((0, 1, 2, 3))
            rhs = Literal(exp, width=2, base="d", signed=False, original_text=f"2'd{exp}")
        else:
            rhs = self.expr(rng, depth - 1, callables=callables, exclude=exclude)
        if op in ("/", "%"):
            # Ensure RHS is non-zero to avoid div-by-zero noise
            rhs = self._make_nonzero(rhs)
        return BinaryOp(op, lhs, rhs)

    def _unary(self, rng, depth, callables, exclude=None) -> UnaryOp:
        op = rng.choice(self._UNARY_OPS)
        return UnaryOp(op, self.expr(rng, depth - 1, callables=callables, exclude=exclude))

    def _reduction(self, rng, depth, callables, exclude=None) -> UnaryOp:
        op = rng.choice(self._REDUCTION_OPS)
        return UnaryOp(op, self.expr(rng, depth - 1, callables=callables, exclude=exclude))

    def _ternary(self, rng, depth, callables, exclude=None) -> TernaryOp:
        return TernaryOp(
            condition=self.expr(rng, depth - 1, callables=callables, exclude=exclude),
            true_expr=self.expr(rng, depth - 1, callables=callables, exclude=exclude),
            false_expr=self.expr(rng, depth - 1, callables=callables, exclude=exclude),
        )

    def _concat(self, rng, depth, callables, exclude=None) -> Concatenation:
        n = rng.choice((2, 3))
        self._in_concat += 1
        try:
            parts = [self.expr(rng, depth - 1, callables=callables, exclude=exclude) for _ in range(n)]
        finally:
            self._in_concat -= 1
        return Concatenation(parts)

    def _replicate(self, rng, depth, callables, exclude=None) -> Replication:
        n = rng.choice((2, 3))
        self._in_concat += 1
        try:
            value = self.expr(rng, depth - 1, callables=callables, exclude=exclude)
        finally:
            self._in_concat -= 1
        return Replication(Literal(n, width=32, base="d", signed=False), value)

    def _streaming_concat(self, rng, depth, callables, exclude=None) -> Expression:
        """Generate a `{<<{...}}` / `{>>{...}}` streaming concatenation.

        Only the `<<` (left-stream) direction has a dedicated model node --
        `>>` with no slice_size is definitionally identical to plain
        concatenation and is desugared straight to `Concatenation` at
        AST-build time (see `StreamingConcatenation`'s own docstring), so
        picking `>>` here just emits a `Concatenation` directly instead of
        constructing a node the real parser would never itself produce.
        """
        n = rng.choice((2, 3))
        self._in_concat += 1
        try:
            parts = [self.expr(rng, depth - 1, callables=callables, exclude=exclude) for _ in range(n)]
        finally:
            self._in_concat -= 1
        direction = rng.choice(("<<", ">>"))
        if direction == ">>":
            return Concatenation(parts)

        # A slice_size > 64 hits an explicit, intentional limitation in both
        # the vm compiler and the compiled engine's wide emitter
        # ("Streaming concatenation slice_size > 64 is not supported") --
        # confirmed directly. Any declared parameter is only a candidate
        # slice_size if its own constant VALUE happens to already fall in
        # the legal range; most won't (params are generated with the same
        # wide-edge-case-biased widths/values as everything else), so this
        # is usually just a plain literal.
        safe_params = [p for p in self._ctx.parameters if p.value is not None and 1 <= p.value <= 64]

        slice_size: Expression | None
        roll = rng.random()
        if roll < 0.4:
            # No slice_size -- bit-level stream, the common case.
            slice_size = None
        elif roll < 0.7 or not safe_params:
            slice_size = Literal(rng.choice((1, 2, 4, 8)), width=32, base="d", signed=False)
        else:
            # A declared parameter is a valid constant-expression slice_size
            # (unlike a wire/reg, which IEEE 1800 disallows here).
            slice_size = rng.choice(safe_params).as_identifier()
        return StreamingConcatenation(parts, slice_size)

    def _cast(self, rng, depth, callables, exclude=None) -> FunctionCall:
        cast = rng.choice(("$signed", "$unsigned"))
        return FunctionCall(
            cast,
            [self.expr(rng, depth - 1, callables=callables, exclude=exclude)],
            is_system=True,
        )

    def _width_cast(self, rng, depth, callables, exclude=None) -> WidthCast:
        """SystemVerilog size cast ``N'(e)`` (dropped by the parser until
        2026-10-09). The operand is evaluated in a context of max(N, its
        width), so a narrower cast of a wide shift/add is the interesting
        shape."""
        n = rng.choice(_CAST_WIDTHS)
        return WidthCast(
            Literal(n, original_text=str(n)), self.expr(rng, depth - 1, callables=callables, exclude=exclude)
        )

    def _call(self, rng, depth, callables, exclude=None) -> FunctionCall:
        from collections.abc import Sequence as Seq

        spec = rng.choice(callables if isinstance(callables, Seq) else list(callables))
        args = [self.expr(rng, depth - 1, callables=callables, exclude=exclude) for _ in range(len(spec.arg_widths))]
        return FunctionCall(spec.name, args)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_nonzero(expr: Expression) -> Expression:
        """Wrap *expr* so it can never be zero at runtime.

        Uses ``expr | 1`` as a simple non-zero guard (same strategy as the
        existing test_differential.py). The literal MUST be explicitly
        sized: confirmed directly against Icarus that an unsized `1` here
        makes the enclosing `/`/`%` expression's own width "indefinite"
        (IEEE 1364-2005 §5.1.14) once embedded in a `{...}` concatenation
        member -- `{a % (b | 1), a}` is rejected outright
        ("Concatenation operand ... has indefinite width"), while
        `{a % (b | 1'b1), a}` compiles fine. This was the confirmed root
        cause of every observed "indefinite width" Icarus rejection from
        this fuzzer.
        """
        return BinaryOp("|", expr, Literal(1, width=1, base="b", signed=False))

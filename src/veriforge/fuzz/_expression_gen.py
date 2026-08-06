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
    TernaryOp,
    UnaryOp,
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
    "<<",
    ">>",
    "<",
    "<=",
    "==",
    "!=",
    "&&",
    "||",
]
_UNARY_OPS = ["~", "-", "!"]
_REDUCTION_OPS = ["&", "|", "^", "~&", "~|", "~^"]
_NODE_KINDS = ("binary", "unary", "reduction", "ternary", "concat", "replicate", "cast")


class ExpressionGenerator:
    """Generates random ``Expression`` trees over a ``SignalContext``."""

    _BINARY_OPS = _BINARY_OPS
    _UNARY_OPS = _UNARY_OPS
    _REDUCTION_OPS = _REDUCTION_OPS
    _NODE_KINDS = _NODE_KINDS

    def __init__(self, ctx: SignalContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Leaf / literal
    # ------------------------------------------------------------------

    def leaf(self, rng: random.Random) -> Expression:
        """Pick a signal from context, optionally bit/part selected."""
        sig = self._ctx.pick_readable(rng)
        if sig.width == 1:
            return sig.as_identifier()
        roll = rng.random()
        if roll < 0.5:
            return sig.as_identifier()
        if roll < 0.75:
            idx = rng.randrange(sig.width)
            return BitSelect(sig.as_identifier(), Literal(idx))
        hi = rng.randrange(sig.width)
        lo = rng.randrange(hi + 1)
        if hi == lo:
            return BitSelect(sig.as_identifier(), Literal(hi))
        return RangeSelect(sig.as_identifier(), Literal(hi), Literal(lo))

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
        """
        if depth <= 0 or rng.random() < 0.35:
            return self.leaf(rng)

        kinds = self._NODE_KINDS if not callables else (*self._NODE_KINDS, "call")
        kind = rng.choice(kinds)

        if kind == "binary":
            return self._binary(rng, depth, callables)
        if kind == "unary":
            return self._unary(rng, depth, callables)
        if kind == "reduction":
            return self._reduction(rng, depth, callables)
        if kind == "ternary":
            return self._ternary(rng, depth, callables)
        if kind == "concat":
            return self._concat(rng, depth, callables)
        if kind == "replicate":
            return self._replicate(rng, depth, callables)
        if kind == "cast":
            return self._cast(rng, depth, callables)
        if kind == "call":
            return self._call(rng, depth, callables)
        raise AssertionError(f"unknown kind: {kind}")

    # ------------------------------------------------------------------
    # Compound generators
    # ------------------------------------------------------------------

    def _binary(self, rng, depth, callables) -> BinaryOp:
        op = rng.choice(self._BINARY_OPS)
        lhs = self.expr(rng, depth - 1, callables=callables)
        rhs = self.expr(rng, depth - 1, callables=callables)
        if op in ("/", "%"):
            # Ensure RHS is non-zero to avoid div-by-zero noise
            rhs = self._make_nonzero(rhs)
        return BinaryOp(op, lhs, rhs)

    def _unary(self, rng, depth, callables) -> UnaryOp:
        op = rng.choice(self._UNARY_OPS)
        return UnaryOp(op, self.expr(rng, depth - 1, callables=callables))

    def _reduction(self, rng, depth, callables) -> UnaryOp:
        op = rng.choice(self._REDUCTION_OPS)
        return UnaryOp(op, self.expr(rng, depth - 1, callables=callables))

    def _ternary(self, rng, depth, callables) -> TernaryOp:
        return TernaryOp(
            condition=self.expr(rng, depth - 1, callables=callables),
            true_expr=self.expr(rng, depth - 1, callables=callables),
            false_expr=self.expr(rng, depth - 1, callables=callables),
        )

    def _concat(self, rng, depth, callables) -> Concatenation:
        n = rng.choice((2, 3))
        parts = [self.expr(rng, depth - 1, callables=callables) for _ in range(n)]
        return Concatenation(parts)

    def _replicate(self, rng, depth, callables) -> Replication:
        n = rng.choice((2, 3))
        return Replication(
            Literal(n),
            self.expr(rng, depth - 1, callables=callables),
        )

    def _cast(self, rng, depth, callables) -> FunctionCall:
        cast = rng.choice(("$signed", "$unsigned"))
        return FunctionCall(
            cast,
            [self.expr(rng, depth - 1, callables=callables)],
            is_system=True,
        )

    def _call(self, rng, depth, callables) -> FunctionCall:
        from collections.abc import Sequence as Seq

        spec = rng.choice(callables if isinstance(callables, Seq) else list(callables))
        args = [self.expr(rng, depth - 1, callables=callables) for _ in range(len(spec.arg_widths))]
        return FunctionCall(spec.name, args)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_nonzero(expr: Expression) -> Expression:
        """Wrap *expr* so it can never be zero at runtime.

        Uses ``expr | 1`` as a simple non-zero guard (same strategy as the
        existing test_differential.py).
        """
        return BinaryOp("|", expr, Literal(1))

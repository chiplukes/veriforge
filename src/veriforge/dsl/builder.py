"""Hardware Construction DSL — build Verilog model objects from Python expressions.

Operator-overloaded Python API for designing hardware. Signal objects build
model nodes instead of computing values — Python expressions become circuit
descriptions.

Usage::

    from veriforge.dsl import Module, posedge

    with Module("counter") as m:
        clk = m.input("clk")
        rst = m.input("rst")
        count = m.output_reg("count", width=8)

        with m.always(posedge(clk)):
            with m.if_(rst):
                count <<= 0
            with m.else_():
                count <<= count + 1

    module = m.build()
"""

from __future__ import annotations

from ..model.base import Comment, SourceLocation, VerilogNode
from ..model.assignments import ContinuousAssign
from ..model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from ..model.design import Module as ModelModule
from ..model.instances import Instance, ParameterBinding, PortConnection
from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    Range,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from ..model.nets import Net, NetKind
from ..model.package import ImportDecl
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    EventControl,
    IfStatement,
    NonblockingAssign,
    SeqBlock,
    SensitivityEdge,
    Statement,
    SystemTaskCall,
)
from ..model.sv_types import EnumMember, EnumType, StructField, StructType, TypedefDecl, UnionType
from ..model.variables import Variable, VariableKind
from .interface import BoundInterface, Interface

import re
import warnings

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_VALID_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]*$")


def _check_identifier(name: str, kind: str = "signal") -> None:
    """Raise ValueError if *name* is not a valid Verilog identifier."""
    if not _VALID_IDENT_RE.match(name):
        raise ValueError(f"Invalid Verilog identifier for {kind}: {name!r}")


def _to_expr_node(value: object) -> Expression:
    """Convert a Python value to an Expression model node."""
    if isinstance(value, Expr):
        return value._as_expr()
    if isinstance(value, int):  # bool is subclass of int
        return Literal(int(value), original_text=str(int(value)))
    if isinstance(value, str):
        return StringLiteral(value)
    if isinstance(value, Expression):
        return value
    # M26/M27: Provide actionable error messages for common wrong types
    if isinstance(value, float):
        raise TypeError(f"Cannot use float ({value}) in hardware expressions — use int instead (e.g. {int(value)})")
    if value is None:
        raise TypeError("Cannot use None in hardware expressions — provide a value or signal")
    if isinstance(value, (list, tuple)):
        raise TypeError(
            f"Cannot use {type(value).__name__} in hardware expressions — use cat() for concatenation instead"
        )
    if isinstance(value, dict):
        raise TypeError("Cannot use dict in hardware expressions")
    if isinstance(value, WhenChain):
        raise TypeError("Unclosed when() chain — call .otherwise(default) to produce an expression")
    raise TypeError(f"Cannot convert {type(value).__name__} to Expression")


def _to_lit(value: object) -> Literal:
    """Convert a Python int to a Literal."""
    if isinstance(value, int):
        return Literal(int(value), original_text=str(int(value)))
    raise TypeError(f"Cannot convert {type(value).__name__} to Literal")


def _make_range(width: object) -> Range | None:
    """Convert a width specification to a Range [width-1:0], or None for scalar."""
    if width is None:
        return None
    if isinstance(width, int):
        if width <= 0:
            raise ValueError(f"Signal width must be positive, got {width}")
        if width == 1:
            return None
        return Range(
            Literal(width - 1, original_text=str(width - 1)),
            Literal(0, original_text="0"),
        )
    if isinstance(width, Expr):
        return Range(
            BinaryOp("-", width._as_expr(), Literal(1, original_text="1")),
            Literal(0, original_text="0"),
        )
    raise TypeError(f"Invalid width type: {type(width).__name__}")


def _find_builder(args: object) -> Module | None:
    """Find a Module builder reference from a sequence of arguments."""
    for a in args:
        if isinstance(a, Expr) and a._builder is not None:
            return a._builder
    return None


def _wrap_statements(stmts: list[Statement]) -> Statement:
    """Wrap a list of statements in a SeqBlock if needed."""
    if not stmts:
        return SeqBlock([])
    if len(stmts) == 1:
        return stmts[0]
    return SeqBlock(stmts)


def _find_deepest_if(stmt: IfStatement) -> IfStatement:
    """Walk an if-elif chain to the deepest IfStatement without an else."""
    while isinstance(stmt.else_body, IfStatement):
        stmt = stmt.else_body
    return stmt


def _contains_stmt_type(stmt: Statement | None, target: type) -> bool:
    """Recursively check whether *stmt* contains an instance of *target*."""
    if stmt is None:
        return False
    if isinstance(stmt, target):
        return True
    if isinstance(stmt, SeqBlock):
        return any(_contains_stmt_type(s, target) for s in stmt.statements)
    if isinstance(stmt, IfStatement):
        if _contains_stmt_type(stmt.then_body, target):
            return True
        if stmt.else_body is not None and _contains_stmt_type(stmt.else_body, target):
            return True
        return False
    if isinstance(stmt, CaseStatement):
        for item in stmt.items:
            if _contains_stmt_type(item.body, target):
                return True
        return False
    return False


def _classify_sensitivity(edges: list[SensitivityEdge]) -> SensitivityType:
    """Classify a sensitivity list as COMBINATIONAL, SEQUENTIAL, or UNKNOWN."""
    if not edges:
        return SensitivityType.COMBINATIONAL
    has_edge = any(e.edge in ("posedge", "negedge") for e in edges)
    has_level = any(e.edge == "level" for e in edges)
    if has_edge and not has_level:
        return SensitivityType.SEQUENTIAL
    if has_level and not has_edge:
        return SensitivityType.COMBINATIONAL
    return SensitivityType.UNKNOWN


def _make_comment(text: str, position: str = "leading", *, block: bool = False) -> Comment:
    """Create a Comment model node for DSL-generated annotations."""
    kind = "block" if block else "line"
    return Comment(text, SourceLocation(), kind=kind, position=position)


def _make_dim(depth: int) -> Range:
    """Create a dimension Range [0:depth-1] for memory arrays."""
    # M24: memory depth validation
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise TypeError(f"Memory depth must be a positive integer, got {type(depth).__name__}")
    if depth <= 0:
        raise ValueError(f"Memory depth must be positive, got {depth}")
    return Range(
        Literal(0, original_text="0"),
        Literal(depth - 1, original_text=str(depth - 1)),
    )


def _emit_attr(attrs: dict[str, str | None]) -> str:
    """Format a Verilog attribute string: ``(* key = "val", ... *)``."""
    parts: list[str] = []
    for k, v in attrs.items():
        if v is not None:
            parts.append(f'{k} = "{v}"')
        else:
            parts.append(k)
    return "(* " + ", ".join(parts) + " *)"


# ---------------------------------------------------------------------------
# Expr — expression proxy with operator overloading
# ---------------------------------------------------------------------------


class Expr:
    """Expression proxy that builds model Expression trees via operator overloading.

    When you write ``a + b`` where ``a`` and ``b`` are Expr objects, the result
    is a new Expr wrapping ``BinaryOp("+", Identifier("a"), Identifier("b"))``.
    No computation occurs — the expression tree is captured for later emission
    or simulation.
    """

    __slots__ = ("_builder", "_expr")

    def __init__(self, builder: Module | None, expr: Expression):
        self._builder = builder
        self._expr = expr

    def _as_expr(self) -> Expression:
        """Return the underlying Expression model node."""
        return self._expr

    def __repr__(self) -> str:
        return f"Expr({self._expr!r})"

    def __hash__(self) -> int:
        return id(self)

    def __bool__(self) -> bool:
        raise TypeError(
            "Cannot use hardware expression as Python boolean. Use m.if_(expr) for conditional hardware logic."
        )

    def __iter__(self):
        raise TypeError(
            "Cannot iterate over a hardware signal. Use a Python range loop with bit indexing: "
            "for i in range(width): ... sig[i] ..."
        )

    def __len__(self) -> int:
        raise TypeError(
            "Cannot call len() on a hardware signal. Signal widths are set at declaration time, "
            "not queryable at build time via len()."
        )

    # --- Arithmetic operators ---

    def __add__(self, other: object) -> Expr:
        return self._binop("+", other)

    def __radd__(self, other: object) -> Expr:
        return self._rbinop("+", other)

    def __sub__(self, other: object) -> Expr:
        return self._binop("-", other)

    def __rsub__(self, other: object) -> Expr:
        return self._rbinop("-", other)

    def __mul__(self, other: object) -> Expr:
        return self._binop("*", other)

    def __rmul__(self, other: object) -> Expr:
        return self._rbinop("*", other)

    def __truediv__(self, other: object) -> Expr:
        raise TypeError(
            "Use `//` for Verilog integer division (`/` in Verilog is integer division, "
            "not floating-point). Write `a // b` in the DSL."
        )

    def __rtruediv__(self, other: object) -> Expr:
        raise TypeError(
            "Use `//` for Verilog integer division (`/` in Verilog is integer division, "
            "not floating-point). Write `a // b` in the DSL."
        )

    def __floordiv__(self, other: object) -> Expr:
        return self._binop("/", other)

    def __rfloordiv__(self, other: object) -> Expr:
        return self._rbinop("/", other)

    def __mod__(self, other: object) -> Expr:
        return self._binop("%", other)

    def __rmod__(self, other: object) -> Expr:
        return self._rbinop("%", other)

    def __pow__(self, other: object) -> Expr:
        return self._binop("**", other)

    def __rpow__(self, other: object) -> Expr:
        return self._rbinop("**", other)

    # --- Bitwise operators ---

    def __and__(self, other: object) -> Expr:
        return self._binop("&", other)

    def __rand__(self, other: object) -> Expr:
        return self._rbinop("&", other)

    def __or__(self, other: object) -> Expr:
        return self._binop("|", other)

    def __ror__(self, other: object) -> Expr:
        return self._rbinop("|", other)

    def __xor__(self, other: object) -> Expr:
        return self._binop("^", other)

    def __rxor__(self, other: object) -> Expr:
        return self._rbinop("^", other)

    def __invert__(self) -> Expr:
        return Expr(self._builder, UnaryOp("~", self._as_expr()))

    def __neg__(self) -> Expr:
        return Expr(self._builder, UnaryOp("-", self._as_expr()))

    # --- Shift operators ---

    def __lshift__(self, other: object) -> Expr:
        return self._binop("<<", other)

    def __rlshift__(self, other: object) -> Expr:
        return self._rbinop("<<", other)

    def __rshift__(self, other: object) -> Expr:
        return self._binop(">>", other)

    def __rrshift__(self, other: object) -> Expr:
        return self._rbinop(">>", other)

    # --- Comparison operators ---

    def __eq__(self, other: object) -> Expr:
        return self._binop("==", other)

    def __ne__(self, other: object) -> Expr:
        return self._binop("!=", other)

    def __lt__(self, other: object) -> Expr:
        return self._binop("<", other)

    def __le__(self, other: object) -> Expr:
        return self._binop("<=", other)

    def __gt__(self, other: object) -> Expr:
        return self._binop(">", other)

    def __ge__(self, other: object) -> Expr:
        return self._binop(">=", other)

    # --- Bit / range / part selection ---

    def __getitem__(self, key: object) -> Expr:
        """Bit select (int/Expr index) or range select (slice)."""
        if isinstance(key, slice):
            msb = _to_expr_node(key.start)
            lsb = _to_expr_node(key.stop)
            return Expr(self._builder, RangeSelect(self._as_expr(), msb, lsb))
        return Expr(self._builder, BitSelect(self._as_expr(), _to_expr_node(key)))

    def part_select(self, base: Signal | Expr | int, width: Signal | Expr | int) -> Expr:
        """Ascending part select: ``signal[base +: width]``"""
        return Expr(
            self._builder,
            PartSelect(self._as_expr(), _to_expr_node(base), _to_expr_node(width), "+:"),
        )

    def part_select_down(self, base: Signal | Expr | int, width: Signal | Expr | int) -> Expr:
        """Descending part select: ``signal[base -: width]``"""
        return Expr(
            self._builder,
            PartSelect(self._as_expr(), _to_expr_node(base), _to_expr_node(width), "-:"),
        )

    def bits(
        self,
        *,
        lsb: Signal | Expr | int | None = None,
        msb: Signal | Expr | int | None = None,
        width: Signal | Expr | int = 1,
    ) -> Expr:
        """Keyword part select: ``sig.bits(lsb=k, width=8)`` → ``sig[k +: 8]``.

        Exactly one of *lsb* / *msb* must be given:

        - ``bits(lsb=base, width=w)`` — ascending select ``sig[base +: w]``
        - ``bits(msb=base, width=w)`` — descending select ``sig[base -: w]``
        """
        if (lsb is None) == (msb is None):
            raise TypeError("bits() requires exactly one of lsb= or msb=")
        if lsb is not None:
            return self.part_select(lsb, width)
        return self.part_select_down(msb, width)

    def __setitem__(self, key: object, value: object) -> None:
        """Guard for subscript augmented assignment (data[i] <<= x).

        The actual statement is created by __ilshift__ / __imatmul__.  Python's
        augmented-assignment protocol calls __setitem__ after __ilshift__ with
        the same Expr object that __getitem__ returned — we verify that here so
        the natural typo ``data[i] = x`` raises instead of silently doing nothing.
        """
        builder = self._builder
        if builder is not None:
            expected = builder._last_aug_assign
            builder._last_aug_assign = None
            if value is not expected:
                raise TypeError(
                    "Direct subscript assignment `data[i] = x` is not a hardware "
                    "statement and will be silently discarded. "
                    "Use `data[i] <<= x` for a non-blocking assignment or "
                    "`data[i] @= x` for a blocking assignment."
                )

    # --- Assignments (create statements) ---

    def __ilshift__(self, other: object) -> Expr:
        """Non-blocking assignment: ``signal <<= expr``"""
        if self._builder is None or not self._builder._block_stack:
            raise RuntimeError("Non-blocking assignment (<<=) must be inside an always or initial block")
        stmt = NonblockingAssign(self._as_expr(), _to_expr_node(other))
        self._builder._append_stmt(stmt)
        self._builder._last_aug_assign = self
        return self

    def __imatmul__(self, other: object) -> Expr:
        """Blocking assignment: ``signal @= expr``"""
        if self._builder is None or not self._builder._block_stack:
            raise RuntimeError("Blocking assignment (@=) must be inside an always or initial block")
        stmt = BlockingAssign(self._as_expr(), _to_expr_node(other))
        self._builder._append_stmt(stmt)
        self._builder._last_aug_assign = self
        return self

    def set(self, other: object) -> None:
        """Blocking assignment (deprecated, use ``@=`` instead): ``signal.set(expr)``"""
        if self._builder is None or not self._builder._block_stack:
            raise RuntimeError("Blocking assignment (.set()) must be inside an always or initial block")
        stmt = BlockingAssign(self._as_expr(), _to_expr_node(other))
        self._builder._append_stmt(stmt)

    @property
    def next(self) -> Expr:  # noqa: A003 — deliberate MyHDL-style assignment target
        """Reading ``.next`` is not meaningful — it exists only as an assignment target."""
        raise TypeError(
            "'.next' is write-only: use `sig.next = expr` for a non-blocking assignment. "
            "To read the signal's current value, use the signal itself."
        )

    @next.setter  # noqa: A003
    def next(self, other: object) -> None:
        """Non-blocking assignment: ``signal.next = expr`` (alias for ``signal <<= expr``)."""
        if self._builder is None or not self._builder._block_stack:
            raise RuntimeError("Non-blocking assignment (.next =) must be inside an always or initial block")
        stmt = NonblockingAssign(self._as_expr(), _to_expr_node(other))
        self._builder._append_stmt(stmt)

    # --- Internal ---

    def _binop(self, op: str, other: object) -> Expr:
        return Expr(self._builder, BinaryOp(op, self._as_expr(), _to_expr_node(other)))

    def _rbinop(self, op: str, other: object) -> Expr:
        return Expr(self._builder, BinaryOp(op, _to_expr_node(other), self._as_expr()))


# ---------------------------------------------------------------------------
# Signal — named signal proxy (port, wire, reg)
# ---------------------------------------------------------------------------


class Signal(Expr):  # cm:7d5f3a
    """A named signal (port, wire, reg) in the DSL.

    Inherits all operator overloading from Expr. The underlying Expression
    is an Identifier with the signal's name.
    """

    __slots__ = ("_attributes", "_comments", "_name", "_width")

    def __init__(self, builder: Module, name: str, width: int | Expr | None):
        super().__init__(builder, Identifier(name))
        self._name = name
        self._width = width
        self._comments: list[str] = []
        self._attributes: dict[str, str | None] = {}

    def __repr__(self) -> str:
        return f"Signal({self._name!r})"

    def comment(self, text: str) -> Signal:
        """Attach a comment to this signal's declaration in emitted Verilog.

        Port signals get trailing comments on the same line; internal wires
        and regs get leading comments on the line above.  Multiple calls add
        multiple comment lines.  Returns self for chaining::

            clk = m.input("clk").comment("100 MHz system clock")
        """
        self._comments.append(text)
        return self

    def attr(self, name: str, value: str | None = None) -> Signal:
        """Attach a synthesis attribute to this signal's declaration.

        Emits ``(* name *)`` or ``(* name = "value" *)`` above the
        declaration in Verilog::

            state = m.reg("state", width=3).attr("fsm_encoding", "one_hot")

        Multiple calls add multiple attributes.  Returns self for chaining.
        """
        self._attributes[name] = value
        return self


# ---------------------------------------------------------------------------
# Context managers for behavioral blocks
# ---------------------------------------------------------------------------


class _AlwaysContext:
    """Context manager for ``with m.always(posedge(clk)):`` blocks."""

    __slots__ = ("_builder", "_comment", "_pending", "_reset", "_sensitivity")

    def __init__(
        self,
        builder: Module,
        sensitivity: tuple,
        comment: str | None = None,
        pending_comments: list[tuple[str, bool]] | None = None,
        reset: tuple[Expression, bool, list[tuple[Expression, Expression]]] | None = None,
    ):
        self._builder = builder
        self._sensitivity = sensitivity
        self._comment = comment
        self._pending = pending_comments or []
        # (rst_expr, active_low, [(lhs, rhs), ...]) — set by m.seq(rst=..., rst_vals=...)
        self._reset = reset

    def __enter__(self) -> _AlwaysContext:
        if self._builder._block_stack:
            raise RuntimeError("Cannot nest always blocks — already inside an always or initial block")
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()

        # M13: empty always block
        if not stmts:
            warnings.warn(
                "Empty always block — no statements inside 'with m.always(...)'",
                stacklevel=2,
            )
            return False

        body = _wrap_statements(stmts)

        # m.seq(rst=..., rst_vals=...): wrap the body in the standard reset skeleton
        #   if (rst) begin <resets> end else begin <body> end
        if self._reset is not None:
            rst_expr, active_low, reset_assigns = self._reset
            cond: Expression = UnaryOp("!", rst_expr) if active_low else rst_expr
            reset_body = _wrap_statements([NonblockingAssign(lhs, rhs) for lhs, rhs in reset_assigns])
            body = IfStatement(cond, reset_body, else_body=body)

        # Build sensitivity list
        edges: list[SensitivityEdge] = []
        for s in self._sensitivity:
            if isinstance(s, SensitivityEdge):
                edges.append(s)
            elif isinstance(s, (Signal, Expr)):
                edges.append(SensitivityEdge("level", s._as_expr()))
            else:
                raise TypeError(f"Invalid sensitivity item: {type(s).__name__}")

        stype = _classify_sensitivity(edges)

        # M11: NBA in combinational block
        if stype == SensitivityType.COMBINATIONAL and _contains_stmt_type(body, NonblockingAssign):
            warnings.warn(
                "Non-blocking assignment (<<=) in combinational always block — "
                "use blocking assignment (@=) for combinational logic",
                stacklevel=2,
            )

        # M12: blocking assign in sequential block
        if stype == SensitivityType.SEQUENTIAL and _contains_stmt_type(body, BlockingAssign):
            warnings.warn(
                "Blocking assignment (@=) in sequential always block — "
                "use non-blocking assignment (<<=) for sequential logic",
                stacklevel=2,
            )

        ab = AlwaysBlock(body, sensitivity_list=edges, sensitivity_type=stype)
        for text, is_block in self._pending:
            ab.comments.append(_make_comment(text, block=is_block))
        if self._comment:
            ab.comments.append(_make_comment(self._comment))
        self._builder._always_blocks.append(ab)
        return False


class _InitialContext:
    """Context manager for ``with m.initial():`` blocks."""

    __slots__ = ("_builder", "_comment", "_pending")

    def __init__(
        self, builder: Module, comment: str | None = None, pending_comments: list[tuple[str, bool]] | None = None
    ):
        self._builder = builder
        self._comment = comment
        self._pending = pending_comments or []

    def __enter__(self) -> _InitialContext:
        if self._builder._block_stack:
            raise RuntimeError("Cannot nest initial blocks — already inside an always or initial block")
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()

        # M13: empty initial block
        if not stmts:
            warnings.warn(
                "Empty initial block — no statements inside 'with m.initial()'",
                stacklevel=2,
            )
            return False

        body = _wrap_statements(stmts)
        ib = InitialBlock(body)
        for text, is_block in self._pending:
            ib.comments.append(_make_comment(text, block=is_block))
        if self._comment:
            ib.comments.append(_make_comment(self._comment))
        self._builder._initial_blocks.append(ib)
        return False


class _IfContext:
    """Context manager for ``with m.if_(condition):`` blocks."""

    __slots__ = ("_builder", "_condition")

    def __init__(self, builder: Module, condition: Expression):
        self._builder = builder
        self._condition = condition

    def __enter__(self) -> _IfContext:
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)
        if_stmt = IfStatement(self._condition, body)
        self._builder._append_stmt(if_stmt)
        return False


class _ElifContext:
    """Context manager for ``with m.elif_(condition):`` blocks.

    Must immediately follow an ``m.if_()`` or ``m.elif_()`` block.
    """

    __slots__ = ("_builder", "_condition")

    def __init__(self, builder: Module, condition: Expression):
        self._builder = builder
        self._condition = condition

    def __enter__(self) -> _ElifContext:
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)

        parent = self._builder._block_stack[-1]
        if not parent or not isinstance(parent[-1], IfStatement):
            raise RuntimeError("elif_() must immediately follow an if_() or elif_() block")

        leaf = _find_deepest_if(parent[-1])
        if leaf.else_body is not None:
            raise RuntimeError("elif_() cannot follow an else_() block")
        leaf.else_body = IfStatement(self._condition, body)
        return False


class _ElseContext:
    """Context manager for ``with m.else_():`` blocks.

    Must immediately follow an ``m.if_()`` or ``m.elif_()`` block.
    """

    __slots__ = ("_builder",)

    def __init__(self, builder: Module):
        self._builder = builder

    def __enter__(self) -> _ElseContext:
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)

        parent = self._builder._block_stack[-1]
        if not parent or not isinstance(parent[-1], IfStatement):
            raise RuntimeError("else_() must immediately follow an if_() or elif_() block")

        leaf = _find_deepest_if(parent[-1])
        if leaf.else_body is not None:
            raise RuntimeError("else_() cannot follow another else_() block")
        leaf.else_body = body
        return False


class _CaseContext:
    """Context manager for ``with m.case(sel) as c:`` blocks.

    Use ``c.when(value)`` and ``c.default()`` within this context.
    """

    __slots__ = ("_builder", "_case_type", "_expr", "_items")

    def __init__(self, builder: Module, case_type: str, expr: Expression):
        if case_type not in ("case", "casex", "casez"):
            raise ValueError(f"Invalid case_type: {case_type!r} — must be 'case', 'casex', or 'casez'")
        self._builder = builder
        self._case_type = case_type
        self._expr = expr
        self._items: list[CaseItem] = []

    def when(self, *values: object) -> _CaseWhenContext:
        """Add a case item matching the given value(s)."""
        return _CaseWhenContext(self._builder, self, values)

    def default(self) -> _CaseDefaultContext:
        """Add a default case item."""
        return _CaseDefaultContext(self._builder, self)

    def __enter__(self) -> _CaseContext:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            return False
        # M18: empty case statement
        if not self._items:
            raise RuntimeError("Empty case statement — no 'when' or 'default' items inside 'with m.case(...)'")
        stmt = CaseStatement(self._case_type, self._expr, self._items)
        self._builder._append_stmt(stmt)
        return False


class _CaseWhenContext:
    """Context manager for ``with c.when(value):`` inside a case block."""

    __slots__ = ("_builder", "_case_ctx", "_values")

    def __init__(self, builder: Module, case_ctx: _CaseContext, values: tuple):
        self._builder = builder
        self._case_ctx = case_ctx
        self._values = values

    def __enter__(self) -> _CaseWhenContext:
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)
        value_exprs = [_to_expr_node(v) for v in self._values]
        item = CaseItem(value_exprs, body)
        self._case_ctx._items.append(item)
        return False


class _CaseDefaultContext:
    """Context manager for ``with c.default():`` inside a case block."""

    __slots__ = ("_builder", "_case_ctx")

    def __init__(self, builder: Module, case_ctx: _CaseContext):
        self._builder = builder
        self._case_ctx = case_ctx

    def __enter__(self) -> _CaseDefaultContext:
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)
        item = CaseItem(None, body, is_default=True)
        self._case_ctx._items.append(item)
        return False


class _DelayContext:
    """Context manager for ``with m.delay(time):`` blocks.

    Also usable without ``with`` for standalone ``#time;`` delays —
    when ``__enter__`` is never called, ``__del__`` emits a bare delay.
    """

    __slots__ = ("_builder", "_delay", "_entered")

    def __init__(self, builder: Module, delay: Expression):
        self._builder = builder
        self._delay = delay
        self._entered = False
        # Immediately emit a bare #delay; — if __enter__ is called, it
        # removes this and replaces it with the body version.
        stmt = DelayControl(self._delay)
        self._builder._append_stmt(stmt)

    def __enter__(self) -> _DelayContext:
        self._entered = True
        # Remove the bare delay we just appended
        self._builder._block_stack[-1].pop()
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)
        stmt = DelayControl(self._delay, body)
        self._builder._append_stmt(stmt)
        return False


class _EventContext:
    """Context manager for ``with m.wait_event(posedge(clk)):`` blocks.

    Also usable without ``with`` for standalone ``@(event);`` waits.
    """

    __slots__ = ("_builder", "_edges", "_entered")

    def __init__(self, builder: Module, edges: list[SensitivityEdge]):
        self._builder = builder
        self._edges = edges
        self._entered = False
        # Immediately emit a bare @(event); — if __enter__ is called, it
        # removes this and replaces it with the body version.
        stmt = EventControl(self._edges)
        self._builder._append_stmt(stmt)

    def __enter__(self) -> _EventContext:
        self._entered = True
        # Remove the bare event we just appended
        self._builder._block_stack[-1].pop()
        self._builder._push_block()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if exc_type is not None:
            self._builder._pop_block()
            return False
        stmts = self._builder._pop_block()
        body = _wrap_statements(stmts)
        stmt = EventControl(self._edges, body)
        self._builder._append_stmt(stmt)
        return False


# ---------------------------------------------------------------------------
# Module — top-level hardware module builder
# ---------------------------------------------------------------------------


class Module:  # cm:2b9e4c
    """Hardware module builder with operator-overloaded DSL.

    Builds a semantic model ``Module`` object from Python expressions and
    context managers. The resulting module can be emitted to Verilog or
    simulated directly.

    Example::

        m = Module("adder")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        s = m.output("sum", width=9)
        m.assign(s, a + b)
        module = m.build()
    """

    __slots__ = (
        "_always_blocks",
        "_block_stack",
        "_continuous_assigns",
        "_imports",
        "_initial_blocks",
        "_instance_names",
        "_instances",
        "_interfaces",
        "_last_aug_assign",
        "_name",
        "_nets",
        "_parameters",
        "_pending_comments",
        "_ports",
        "_signals",
        "_typedefs",
        "_variables",
    )

    def __init__(self, name: str):
        _check_identifier(name, "module")
        self._name = name
        self._ports: list[Port] = []
        self._parameters: list[Parameter] = []
        self._nets: list[Net] = []
        self._variables: list[Variable] = []
        self._continuous_assigns: list[ContinuousAssign] = []
        self._always_blocks: list[AlwaysBlock] = []
        self._initial_blocks: list[InitialBlock] = []
        self._instances: list[Instance] = []
        self._interfaces: dict[str, BoundInterface] = {}
        self._block_stack: list[list[Statement]] = []
        self._pending_comments: list[str] = []
        self._signals: dict[str, Signal] = {}
        self._instance_names: set[str] = set()
        self._typedefs: list[TypedefDecl] = []
        self._imports: list[ImportDecl] = []
        self._last_aug_assign: Expr | None = None

    def __enter__(self) -> Module:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        return False

    # --- Standalone comments ---

    def comment(self, text: str, *, block: bool = False) -> None:
        """Insert a standalone comment at this point in the module.

        The comment appears above the next declaration, assignment, or
        block in the emitted Verilog::

            m.comment("Adder stage 1")
            m.assign(s01, a[0] + a[1])

        Emits ``// Adder stage 1`` on the line before the assign.

        Pass ``block=True`` for a block comment (``/* ... */``).
        """
        self._pending_comments.append((text, block))

    def _flush_pending(self, node: VerilogNode) -> None:
        """Attach any pending standalone comments to *node* as leading comments."""
        for text, is_block in self._pending_comments:
            node.comments.append(_make_comment(text, block=is_block))
        self._pending_comments.clear()

    def _check_duplicate_signal(self, name: str) -> None:
        """Raise ValueError if a signal with *name* already exists."""
        if name in self._signals:
            raise ValueError(f"Signal '{name}' already declared in module '{self._name}'")

    def _declare_signal(self, name: str, width: int | Expr | None = 1) -> Signal:
        """Validate and register a new signal, returning the Signal proxy."""
        _check_identifier(name)
        self._check_duplicate_signal(name)
        sig = Signal(self, name, width)
        self._signals[name] = sig
        return sig

    # --- Port declarations ---

    def input(
        self,
        name: str,
        width: int | Expr = 1,
        *,
        signed: bool = False,
        init: int | Expr | None = None,
    ) -> Signal:
        """Declare an input port."""
        range_obj = _make_range(width)
        default_expr = _to_expr_node(init) if init is not None else None
        port = Port(name, PortDirection.INPUT, width=range_obj, signed=signed, default_value=default_expr)
        self._flush_pending(port)
        self._ports.append(port)
        return self._declare_signal(name, width)

    def output(
        self,
        name: str,
        width: int | Expr = 1,
        *,
        signed: bool = False,
        init: int | Expr | None = None,
    ) -> Signal:
        """Declare an output port (wire)."""
        range_obj = _make_range(width)
        default_expr = _to_expr_node(init) if init is not None else None
        port = Port(name, PortDirection.OUTPUT, width=range_obj, signed=signed, default_value=default_expr)
        self._flush_pending(port)
        self._ports.append(port)
        return self._declare_signal(name, width)

    def output_reg(
        self,
        name: str,
        width: int | Expr = 1,
        *,
        signed: bool = False,
        init: int | Expr | None = None,
    ) -> Signal:
        """Declare an output reg port.

        Pass ``init=`` to set a default value::

            q = m.output_reg("q", width=8, init=0)

        Emits: ``output reg [7:0] q = 0``
        """
        range_obj = _make_range(width)
        default_expr = _to_expr_node(init) if init is not None else None
        port = Port(
            name, PortDirection.OUTPUT, data_type="reg", width=range_obj, signed=signed, default_value=default_expr
        )
        self._flush_pending(port)
        self._ports.append(port)
        var = Variable(name, VariableKind.REG, width=range_obj, signed=signed)
        self._variables.append(var)
        return self._declare_signal(name, width)

    def inout(
        self,
        name: str,
        width: int | Expr = 1,
        *,
        signed: bool = False,
        init: int | Expr | None = None,
    ) -> Signal:
        """Declare an inout port."""
        range_obj = _make_range(width)
        default_expr = _to_expr_node(init) if init is not None else None
        port = Port(name, PortDirection.INOUT, width=range_obj, signed=signed, default_value=default_expr)
        self._flush_pending(port)
        self._ports.append(port)
        return self._declare_signal(name, width)

    # --- Bulk declarations ---

    @staticmethod
    def _parse_signal_spec(spec: str) -> list[tuple[str, int]]:
        """Parse a bulk-declaration spec string into (name, width) pairs.

        Tokens are separated by whitespace and/or commas; each token is
        ``name`` (width 1) or ``name:width``.
        """
        entries: list[tuple[str, int]] = []
        for token in spec.replace(",", " ").split():
            name, sep, width_str = token.partition(":")
            if sep:
                try:
                    width = int(width_str)
                except ValueError:
                    raise ValueError(f"Invalid width in bulk declaration token {token!r}") from None
                if width <= 0:
                    raise ValueError(f"Width must be positive in bulk declaration token {token!r}")
            else:
                width = 1
            entries.append((name, width))
        if not entries:
            raise ValueError("Bulk declaration spec is empty")
        return entries

    def inputs(self, spec: str) -> tuple[Signal, ...]:
        """Declare several input ports from a spec string.

        Each whitespace/comma-separated token is ``name`` or ``name:width``::

            clk, rst, en = m.inputs("clk rst en")
            a, b = m.inputs("a:8 b:8")
        """
        return tuple(self.input(name, width) for name, width in self._parse_signal_spec(spec))

    def outputs(self, spec: str) -> tuple[Signal, ...]:
        """Declare several output (wire) ports from a spec string — see :meth:`inputs`."""
        return tuple(self.output(name, width) for name, width in self._parse_signal_spec(spec))

    def output_regs(self, spec: str) -> tuple[Signal, ...]:
        """Declare several output reg ports from a spec string — see :meth:`inputs`."""
        return tuple(self.output_reg(name, width) for name, width in self._parse_signal_spec(spec))

    def wires(self, spec: str) -> tuple[Signal, ...]:
        """Declare several internal wires from a spec string — see :meth:`inputs`."""
        return tuple(self.wire(name, width) for name, width in self._parse_signal_spec(spec))

    def regs(self, spec: str) -> tuple[Signal, ...]:
        """Declare several internal regs from a spec string — see :meth:`inputs`."""
        return tuple(self.reg(name, width) for name, width in self._parse_signal_spec(spec))

    # --- Interface / bus binding ---

    def interface(
        self,
        prefix: str,
        intf: Interface,
        *,
        role: str = "master",
        reg: bool = False,
    ) -> BoundInterface:
        """Bind an interface to this module, creating prefixed ports.

        Each signal in *intf* becomes a port named ``prefix_signame``.
        Direction is determined by *role*: signals whose ``src`` matches
        *role* become outputs; others become inputs.

        Args:
            prefix: Naming prefix (e.g. ``"m_axis"``).
            intf:   An :class:`Interface` template.
            role:   ``"master"`` or ``"slave"`` — determines port directions.
            reg:    If ``True``, output signals use ``output_reg`` instead
                    of ``output``.

        Returns:
            A :class:`BoundInterface` with attribute access to individual
            signals and a :meth:`~BoundInterface.port_map` helper.

        Example::

            axi_stream = (Interface("axi_stream")
                .signal("tvalid", src="master")
                .signal("tready", src="slave")
                .signal("tdata", width=8, src="master")
                .signal("tlast", src="master"))

            m = Module("producer")
            m_axis = m.interface("m_axis", axi_stream, role="master")
            # output m_axis_tvalid, input m_axis_tready,
            # output [7:0] m_axis_tdata, output m_axis_tlast
        """
        if role not in ("master", "slave"):
            raise ValueError(f"role must be 'master' or 'slave', got {role!r}")
        # M23: duplicate interface prefix
        if prefix in self._interfaces:
            raise ValueError(f"Interface prefix '{prefix}' already bound in module '{self._name}'")
        signals: dict[str, Signal] = {}
        for isig in intf._signals:
            port_name = f"{prefix}_{isig.name}"
            is_output = isig.src == role
            if is_output:
                if reg:
                    sig = self.output_reg(port_name, width=isig.width, signed=isig.signed)
                else:
                    sig = self.output(port_name, width=isig.width, signed=isig.signed)
            else:
                sig = self.input(port_name, width=isig.width, signed=isig.signed)
            signals[isig.name] = sig
        bound = BoundInterface(prefix, intf, role, signals)
        self._interfaces[prefix] = bound
        return bound

    def wire_interface(self, prefix: str, intf: Interface) -> BoundInterface:
        """Create internal wires for all signals in an interface.

        Unlike :meth:`interface` (which creates ports), this creates
        ``wire`` declarations — useful at the top level for connecting
        sub-module instances together.

        Args:
            prefix: Naming prefix (e.g. ``"axis"``).
            intf:   An :class:`Interface` template.

        Returns:
            A :class:`BoundInterface` with attribute access and
            :meth:`~BoundInterface.port_map`.

        Example::

            top = Module("top")
            axis = top.wire_interface("axis", axi_stream)
            top.instance("producer", "i_prod", ports={
                "clk": clk,
                **axis.port_map("m_axis"),
            })
        """
        signals: dict[str, Signal] = {}
        # M23: duplicate interface prefix
        if prefix in self._interfaces:
            raise ValueError(f"Interface prefix '{prefix}' already bound in module '{self._name}'")
        for isig in intf._signals:
            wire_name = f"{prefix}_{isig.name}"
            sig = self.wire(wire_name, width=isig.width, signed=isig.signed)
            signals[isig.name] = sig
        bound = BoundInterface(prefix, intf, None, signals)
        self._interfaces[prefix] = bound
        return bound

    # --- Signal declarations ---

    def wire(
        self,
        name: str,
        width: int | Expr = 1,
        *,
        signed: bool = False,
        init: int | Expr | None = None,
        depth: int | None = None,
    ) -> Signal:
        """Declare an internal wire.

        Args:
            init: Initial value (``wire [7:0] w = 8'hFF;``).
            depth: Memory depth (``wire [7:0] w [0:depth-1];``).
        """
        range_obj = _make_range(width)
        dims = [_make_dim(depth)] if depth is not None else None
        init_expr = _to_expr_node(init) if init is not None else None
        net = Net(name, NetKind.WIRE, width=range_obj, signed=signed, dimensions=dims, initial_value=init_expr)
        self._flush_pending(net)
        self._nets.append(net)
        return self._declare_signal(name, width)

    def reg(
        self,
        name: str,
        width: int | Expr = 1,
        *,
        signed: bool = False,
        init: int | Expr | None = None,
        depth: int | None = None,
    ) -> Signal:
        """Declare an internal reg.

        Args:
            init: Initial value (``reg [7:0] r = 0;``).
            depth: Memory depth (``reg [7:0] mem [0:depth-1];``).
        """
        range_obj = _make_range(width)
        dims = [_make_dim(depth)] if depth is not None else None
        init_expr = _to_expr_node(init) if init is not None else None
        var = Variable(name, VariableKind.REG, width=range_obj, signed=signed, dimensions=dims, initial_value=init_expr)
        self._flush_pending(var)
        self._variables.append(var)
        return self._declare_signal(name, width)

    def integer(self, name: str) -> Signal:
        """Declare an integer variable."""
        var = Variable(name, VariableKind.INTEGER)
        self._flush_pending(var)
        self._variables.append(var)
        return self._declare_signal(name, 32)

    def struct_var(self, name: str, type_name: str) -> Signal:
        """Declare a variable of a previously defined struct typedef.

        The struct must have been declared with :meth:`typedef_struct` and
        must be ``packed`` so its total width can be computed.

        Args:
            name: Variable name.
            type_name: Name of the typedef struct (e.g. ``"bus_t"``).
        """
        # Look up the struct typedef
        td = None
        for t in self._typedefs:
            if t.name == type_name:
                td = t
                break
        if td is None or td.struct_type is None:
            raise ValueError(f"No struct typedef named '{type_name}' found. Declare it with typedef_struct() first.")
        width = td.struct_type.total_width()
        range_obj = _make_range(width)
        var = Variable(name, VariableKind.REG, width=range_obj, type_name=type_name)
        self._flush_pending(var)
        self._variables.append(var)
        return self._declare_signal(name, width)

    def parameter(
        self, name: str, default: int | Expr = 0, *, width: int | None = None, signed: bool = False
    ) -> Signal:
        """Declare a parameter."""
        range_obj = _make_range(width) if width is not None else None
        default_expr = _to_expr_node(default)
        param = Parameter(name, default_value=default_expr, is_local=False, width=range_obj, signed=signed)
        self._flush_pending(param)
        self._parameters.append(param)
        return self._declare_signal(name, width or 32)

    def localparam(self, name: str, value: int | Expr = 0, *, width: int | None = None, signed: bool = False) -> Signal:
        """Declare a localparam."""
        range_obj = _make_range(width) if width is not None else None
        val_expr = _to_expr_node(value)
        param = Parameter(name, default_value=val_expr, is_local=True, width=range_obj, signed=signed)
        self._flush_pending(param)
        self._parameters.append(param)
        return self._declare_signal(name, width or 32)

    # --- Typedefs ---

    def _check_duplicate_typedef(self, name: str) -> None:
        """Raise ValueError if a typedef with *name* already exists."""
        for td in self._typedefs:
            if td.name == name:
                raise ValueError(f"Typedef '{name}' already declared in module '{self._name}'")

    def typedef_enum(
        self,
        name: str,
        members: list[str] | list[tuple[str, int]],
        *,
        width: int | None = None,
        base_type: str | None = None,
        signed: bool = False,
    ) -> None:
        """Declare a typedef enum.

        Args:
            name: The typedef name (e.g. ``"state_t"``).
            members: List of member names, or list of ``(name, value)``
                tuples for explicit values.
            width: Bit width of the enum (e.g. ``2`` for ``[1:0]``).
            base_type: Base type string (e.g. ``"logic"``).
            signed: Whether the enum is signed.

        Example::

            m.typedef_enum("state_t", [("IDLE", 0), ("RUN", 1), ("DONE", 2)], width=2)
        """
        _check_identifier(name, "typedef")
        self._check_duplicate_typedef(name)
        enum_members: list[EnumMember] = []
        for m_item in members:
            if isinstance(m_item, tuple):
                mname, mval = m_item
                enum_members.append(EnumMember(mname, value=Literal(mval, original_text=str(mval))))
            else:
                enum_members.append(EnumMember(m_item))
        range_obj = _make_range(width) if width is not None else None
        enum_type = EnumType(enum_members, base_type=base_type, width=range_obj, signed=signed)
        td = TypedefDecl(name, enum_type=enum_type)
        self._typedefs.append(td)

    def typedef_struct(
        self,
        name: str,
        fields: list[tuple[str, str]] | list[tuple[str, str, int]],
        *,
        packed: bool = False,
        signed: bool = False,
    ) -> None:
        """Declare a typedef struct.

        Args:
            name: The typedef name (e.g. ``"bus_t"``).
            fields: List of ``(field_name, data_type)`` or
                ``(field_name, data_type, width)`` tuples.
            packed: Whether the struct is packed.
            signed: Whether the struct is signed.

        Example::

            m.typedef_struct("bus_t", [("data", "logic", 8), ("valid", "logic")], packed=True)
        """
        _check_identifier(name, "typedef")
        self._check_duplicate_typedef(name)
        struct_fields: list[StructField] = []
        for f_item in fields:
            if len(f_item) == 3:  # noqa: PLR2004
                fname, ftype, fwidth = f_item
                range_obj = _make_range(fwidth)
                struct_fields.append(StructField(fname, ftype, width=range_obj))
            else:
                fname, ftype = f_item
                struct_fields.append(StructField(fname, ftype))
        struct_type = StructType(struct_fields, packed=packed, signed=signed)
        td = TypedefDecl(name, struct_type=struct_type)
        self._typedefs.append(td)

    def typedef_union(
        self,
        name: str,
        fields: list[tuple[str, str]] | list[tuple[str, str, int]],
        *,
        packed: bool = False,
        signed: bool = False,
    ) -> None:
        """Declare a typedef union.

        Args:
            name: The typedef name (e.g. ``"word_t"``).
            fields: List of ``(field_name, data_type)`` or
                ``(field_name, data_type, width)`` tuples.
            packed: Whether the union is packed.
            signed: Whether the union is signed.

        Example::

            m.typedef_union("word_t", [("word", "logic", 32), ("byte_val", "logic", 8)], packed=True)
        """
        _check_identifier(name, "typedef")
        self._check_duplicate_typedef(name)
        union_fields: list[StructField] = []
        for f_item in fields:
            if len(f_item) == 3:  # noqa: PLR2004
                fname, ftype, fwidth = f_item
                range_obj = _make_range(fwidth)
                union_fields.append(StructField(fname, ftype, width=range_obj))
            else:
                fname, ftype = f_item
                union_fields.append(StructField(fname, ftype))
        union_type = UnionType(union_fields, packed=packed, signed=signed)
        td = TypedefDecl(name, union_type=union_type)
        self._typedefs.append(td)

    def typedef_alias(self, name: str, type_ref: str) -> None:
        """Declare a typedef type alias.

        Args:
            name: The new type name.
            type_ref: The existing type to alias.

        Example::

            m.typedef_alias("byte_t", "logic [7:0]")
        """
        _check_identifier(name, "typedef")
        self._check_duplicate_typedef(name)
        td = TypedefDecl(name, type_ref=type_ref)
        self._typedefs.append(td)

    # --- Imports ---

    def import_pkg(self, package_name: str, item_name: str = "*") -> None:
        """Declare a package import.

        Args:
            package_name: The package to import from.
            item_name: Specific item name, or ``"*"`` for wildcard.

        Example::

            m.import_pkg("my_pkg")           # import my_pkg::*;
            m.import_pkg("my_pkg", "WIDTH")  # import my_pkg::WIDTH;
        """
        imp = ImportDecl(package_name, item_name)
        self._imports.append(imp)

    # --- Instantiation ---

    def instance(
        self,
        module_name: str,
        instance_name: str,
        ports: dict[str, Signal | Expr | int | None] | None = None,
        parameters: dict[str, Signal | Expr | int] | None = None,
    ) -> None:
        """Instantiate a sub-module.

        Args:
            module_name:   Name of the module to instantiate.
            instance_name: Instance identifier.
            ports:         Named port connections {port: signal_or_expr}.
                           Use None for unconnected ports.
            parameters:    Named parameter overrides {param: value}.

        Example::

            m.instance("counter", "u_cnt",
                       ports={"clk": clk, "rst": rst, "count": cnt},
                       parameters={"WIDTH": 16})
        """
        # M9: duplicate instance name
        if instance_name in self._instance_names:
            raise ValueError(f"Duplicate instance name '{instance_name}' in module '{self._name}'")
        self._instance_names.add(instance_name)

        param_bindings: list[ParameterBinding] = []
        if parameters:
            for name, val in parameters.items():
                param_bindings.append(ParameterBinding(name=name, value=_to_expr_node(val)))

        port_conns: list[PortConnection] = []
        if ports:
            for name, sig in ports.items():
                expr = None if sig is None else _to_expr_node(sig)
                port_conns.append(PortConnection(port_name=name, expression=expr, is_named=True))

        inst = Instance(
            module_name,
            instance_name,
            has_parameter_override=bool(param_bindings),
            parameter_bindings=param_bindings,
            port_connections=port_conns,
        )
        self._flush_pending(inst)
        self._instances.append(inst)

    # --- Continuous assignment ---

    def assign(self, lhs: Signal | Expr, rhs: Signal | Expr | int, *, comment: str | None = None) -> None:
        """Continuous assignment: ``assign lhs = rhs;``"""
        if self._block_stack:
            raise RuntimeError(
                "m.assign() creates a continuous assignment and cannot be used inside an always/initial block. "
                "Use <<= (non-blocking) or @= (blocking) instead."
            )
        # M16: assign to input port
        if isinstance(lhs, Signal) and lhs._name in {p.name for p in self._ports if p.direction == PortDirection.INPUT}:
            raise ValueError(f"Cannot assign to input port '{lhs._name}' — inputs are driven externally")
        # M10: continuous assign to reg-type signal
        if isinstance(lhs, Signal):
            reg_names = {v.name for v in self._variables}
            out_reg_names = {p.name for p in self._ports if p.data_type == "reg"}
            if lhs._name in reg_names or lhs._name in out_reg_names:
                raise ValueError(f"Cannot continuous-assign to reg '{lhs._name}' — use procedural assignment instead")
        lhs_expr = _to_expr_node(lhs)
        rhs_expr = _to_expr_node(rhs)
        ca = ContinuousAssign(lhs_expr, rhs_expr)
        self._flush_pending(ca)
        if comment:
            ca.comments.append(_make_comment(comment))
        self._continuous_assigns.append(ca)

    def assign_blocking(self, lhs: Signal | Expr, rhs: Signal | Expr | int) -> None:
        """Blocking assignment inside an always/initial block: ``lhs = rhs;``

        Equivalent to ``lhs @= rhs``.
        """
        if not self._block_stack:
            raise RuntimeError("assign_blocking() must be inside an always or initial block")
        stmt = BlockingAssign(_to_expr_node(lhs), _to_expr_node(rhs))
        self._append_stmt(stmt)

    assign_b = assign_blocking

    def assign_nonblocking(self, lhs: Signal | Expr, rhs: Signal | Expr | int) -> None:
        """Non-blocking assignment inside an always/initial block: ``lhs <= rhs;``

        Equivalent to ``lhs <<= rhs``.
        """
        if not self._block_stack:
            raise RuntimeError("assign_nonblocking() must be inside an always or initial block")
        stmt = NonblockingAssign(_to_expr_node(lhs), _to_expr_node(rhs))
        self._append_stmt(stmt)

    assign_nb = assign_nonblocking

    # --- System tasks ---

    def display(self, *args: str | Signal | Expr | int) -> None:
        r"""``$display(...)`` — print with newline.

        Args:
            *args: Format string and/or signal expressions.  The first
                   argument is typically a format string.

        Example::

            with m.initial():
                m.display("count = %d", count)
                # Emits: $display("count = %d", count);
        """
        self._system_task("$display", args)

    def write(self, *args: str | Signal | Expr | int) -> None:
        r"""``$write(...)`` — print without newline."""
        self._system_task("$write", args)

    def monitor(self, *args: str | Signal | Expr | int) -> None:
        r"""``$monitor(...)`` — print on any argument change."""
        self._system_task("$monitor", args)

    def finish(self) -> None:
        """``$finish;`` — end simulation."""
        self._system_task("$finish", ())

    def stop(self) -> None:
        """``$stop;`` — pause simulation."""
        self._system_task("$stop", ())

    def readmemh(self, filename: str, mem: Signal | Expr) -> None:
        """``$readmemh("file", mem);`` — load hex memory file.

        Example::

            mem = m.reg("mem", width=8, depth=256)
            with m.initial():
                m.readmemh("data.hex", mem)
        """
        self._system_task("$readmemh", (filename, mem))

    def readmemb(self, filename: str, mem: Signal | Expr) -> None:
        """``$readmemb("file", mem);`` — load binary memory file."""
        self._system_task("$readmemb", (filename, mem))

    def _system_task(self, name: str, args: tuple) -> None:
        """Create a SystemTaskCall statement from DSL arguments."""
        if not self._block_stack:
            raise RuntimeError(f"{name}() must be inside an always or initial block")
        expr_args = [_to_expr_node(a) for a in args]
        stmt = SystemTaskCall(name, expr_args)
        self._append_stmt(stmt)

    # --- Delay and event control ---

    def delay(self, time: int | Signal | Expr) -> _DelayContext:
        """Procedural delay: ``#time``.

        Use as a context manager for a delayed block, or as a standalone
        call for a bare delay::

            # Standalone delay
            with m.initial():
                m.delay(10)           # #10;
                rst @= 0

            # Delayed block
            with m.initial():
                with m.delay(100):    # #100 begin ... end
                    rst @= 0
                    en  @= 1

        Returns:
            A context manager.  If used without ``with``, inserts a
            bare ``#time;`` statement.
        """
        if not self._block_stack:
            raise RuntimeError("delay() must be inside an always or initial block")
        # M30: negative delay validation
        if isinstance(time, int) and not isinstance(time, bool) and time < 0:
            raise ValueError(f"Delay time must be non-negative, got {time}")
        return _DelayContext(self, _to_expr_node(time))

    def wait_posedge(self, sig: Signal | Expr) -> None:
        """``@(posedge sig);`` — wait for a rising edge.

        Example::

            with m.initial():
                m.wait_posedge(clk)
                data @= 42
        """
        if not self._block_stack:
            raise RuntimeError("wait_posedge() must be inside an always or initial block")
        edge = SensitivityEdge("posedge", _to_expr_node(sig))
        stmt = EventControl([edge])
        self._append_stmt(stmt)

    def wait_negedge(self, sig: Signal | Expr) -> None:
        """``@(negedge sig);`` — wait for a falling edge."""
        if not self._block_stack:
            raise RuntimeError("wait_negedge() must be inside an always or initial block")
        edge = SensitivityEdge("negedge", _to_expr_node(sig))
        stmt = EventControl([edge])
        self._append_stmt(stmt)

    def wait_event(self, *sensitivity: SensitivityEdge | Signal | Expr) -> _EventContext:
        """``@(event_expr)`` — wait for an event.

        Can be used standalone or as a context manager::

            # Standalone
            with m.initial():
                m.wait_event(posedge(clk))   # @(posedge clk);

            # With body
            with m.initial():
                with m.wait_event(posedge(clk)):   # @(posedge clk) begin...end
                    data @= 42

        Args:
            *sensitivity: ``posedge(sig)``, ``negedge(sig)``, or ``Signal``
                for level sensitivity.

        Returns:
            A context manager.
        """
        if not self._block_stack:
            raise RuntimeError("wait_event() must be inside an always or initial block")
        edges: list[SensitivityEdge] = []
        for s in sensitivity:
            if isinstance(s, SensitivityEdge):
                edges.append(s)
            elif isinstance(s, (Signal, Expr)):
                edges.append(SensitivityEdge("level", s._as_expr()))
            else:
                raise TypeError(f"Invalid sensitivity item: {type(s).__name__}")
        return _EventContext(self, edges)

    # --- Behavioral blocks ---

    def always(self, *sensitivity: SensitivityEdge | Signal | Expr, comment: str | None = None) -> _AlwaysContext:
        """Begin an always block.

        Args:
            *sensitivity: ``posedge(sig)``, ``negedge(sig)``, or ``Signal``
                for level sensitivity. Empty = ``@(*)``.
            comment: Optional text emitted as ``// text`` above the block.

        Returns:
            Context manager for the always block body.
        """
        pending = list(self._pending_comments)
        self._pending_comments.clear()
        return _AlwaysContext(self, sensitivity, comment=comment, pending_comments=pending)

    def seq(
        self,
        clk: Signal | Expr,
        rst: Signal | Expr | None = None,
        *,
        rst_vals: dict[Signal | Expr, int | Expr] | None = None,
        rst_active_low: bool = False,
        async_reset: bool = False,
        comment: str | None = None,
    ) -> _AlwaysContext:
        """Begin a sequential always block: shorthand for ``m.always(posedge(clk))``.

        With just a clock, ``m.seq(clk)`` is exactly ``m.always(posedge(clk))``.

        Passing ``rst=`` and ``rst_vals=`` generates the standard reset
        skeleton around the block body::

            with m.seq(clk, rst=rst, rst_vals={count: 0, state: 0}):
                count.next = count + 1     # body = the non-reset branch

        emits::

            always @(posedge clk) begin
                if (rst) begin
                    count <= 0;
                    state <= 0;
                end
                else begin
                    count <= count + 1;
                end
            end

        Args:
            clk: Clock signal (``posedge`` sensitivity).
            rst: Reset signal. Requires ``rst_vals``.
            rst_vals: Ordered ``{signal: reset_value}`` map emitted as
                non-blocking assigns in the reset branch.
            rst_active_low: Reset condition becomes ``if (!rst)``.
            async_reset: Add the reset edge to the sensitivity list
                (``negedge rst`` when active-low, else ``posedge rst``).
            comment: Optional text emitted as ``// text`` above the block.
        """
        if rst is None:
            if rst_vals is not None or rst_active_low or async_reset:
                raise TypeError("m.seq(): rst_vals/rst_active_low/async_reset require rst=")
            return self.always(posedge(clk), comment=comment)
        if not rst_vals:
            raise TypeError("m.seq(): rst= requires a non-empty rst_vals= mapping of reset assignments")

        sensitivity: tuple = (posedge(clk),)
        if async_reset:
            edge = negedge(rst) if rst_active_low else posedge(rst)
            sensitivity = (posedge(clk), edge)

        reset_assigns = [(_to_expr_node(lhs), _to_expr_node(rhs)) for lhs, rhs in rst_vals.items()]
        pending = list(self._pending_comments)
        self._pending_comments.clear()
        return _AlwaysContext(
            self,
            sensitivity,
            comment=comment,
            pending_comments=pending,
            reset=(_to_expr_node(rst), rst_active_low, reset_assigns),
        )

    def comb(self, *, comment: str | None = None) -> _AlwaysContext:
        """Begin a combinational always block: shorthand for ``m.always()`` (``always @(*)``)."""
        return self.always(comment=comment)

    def initial(self, *, comment: str | None = None) -> _InitialContext:
        """Begin an initial block.

        Args:
            comment: Optional text emitted as ``// text`` above the block.

        Returns:
            Context manager for the initial block body.
        """
        pending = list(self._pending_comments)
        self._pending_comments.clear()
        return _InitialContext(self, comment=comment, pending_comments=pending)

    # --- Control flow (within behavioral blocks) ---

    def if_(self, condition: Signal | Expr | int) -> _IfContext:
        """Begin an if block.

        Returns:
            Context manager for the if-then body.
        """
        if not self._block_stack:
            raise RuntimeError("if_() must be inside an always or initial block")
        return _IfContext(self, _to_expr_node(condition))

    def elif_(self, condition: Signal | Expr | int) -> _ElifContext:
        """Begin an elif block. Must follow ``if_()`` or another ``elif_()``.

        Returns:
            Context manager for the elif body.
        """
        if not self._block_stack:
            raise RuntimeError("elif_() must be inside an always or initial block")
        return _ElifContext(self, _to_expr_node(condition))

    def else_(self) -> _ElseContext:
        """Begin an else block. Must follow ``if_()`` or ``elif_()``.

        Returns:
            Context manager for the else body.
        """
        if not self._block_stack:
            raise RuntimeError("else_() must be inside an always or initial block")
        return _ElseContext(self)

    def case(self, expression: Signal | Expr | int, *, case_type: str = "case") -> _CaseContext:
        """Begin a case block.

        Args:
            expression: The case selector expression.
            case_type: "case", "casex", or "casez".

        Returns:
            Context manager with ``.when()`` and ``.default()`` methods.
        """
        if not self._block_stack:
            raise RuntimeError("case() must be inside an always or initial block")
        return _CaseContext(self, case_type, _to_expr_node(expression))

    def casex(self, expression: Signal | Expr | int) -> _CaseContext:
        """Begin a casex block."""
        if not self._block_stack:
            raise RuntimeError("casex() must be inside an always or initial block")
        return _CaseContext(self, "casex", _to_expr_node(expression))

    def casez(self, expression: Signal | Expr | int) -> _CaseContext:
        """Begin a casez block."""
        if not self._block_stack:
            raise RuntimeError("casez() must be inside an always or initial block")
        return _CaseContext(self, "casez", _to_expr_node(expression))

    # --- Block stack (internal) ---

    def _push_block(self) -> None:
        """Push a new statement collector onto the stack."""
        self._block_stack.append([])

    def _pop_block(self) -> list[Statement]:
        """Pop the current statement collector and return its statements."""
        return self._block_stack.pop()

    def _append_stmt(self, stmt: Statement) -> None:
        """Append a statement to the current block."""
        if not self._block_stack:
            raise RuntimeError("Statement must be inside a behavioral block (always/initial)")
        self._block_stack[-1].append(stmt)

    # --- Build ---

    def build(self) -> ModelModule:
        """Build and return the completed Module model object.

        Returns:
            A ``veriforge.model.design.Module`` ready for emission or simulation.
        """
        if self._block_stack:
            raise RuntimeError("Cannot build: unclosed behavioral block(s)")
        module = ModelModule(
            self._name,
            parameters=list(self._parameters),
            ports=list(self._ports),
            nets=list(self._nets),
            variables=list(self._variables),
            instances=list(self._instances),
            continuous_assigns=list(self._continuous_assigns),
        )
        module.always_blocks = list(self._always_blocks)
        module.initial_blocks = list(self._initial_blocks)
        module.typedefs = list(self._typedefs)
        module.imports = list(self._imports)

        # M32: warn about duplicate continuous assign targets
        assign_targets: list[str] = []
        for ca in self._continuous_assigns:
            if isinstance(ca.lhs, Identifier):
                assign_targets.append(ca.lhs.name)
        seen: dict[str, int] = {}
        for tgt in assign_targets:
            seen[tgt] = seen.get(tgt, 0) + 1
        for tgt, count in seen.items():
            if count > 1:
                warnings.warn(
                    f"Signal '{tgt}' is driven by {count} continuous assignments — "
                    f"multiple drivers may cause simulation conflicts",
                    stacklevel=2,
                )

        # Attach signal comments and attributes to model declarations
        port_names = {p.name for p in module.ports}
        for sig in self._signals.values():
            has_comments = bool(sig._comments)
            has_attrs = bool(sig._attributes)
            if not has_comments and not has_attrs:
                continue
            name = sig._name
            if name in port_names:
                for p in module.ports:
                    if p.name == name:
                        for text in sig._comments:
                            p.comments.append(_make_comment(text, "trailing"))
                        if has_attrs:
                            p.attributes.update(sig._attributes)
                        break
            else:
                for n in module.nets:
                    if n.name == name:
                        for text in sig._comments:
                            n.comments.append(_make_comment(text))
                        if has_attrs:
                            n.attributes.update(sig._attributes)
                        break
                for v in module.variables:
                    if v.name == name:
                        for text in sig._comments:
                            v.comments.append(_make_comment(text))
                        if has_attrs:
                            v.attributes.update(sig._attributes)
                        break

        return module


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def posedge(sig: Signal | Expr) -> SensitivityEdge:
    """Create a posedge sensitivity edge for ``m.always(posedge(clk))``."""
    return SensitivityEdge("posedge", _to_expr_node(sig))


def negedge(sig: Signal | Expr) -> SensitivityEdge:
    """Create a negedge sensitivity edge for ``m.always(negedge(clk))``."""
    return SensitivityEdge("negedge", _to_expr_node(sig))


def cat(*args: Signal | Expr | int | list | tuple) -> Expr:
    """Concatenation: ``{a, b, c}``.

    List/tuple arguments are flattened, so ``cat(parts)`` and
    ``cat(a, parts, b)`` work as well as ``cat(*parts)``.
    """
    flat: list = []

    def _flatten(items) -> None:
        for a in items:
            if isinstance(a, (list, tuple)):
                _flatten(a)
            else:
                flat.append(a)

    _flatten(args)
    # M28: empty concatenation
    if not flat:
        raise ValueError("cat() requires at least one argument")
    exprs = [_to_expr_node(a) for a in flat]
    builder = _find_builder(flat)
    return Expr(builder, Concatenation(exprs))


def rep(count: int, sig: Signal | Expr | int) -> Expr:
    """Replication: ``{count{sig}}``"""
    # M29: replication count validation
    if isinstance(count, int) and not isinstance(count, bool) and count <= 0:
        raise ValueError(f"Replication count must be positive, got {count}")
    count_expr = _to_lit(count) if isinstance(count, int) else _to_expr_node(count)
    sig_expr = _to_expr_node(sig)
    builder = sig._builder if isinstance(sig, Expr) else None
    return Expr(builder, Replication(count_expr, sig_expr))


def mux(cond: Signal | Expr | int, true_val: Signal | Expr | int, false_val: Signal | Expr | int) -> Expr:
    """Ternary mux: ``cond ? true_val : false_val``"""
    builder = _find_builder([cond, true_val, false_val])
    return Expr(
        builder,
        TernaryOp(_to_expr_node(cond), _to_expr_node(true_val), _to_expr_node(false_val)),
    )


class WhenChain:
    """Priority-mux expression builder — see :func:`when`.

    Not itself an expression: call :meth:`otherwise` to close the chain and
    obtain the resulting :class:`Expr`.
    """

    __slots__ = ("_arms",)

    def __init__(self, arms: list[tuple[object, object]]):
        self._arms = arms

    def when(self, cond: Signal | Expr | int, val: Signal | Expr | int) -> WhenChain:
        """Add a lower-priority arm: taken when *cond* is true and no earlier arm matched."""
        return WhenChain([*self._arms, (cond, val)])

    def otherwise(self, default: Signal | Expr | int) -> Expr:
        """Close the chain with the value used when no condition matched."""
        parts: list = [default]
        for cond, val in self._arms:
            parts.extend((cond, val))
        builder = _find_builder(parts)
        result = _to_expr_node(default)
        for cond, val in reversed(self._arms):
            result = TernaryOp(_to_expr_node(cond), _to_expr_node(val), result)
        return Expr(builder, result)


def when(cond: Signal | Expr | int, val: Signal | Expr | int) -> WhenChain:
    """Start a priority-mux chain — a readable alternative to nested :func:`mux`::

        value = when(c1, v1).when(c2, v2).otherwise(v3)
        # c1 ? v1 : (c2 ? v2 : v3)

    The chain must be closed with ``.otherwise(default)`` before use in an
    expression or assignment.
    """
    return WhenChain([(cond, val)])


def select(
    sel: Signal | Expr,
    cases: dict[int | Signal | Expr, Signal | Expr | int],
    default: Signal | Expr | int,
) -> Expr:
    """Case-style mux: compare *sel* against each key in order::

        nxt = select(state, {IDLE: s_idle, BUSY: s_busy}, default=state)
        # (state == IDLE) ? s_idle : ((state == BUSY) ? s_busy : state)

    *default* is required — hardware muxes need a value for the unmatched case.
    """
    chain: WhenChain | None = None
    for key, val in cases.items():
        arm_cond = sel == key
        chain = when(arm_cond, val) if chain is None else chain.when(arm_cond, val)
    if chain is None:
        raise ValueError("select() requires at least one case")
    return chain.otherwise(default)


def land(a: Signal | Expr | int, b: Signal | Expr | int) -> Expr:
    """Logical AND: ``a && b``"""
    builder = _find_builder([a, b])
    return Expr(builder, BinaryOp("&&", _to_expr_node(a), _to_expr_node(b)))


def lor(a: Signal | Expr | int, b: Signal | Expr | int) -> Expr:
    """Logical OR: ``a || b``"""
    builder = _find_builder([a, b])
    return Expr(builder, BinaryOp("||", _to_expr_node(a), _to_expr_node(b)))


def lnot(a: Signal | Expr | int) -> Expr:
    """Logical NOT: ``!a``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, UnaryOp("!", _to_expr_node(a)))


def reduce_and(a: Signal | Expr) -> Expr:
    """Reduction AND: ``&a``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, UnaryOp("&", _to_expr_node(a)))


def reduce_or(a: Signal | Expr) -> Expr:
    """Reduction OR: ``|a``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, UnaryOp("|", _to_expr_node(a)))


def reduce_xor(a: Signal | Expr) -> Expr:
    """Reduction XOR: ``^a``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, UnaryOp("^", _to_expr_node(a)))


def ashl(a: Signal | Expr | int, b: Signal | Expr | int) -> Expr:
    """Arithmetic left shift: ``a <<< b``"""
    builder = _find_builder([a, b])
    return Expr(builder, BinaryOp("<<<", _to_expr_node(a), _to_expr_node(b)))


def ashr(a: Signal | Expr | int, b: Signal | Expr | int) -> Expr:
    """Arithmetic right shift: ``a >>> b``"""
    builder = _find_builder([a, b])
    return Expr(builder, BinaryOp(">>>", _to_expr_node(a), _to_expr_node(b)))


def case_eq(a: Signal | Expr | int, b: Signal | Expr | int) -> Expr:
    """Case equality: ``a === b``"""
    builder = _find_builder([a, b])
    return Expr(builder, BinaryOp("===", _to_expr_node(a), _to_expr_node(b)))


def case_ne(a: Signal | Expr | int, b: Signal | Expr | int) -> Expr:
    """Case inequality: ``a !== b``"""
    builder = _find_builder([a, b])
    return Expr(builder, BinaryOp("!==", _to_expr_node(a), _to_expr_node(b)))


def clog2(a: Signal | Expr | int) -> Expr:
    """System function: ``$clog2(a)``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, FunctionCall("$clog2", [_to_expr_node(a)], is_system=True))


def sim_time() -> Expr:
    """System function: ``$time``"""
    return Expr(None, FunctionCall("$time", [], is_system=True))


def signed(a: Signal | Expr | int) -> Expr:
    """System function: ``$signed(a)``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, FunctionCall("$signed", [_to_expr_node(a)], is_system=True))


def unsigned(a: Signal | Expr | int) -> Expr:
    """System function: ``$unsigned(a)``"""
    builder = a._builder if isinstance(a, Expr) else None
    return Expr(builder, FunctionCall("$unsigned", [_to_expr_node(a)], is_system=True))

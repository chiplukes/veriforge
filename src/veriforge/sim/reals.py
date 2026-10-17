"""``real`` / ``realtime`` (IEEE 1800-2017 6.12), lowered onto 64-bit vectors.

A real is held as its IEEE 754 double bit pattern in a ``[63:0]`` variable
(2-state: it starts at 0.0), and every real operation becomes a call of the
internal system function ``$__vf_real(op, a, b)``, which each engine
implements natively (`real_op` here for reference and vm; C doubles in
vm-fast and compiled). Negation (flip the sign bit) and truthiness (any bit
but the sign set) are plain Verilog. Operations on literals are folded here.

Typing follows the LRM: an operator with a real operand is real (``+ - * /
**``, unary ``-``/``+``, ``?:`` with a real branch); a comparison of reals
is a 1-bit integer; ``!``, ``&&``, ``||`` and conditions test ``!= 0.0``.
An integral operand of a real operator is converted at its own
(self-determined) width and signedness, x/z bits read as 0 -- ``(a + b) *
1.0`` of two 8-bit regs wraps at 8 bits, as in Icarus and Verilator. A real
in an integral context (an integral assignment target, an index, a width
cast, ``%d``) rounds to the nearest integer, ties away from zero, as a
64-bit signed value. Bitwise, shift, reduction, concatenation and select
operations on a real are errors, as in the LRM.

``$display``-family arguments that are real are wrapped in the marker
``$__vf_rdisp``, which `display_format.lower_display_args` (run later)
turns into the ``REAL_FLAG`` / ``REAL_ALT_FLAG`` format flags (an
unformatted real prints as C's ``%#g``, Icarus's default) or, under an
integer specifier, into the rounded integer printed unpadded (Icarus).

Not supported (raise ``NotImplementedError``): converting an integral
operand wider than 64 bits, and ``case`` on a real expression.
"""

from __future__ import annotations

import copy
import math
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.base import VerilogNode
from veriforge.model.expressions import (
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
    StreamingConcatenation,
    StringLiteral,
    TernaryOp,
    UnaryOp,
    WidthCast,
)
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    EventControl,
    NonblockingAssign,
    SensitivityEdge,
    SystemTaskCall,
    TaskEnable,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.semantics import expr_signed, expr_width

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.functions import FunctionDecl, TaskDecl

REAL_FUNC = "$__vf_real"
DISPLAY_MARKER = "$__vf_rdisp"

# `$__vf_real(op, a, b)` operations. a and b are double bit patterns, except
# ITOR_S/ITOR_U's a (an integer of b bits, b > 0) and the unary ops' b (0).
ITOR_S, ITOR_U, ADD, SUB, MUL, DIV, POW, LT, LE, EQ, RTOI, TRUNC = range(12)
COMPARE_OPS = frozenset((LT, LE, EQ))  # (1-bit results; the others are 64 bits)
_BINARY = {"+": ADD, "-": SUB, "*": MUL, "/": DIV, "**": POW}
_SIGN = 1 << 63
# Every NaN a real operation makes (C and Python alike would otherwise keep
# the hardware's sign: x86's 0.0/0.0 is -nan).
CANONICAL_NAN = 0x7FF8000000000000
_MASK64 = (1 << 64) - 1
_DISPLAY_TASKS = {"$display", "$write", "$monitor", "$strobe", "$info", "$warning", "$error", "$fatal"}
_FILE_TASKS = {"$fdisplay", "$fwrite"}
_RADIX_TASKS = {
    f"{base}{radix}"
    for base in ("$display", "$write", "$strobe", "$monitor", "$fdisplay", "$fwrite")
    for radix in "hbo"
}
_SKIP_SLOTS = {"_parse_tree", "attributes", "comments", "loc", "parent", "drivers", "loads"}


# ── The operations (reference and vm; vm-fast and compiled mirror them in C) ──


def to_bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def from_bits(bits: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", bits & _MASK64))[0]


def real_to_int(x: float, *, truncate: bool = False) -> int:
    """*x* as a 64-bit two's complement integer: rounded half away from zero
    (an assignment's conversion) or truncated (`$rtoi`); the exact value
    modulo 2**64 when it doesn't fit, as `i = 1e10;` keeps its low 32 bits
    in Icarus. NaN and infinities give 0."""
    if math.isnan(x) or math.isinf(x):
        return 0
    n = math.trunc(x)  # (exact: a double's fraction is exact)
    if not truncate and abs(x - n) >= 0.5:  # noqa: PLR2004
        n += 1 if x > 0 else -1
    return n & _MASK64


def int_to_real(val: int, mask: int, width: int, *, signed: bool) -> float:
    """An integer operand as a double: x/z bits read as 0."""
    width = max(width, 1)
    v = val & ~mask & ((1 << width) - 1)
    if signed and v >> (width - 1):
        v -= 1 << width
    try:
        return float(v)
    except OverflowError:
        return -math.inf if v < 0 else math.inf


def _divide(a: float, b: float) -> float:
    if b != 0.0:
        return a / b
    if a == 0.0 or math.isnan(a):
        return math.nan
    return math.copysign(math.inf, a) * math.copysign(1.0, b)


def _power(a: float, b: float) -> float:
    try:
        return math.pow(a, b)
    except OverflowError:
        return math.inf if a > 0 or (float(b).is_integer() and int(b) % 2 == 0) else -math.inf
    except ValueError:  # (a negative base to a fractional power; 0 ** negative)
        return math.inf if a == 0.0 else math.nan


def real_op(op: int, a: int, a_mask: int, a_width: int, b: int) -> tuple[int, int]:
    """``$__vf_real(op, a, b)``: (value, width). *a_mask* matters only to
    ITOR_S/ITOR_U (a real's bits are never x)."""
    if op in (ITOR_S, ITOR_U):
        return to_bits(int_to_real(a, a_mask, b or a_width, signed=op == ITOR_S)), 64
    x = from_bits(a)
    if op == RTOI:
        return real_to_int(x), 64
    if op == TRUNC:
        return real_to_int(x, truncate=True), 64
    y = from_bits(b)
    if op == LT:
        return int(x < y), 1
    if op == LE:
        return int(x <= y), 1
    if op == EQ:
        return int(x == y), 1
    if op == ADD:
        r = x + y
    elif op == SUB:
        r = x - y
    elif op == MUL:
        r = x * y
    elif op == DIV:
        r = _divide(x, y)
    elif op == POW:
        r = _power(x, y)
    else:
        raise ValueError(f"$__vf_real: unknown operation {op}")
    return (CANONICAL_NAN if math.isnan(r) else to_bits(r)), 64


def real_call_width(call: FunctionCall) -> int:
    """The width of a ``$__vf_real`` call's result."""
    op = call.arguments[0]
    return 1 if isinstance(op, Literal) and op.value in COMPARE_OPS else 64


# ── The lowering ─────────────────────────────────────────────────────────


def _lit64(bits: int) -> Literal:
    return Literal(bits & _MASK64, width=64, base="h", original_text=f"64'h{bits & _MASK64:016x}")


def _lit32(value: int) -> Literal:
    return Literal(value, width=32, original_text=f"32'd{value}")


def _call(op: int, a: Expression, b: Expression | None = None) -> Expression:
    """``$__vf_real(op, a, b)``, folded when *a* and *b* are literals."""
    b = b if b is not None else _lit64(0)
    if op not in (ITOR_S, ITOR_U) and _is_lit(a) and _is_lit(b):
        value, width = real_op(op, _lit_int(a), 0, 64, _lit_int(b))
        return Literal(value, width=width, original_text=f"{width}'h{value:x}")
    return FunctionCall(REAL_FUNC, [_lit32(op), a, b])


def _lit_int(e: Expression) -> int:
    """A literal's integer value (callers check `_is_lit` first)."""
    return int(e.value)  # type: ignore[attr-defined]


def _is_lit(e: Expression) -> bool:
    return isinstance(e, Literal) and isinstance(e.value, int) and not e.is_x and not e.is_z


def _neg(a: Expression) -> Expression:
    if _is_lit(a):
        return _lit64(_lit_int(a) ^ _SIGN)
    return BinaryOp("^", a, _lit64(_SIGN))


def _truth(a: Expression) -> Expression:
    """1 when the double *a* isn't +/-0.0 (NaN is true)."""
    return BinaryOp("!=", BinaryOp("&", a, _lit64(_SIGN - 1)), _lit64(0))


def _as_signed_int(real: Expression, op: int = RTOI) -> Expression:
    return FunctionCall("$signed", [_call(op, real)])


class _Scope:
    """Which names are real (and the values of real parameters) in a module
    or a function/task body, plus the width/signedness lookups `itor` needs."""

    def __init__(  # noqa: PLR0913
        self,
        reals: set[str],
        params: dict[str, int],
        real_memories: dict[str, int],
        functions: dict[str, FunctionDecl],
        tasks: dict[str, TaskDecl],
        widths: dict[str, int],
        signed: set[str],
        env: dict[str, int],
    ) -> None:
        self.reals = reals
        self.params = params  # name -> bits
        self.real_memories = real_memories  # name -> number of unpacked dimensions
        self.functions = functions
        self.tasks = tasks
        self.widths = widths
        self.signed = signed
        self.env = env

    def child(self, reals: set[str], not_real: set[str], widths: dict[str, int], signed: set[str]) -> _Scope:
        out = copy.copy(self)
        out.reals = (self.reals - not_real) | reals
        out.params = {k: v for k, v in self.params.items() if k not in not_real | reals}
        out.real_memories = {k: v for k, v in self.real_memories.items() if k not in not_real | reals}
        out.widths = {**self.widths, **widths}
        out.signed = (self.signed - not_real) | signed
        return out


class _Lowerer:
    def __init__(self, scope: _Scope) -> None:
        self.scope = scope

    # Expressions: lower(e) -> (lowered, is_real)

    def real(self, e: Expression) -> Expression:
        """*e* as a double bit pattern."""
        out, is_real = self.lower(e)
        if is_real:
            return out
        width = expr_width(e, lambda n: self.scope.widths.get(n, 32), self.scope.env)
        if width > 64:  # noqa: PLR2004
            raise NotImplementedError(f"converting a {width}-bit integer to real (at most 64 bits are supported)")
        signed = self._signed(e)
        if _is_lit(out):
            value = to_bits(int_to_real(_lit_int(out), 0, width, signed=signed))
            return _lit64(value)
        return FunctionCall(REAL_FUNC, [_lit32(ITOR_S if signed else ITOR_U), out, _lit32(width)])

    def integral(self, e: Expression) -> Expression:
        """*e* in an integral context (a real rounds)."""
        out, is_real = self.lower(e)
        return _as_signed_int(out) if is_real else out

    def condition(self, e: Expression) -> Expression:
        out, is_real = self.lower(e)
        return _truth(out) if is_real else out

    def _signed(self, e: Expression) -> bool:
        try:
            return expr_signed(
                e,
                lambda n: n in self.scope.signed,
                lambda f: bool(getattr(self.scope.functions.get(f), "signed", False)),
            )
        except Exception:  # noqa: BLE001 -- unusual expression: unsigned
            return False

    def lower(self, e: Expression) -> tuple[Expression, bool]:  # noqa: PLR0911, PLR0912
        scope = self.scope
        if isinstance(e, Literal):
            if isinstance(e.value, float):
                return _lit64(to_bits(e.value)), True
            return e, False
        if isinstance(e, Identifier):
            name = ".".join([*e.hierarchy, e.name]) if e.hierarchy else e.name
            if name in scope.params:
                return _lit64(scope.params[name]), True
            if name in scope.reals:
                return e, True
            return e, False
        if isinstance(e, BitSelect):
            node: Expression = e
            depth = 0
            while isinstance(node, BitSelect):
                node = node.target
                depth += 1
            root = _name(node)
            if root is not None and (root in scope.reals or root in scope.params):
                raise TypeError(f"bit-select of real {root!r}")
            target, _ = self.lower(e.target) if isinstance(e.target, BitSelect) else (e.target, False)
            new = BitSelect(target, self.integral(e.index), loc=e.loc)
            is_real = root is not None and scope.real_memories.get(root) == depth
            return new, is_real
        if isinstance(e, (RangeSelect, PartSelect)):
            root = _name(e.target)
            if root is not None and (root in scope.reals or root in scope.params or root in scope.real_memories):
                raise TypeError(f"part-select of real {root!r}")
            return self._generic(e), False
        if isinstance(e, UnaryOp):
            operand, is_real = self.lower(e.operand)
            if not is_real:
                return UnaryOp(e.op, operand, loc=e.loc), False
            if e.op == "-":
                return _neg(operand), True
            if e.op == "+":
                return operand, True
            if e.op == "!":
                return UnaryOp("!", _truth(operand), loc=e.loc), False
            raise TypeError(f"operator {e.op!r} on a real operand")
        if isinstance(e, BinaryOp):
            return self._binary(e)
        if isinstance(e, TernaryOp):
            cond = self.condition(e.condition)
            t, t_real = self.lower(e.true_expr)
            f, f_real = self.lower(e.false_expr)
            if t_real or f_real:
                return TernaryOp(cond, self.real(e.true_expr), self.real(e.false_expr), loc=e.loc), True
            return TernaryOp(cond, t, f, loc=e.loc), False
        if isinstance(e, FunctionCall):
            return self._function(e)
        if isinstance(e, WidthCast):
            return WidthCast(e.width, self.integral(e.operand), loc=e.loc), False
        if isinstance(e, Mintypmax):
            return self.lower(e.typ_val)
        if isinstance(e, (Concatenation, Replication, StreamingConcatenation)):
            for part in e.walk():
                if part is not e and isinstance(part, Expression) and self._is_real_leaf(part):
                    raise TypeError("a real operand in a concatenation")
            return self._generic(e), False
        if isinstance(e, StringLiteral):
            return e, False
        return self._generic(e), False

    def _is_real_leaf(self, e: Expression) -> bool:
        return (isinstance(e, Literal) and isinstance(e.value, float)) or (
            isinstance(e, Identifier) and (e.name in self.scope.reals or e.name in self.scope.params)
        )

    def _generic(self, e: Expression) -> Expression:
        """Lower every sub-expression of *e* in an integral context."""
        out = copy.copy(e)
        _map_children(out, self.integral)
        return out

    def _binary(self, e: BinaryOp) -> tuple[Expression, bool]:
        op = e.op
        if op in ("&&", "||"):
            return BinaryOp(op, self.condition(e.left), self.condition(e.right), loc=e.loc), False
        left, l_real = self.lower(e.left)
        right, r_real = self.lower(e.right)
        if not (l_real or r_real):
            return BinaryOp(op, left, right, loc=e.loc), False
        if op in _BINARY:
            return _call(_BINARY[op], self.real(e.left), self.real(e.right)), True
        a, b = self.real(e.left), self.real(e.right)
        if op == "<":
            return _call(LT, a, b), False
        if op == "<=":
            return _call(LE, a, b), False
        if op == ">":
            return _call(LT, b, a), False
        if op == ">=":
            return _call(LE, b, a), False
        if op in ("==", "==="):
            return _call(EQ, a, b), False
        if op in ("!=", "!=="):
            return UnaryOp("!", _call(EQ, a, b)), False
        raise TypeError(f"operator {op!r} on a real operand")

    def _function(self, e: FunctionCall) -> tuple[Expression, bool]:  # noqa: PLR0911
        name = e.name.lower()
        args = e.arguments
        if e.name in (REAL_FUNC, DISPLAY_MARKER):
            return e, e.name == REAL_FUNC and real_call_width(e) == 64  # noqa: PLR2004 -- (already lowered)
        if name == "$realtime":
            return FunctionCall(REAL_FUNC, [_lit32(ITOR_U), FunctionCall("$time", []), _lit32(64)]), True
        if name == "$itor" and args:
            return self.real(args[0]), True
        if name == "$rtoi" and args:
            return FunctionCall("$signed", [WidthCast(_lit32(32), _call(TRUNC, self.real(args[0])))]), False
        if name == "$realtobits" and args:
            return self.real(args[0]), False
        if name == "$bitstoreal" and args:
            return WidthCast(_lit32(64), self.integral(args[0])), True
        if name in ("$signed", "$unsigned") and args:
            inner, is_real = self.lower(args[0])
            if is_real:
                raise TypeError(f"{e.name} of a real")
            return FunctionCall(e.name, [inner], loc=e.loc), False
        func = self.scope.functions.get(e.name)
        if func is not None:
            real_ports = [_is_real_port(p) for p in func.ports]
            new_args = [
                self.real(a) if i < len(real_ports) and real_ports[i] else self.integral(a) for i, a in enumerate(args)
            ]
            return FunctionCall(e.name, new_args, loc=e.loc), func.return_kind in ("real", "realtime")
        return FunctionCall(e.name, [self.integral(a) for a in args], loc=e.loc), False

    def is_real_target(self, lhs: Expression) -> bool:
        if isinstance(lhs, Identifier):
            full = ".".join([*lhs.hierarchy, lhs.name]) if lhs.hierarchy else lhs.name
            return full in self.scope.reals
        if isinstance(lhs, BitSelect):
            node: Expression = lhs
            depth = 0
            while isinstance(node, BitSelect):
                node = node.target
                depth += 1
            root = _name(node)
            return root is not None and self.scope.real_memories.get(root) == depth
        return False

    def target(self, lhs: Expression) -> Expression:
        """An assignment target with its indices lowered."""
        if isinstance(lhs, BitSelect):
            inner = self.target(lhs.target) if isinstance(lhs.target, BitSelect) else lhs.target
            return BitSelect(inner, self.integral(lhs.index), loc=lhs.loc)
        if isinstance(lhs, Identifier):
            return lhs
        return self._generic(lhs)

    # Statements

    def statement(self, obj: object) -> None:  # noqa: PLR0912
        if isinstance(obj, (BlockingAssign, NonblockingAssign, ContinuousAssign)):
            real_target = self.is_real_target(obj.lhs)
            obj.rhs = self.real(obj.rhs) if real_target else self.integral(obj.rhs)
            obj.lhs = self.target(obj.lhs)
            return
        if isinstance(obj, SystemTaskCall):
            self._system_task(obj)
            return
        if isinstance(obj, TaskEnable):
            task = self.scope.tasks.get(obj.task_name)
            args: list[Expression] = []
            for i, a in enumerate(obj.arguments):
                port = task.ports[i] if task is not None and i < len(task.ports) else None
                if port is not None and getattr(port.direction, "value", "input") != "input":
                    if _is_real_port(port) != self.is_real_target(a):
                        raise NotImplementedError("a task output port and its argument differ in real-ness")
                    args.append(self.target(a))
                elif port is not None and _is_real_port(port):
                    args.append(self.real(a))
                else:
                    args.append(self.integral(a))
            obj.arguments = args
            return
        if isinstance(obj, CaseStatement):
            exprs = [obj.expression, *(v for item in obj.items for v in item.values)]
            if any(self.lower(x)[1] for x in exprs):
                raise NotImplementedError("case on a real expression")
        if isinstance(obj, (SensitivityEdge, EventControl)):
            if isinstance(obj, EventControl):
                self.statement(obj.body) if obj.body is not None else None
            return
        if isinstance(obj, Variable):
            return  # (declarations: see _declarations)
        for slot in _slots(obj):
            value = getattr(obj, slot, None)
            if isinstance(value, Expression):
                setattr(obj, slot, self.condition(value) if slot == "condition" else self.integral(value))
            elif isinstance(value, VerilogNode):
                self.statement(value)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, Expression):
                        value[i] = self.integral(item)
                    elif isinstance(item, VerilogNode):
                        self.statement(item)

    def _system_task(self, task: SystemTaskCall) -> None:
        name = task.task_name.lower()
        keep = 1 if name in _FILE_TASKS or (name[:-1] in _FILE_TASKS and name in _RADIX_TASKS) else 0
        if name not in _DISPLAY_TASKS | _FILE_TASKS | _RADIX_TASKS:
            task.arguments = [self.integral(a) for a in task.arguments]
            return
        out: list[Expression] = []
        for i, arg in enumerate(task.arguments):
            if i < keep or isinstance(arg, StringLiteral):
                out.append(self.integral(arg) if not isinstance(arg, StringLiteral) else arg)
                continue
            lowered, is_real = self.lower(arg)
            out.append(FunctionCall(DISPLAY_MARKER, [lowered]) if is_real else lowered)
        task.arguments = out


def _slots(obj: object) -> list[str]:
    out: list[str] = []
    for klass in type(obj).__mro__:
        out.extend(s for s in getattr(klass, "__slots__", ()) if s not in _SKIP_SLOTS)
    return out


def _map_children(obj: object, fn: Callable[[Expression], Expression]) -> None:
    for slot in _slots(obj):
        value = getattr(obj, slot, None)
        if isinstance(value, Expression):
            setattr(obj, slot, fn(value))
        elif isinstance(value, list):
            setattr(obj, slot, [fn(v) if isinstance(v, Expression) else v for v in value])


def _name(e: Expression) -> str | None:
    if isinstance(e, Identifier):
        return ".".join([*e.hierarchy, e.name]) if e.hierarchy else e.name
    return None


def _is_real_kind(decl: object) -> bool:
    kind = getattr(decl, "kind", None)
    if kind in (VariableKind.REAL, VariableKind.REALTIME):
        return True
    return getattr(decl, "data_type", None) in ("real", "realtime")


def _is_real_port(port: object) -> bool:
    return getattr(port, "data_type", None) in ("real", "realtime")


def _mentions_real(module: Module) -> bool:
    from .sv_functions import iter_nodes  # noqa: PLC0415

    for node in iter_nodes(module, set()):
        if isinstance(node, Literal) and isinstance(node.value, float):
            return True
        if isinstance(node, (Variable,)) and _is_real_kind(node):
            return True
        if getattr(node, "data_type", None) in ("real", "realtime"):
            return True
        if getattr(node, "param_type", None) in ("real", "realtime"):
            return True
        if getattr(node, "return_kind", None) in ("real", "realtime"):
            return True
        if isinstance(node, FunctionCall) and node.name.lower() in (
            "$realtime",
            "$itor",
            "$rtoi",
            "$realtobits",
            "$bitstoreal",
        ):
            return True
    return False


def _width_of_decl(decl: object, env: dict[str, int]) -> int:
    from veriforge.semantics import range_width  # noqa: PLC0415
    from veriforge.model.variables import INTEGER_ATOM_WIDTHS  # noqa: PLC0415

    if _is_real_kind(decl):
        return 64
    width = getattr(decl, "width", None)
    if width is not None:
        return range_width(width, env)
    kind = getattr(decl, "kind", None)
    if isinstance(kind, VariableKind) and kind.value in INTEGER_ATOM_WIDTHS:
        return INTEGER_ATOM_WIDTHS[kind.value]
    if kind is VariableKind.INTEGER:
        return 32
    return 1


_RANGE64 = (63, 0)


def _range64() -> Range:
    return Range(_lit32(_RANGE64[0]), _lit32(_RANGE64[1]))


def lower_reals(module: Module, record: set[str] | None = None) -> Module:
    """Lower every real in *module*, in place (see the module docstring).
    *record* gets the names of the module-level real variables and ports
    (and real memories), for the Python API (`Simulator.read_real`)."""
    from .elaborate import _build_param_env  # noqa: PLC0415

    if not _mentions_real(module):
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    decls: list[Any] = [*module.ports, *module.nets, *module.variables]
    widths = {d.name: _width_of_decl(d, env) for d in decls}
    signed = {d.name for d in decls if getattr(d, "signed", False) and not _is_real_kind(d)}
    reals = {d.name for d in decls if _is_real_kind(d) and not getattr(d, "dimensions", None)}
    real_memories = {
        d.name: len(d.dimensions) - getattr(d, "packed_dim_count", 0)
        for d in decls
        if _is_real_kind(d) and getattr(d, "dimensions", None)
    }
    scope = _Scope(
        reals,
        {},
        real_memories,
        {f.name: f for f in module.functions},
        {t.name: t for t in module.tasks},
        widths,
        signed,
        env,
    )
    lowerer = _Lowerer(scope)
    if record is not None:
        record.update(reals, real_memories)

    # Parameters, in order: a real one is folded and substituted.
    for param in module.parameters:
        value = param.default_value
        if value is None:
            continue
        typed_real = param.param_type in ("real", "realtime")
        typed_integral = (param.param_type is not None and not typed_real) or param.width is not None
        lowered, is_real = lowerer.lower(value)
        if typed_real and not is_real:
            lowered, is_real = lowerer.real(value), True
        if is_real and typed_integral:
            # (`localparam integer W = 1.5 * 4;`: an integer, rounded)
            if not _is_lit(lowered):
                raise NotImplementedError(f"parameter {param.name!r} with a non-constant real value")
            param.default_value = _rounded_literal(_lit_int(lowered))
            continue
        if is_real:
            if not _is_lit(lowered):
                raise NotImplementedError(f"real parameter {param.name!r} with a non-constant value")
            scope.params[param.name] = _lit_int(lowered)
            # (An integral use -- a width -- sees the rounded value.)
            param.default_value = _rounded_literal(_lit_int(lowered))
            param.param_type = None
        elif param.param_type is None or not typed_real:
            param.default_value = lowered

    # Module-level processes.
    for container in (module.continuous_assigns, module.always_blocks, module.initial_blocks):
        for item in container:
            lowerer.statement(item)
    for decl in module.variables:
        _lower_decl(decl, lowerer)

    # Functions and tasks: their own ports and locals.
    subs: list[Any] = [*module.functions, *module.tasks]
    for sub in subs:
        locals_: list[Any] = [*sub.ports, *getattr(sub, "locals", ()), *getattr(sub, "local_vars", ())]
        sub_reals = {d.name for d in locals_ if _is_real_kind(d) and not getattr(d, "dimensions", None)}
        if getattr(sub, "return_kind", None) in ("real", "realtime"):
            sub_reals.add(sub.name)
        names = {d.name for d in locals_} | {sub.name}
        sub_widths = {d.name: _width_of_decl(d, env) for d in locals_}
        sub_signed = {d.name for d in locals_ if getattr(d, "signed", False) and not _is_real_kind(d)}
        sub_lowerer = _Lowerer(scope.child(sub_reals, names - sub_reals, sub_widths, sub_signed))
        sub_lowerer.statement(sub.body)
        for decl in locals_:
            if isinstance(decl, Variable):
                _lower_decl(decl, sub_lowerer)
            elif _is_real_port(decl):
                decl.data_type = None
                decl.width = _range64()
        if getattr(sub, "return_kind", None) in ("real", "realtime"):
            sub.return_kind = None
            sub.return_range = _range64()

    for port in module.ports:
        if _is_real_port(port):
            port.data_type = "reg" if getattr(port.direction, "value", "") == "output" else None
            port.width = _range64()
    return module


def _rounded_literal(bits: int) -> Expression:
    """The double *bits*, rounded, as a (signed) integer constant."""
    n = real_to_int(from_bits(bits))
    n = n - (1 << 64) if n >> 63 else n
    lit = Literal(abs(n), signed=True, original_text=str(abs(n)))
    return UnaryOp("-", lit) if n < 0 else lit


def _lower_decl(decl: Variable, lowerer: _Lowerer) -> None:
    real = _is_real_kind(decl)
    if decl.initial_value is not None:
        decl.initial_value = lowerer.real(decl.initial_value) if real else lowerer.integral(decl.initial_value)
    if real:
        decl.kind = VariableKind.REG
        decl.width = _range64()
        decl.signed = False
        if decl.initial_value is None and not decl.dimensions:
            decl.initial_value = _lit64(0)  # (2-state: a real starts at 0.0)

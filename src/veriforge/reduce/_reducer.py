"""Model-level delta-debugging reducer (see package docstring)."""

from __future__ import annotations

import multiprocessing
import re
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Any

from ..model.base import VerilogNode
from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    TernaryOp,
    UnaryOp,
    WidthCast,
)
from ..model.statements import (
    CaseStatement,
    DelayControl,
    EventControl,
    ForeverLoop,
    ForLoop,
    IfStatement,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    Statement,
    WaitStatement,
    WhileLoop,
)

if TYPE_CHECKING:
    from ..model.design import Design

# Lists whose elements the reducer tries to delete, by owner class name.
ITEM_LISTS: dict[str, tuple[str, ...]] = {
    "Design": ("modules", "packages", "interfaces"),
    "Module": (
        "instances",
        "always_blocks",
        "initial_blocks",
        "continuous_assigns",
        "generate_blocks",
        "functions",
        "tasks",
        "defparams",
        "variables",
        "nets",
        "parameters",
        "ports",
        "typedefs",
    ),
    "SeqBlock": ("statements", "local_vars"),
    "ParBlock": ("statements", "local_vars"),
    "CaseStatement": ("items",),
    "GenerateBlock": ("items",),
    "GenerateCase": ("items",),
    "Instance": ("port_connections", "parameter_bindings"),
}

_SKIP_SLOTS = {"parent", "_parse_tree", "loc", "comments", "attributes", "inferred_width", "hierarchy_map"}
_LVALUE_SLOTS = {("BlockingAssign", "lhs"), ("NonblockingAssign", "lhs"), ("ContinuousAssign", "lhs")}
_IDENT_RE = re.compile(r"[A-Za-z_][\w$]*")

_parser = None


def _print_flush(msg: str) -> None:
    print(msg, flush=True)


class ReduceError(Exception):
    """The reduction cannot start (the failure does not reproduce)."""


def parse_text(text: str, *, source_file: str = "reduce.v") -> Design:
    """Parse Verilog *text* into a linked design (as ``parse_files`` does)."""
    global _parser  # noqa: PLW0603
    from ..analysis.resolver import link_instances
    from ..transforms.tree_to_model import tree_to_design
    from ..verilog_parser import VerilogParser

    if _parser is None:
        _parser = VerilogParser(start="verilog")
    design = tree_to_design(_parser.build_tree(text), source_file=source_file)
    link_instances(design)
    return design


def emit(design: Design) -> str:
    from ..codegen.verilog_emitter import emit_design

    return emit_design(design, emit_comments=False)


def size_of(text: str) -> tuple[int, int]:
    """Reduction metric: (characters, identifier tokens), compared in order."""
    return len(text), len(_IDENT_RE.findall(text))


# ── Model traversal ──────────────────────────────────────────────────


def _slots(node: VerilogNode) -> Iterator[str]:
    seen: set[str] = set()
    for klass in type(node).__mro__:
        for s in getattr(klass, "__slots__", ()):
            if s in seen or s in _SKIP_SLOTS or s.startswith("resolved"):
                continue
            seen.add(s)
            yield s


Field = tuple[VerilogNode, str, int | None, VerilogNode, bool]  # owner, slot, index, child, in_lvalue


def iter_fields(root: VerilogNode) -> Iterator[Field]:
    """Breadth-first (owner, slot, index, child, in_lvalue) for every child node under *root*."""
    queue: list[tuple[VerilogNode, bool]] = [(root, False)]
    seen: set[int] = set()
    while queue:
        nxt: list[tuple[VerilogNode, bool]] = []
        for owner, lv in queue:
            if id(owner) in seen:
                continue
            seen.add(id(owner))
            cls = type(owner).__name__
            for slot in _slots(owner):
                val = getattr(owner, slot, None)
                child_lv = lv or (cls, slot) in _LVALUE_SLOTS
                if isinstance(val, VerilogNode):
                    yield owner, slot, None, val, child_lv
                    nxt.append((val, child_lv))
                elif isinstance(val, list):
                    for i, item in enumerate(val):
                        if isinstance(item, VerilogNode):
                            yield owner, slot, i, item, child_lv
                            nxt.append((item, child_lv))
        queue = nxt


def _get(owner: VerilogNode, slot: str, index: int | None) -> Any:
    val = getattr(owner, slot)
    return val if index is None else val[index]


def _set(owner: VerilogNode, slot: str, index: int | None, value: Any) -> None:
    if index is None:
        setattr(owner, slot, value)
    else:
        getattr(owner, slot)[index] = value


def _statement_replacements(stmt: Statement) -> list[Any]:
    """Simpler statements that could take *stmt*'s place."""
    out: list[Any] = []
    if isinstance(stmt, IfStatement):
        out += [s for s in (stmt.then_body, stmt.else_body) if s is not None]
    elif isinstance(stmt, CaseStatement):
        out += [item.body for item in stmt.items if item.body is not None]
    elif isinstance(stmt, (ForLoop, WhileLoop, RepeatLoop, ForeverLoop, DelayControl, EventControl, WaitStatement)):
        if stmt.body is not None:
            out.append(stmt.body)
    elif isinstance(stmt, (SeqBlock, ParBlock)) and len(stmt.statements) == 1 and not stmt.local_vars:
        out.append(stmt.statements[0])
    return out


def _expression_replacements(expr: Expression) -> list[Expression]:
    """Sub-expressions of *expr*, then the literal 0."""
    out: list[Expression] = []
    if isinstance(expr, BinaryOp):
        out += [expr.left, expr.right]
    elif isinstance(expr, TernaryOp):
        out += [expr.true_expr, expr.false_expr, expr.condition]
    elif isinstance(expr, (UnaryOp, WidthCast)):
        out.append(expr.operand)
    elif isinstance(expr, Concatenation):
        out += list(expr.parts)
    elif isinstance(expr, Replication):
        out.append(expr.value)
    elif isinstance(expr, FunctionCall):
        out += [a for a in expr.arguments if isinstance(a, Expression)]
    elif isinstance(expr, (BitSelect, RangeSelect, PartSelect)):
        out.append(expr.target)
    if not isinstance(expr, Literal):
        out.append(Literal(0))
    return [e for e in out if isinstance(e, Expression)]


# ── Checking ─────────────────────────────────────────────────────────


def _call(predicate: Callable[[str], bool], text: str) -> bool:
    return bool(predicate(text))


class Checker:
    """Runs a picklable predicate in a worker process, killing it on timeout."""

    def __init__(self, predicate: Callable[[str], bool], timeout: float | None) -> None:
        self.predicate = predicate
        self.timeout = timeout
        self.timeouts = 0
        self._pool: ProcessPoolExecutor | None = None

    def __call__(self, text: str) -> bool:
        if self.timeout is None:
            try:
                return _call(self.predicate, text)
            except Exception:  # noqa: BLE001 -- a crashing check is "not interesting"
                return False
        if self._pool is None:
            # (spawn everywhere: what Windows does anyway, and no fork of a threaded parent.)
            self._pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        fut = self._pool.submit(_call, self.predicate, text)
        try:
            return fut.result(timeout=self.timeout)
        except FutureTimeout:
            self.timeouts += 1
            self._kill()
            return False
        except Exception:  # noqa: BLE001
            # (A worker that died -- BrokenProcessPool -- is replaced on the next call.)
            self._kill()
            return False

    def _kill(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        for proc in list(getattr(pool, "_processes", {}).values()):
            proc.kill()
        pool.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None


# ── Reducer ──────────────────────────────────────────────────────────


# cm:8a4c62
class Reducer:
    """Shrink a design while *predicate(verilog_text)* stays true.

    Args:
        predicate: Picklable callable taking emitted Verilog text, returning
            True while the failure of interest still reproduces (see
            ``veriforge.reduce.predicates``).
        timeout: Seconds per check (run in a worker process); ``None`` runs
            checks in-process with no timeout.
        log: Progress sink (default: print, flushed -- logs are often redirected to a file).
    """

    def __init__(
        self,
        predicate: Callable[[str], bool],
        *,
        timeout: float | None = 120.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.check = Checker(predicate, timeout)
        self.log = log or _print_flush
        self.checks = 0
        self.successes = 0
        self._tried: set[str] = set()
        self._design: Design | None = None
        self._text = ""

    def reduce(self, design: Design) -> str:
        """Reduce *design* in place; return the smallest interesting Verilog text found."""
        t0 = time.time()
        self._design = design
        self._text = emit(design)
        start_size = size_of(self._text)
        if not self.check(self._text):
            self.check.close()
            raise ReduceError(
                "the failure does not reproduce on the design as emitted by veriforge "
                "(check the predicate, or the emit round trip lost what triggers it)"
            )
        self.log(f"[reduce] start: {start_size[0]} chars, {self._text.count(chr(10))} lines")
        try:
            rnd = 0
            while True:
                rnd += 1
                progress = False
                for name, run in (
                    ("delete", self._pass_delete),
                    ("statements", self._pass_statements),
                    ("expressions", self._pass_expressions),
                ):
                    before = self.successes
                    run()
                    if self.successes != before:
                        progress = True
                        self.log(
                            f"[reduce] round {rnd} {name}: {len(self._text)} chars, "
                            f"{self._text.count(chr(10))} lines ({self.checks} checks, {time.time() - t0:.0f}s)"
                        )
                if not progress:
                    break
        finally:
            self.check.close()
        self.log(
            f"[reduce] done: {start_size[0]} -> {len(self._text)} chars in {self.checks} checks, "
            f"{time.time() - t0:.0f}s" + (f" ({self.check.timeouts} timed out)" if self.check.timeouts else "")
        )
        return self._text

    # -- one attempt --------------------------------------------------

    def _try(self) -> bool:
        assert self._design is not None
        try:
            text = emit(self._design)
        except Exception:  # noqa: BLE001 -- an edit the emitter cannot print
            return False
        if text in self._tried or size_of(text) >= size_of(self._text):
            return False
        self._tried.add(text)
        self.checks += 1
        if self.check(text):
            self._text = text
            self.successes += 1
            return True
        return False

    # -- passes -------------------------------------------------------

    def _owners(self) -> list[VerilogNode]:
        assert self._design is not None
        owners: list[VerilogNode] = [self._design]
        owners += [child for _o, _s, _i, child, _lv in iter_fields(self._design)]
        return owners

    def _pass_delete(self) -> None:
        for owner in self._owners():
            for slot in ITEM_LISTS.get(type(owner).__name__, ()):
                lst = getattr(owner, slot, None)
                if not isinstance(lst, list) or not lst:
                    continue
                k = max(len(lst) // 2, 1)
                while True:
                    i = 0
                    while i < len(lst):
                        removed = lst[i : i + k]
                        del lst[i : i + k]
                        if not self._try():
                            lst[i:i] = removed
                            i += k
                    if k == 1:
                        break
                    k = max(k // 2, 1)

    def _pass_statements(self) -> None:
        assert self._design is not None
        for owner, slot, index, child, _lv in list(iter_fields(self._design)):
            if not isinstance(child, Statement) or _get(owner, slot, index) is not child:
                continue
            for repl in _statement_replacements(child):
                _set(owner, slot, index, repl)
                if self._try():
                    break
                _set(owner, slot, index, child)
            else:
                if isinstance(child, IfStatement) and child.else_body is not None:
                    old = child.else_body
                    child.else_body = None
                    if not self._try():
                        child.else_body = old

    def _pass_expressions(self) -> None:
        assert self._design is not None
        for owner, slot, index, child, in_lvalue in list(iter_fields(self._design)):
            if in_lvalue or not isinstance(child, Expression) or isinstance(child, Literal):
                continue
            if type(owner).__name__ == "SensitivityEdge" or _get(owner, slot, index) is not child:
                continue
            for repl in _expression_replacements(child):
                _set(owner, slot, index, repl)
                if self._try():
                    break
                _set(owner, slot, index, child)

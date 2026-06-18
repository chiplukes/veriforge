"""Statement executor for the Verilog simulation engine.

Walks the model's Statement tree and mutates simulation state via ExecContext.
Handles blocking/non-blocking assignment semantics, control flow (if, case,
loops), and delay/event controls.

Uses flat if/elif dispatch with ``type(stmt) is X`` for fast exact-type
matching (single pointer compare, no MRO walk).
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from veriforge.model.expressions import BitSelect, Concatenation, FunctionCall, Identifier, PartSelect, RangeSelect
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventControl,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)

from .evaluator import EvalContext, ExpressionEvaluator, _expr_signed, _resolve_struct_write_target
from .value import Value

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from veriforge.model.expressions import Expression


class StopExecution(Exception):
    """Raised by $finish to halt simulation."""

    __slots__ = ()


class SuspendExecution(Exception):
    """Raised by delay/event control to suspend the current process.

    The scheduler should catch this, record the suspension reason,
    and resume the process when the condition is met.
    """

    __slots__ = ("delay", "events")

    def __init__(self, *, delay: int | None = None, events: list | None = None) -> None:
        super().__init__()
        self.delay = delay
        self.events = events or []


class DisableBlock(Exception):
    """Raised by 'disable' statement to exit a named block."""

    __slots__ = ("target",)

    def __init__(self, target: str) -> None:
        super().__init__()
        self.target = target


class NbaEntry:
    """A scheduled non-blocking assignment update."""

    __slots__ = ("lhs_name", "value")

    def __init__(self, lhs_name: str, value: Value) -> None:
        self.lhs_name = lhs_name
        self.value = value


class MemNbaEntry:
    """A scheduled non-blocking assignment to a memory element."""

    __slots__ = ("index", "mem_name", "value")

    def __init__(self, mem_name: str, index: int, value: Value) -> None:
        self.mem_name = mem_name
        self.index = index
        self.value = value


class MemRangeNbaEntry:
    """A scheduled non-blocking assignment to a bit range of a memory element.

    e.g. memory[addr][7:0] <= value.  Defers the read-modify-write to
    apply_nba time so multiple byte-lane writes accumulate correctly.
    """

    __slots__ = ("index", "lsb", "mem_name", "msb", "value")

    def __init__(self, mem_name: str, index: int, msb: int, lsb: int, value: Value) -> None:
        self.mem_name = mem_name
        self.index = index
        self.msb = msb
        self.lsb = lsb
        self.value = value


class StructFieldNbaEntry:
    """A scheduled non-blocking assignment to a struct field.

    Defers field insertion to apply_nba time so multiple field writes
    to the same struct don't clobber each other.
    """

    __slots__ = ("base_name", "field_value", "lsb", "msb")

    def __init__(self, base_name: str, msb: int, lsb: int, field_value: Value) -> None:
        self.base_name = base_name
        self.msb = msb
        self.lsb = lsb
        self.field_value = field_value


class StatementExecutor:  # cm:c2f9a1
    """Walk a Statement tree and execute it, mutating an EvalContext.

    Non-blocking assignments are collected into the nba_queue rather
    than applied immediately. The caller (scheduler) applies them
    at the end of the Active region.

    Attributes:
        evaluator:  Expression evaluator instance.
        nba_queue:  List of NbaEntry, populated by non-blocking assigns.
        display_output: List of strings from $display calls.
        time:       Current simulation time (for $time).
        loop_limit: Maximum loop iterations (prevents infinite loops in tests).
    """

    __slots__ = (
        "_function_map",
        "_task_map",
        "_vcd_filename",
        "_vcd_writer",
        "_write_buffer",
        "display_output",
        "evaluator",
        "loop_limit",
        "nba_queue",
        "time",
    )

    def __init__(
        self,
        evaluator: ExpressionEvaluator | None = None,
        *,
        loop_limit: int = 100_000,
    ) -> None:
        self.evaluator = evaluator or ExpressionEvaluator()
        self.evaluator._executor = self  # back-reference for function calls
        self.nba_queue: list[NbaEntry] = []
        self.display_output: list[str] = []
        self._write_buffer: str = ""
        self.time: int = 0
        self.loop_limit = loop_limit
        self._function_map: dict[str, object] = {}
        self._task_map: dict[str, object] = {}
        self._vcd_filename: str | None = None
        self._vcd_writer: object | None = None  # VcdWriter, lazily imported

    def execute(self, stmt: Statement, ctx: EvalContext) -> None:  # noqa: PLR0911, PLR0912, PLR0915
        """Execute a statement tree, mutating ctx."""
        stype = type(stmt)

        # -- Blocking assignment (most frequent) -------------------
        if stype is BlockingAssign:
            if self._copy_whole_memory(stmt.lhs, stmt.rhs, ctx, immediate=True):
                return
            lhs_w = self._lhs_width(stmt.lhs, ctx)
            rhs_val = self.evaluator.eval(stmt.rhs, ctx, width=lhs_w)
            rhs_val = self._maybe_sign_extend(stmt.rhs, rhs_val, stmt.lhs, ctx)
            self._write_target(stmt.lhs, rhs_val, ctx, immediate=True)
            return

        # -- Non-blocking assignment -------------------------------
        if stype is NonblockingAssign:
            if self._copy_whole_memory(stmt.lhs, stmt.rhs, ctx, immediate=False):
                return
            lhs_w = self._lhs_width(stmt.lhs, ctx)
            rhs_val = self.evaluator.eval(stmt.rhs, ctx, width=lhs_w)
            rhs_val = self._maybe_sign_extend(stmt.rhs, rhs_val, stmt.lhs, ctx)
            self._write_target(stmt.lhs, rhs_val, ctx, immediate=False)
            return

        # -- Sequential block (begin...end) ------------------------
        if stype is SeqBlock:
            try:
                for s in stmt.statements:
                    self.execute(s, ctx)
            except DisableBlock as e:
                if stmt.name and e.target == stmt.name:
                    return  # exit this block
                raise
            return

        # -- Parallel block (fork...join) --------------------------
        if stype is ParBlock:
            try:
                for s in stmt.statements:
                    self.execute(s, ctx)
            except DisableBlock as e:
                if stmt.name and e.target == stmt.name:
                    return
                raise
            return

        # -- If statement ------------------------------------------
        if stype is IfStatement:
            cond = self.evaluator.eval(stmt.condition, ctx)
            if cond.is_defined and cond.val:
                if stmt.then_body:
                    self.execute(stmt.then_body, ctx)
            elif stmt.else_body:
                self.execute(stmt.else_body, ctx)
            return

        # -- Case statement ----------------------------------------
        if stype is CaseStatement:
            sel = self.evaluator.eval(stmt.expression, ctx)
            for item in stmt.items:
                if item.is_default:
                    if item.body:
                        self.execute(item.body, ctx)
                    return
                for val_expr in item.values:
                    val = self.evaluator.eval(val_expr, ctx)
                    if _case_match(stmt.case_type, sel, val):
                        if item.body:
                            self.execute(item.body, ctx)
                        return
            return

        # -- For loop ----------------------------------------------
        if stype is ForLoop:
            self.execute(stmt.init, ctx)
            iterations = 0
            # For signed loop variables (e.g. `int i`), detect underflow
            signed_var_name = None
            if stmt.signed_var:
                lhs = stmt.init.lhs
                if type(lhs) is Identifier:
                    signed_var_name = lhs.name
            while True:
                # Signed underflow check: if MSB set, variable is "negative"
                if signed_var_name is not None:
                    v = ctx.read_signal(signed_var_name)
                    if v.is_defined and v.width > 1 and (v.val >> (v.width - 1)) & 1:
                        break
                cond = self.evaluator.eval(stmt.condition, ctx)
                if not (cond.is_defined and cond.val):
                    break
                if stmt.body:
                    self.execute(stmt.body, ctx)
                self.execute(stmt.update, ctx)
                iterations += 1
                if iterations > self.loop_limit:
                    # Debug: show offending loop's init/cond/update
                    loc = getattr(stmt, "loc", None)
                    log.error(
                        "For loop exceeded %d iterations (loc=%s, init=%s, cond=%s, update=%s)",
                        self.loop_limit,
                        loc,
                        stmt.init,
                        stmt.condition,
                        stmt.update,
                    )
                    raise RuntimeError(f"For loop exceeded {self.loop_limit} iterations")
            return

        # -- While loop --------------------------------------------
        if stype is WhileLoop:
            iterations = 0
            while True:
                cond = self.evaluator.eval(stmt.condition, ctx)
                if not (cond.is_defined and cond.val):
                    break
                if stmt.body:
                    self.execute(stmt.body, ctx)
                iterations += 1
                if iterations > self.loop_limit:
                    raise RuntimeError(f"While loop exceeded {self.loop_limit} iterations")
            return

        # -- Forever loop ------------------------------------------
        if stype is ForeverLoop:
            iterations = 0
            while True:
                if stmt.body:
                    self.execute(stmt.body, ctx)
                iterations += 1
                if iterations > self.loop_limit:
                    raise RuntimeError(f"Forever loop exceeded {self.loop_limit} iterations")
            # unreachable (loop_limit or SuspendExecution/StopExecution)

        # -- Repeat loop -------------------------------------------
        if stype is RepeatLoop:
            count_val = self.evaluator.eval(stmt.count, ctx)
            if count_val.is_defined:
                count = int(count_val)
                for _ in range(min(count, self.loop_limit)):
                    if stmt.body:
                        self.execute(stmt.body, ctx)
            return

        # -- Delay control (#N) ------------------------------------
        if stype is DelayControl:
            delay_val = self.evaluator.eval(stmt.delay, ctx)
            delay = int(delay_val) if delay_val.is_defined else 0
            raise SuspendExecution(delay=delay)

        # -- Event control (@(...)) --------------------------------
        if stype is EventControl:
            raise SuspendExecution(events=stmt.events)

        # -- Wait statement ----------------------------------------
        if stype is WaitStatement:
            cond = self.evaluator.eval(stmt.condition, ctx)
            if cond.is_defined and cond.val:
                if stmt.body:
                    self.execute(stmt.body, ctx)
            else:
                raise SuspendExecution()
            return

        # -- Disable statement -------------------------------------
        if stype is DisableStatement:
            raise DisableBlock(stmt.target)

        # -- Event trigger (-> event) ------------------------------
        if stype is EventTrigger:
            current = ctx.read_signal(stmt.event)
            new_val = Value(0 if current.val else 1, width=1)
            ctx.write_signal(stmt.event, new_val)
            return

        # -- System task call --------------------------------------
        if stype is SystemTaskCall:
            self._exec_system_task(stmt, ctx)
            return

        # -- Task enable -------------------------------------------
        if stype is TaskEnable:
            self._exec_task_enable(stmt, ctx)
            return

        raise TypeError(f"Cannot execute statement type: {type(stmt).__name__}")

    # ── Coroutine-based execution (supports suspend/resume) ──────

    def execute_coroutine(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        stmt: Statement,
        ctx: EvalContext,
    ) -> Generator[SuspendExecution, None, None]:
        """Generator-based executor that yields at suspension points.

        Unlike ``execute()`` which raises ``SuspendExecution`` (losing the
        call stack), this generator *yields* it.  The scheduler saves the
        generator and calls ``next()`` to resume exactly where execution
        left off.  Python generators preserve the full call-frame stack
        across yields, so nested control flow (if/case/for/while) works
        without any special bookkeeping.

        Yields:
            ``SuspendExecution`` when execution hits ``#delay`` or ``@(event)``.

        Raises:
            ``StopExecution`` when ``$finish`` is encountered.
        """
        stype = type(stmt)

        # -- Blocking assignment -----------------------------------
        if stype is BlockingAssign:
            if self._copy_whole_memory(stmt.lhs, stmt.rhs, ctx, immediate=True):
                return
            lhs_w = self._lhs_width(stmt.lhs, ctx)
            rhs_val = self.evaluator.eval(stmt.rhs, ctx, width=lhs_w)
            rhs_val = self._maybe_sign_extend(stmt.rhs, rhs_val, stmt.lhs, ctx)
            self._write_target(stmt.lhs, rhs_val, ctx, immediate=True)
            return

        # -- Non-blocking assignment -------------------------------
        if stype is NonblockingAssign:
            if self._copy_whole_memory(stmt.lhs, stmt.rhs, ctx, immediate=False):
                return
            lhs_w = self._lhs_width(stmt.lhs, ctx)
            rhs_val = self.evaluator.eval(stmt.rhs, ctx, width=lhs_w)
            rhs_val = self._maybe_sign_extend(stmt.rhs, rhs_val, stmt.lhs, ctx)
            self._write_target(stmt.lhs, rhs_val, ctx, immediate=False)
            return

        # -- Sequential block (begin...end) ------------------------
        if stype is SeqBlock:
            try:
                for s in stmt.statements:
                    yield from self.execute_coroutine(s, ctx)
            except DisableBlock as e:
                if stmt.name and e.target == stmt.name:
                    return
                raise
            return

        # -- Parallel block (fork...join) --------------------------
        if stype is ParBlock:
            try:
                for s in stmt.statements:
                    yield from self.execute_coroutine(s, ctx)
            except DisableBlock as e:
                if stmt.name and e.target == stmt.name:
                    return
                raise
            return

        # -- If statement ------------------------------------------
        if stype is IfStatement:
            cond = self.evaluator.eval(stmt.condition, ctx)
            if cond.is_defined and cond.val:
                if stmt.then_body:
                    yield from self.execute_coroutine(stmt.then_body, ctx)
            elif stmt.else_body:
                yield from self.execute_coroutine(stmt.else_body, ctx)
            return

        # -- Case statement ----------------------------------------
        if stype is CaseStatement:
            sel = self.evaluator.eval(stmt.expression, ctx)
            for item in stmt.items:
                if item.is_default:
                    if item.body:
                        yield from self.execute_coroutine(item.body, ctx)
                    return
                for val_expr in item.values:
                    val = self.evaluator.eval(val_expr, ctx)
                    if _case_match(stmt.case_type, sel, val):
                        if item.body:
                            yield from self.execute_coroutine(item.body, ctx)
                        return
            return

        # -- For loop ----------------------------------------------
        if stype is ForLoop:
            yield from self.execute_coroutine(stmt.init, ctx)
            iterations = 0
            signed_var_name = None
            if stmt.signed_var:
                lhs = stmt.init.lhs
                if type(lhs) is Identifier:
                    signed_var_name = lhs.name
            while True:
                if signed_var_name is not None:
                    v = ctx.read_signal(signed_var_name)
                    if v.is_defined and v.width > 1 and (v.val >> (v.width - 1)) & 1:
                        break
                cond = self.evaluator.eval(stmt.condition, ctx)
                if not (cond.is_defined and cond.val):
                    break
                if stmt.body:
                    yield from self.execute_coroutine(stmt.body, ctx)
                yield from self.execute_coroutine(stmt.update, ctx)
                iterations += 1
                if iterations > self.loop_limit:
                    raise RuntimeError(f"For loop exceeded {self.loop_limit} iterations")
            return

        # -- While loop --------------------------------------------
        if stype is WhileLoop:
            iterations = 0
            while True:
                cond = self.evaluator.eval(stmt.condition, ctx)
                if not (cond.is_defined and cond.val):
                    break
                if stmt.body:
                    yield from self.execute_coroutine(stmt.body, ctx)
                iterations += 1
                if iterations > self.loop_limit:
                    raise RuntimeError(f"While loop exceeded {self.loop_limit} iterations")
            return

        # -- Forever loop ------------------------------------------
        if stype is ForeverLoop:
            iterations = 0
            while True:
                if stmt.body:
                    yield from self.execute_coroutine(stmt.body, ctx)
                iterations += 1
                if iterations > self.loop_limit:
                    raise RuntimeError(f"Forever loop exceeded {self.loop_limit} iterations")

        # -- Repeat loop -------------------------------------------
        if stype is RepeatLoop:
            count_val = self.evaluator.eval(stmt.count, ctx)
            if count_val.is_defined:
                count = int(count_val)
                for _ in range(min(count, self.loop_limit)):
                    if stmt.body:
                        yield from self.execute_coroutine(stmt.body, ctx)
            return

        # -- Delay control (#N) ------------------------------------
        if stype is DelayControl:
            delay_val = self.evaluator.eval(stmt.delay, ctx)
            delay = int(delay_val) if delay_val.is_defined else 0
            yield SuspendExecution(delay=delay)
            if stmt.body:
                yield from self.execute_coroutine(stmt.body, ctx)
            return

        # -- Event control (@(...)) --------------------------------
        if stype is EventControl:
            yield SuspendExecution(events=stmt.events)
            if stmt.body:
                yield from self.execute_coroutine(stmt.body, ctx)
            return

        # -- Wait statement ----------------------------------------
        if stype is WaitStatement:
            cond = self.evaluator.eval(stmt.condition, ctx)
            if cond.is_defined and cond.val:
                if stmt.body:
                    yield from self.execute_coroutine(stmt.body, ctx)
            else:
                yield SuspendExecution()
            return

        # -- Disable statement -------------------------------------
        if stype is DisableStatement:
            raise DisableBlock(stmt.target)

        # -- Event trigger (-> event) ------------------------------
        if stype is EventTrigger:
            current = ctx.read_signal(stmt.event)
            new_val = Value(0 if current.val else 1, width=1)
            ctx.write_signal(stmt.event, new_val)
            return

        # -- System task call --------------------------------------
        if stype is SystemTaskCall:
            self._exec_system_task(stmt, ctx)
            return

        # -- Task enable -------------------------------------------
        if stype is TaskEnable:
            self._exec_task_enable(stmt, ctx)
            return

        raise TypeError(f"Cannot execute statement type: {type(stmt).__name__}")

    def apply_nba(self, ctx: EvalContext) -> set[str]:
        """Apply all non-blocking assignment updates.

        Returns the set of signal names that actually changed (empty set
        means nothing changed).
        """
        changed: set[str] = set()
        for entry in self.nba_queue:
            if type(entry) is MemNbaEntry:
                mem_data, _ew = ctx._memories.get(entry.mem_name, (None, 0))
                if mem_data is not None and 0 <= entry.index < len(mem_data):
                    old = mem_data[entry.index]
                    if old.val != entry.value.val or old.mask != entry.value.mask:
                        mem_data[entry.index] = entry.value
                        changed.add(entry.mem_name)
                continue
            if type(entry) is MemRangeNbaEntry:
                mem_data, _ew = ctx._memories.get(entry.mem_name, (None, 0))
                if mem_data is not None and 0 <= entry.index < len(mem_data):
                    old = mem_data[entry.index]
                    updated = old.set_range(entry.msb, entry.lsb, entry.value)
                    if updated.val != old.val or updated.mask != old.mask:
                        mem_data[entry.index] = updated
                        changed.add(entry.mem_name)
                continue
            if type(entry) is StructFieldNbaEntry:
                base_val = ctx._signals.get(entry.base_name)
                if base_val is not None:
                    updated = base_val.set_range(entry.msb, entry.lsb, entry.field_value)
                    if updated.val != base_val.val or updated.mask != base_val.mask:
                        ctx._signals[entry.base_name] = updated
                        changed.add(entry.base_name)
                continue
            old = ctx.read_signal(entry.lhs_name)
            if old.val != entry.value.val or old.mask != entry.value.mask:
                ctx._signals[entry.lhs_name] = entry.value
                changed.add(entry.lhs_name)
        self.nba_queue.clear()
        return changed

    @staticmethod
    def _identifier_name(expr: Identifier) -> str:
        """Return a fully qualified identifier name."""
        if expr.hierarchy:
            return ".".join(expr.hierarchy) + "." + expr.name
        return expr.name

    def _copy_whole_memory(self, lhs: Expression, rhs: Expression, ctx: EvalContext, *, immediate: bool) -> bool:
        """Handle plain whole-memory identifier copies."""
        if type(lhs) is not Identifier or type(rhs) is not Identifier:
            return False
        lhs_name = self._identifier_name(lhs)
        rhs_name = self._identifier_name(rhs)
        if lhs_name not in ctx._memory_names or rhs_name not in ctx._memory_names:
            return False
        lhs_mem = ctx._memories.get(lhs_name)
        rhs_mem = ctx._memories.get(rhs_name)
        if lhs_mem is None or rhs_mem is None:
            return False
        lhs_data, lhs_elem_w = lhs_mem
        rhs_data, rhs_elem_w = rhs_mem
        if lhs_elem_w != rhs_elem_w or len(lhs_data) != len(rhs_data):
            return False
        copied = [val.resize(lhs_elem_w) if val.width != lhs_elem_w else val for val in rhs_data]
        if immediate:
            for idx, val in enumerate(copied):
                lhs_data[idx] = val
            if ctx._originals is not None and lhs_name not in ctx._originals:
                ctx._originals[lhs_name] = Value(0)
            return True
        for idx, val in enumerate(copied):
            self.nba_queue.append(MemNbaEntry(lhs_name, idx, val))
        return True

    def _write_target(self, lhs: Expression, value: Value, ctx: EvalContext, *, immediate: bool) -> None:  # noqa: PLR0912, PLR0915
        """Write to an assignment target (identifier, bit select, range select, concatenation)."""
        ltype = type(lhs)

        if ltype is Identifier:
            name = lhs.name
            # Hierarchical identifier: reconstruct full dotted name
            if lhs.hierarchy:
                name = ".".join(lhs.hierarchy) + "." + name
            # Inline read_signal for speed — this is the hottest path.
            current = ctx._signals.get(name)
            if current is None:
                struct_target = _resolve_struct_write_target(name, ctx)
                if struct_target is not None:
                    base_name, base_index, msb, lsb, base_val = struct_target
                    width = msb - lsb + 1
                    fval = value.resize(width) if value.width != width else value
                    if immediate:
                        updated = base_val.set_range(msb, lsb, fval)
                        if base_index is None:
                            ctx.write_signal(base_name, updated)
                        else:
                            mem_data, _elem_w = ctx._memories.get(base_name, (None, 0))
                            if mem_data is not None and 0 <= base_index < len(mem_data):
                                mem_data[base_index] = updated
                                if ctx._originals is not None and base_name not in ctx._originals:
                                    ctx._originals[base_name] = Value(0)
                    else:
                        if base_index is None:
                            self.nba_queue.append(StructFieldNbaEntry(base_name, msb, lsb, fval))
                        else:
                            self.nba_queue.append(MemRangeNbaEntry(base_name, base_index, msb, lsb, fval))
                    return
                current = Value.x(value.width)
            if current.width != value.width:
                value = value.resize(current.width)
            if immediate:
                ctx.write_signal(name, value)
            else:
                self.nba_queue.append(NbaEntry(name, value))
            return

        if ltype is BitSelect:
            if type(lhs.target) is Identifier:
                name = lhs.target.name
                if lhs.target.hierarchy:
                    name = ".".join(lhs.target.hierarchy) + "." + name
                struct_target = _resolve_struct_write_target(name, ctx)
                if struct_target is not None:
                    idx_val = self.evaluator.eval(lhs.index, ctx)
                    if idx_val.is_defined:
                        base_name, base_index, _field_msb, field_lsb, base_val = struct_target
                        idx_int = int(idx_val) - ctx._signal_bases.get(name, 0)
                        abs_idx = field_lsb + idx_int
                        bit_val = Value(value.val & 1, width=1, mask=value.mask & 1)
                        if immediate:
                            updated = base_val.set_range(abs_idx, abs_idx, bit_val)
                            if base_index is None:
                                ctx.write_signal(base_name, updated)
                            else:
                                mem_data, _elem_w = ctx._memories.get(base_name, (None, 0))
                                if mem_data is not None and 0 <= base_index < len(mem_data):
                                    mem_data[base_index] = updated
                                    if ctx._originals is not None and base_name not in ctx._originals:
                                        ctx._originals[base_name] = Value(0)
                        else:
                            if base_index is None:
                                self.nba_queue.append(StructFieldNbaEntry(base_name, abs_idx, abs_idx, bit_val))
                            else:
                                self.nba_queue.append(
                                    MemRangeNbaEntry(base_name, base_index, abs_idx, abs_idx, bit_val)
                                )
                    return
                idx_val = self.evaluator.eval(lhs.index, ctx)
                # Memory element write: mem[addr] = value
                if name in ctx._memory_names:
                    if idx_val.is_defined:
                        mem_data, elem_w = ctx._memories[name]
                        idx = int(idx_val)
                        if 0 <= idx < len(mem_data):
                            wval = value.resize(elem_w) if value.width != elem_w else value
                            if immediate:
                                mem_data[idx] = wval
                                # Flag memory as dirty so combo blocks re-trigger
                                if ctx._originals is not None and name not in ctx._originals:
                                    ctx._originals[name] = Value(0)
                            else:
                                self.nba_queue.append(MemNbaEntry(name, idx, wval))
                    return
                if idx_val.is_defined:
                    current = ctx.read_signal(name)
                    idx_int = int(idx_val)
                    # Adjust for non-zero base offset
                    idx_int -= ctx._signal_bases.get(name, 0)
                    if idx_int < 0 or idx_int >= current.width:
                        log.debug(
                            "Bit-select %s[%d] out of range (width=%d), skipping",
                            name,
                            idx_int,
                            current.width,
                        )
                        return
                    if immediate:
                        updated = current.set_bit(idx_int, value.val & 1)
                        ctx.write_signal(name, updated)
                    else:
                        bit_val = Value(value.val & 1, width=1, mask=value.mask & 1)
                        self.nba_queue.append(StructFieldNbaEntry(name, idx_int, idx_int, bit_val))
            if type(lhs.target) is BitSelect and type(lhs.target.target) is Identifier:
                name = lhs.target.target.name
                if lhs.target.target.hierarchy:
                    name = ".".join(lhs.target.target.hierarchy) + "." + name
                if name in ctx._memory_names:
                    outer_idx_val = self.evaluator.eval(lhs.target.index, ctx)
                    inner_idx_val = self.evaluator.eval(lhs.index, ctx)
                    if outer_idx_val.is_defined and inner_idx_val.is_defined:
                        mem_data, elem_w = ctx._memories[name]
                        outer_idx = int(outer_idx_val)
                        inner_idx = int(inner_idx_val) - ctx._memory_bases.get(name, 0)
                        if 0 <= outer_idx < len(mem_data) and 0 <= inner_idx < elem_w:
                            bit_val = Value(value.val & 1, width=1, mask=value.mask & 1)
                            if immediate:
                                mem_data[outer_idx] = mem_data[outer_idx].set_bit(inner_idx, bit_val.val)
                                if ctx._originals is not None and name not in ctx._originals:
                                    ctx._originals[name] = Value(0)
                            else:
                                self.nba_queue.append(MemRangeNbaEntry(name, outer_idx, inner_idx, inner_idx, bit_val))
            return

        if ltype is RangeSelect:
            if type(lhs.target) is BitSelect and type(lhs.target.target) is Identifier:
                # Memory element partial write: memory[addr][msb:lsb] <= value
                name = lhs.target.target.name
                if lhs.target.target.hierarchy:
                    name = ".".join(lhs.target.target.hierarchy) + "." + name
                if name in ctx._memory_names:
                    idx_val = self.evaluator.eval(lhs.target.index, ctx)
                    msb_val = self.evaluator.eval(lhs.msb, ctx)
                    lsb_val = self.evaluator.eval(lhs.lsb, ctx)
                    if idx_val.is_defined and msb_val.is_defined and lsb_val.is_defined:
                        idx = int(idx_val)
                        mem_data, elem_w = ctx._memories[name]
                        if 0 <= idx < len(mem_data):
                            base = ctx._memory_bases.get(name, 0)
                            msb_i, lsb_i = int(msb_val) - base, int(lsb_val) - base
                            rng_w = msb_i - lsb_i + 1
                            wval = value.resize(rng_w) if value.width != rng_w else value
                            if immediate:
                                updated = mem_data[idx].set_range(msb_i, lsb_i, wval)
                                mem_data[idx] = updated
                                if ctx._originals is not None and name not in ctx._originals:
                                    ctx._originals[name] = Value(0)
                            else:
                                self.nba_queue.append(MemRangeNbaEntry(name, idx, msb_i, lsb_i, wval))
                    return
            if type(lhs.target) is Identifier:
                name = lhs.target.name
                if lhs.target.hierarchy:
                    name = ".".join(lhs.target.hierarchy) + "." + name
                struct_target = _resolve_struct_write_target(name, ctx)
                msb_val = self.evaluator.eval(lhs.msb, ctx)
                lsb_val = self.evaluator.eval(lhs.lsb, ctx)
                if struct_target is not None:
                    if msb_val.is_defined and lsb_val.is_defined:
                        base_name, base_index, _field_msb, field_lsb, base_val = struct_target
                        select_base = ctx._signal_bases.get(name, 0)
                        m = field_lsb + (int(msb_val) - select_base)
                        l = field_lsb + (int(lsb_val) - select_base)
                        wval = value.resize(m - l + 1) if value.width != (m - l + 1) else value
                        if immediate:
                            updated = base_val.set_range(m, l, wval)
                            if base_index is None:
                                ctx.write_signal(base_name, updated)
                            else:
                                mem_data, _elem_w = ctx._memories.get(base_name, (None, 0))
                                if mem_data is not None and 0 <= base_index < len(mem_data):
                                    mem_data[base_index] = updated
                                    if ctx._originals is not None and base_name not in ctx._originals:
                                        ctx._originals[base_name] = Value(0)
                        else:
                            if base_index is None:
                                self.nba_queue.append(StructFieldNbaEntry(base_name, m, l, wval))
                            else:
                                self.nba_queue.append(MemRangeNbaEntry(base_name, base_index, m, l, wval))
                    return
                if msb_val.is_defined and lsb_val.is_defined:
                    m, l = int(msb_val), int(lsb_val)
                    # Adjust for non-zero base offset
                    base = ctx._signal_bases.get(name, 0)
                    m -= base
                    l -= base
                    if immediate:
                        current = ctx.read_signal(name)
                        updated = current.set_range(m, l, value)
                        ctx.write_signal(name, updated)
                    else:
                        self.nba_queue.append(StructFieldNbaEntry(name, m, l, value))
            return

        if ltype is PartSelect:
            if type(lhs.target) is BitSelect and type(lhs.target.target) is Identifier:
                name = lhs.target.target.name
                if lhs.target.target.hierarchy:
                    name = ".".join(lhs.target.target.hierarchy) + "." + name
                if name in ctx._memory_names:
                    idx_val = self.evaluator.eval(lhs.target.index, ctx)
                    base_val = self.evaluator.eval(lhs.base, ctx)
                    width_val = self.evaluator.eval(lhs.width, ctx)
                    if idx_val.is_defined and base_val.is_defined and width_val.is_defined:
                        idx = int(idx_val)
                        mem_data, _elem_w = ctx._memories[name]
                        if 0 <= idx < len(mem_data):
                            base_i = int(base_val) - ctx._memory_bases.get(name, 0)
                            width_i = int(width_val)
                            if lhs.direction == "+:":
                                msb_i = base_i + width_i - 1
                                lsb_i = base_i
                            else:
                                msb_i = base_i
                                lsb_i = base_i - width_i + 1
                            wval = value.resize(width_i) if value.width != width_i else value
                            if immediate:
                                mem_data[idx] = mem_data[idx].set_range(msb_i, lsb_i, wval)
                                if ctx._originals is not None and name not in ctx._originals:
                                    ctx._originals[name] = Value(0)
                            else:
                                self.nba_queue.append(MemRangeNbaEntry(name, idx, msb_i, lsb_i, wval))
                    return
            if type(lhs.target) is Identifier:
                name = lhs.target.name
                if lhs.target.hierarchy:
                    name = ".".join(lhs.target.hierarchy) + "." + name
                struct_target = _resolve_struct_write_target(name, ctx)
                base_val = self.evaluator.eval(lhs.base, ctx)
                width_val = self.evaluator.eval(lhs.width, ctx)
                if struct_target is not None:
                    if base_val.is_defined and width_val.is_defined:
                        base_name, base_index, _field_msb, field_lsb, base_storage_val = struct_target
                        base_i = int(base_val) - ctx._signal_bases.get(name, 0)
                        width_i = int(width_val)
                        if lhs.direction == "+:":
                            msb_i = base_i + width_i - 1
                            lsb_i = base_i
                        else:
                            msb_i = base_i
                            lsb_i = base_i - width_i + 1
                        abs_m = field_lsb + msb_i
                        abs_l = field_lsb + lsb_i
                        wval = value.resize(width_i) if value.width != width_i else value
                        if immediate:
                            updated = base_storage_val.set_range(abs_m, abs_l, wval)
                            if base_index is None:
                                ctx.write_signal(base_name, updated)
                            else:
                                mem_data, _elem_w = ctx._memories.get(base_name, (None, 0))
                                if mem_data is not None and 0 <= base_index < len(mem_data):
                                    mem_data[base_index] = updated
                                    if ctx._originals is not None and base_name not in ctx._originals:
                                        ctx._originals[base_name] = Value(0)
                        else:
                            if base_index is None:
                                self.nba_queue.append(StructFieldNbaEntry(base_name, abs_m, abs_l, wval))
                            else:
                                self.nba_queue.append(MemRangeNbaEntry(base_name, base_index, abs_m, abs_l, wval))
                    return
                if base_val.is_defined and width_val.is_defined:
                    base_i = int(base_val)
                    width_i = int(width_val)
                    sig_base = ctx._signal_bases.get(name, 0)
                    base_i -= sig_base
                    if lhs.direction == "+:":
                        msb_i = base_i + width_i - 1
                        lsb_i = base_i
                    else:
                        msb_i = base_i
                        lsb_i = base_i - width_i + 1
                    wval = value.resize(width_i) if value.width != width_i else value
                    if immediate:
                        current = ctx.read_signal(name)
                        updated = current.set_range(msb_i, lsb_i, wval)
                        ctx.write_signal(name, updated)
                    else:
                        self.nba_queue.append(StructFieldNbaEntry(name, msb_i, lsb_i, wval))
            return

        if ltype is Concatenation:
            bit_pos = sum(self._lhs_width(p, ctx) for p in lhs.parts)
            if immediate:
                for part in lhs.parts:
                    w = self._lhs_width(part, ctx)
                    bit_pos -= w
                    part_val = value[bit_pos + w - 1 : bit_pos]
                    self._write_target(part, part_val, ctx, immediate=True)
            else:
                # NBA concat: apply all parts sequentially to build final values,
                # then enqueue one NbaEntry per target signal so later parts
                # don't clobber earlier parts that target the same signal.
                # Accumulate changes per signal name.
                pending: dict[str, Value] = {}
                for part in lhs.parts:
                    w = self._lhs_width(part, ctx)
                    bit_pos -= w
                    part_val = value[bit_pos + w - 1 : bit_pos]
                    self._concat_nba_accumulate(part, part_val, ctx, pending)
                for sig_name, sig_val in pending.items():
                    self.nba_queue.append(NbaEntry(sig_name, sig_val))
            return

    def _maybe_sign_extend(self, rhs_expr, rhs_val: Value, lhs_expr, ctx: EvalContext) -> Value:
        """Sign-extend RHS value if the source expression is signed and LHS is wider."""
        if _expr_signed(rhs_expr, ctx):
            lhs_w = self._lhs_width(lhs_expr, ctx)
            if lhs_w > rhs_val.width:
                return rhs_val.sign_extend(lhs_w)
        return rhs_val

    def _lhs_width(self, expr: Expression, ctx: EvalContext) -> int:
        """Estimate the width of an LHS target expression."""
        etype = type(expr)
        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sig = ctx.read_signal(name)
            return sig.width
        if etype is BitSelect:
            if type(expr.target) is Identifier:
                name = expr.target.name
                if expr.target.hierarchy:
                    name = ".".join(expr.target.hierarchy) + "." + name
                if name in ctx._memory_names:
                    _mem_data, elem_w = ctx._memories[name]
                    return elem_w
            return 1
        if etype is RangeSelect:
            msb = self.evaluator.eval(expr.msb, ctx)
            lsb = self.evaluator.eval(expr.lsb, ctx)
            if msb.is_defined and lsb.is_defined:
                return int(msb) - int(lsb) + 1
        if etype is PartSelect:
            width = self.evaluator.eval(expr.width, ctx)
            if width.is_defined:
                return int(width)
        if etype is Concatenation:
            return sum(self._lhs_width(p, ctx) for p in expr.parts)
        return 1

    def _concat_nba_accumulate(
        self, part: Expression, part_val: Value, ctx: EvalContext, pending: dict[str, Value]
    ) -> None:
        """Accumulate a concat-part NBA into the pending dict (one entry per signal)."""
        ptype = type(part)
        if ptype is Identifier:
            name = part.name
            if part.hierarchy:
                name = ".".join(part.hierarchy) + "." + name
            if _resolve_struct_write_target(name, ctx) is not None:
                self._write_target(part, part_val, ctx, immediate=False)
                return
            wval = (
                part_val.resize(ctx.read_signal(name).width)
                if part_val.width != ctx.read_signal(name).width
                else part_val
            )
            pending[name] = wval
            return
        if ptype is BitSelect and type(part.target) is Identifier:
            name = part.target.name
            if part.target.hierarchy:
                name = ".".join(part.target.hierarchy) + "." + name
            if _resolve_struct_write_target(name, ctx) is not None:
                self._write_target(part, part_val, ctx, immediate=False)
                return
            idx_val = self.evaluator.eval(part.index, ctx)
            if idx_val.is_defined:
                current = pending.get(name)
                if current is None:
                    current = ctx.read_signal(name)
                pending[name] = current.set_bit(int(idx_val), part_val.val & 1)
            return
        if ptype is RangeSelect and type(part.target) is Identifier:
            name = part.target.name
            if part.target.hierarchy:
                name = ".".join(part.target.hierarchy) + "." + name
            if _resolve_struct_write_target(name, ctx) is not None:
                self._write_target(part, part_val, ctx, immediate=False)
                return
            msb_val = self.evaluator.eval(part.msb, ctx)
            lsb_val = self.evaluator.eval(part.lsb, ctx)
            if msb_val.is_defined and lsb_val.is_defined:
                current = pending.get(name)
                if current is None:
                    current = ctx.read_signal(name)
                pending[name] = current.set_range(int(msb_val), int(lsb_val), part_val)
            return
        # Fallback: delegate to _write_target as NBA
        self._write_target(part, part_val, ctx, immediate=False)

    def _exec_system_task(self, task: SystemTaskCall, ctx: EvalContext) -> None:
        """Execute a system task ($display, $finish, etc.)."""
        name = task.task_name.lower()

        if name == "$display":
            text = self._write_buffer + self._format_display(task, ctx)
            self._write_buffer = ""
            self.display_output.append(text)
            return

        if name == "$write":
            self._write_buffer += self._format_display(task, ctx)
            return

        if name in ("$monitor",):
            text = self._format_display(task, ctx)
            self.display_output.append(text)
            return

        if name in ("$finish", "$stop"):
            raise StopExecution()

        if name in ("$time", "$realtime", "$stime"):
            return  # time functions as statement are no-ops; as expression handled by evaluator

        if name in ("$readmemh", "$readmemb"):
            self._exec_readmem(task, ctx, is_hex=(name == "$readmemh"))
            return

        if name == "$dumpfile":
            self._exec_dumpfile(task, ctx)
            return

        if name == "$dumpvars":
            self._exec_dumpvars(task, ctx)
            return

        # Unknown system task — ignore silently

    def _format_display(self, task: SystemTaskCall, ctx: EvalContext) -> str:
        """Format $display arguments into a string, handling Verilog format strings."""
        from veriforge.model.expressions import StringLiteral  # noqa: PLC0415

        if not task.arguments:
            return ""

        # Check if the first argument is a format string
        first = task.arguments[0]
        if isinstance(first, StringLiteral):
            fmt = first.value
            data_args = [self.evaluator.eval(a, ctx) for a in task.arguments[1:]]
            return self._apply_format(fmt, data_args)

        # No format string: evaluate all arguments and join with spaces
        parts: list[str] = []
        for arg in task.arguments:
            val = self.evaluator.eval(arg, ctx)
            if val.is_defined:
                parts.append(str(int(val)))
            else:
                parts.append(str(val))
        return " ".join(parts)

    def _apply_format(self, fmt: str, args: list) -> str:  # noqa: PLR0912
        """Apply a Verilog format string to a list of Value arguments."""
        result: list[str] = []
        arg_idx = 0
        i = 0
        while i < len(fmt):
            ch = fmt[i]
            if ch == "%":
                i += 1
                if i >= len(fmt):
                    break
                # Parse optional zero-pad flag and width
                zero_pad = False
                if fmt[i] == "0":
                    zero_pad = True
                    i += 1
                    if i >= len(fmt):
                        break
                width = 0
                while i < len(fmt) and fmt[i].isdigit():
                    width = width * 10 + int(fmt[i])
                    i += 1
                if i >= len(fmt):
                    break
                spec = fmt[i].lower()
                i += 1
                if spec == "%":
                    result.append("%")
                    continue
                if spec == "t":
                    result.append(str(getattr(self, "_sim_time", 0)))
                    continue
                if spec == "m":
                    result.append("<module>")
                    continue
                if arg_idx < len(args):
                    v = args[arg_idx]
                    arg_idx += 1
                    fill = "0" if zero_pad else " "
                    if spec == "d":
                        s = str(int(v)) if v.is_defined else "x"
                        result.append(s.rjust(width, fill) if width else s)
                    elif spec in ("h", "x"):
                        s = format(int(v), "x") if v.is_defined else "x"
                        result.append(s.rjust(width, fill) if width else s)
                    elif spec == "b":
                        s = format(int(v), "b") if v.is_defined else "x"
                        result.append(s.rjust(width, fill) if width else s)
                    elif spec == "o":
                        s = format(int(v), "o") if v.is_defined else "x"
                        result.append(s.rjust(width, fill) if width else s)
                    elif spec == "c":
                        result.append(chr(int(v) & 0xFF) if v.is_defined else "?")
                    elif spec == "s":
                        if v.is_defined:
                            n = int(v)
                            chars = []
                            while n:
                                chars.append(chr(n & 0xFF))
                                n >>= 8
                            result.append("".join(reversed(chars)))
                        else:
                            result.append("")
                    else:
                        arg_idx -= 1
                        result.append("%" + spec)
                else:
                    result.append("%" + spec)
            elif ch == "\\":
                i += 1
                if i < len(fmt):
                    esc = fmt[i]
                    i += 1
                    if esc == "n":
                        result.append("\n")
                    elif esc == "t":
                        result.append("\t")
                    elif esc == "\\":
                        result.append("\\")
                    else:
                        result.append("\\" + esc)
            else:
                result.append(ch)
                i += 1
        return "".join(result)

    def _exec_readmem(self, task: SystemTaskCall, ctx: EvalContext, *, is_hex: bool) -> None:
        """Execute $readmemh or $readmemb: load file contents into a memory array."""
        from veriforge.model.expressions import StringLiteral  # noqa: PLC0415

        if len(task.arguments) < 2:  # noqa: PLR2004
            return
        fname_expr = task.arguments[0]
        mem_expr = task.arguments[1]
        if not isinstance(fname_expr, StringLiteral):
            return
        if not isinstance(mem_expr, Identifier):
            return
        filename = fname_expr.value
        mem_name = mem_expr.name
        if mem_expr.hierarchy:
            mem_name = ".".join(mem_expr.hierarchy) + "." + mem_name
        if mem_name not in ctx._memory_names:
            return
        mem_data, elem_w = ctx._memories[mem_name]
        depth = len(mem_data)
        wmask = (1 << elem_w) - 1
        loaded = False
        with open(filename) as f:
            addr = 0
            for line in f:  # noqa: PLW2901
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                if line.startswith("@"):
                    addr = int(line[1:], 16)
                    continue
                for token in line.split():
                    if addr >= depth:
                        break
                    val = int(token, 16 if is_hex else 2)
                    mem_data[addr] = Value(val & wmask, width=elem_w)
                    addr += 1
                    loaded = True
        # Flag memory as dirty so combo blocks re-trigger
        if loaded and ctx._originals is not None and mem_name not in ctx._originals:
            ctx._originals[mem_name] = Value(0)

    def _exec_dumpfile(self, task: SystemTaskCall, ctx: EvalContext) -> None:
        """Execute $dumpfile: record the VCD output filename."""
        from veriforge.model.expressions import StringLiteral  # noqa: PLC0415

        if task.arguments and isinstance(task.arguments[0], StringLiteral):
            self._vcd_filename = task.arguments[0].value

    def _exec_dumpvars(self, task: SystemTaskCall, ctx: EvalContext) -> None:
        """Execute $dumpvars: create VcdWriter and dump initial values."""
        from .vcd import VcdWriter  # noqa: PLC0415

        filename = self._vcd_filename or "dump.vcd"
        writer = VcdWriter(filename)
        # Register all scalar signals
        for name, val in ctx._signals.items():
            writer.add_signal(name, width=val.width)
        writer.write_header()
        writer.write_initial(ctx._signals)
        self._vcd_writer = writer

    def vcd_time_step_callback(self, scheduler) -> None:
        """Callback invoked after each time step to dump signal changes."""
        writer = self._vcd_writer
        if writer is None:
            return
        writer.dump_all(scheduler.time, scheduler.ctx._signals)

    def lookup_function(self, name: str):
        """Look up a user-defined function by name."""
        return self._function_map.get(name)

    def _exec_task_enable(self, stmt: TaskEnable, ctx: EvalContext) -> None:
        """Execute a user-defined task call.

        Tasks share the module scope — writes to module-level signals
        inside the task body take effect immediately.  Port variables
        are temporarily injected into the context and removed afterward.
        """
        from veriforge.model.functions import TaskDecl
        from veriforge.model.ports import PortDirection

        def _port_width(port):
            from veriforge.model.expressions import Literal as _Lit

            r = port.width
            if r is None:
                return 1
            if isinstance(r.msb, _Lit) and isinstance(r.lsb, _Lit):
                return abs(int(r.msb.value) - int(r.lsb.value)) + 1
            return 1

        task: TaskDecl | None = self._task_map.get(stmt.task_name)
        if task is None:
            return  # unknown task — silently skip

        # Save any shadowed signals and inject port variables
        saved: dict[str, Value | None] = {}
        output_bindings: list[tuple[str, Expression]] = []
        for i, port in enumerate(task.ports):
            saved[port.name] = ctx._signals.get(port.name)
            if i < len(stmt.arguments):
                if port.direction == PortDirection.INPUT:
                    val = self.evaluator.eval(stmt.arguments[i], ctx)
                    ctx._signals[port.name] = val
                elif port.direction in (PortDirection.OUTPUT, PortDirection.INOUT):
                    if port.direction == PortDirection.INOUT:
                        val = self.evaluator.eval(stmt.arguments[i], ctx)
                        ctx._signals[port.name] = val
                    else:
                        ctx._signals[port.name] = Value(0, width=_port_width(port))
                    output_bindings.append((port.name, stmt.arguments[i]))
            else:
                ctx._signals[port.name] = Value(0, width=_port_width(port))

        # Execute the task body in the caller's context
        if task.body:
            self.execute(task.body, ctx)

        # Copy output values back to caller
        for port_name, lhs_expr in output_bindings:
            out_val = ctx.read_signal(port_name)
            self._write_target(lhs_expr, out_val, ctx, immediate=True)

        # Restore shadowed signals
        for port_name, old_val in saved.items():
            if old_val is None:
                ctx._signals.pop(port_name, None)
            else:
                ctx._signals[port_name] = old_val


def _case_match(case_type: str, sel: Value, item: Value) -> bool:
    """Check if a case item matches the selector.

    case:  exact match (x if either has x/z)
    casex: x/z bits are don't-care in BOTH selector and item
    casez: z bits are don't-care
    """
    if case_type == "case":
        if sel.mask or item.mask:
            return False  # standard case: x/z never matches
        return sel.val == item.val

    if case_type == "casex":
        # Don't-care: any bit that is x/z in either operand
        dc = sel.mask | item.mask
        return (sel.val & ~dc) == (item.val & ~dc)

    if case_type == "casez":
        # Don't-care: z bits (we conflate x/z in mask, so use mask)
        dc = sel.mask | item.mask
        return (sel.val & ~dc) == (item.val & ~dc)

    return sel.val == item.val

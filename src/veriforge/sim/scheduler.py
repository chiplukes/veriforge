"""Event-driven simulation scheduler.

Implements the IEEE 1364-2005 scheduling regions:
  1. Active region  — evaluate continuous assigns, execute blocking
     assigns, evaluate RHS of NBAs, wake testbench coroutines
  2. NBA region     — apply scheduled non-blocking updates
  3. Delta check    — if any signal changed → repeat Active region
  4. Advance time   — move to next queued event

Uses flat iteration (no recursion) for Cython compatibility.
"""

from __future__ import annotations

import heapq
from enum import Enum, auto
from typing import TYPE_CHECKING

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.statements import SensitivityEdge

from .evaluator import EvalContext, ExpressionEvaluator
from .executor import StatementExecutor, StopExecution, SuspendExecution
from .value import Value

if TYPE_CHECKING:
    from collections.abc import Callable

    from veriforge.model.design import Module
    from veriforge.model.variables import Variable


# ── Process types ────────────────────────────────────────────────────


class ProcessState(Enum):
    IDLE = auto()
    ACTIVE = auto()
    SUSPENDED = auto()
    DONE = auto()


class Process:  # cm:b3e5d8
    """Base class for all simulation processes."""

    __slots__ = ("_id", "state")
    _next_id: int = 0

    def __init__(self) -> None:
        Process._next_id += 1
        self._id = Process._next_id
        self.state = ProcessState.IDLE

    @property
    def id(self) -> int:
        return self._id


class ContinuousProcess(Process):
    """Process wrapping a continuous assign: assign lhs = rhs;

    Re-evaluates whenever any RHS signal changes.
    """

    __slots__ = ("assign", "sensitivity")

    def __init__(self, assign: ContinuousAssign, sensitivity: set[str]) -> None:
        super().__init__()
        self.assign = assign
        self.sensitivity = sensitivity


class AlwaysProcess(Process):
    """Process wrapping an always block.

    For COMBINATIONAL: re-executes on any input change.
    For SEQUENTIAL: re-executes on clock edge.
    """

    __slots__ = ("_coroutine", "block", "edge_signals", "has_timing", "sensitivity", "suspend_info")

    def __init__(
        self,
        block: AlwaysBlock,
        sensitivity: set[str],
        edge_signals: dict[str, str] | None = None,
        *,
        has_timing: bool = False,
    ) -> None:
        super().__init__()
        self.block = block
        self.sensitivity = sensitivity
        self.edge_signals = edge_signals or {}  # name → "posedge"/"negedge"
        self.suspend_info: SuspendExecution | None = None
        self.has_timing = has_timing
        self._coroutine = None  # Lazily created generator for suspend/resume


class InitialProcess(Process):
    """Process wrapping an initial block (runs once at t=0)."""

    __slots__ = ("_coroutine", "block")

    def __init__(self, block: InitialBlock) -> None:
        super().__init__()
        self.block = block
        self._coroutine = None  # Lazily created generator for suspend/resume


# ── Event queue ──────────────────────────────────────────────────────


class _TimedEvent:
    """An event scheduled for a specific simulation time."""

    __slots__ = ("process", "seq", "time")

    def __init__(self, time: int, process: Process, seq: int) -> None:
        self.time = time
        self.process = process
        self.seq = seq  # insertion order for stable sort

    def __lt__(self, other: _TimedEvent) -> bool:
        if self.time != other.time:
            return self.time < other.time
        return self.seq < other.seq


class EventQueue:
    """Time-ordered priority queue of simulation events."""

    __slots__ = ("_queue", "_seq")

    def __init__(self) -> None:
        self._queue: list[_TimedEvent] = []
        self._seq: int = 0

    def schedule(self, time: int, process: Process) -> None:
        """Schedule a process to resume at the given time."""
        self._seq += 1
        heapq.heappush(self._queue, _TimedEvent(time, process, self._seq))

    def peek_time(self) -> int | None:
        """Return the time of the next event, or None if empty."""
        if self._queue:
            return self._queue[0].time
        return None

    def pop_at(self, time: int) -> list[Process]:
        """Pop all processes scheduled at exactly the given time."""
        result: list[Process] = []
        while self._queue and self._queue[0].time == time:
            result.append(heapq.heappop(self._queue).process)
        return result

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def __len__(self) -> int:
        return len(self._queue)


# ── Scheduler ────────────────────────────────────────────────────────


class Scheduler:  # cm:9a7f2c
    """Event-driven simulation scheduler.

    Manages signal state, process scheduling, sensitivity tracking,
    and the Active → NBA → Delta-cycle loop.

    Attributes:
        time:          Current simulation time.
        ctx:           Signal state (EvalContext).
        evaluator:     Expression evaluator.
        executor:      Statement executor (handles blocking/NBA).
        event_queue:   Time-ordered future event queue.
        delta_limit:   Max delta cycles per time step (prevents livelock).
        display_output: Collected $display output from all processes.
    """

    __slots__ = (
        "_always_procs",
        "_combo_procs",
        "_continuous_procs",
        "_event_waiting",
        "_initial_procs",
        "_last_run_signals",
        "_on_time_step",
        "_pending_drives",
        "_prev_signals",
        "_seq_procs",
        "_settle_snapshot",
        "_sig_to_procs",
        "_timing_procs",
        "_triggered_seq_procs",
        "ctx",
        "delta_limit",
        "display_output",
        "evaluator",
        "event_queue",
        "executor",
        "time",
    )

    def __init__(self, *, delta_limit: int = 10_000) -> None:
        self.time: int = 0
        self.ctx = EvalContext()
        self.evaluator = ExpressionEvaluator()
        self.executor = StatementExecutor(self.evaluator, loop_limit=100_000)
        self.event_queue = EventQueue()
        self.delta_limit = delta_limit
        self.display_output: list[str] = []

        # Process tracking
        self._continuous_procs: list[ContinuousProcess] = []
        self._always_procs: list[AlwaysProcess] = []
        self._combo_procs: list[AlwaysProcess] = []  # combinational subset
        self._seq_procs: list[AlwaysProcess] = []  # sequential subset
        self._timing_procs: list[AlwaysProcess] = []  # always blocks with #delay/@event
        self._initial_procs: list[InitialProcess] = []

        # Signal → set of processes sensitive to it
        self._sig_to_procs: dict[str, list[Process]] = {}

        # Edge detection state (per time step)
        self._prev_signals: dict[str, Value] = {}
        self._triggered_seq_procs: set[int] = set()

        # Processes waiting for event controls (@(posedge clk), etc.)
        # Each entry: (process, edge_dict) where edge_dict maps signal → edge_type
        self._event_waiting: list[tuple[Process, dict[str, str]]] = []

        # Snapshot of all signals at the end of the previous run() call.
        # Used to detect external drive_signal() changes between run() calls
        # so that edge-triggered always blocks fire correctly.
        self._last_run_signals: dict[str, Value] = {}

        # Pending external drives: names of signals written by drive_signal()
        # since the last settle() or run() call.
        self._pending_drives: set[str] = set()
        # Signal state captured just before the first pending drive — used by
        # settle() as the "previous" snapshot for edge detection.
        self._settle_snapshot: dict[str, Value] = {}

        # Optional callback fired after each time step completes
        # (all delta cycles resolved).  Signature: callback(scheduler)
        self._on_time_step: Callable[[Scheduler], None] | None = None

    # ── Elaboration ──────────────────────────────────────────────

    def elaborate(self, module: Module) -> None:
        """Elaborate a module: create signal state and processes.

        Initializes all nets/variables to x, creates processes for
        continuous assigns, always blocks, and initial blocks.
        """
        # Build parameter environment for evaluating parametric dimensions/widths
        from .elaborate import _build_param_env, parameter_signal_width  # noqa: PLC0415

        param_env = _build_param_env(module)

        # Initialize signal state
        for net in module.nets:
            senv = _scoped_env(net.name, param_env)
            width = _range_width(net.width, senv)
            # Track non-zero base offset for correct bit/range indexing
            lsb = 0
            if net.width is not None:
                lsb_val = _const_int(net.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if net.dimensions:
                # Net array: wire [W-1:0] name [lo:hi]
                dim = net.dimensions[0]
                lo = _const_int(dim.msb, senv)
                hi = _const_int(dim.lsb, senv)
                if lo is not None and hi is not None:
                    if lsb != 0:
                        self.ctx._memory_bases[net.name] = lsb
                    depth = abs(hi - lo) + 1
                    mem_data = [Value.x(width) for _ in range(depth)]
                    self.ctx._memories[net.name] = (mem_data, width)
                    self.ctx._memory_names.add(net.name)
                    continue
            if lsb != 0:
                self.ctx._signal_bases[net.name] = lsb
            init = self._eval_initial_value(net.initial_value, width)
            self.ctx.write_signal(net.name, init)
            if net.signed:
                self.ctx._signal_signed[net.name] = True

        for var in module.variables:
            senv = _scoped_env(var.name, param_env)
            width = _var_width(var, senv)
            # Track non-zero base offset for correct bit/range indexing
            lsb = 0
            if var.width is not None:
                lsb_val = _const_int(var.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if var.dimensions:
                # Memory array: reg [W-1:0] mem [lo:hi]
                dim = var.dimensions[0]
                lo = _const_int(dim.msb, senv)
                hi = _const_int(dim.lsb, senv)
                if lo is not None and hi is not None:
                    if lsb != 0:
                        self.ctx._memory_bases[var.name] = lsb
                    depth = abs(hi - lo) + 1
                    mem_data = [Value.x(width) for _ in range(depth)]
                    self.ctx._memories[var.name] = (mem_data, width)
                    self.ctx._memory_names.add(var.name)
                    continue
            if lsb != 0:
                self.ctx._signal_bases[var.name] = lsb
            init = self._eval_initial_value(var.initial_value, width)
            self.ctx.write_signal(var.name, init)
            if var.signed:
                self.ctx._signal_signed[var.name] = True

        for port in module.ports:
            senv = _scoped_env(port.name, param_env)
            width = _range_width(port.width, senv)
            # Track non-zero base offset for correct bit/range indexing
            lsb = 0
            if port.width is not None:
                lsb_val = _const_int(port.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if getattr(port, "dimensions", None):
                if lsb != 0:
                    self.ctx._memory_bases[port.name] = lsb
            elif lsb != 0:
                self.ctx._signal_bases[port.name] = lsb
            # Only create if not already created by net/var
            if port.name not in self.ctx._signals:
                self.ctx.write_signal(port.name, Value.x(width))
            if port.signed:
                self.ctx._signal_signed[port.name] = True

        # Register parameters as constant-valued signals
        for param in module.parameters:
            if param.name not in self.ctx._signals and param.default_value is not None:
                for p in module.parameters:
                    val = param_env.get(p.name)
                    if val is not None and p.name not in self.ctx._signals:
                        if isinstance(val, str):
                            # Byte-pack string parameters
                            int_val = 0
                            for ch in val:
                                int_val = (int_val << 8) | ord(ch)
                            width = parameter_signal_width(p, param_env, val)
                            self.ctx.write_signal(p.name, Value(int_val, width=width))
                        elif isinstance(val, int):
                            width = parameter_signal_width(p, param_env, val)
                            mask = (1 << width) - 1
                            self.ctx.write_signal(p.name, Value(val & mask, width=width))
                        if p.signed:
                            self.ctx._signal_signed[p.name] = True
                break

        # Register enum member constants from typedefs
        from .elaborate import _build_enum_env  # noqa: PLC0415

        enum_env = _build_enum_env(module)
        for name, (val, width) in enum_env.items():
            if name not in self.ctx._signals:
                mask = (1 << width) - 1
                self.ctx.write_signal(name, Value(val & mask, width=width))

        # Register struct type information for field access
        from .elaborate import _build_struct_env  # noqa: PLC0415

        _type_map, struct_signal_map = _build_struct_env(module)
        self.ctx._struct_type_map.update(_type_map)
        self.ctx._struct_types.update(struct_signal_map)

        # Create continuous assign processes
        for assign in module.continuous_assigns:
            sens = _collect_reads(assign.rhs)
            proc = ContinuousProcess(assign, sens)
            self._continuous_procs.append(proc)
            self._register_sensitivity(proc, sens)

        # Create always block processes
        for block in module.always_blocks:
            sens, edges = _always_sensitivity(block)
            timing = _has_timing(block.body)
            proc = AlwaysProcess(block, sens, edges, has_timing=timing)
            self._always_procs.append(proc)
            if timing and not block.sensitivity_list:
                # always #5 clk = ~clk — timing-controlled, no sensitivity
                self._timing_procs.append(proc)
            elif block.sensitivity_type == SensitivityType.COMBINATIONAL:
                self._combo_procs.append(proc)
            else:
                self._seq_procs.append(proc)
            self._register_sensitivity(proc, sens)

        # Create initial block processes
        for block in module.initial_blocks:
            proc = InitialProcess(block)
            self._initial_procs.append(proc)

        # Populate function/task lookup maps on the executor
        for func in module.functions:
            self.executor._function_map[func.name] = func
        for task in module.tasks:
            self.executor._task_map[task.name] = task

    def _eval_initial_value(self, init_expr: object, width: int) -> Value:
        """Evaluate an initial_value expression, returning Value.x if absent."""
        if init_expr is None:
            return Value.x(width)
        try:
            v = self.evaluator.eval(init_expr, self.ctx)
            return v.resize(width) if v.width != width else v
        except Exception:
            return Value.x(width)

    def _register_sensitivity(self, proc: Process, signals: set[str]) -> None:
        """Register a process as sensitive to a set of signals."""
        for name in signals:
            if name not in self._sig_to_procs:
                self._sig_to_procs[name] = []
            self._sig_to_procs[name].append(proc)

    # ── Main simulation loop ─────────────────────────────────────

    def run(self, *, max_time: int = 1_000_000) -> None:
        """Run the simulation until completion or max_time.

        Steps:
        1. Schedule initial processes at t=0
        2. Process external drive_signal() changes (edge detection)
        3. Evaluate all continuous assigns at t=0
        4. Run the event loop: Active → NBA → Delta → Advance
        """
        # Schedule initial blocks at t=0 (only on first call)
        for proc in self._initial_procs:
            if proc.state == ProcessState.IDLE:
                self.event_queue.schedule(0, proc)

        # Schedule always blocks with timing at t=0 (e.g. always #5 clk = ~clk)
        for proc in self._timing_procs:
            if proc.state == ProcessState.IDLE:
                self.event_queue.schedule(0, proc)

        # Process external drive_signal() changes: detect edges and
        # trigger posedge/negedge-sensitive always blocks.
        self._process_external_drives()

        # Bootstrap: evaluate all continuous assigns until stable
        for _ in range(self.delta_limit):
            if not self._run_continuous_assigns():
                break

        # Bootstrap: evaluate combinational always blocks once at t=0
        if self._combo_procs:
            self._run_active_region(list(self._combo_procs))

        # Wire VCD callback if $dumpvars created a writer and no external callback is set
        if self._on_time_step is None and self.executor._vcd_writer is not None:
            self._on_time_step = self.executor.vcd_time_step_callback

        # Run the event loop
        self._run_time_step(max_time)

        # Save signal snapshot for external drive detection on next run()
        self._last_run_signals = self._snapshot_signals()

    def run_step(self) -> bool:
        """Advance simulation by one time step.

        Pops all events at the next scheduled time, executes them, and runs
        the delta cycle loop to convergence.  Returns True if simulation
        should continue, False if finished (empty queue).
        """
        if self.event_queue.is_empty():
            return False
        next_time = self.event_queue.peek_time()
        if next_time is None:
            return False
        result = self._run_single_step(next_time)
        # Refresh the "last run" snapshot so that subsequent run() calls
        # (e.g. _settle_current_time) do not misidentify simulation-internal
        # signal changes (clock edges, NBA results) as externally-driven
        # changes and re-trigger sequential always blocks.
        self._last_run_signals = self._snapshot_signals()
        return result

    def _process_external_drives(self) -> None:  # noqa: PLR0912
        """Process signal changes from external drive_signal() calls.

        Compares current signal state against the snapshot saved at the
        end of the previous run() call. Any differences are treated as
        externally-driven changes: continuous assigns are propagated,
        edge-sensitive always blocks (posedge/negedge) are triggered,
        and event-waiting processes are checked.
        """
        if not self._last_run_signals:
            return

        # Find signals that changed since last run() completed
        old_signals = self._last_run_signals
        drive_dirty: set[str] = set()
        for name, old_val in old_signals.items():
            new_val = self.ctx._signals.get(name)
            if new_val is not None and (old_val.val != new_val.val or old_val.mask != new_val.mask):
                drive_dirty.add(name)

        if not drive_dirty:
            return

        # Propagate driven signal changes through continuous assigns
        for _ in range(self.delta_limit):
            if not self._run_dirty_continuous_assigns(drive_dirty):
                break

        # Recompute full dirty set after CA propagation
        all_dirty: set[str] = set()
        for name, old_val in old_signals.items():
            new_val = self.ctx._signals.get(name)
            if new_val is not None and (old_val.val != new_val.val or old_val.mask != new_val.mask):
                all_dirty.add(name)

        if not all_dirty:
            return

        # Use pre-drive snapshot as "previous" for edge detection
        self._prev_signals = old_signals
        self._triggered_seq_procs = set()

        # Collect triggered processes (combo + sequential with edge detection)
        triggered = self._collect_triggered(all_dirty)

        if triggered:
            # Run triggered processes and full delta cycle loop
            active_dirty = self._run_active_region(triggered)
            delta = 0
            while active_dirty or self.executor.nba_queue:
                delta += 1
                if delta > self.delta_limit:
                    raise RuntimeError(f"Delta cycle limit ({self.delta_limit}) exceeded during drive processing")
                nba_dirty = self.executor.apply_nba(self.ctx)
                nba_changed = len(nba_dirty) > 0
                if nba_changed:
                    for _ in range(self.delta_limit):
                        if not self._run_dirty_continuous_assigns(nba_dirty):
                            break
                combined = active_dirty | nba_dirty
                rerun = self._collect_triggered(combined)
                if rerun or nba_changed:
                    active_dirty = self._run_active_region(rerun)
                else:
                    active_dirty = set()

        # Wake up event-waiting processes (coroutine @(posedge clk))
        if self._event_waiting:
            self._check_event_waiting()

    def _run_time_step(self, max_time: int) -> None:
        """Process the event queue until empty or max_time exceeded."""
        while not self.event_queue.is_empty():
            next_time = self.event_queue.peek_time()
            if next_time is None:
                break
            if next_time > max_time:
                break
            if not self._run_single_step(next_time):
                break

        # Collect display output
        if self.executor._write_buffer:
            self.executor.display_output.append(self.executor._write_buffer)
            self.executor._write_buffer = ""
        self.display_output.extend(self.executor.display_output)
        self.executor.display_output.clear()

        # Finalize VCD writer if active
        if self.executor._vcd_writer is not None:
            self.executor._vcd_writer.finalize()
            self.executor._vcd_writer = None

    def _run_single_step(self, next_time: int) -> bool:
        """Process one time step at *next_time*. Returns True to continue."""
        self.time = next_time
        self.executor.time = self.time
        self.ctx.time = self.time

        # Snapshot signals at start of time step for edge detection
        self._prev_signals = self._snapshot_signals()
        self._triggered_seq_procs = set()

        # Pop and execute all processes at this time
        procs = self.event_queue.pop_at(self.time)
        active_dirty = self._run_active_region(procs)

        # Delta cycle loop: apply NBAs, re-evaluate until stable
        delta = 0
        while active_dirty or self.executor.nba_queue:
            delta += 1
            if delta > self.delta_limit:
                raise RuntimeError(f"Delta cycle limit ({self.delta_limit}) exceeded at t={self.time}")

            # Apply NBAs
            nba_dirty = self.executor.apply_nba(self.ctx)
            nba_changed = len(nba_dirty) > 0

            # Re-run continuous assigns after NBA — signals may have changed
            if nba_changed:
                for _ in range(self.delta_limit):
                    if not self._run_dirty_continuous_assigns(nba_dirty):
                        break

            # Re-evaluate triggered processes — only those sensitive to
            # signals that actually changed (IEEE 1364-2005 always @*).
            combined_dirty = active_dirty | nba_dirty
            triggered = self._collect_triggered(combined_dirty)
            if triggered or nba_changed:
                active_dirty = self._run_active_region(triggered)
            else:
                active_dirty = set()

        # Wake up event-waiting processes (@(posedge clk), etc.)
        if self._event_waiting:
            self._check_event_waiting()

        # Fire time-step callback (all delta cycles resolved).
        # If the callback drives signals (e.g. an AXI responder), propagate
        # those changes through continuous assigns immediately.  Without this,
        # struct-packed TBs like ``assign dst_resp = {..., dst_b_valid, ...}``
        # would not update until the next sim.run() call, because run_step()
        # takes its snapshot *after* this callback and _process_external_drives
        # would therefore see no change.
        if self._on_time_step is not None:
            _pre_cb = self._snapshot_signals()
            self._on_time_step(self)
            _cb_dirty: set[str] = {
                name
                for name, pre_val in _pre_cb.items()
                if (cur := self.ctx._signals.get(name)) is not None
                and (cur.val != pre_val.val or cur.mask != pre_val.mask)
            }
            if _cb_dirty:
                for _ in range(self.delta_limit):
                    if not self._run_dirty_continuous_assigns(_cb_dirty):
                        break

        # If the on_time_step callback left pending external drives, the
        # _settle_snapshot may predate this time step (e.g. captured at a
        # prior negedge).  Refresh it to the current post-step state so
        # that a subsequent settle() call doesn't see a spurious clock edge
        # and re-trigger always_ff blocks that already ran this step.
        if self._pending_drives:
            self._settle_snapshot = self._snapshot_signals()

        return True

    def _run_active_region(self, procs: list[Process]) -> set[str]:
        """Execute a list of processes. Returns set of dirty signal names."""
        # Track which signals are written and their original values.
        # After all processes run, we compare finals against originals
        # to build the TRUE dirty set (handles A=0; A=1 correctly).
        originals: dict[str, Value | None] = {}
        self.ctx._originals = originals

        for proc in procs:
            try:
                self._execute_process(proc)
            except StopExecution:
                self.ctx._originals = None
                self.display_output.extend(self.executor.display_output)
                self.executor.display_output.clear()
                self.event_queue._queue.clear()
                return set()
            except SuspendExecution as e:
                if isinstance(proc, AlwaysProcess):
                    proc.suspend_info = e
                if e.delay is not None and e.delay > 0:
                    self.event_queue.schedule(self.time + e.delay, proc)

        self.ctx._originals = None

        # Build dirty set: signals whose FINAL value differs from original.
        dirty: set[str] = set()
        signals = self.ctx._signals
        for name, orig in originals.items():
            final = signals.get(name)
            if orig is None or final is None:
                dirty.add(name)
            elif orig.val != final.val or orig.mask != final.mask:
                dirty.add(name)

        # Run only the continuous assigns whose inputs changed
        if dirty:
            for _ in range(self.delta_limit):
                if not self._run_dirty_continuous_assigns(dirty):
                    break

        # Collect display output
        self.display_output.extend(self.executor.display_output)
        self.executor.display_output.clear()

        return dirty

    def _execute_process(self, proc: Process) -> None:
        """Execute a single process."""
        ptype = type(proc)

        if ptype is AlwaysProcess:
            if proc.has_timing:
                # Coroutine-style execution for always blocks with timing
                proc.state = ProcessState.ACTIVE
                if proc._coroutine is None:
                    proc._coroutine = self.executor.execute_coroutine(proc.block.body, self.ctx)
                try:
                    suspend = next(proc._coroutine)
                    self._schedule_suspend(proc, suspend)
                except StopIteration:
                    # Always blocks restart: create new coroutine and reschedule
                    proc._coroutine = None
                    self.event_queue.schedule(self.time, proc)
                return
            proc.state = ProcessState.ACTIVE
            self.executor.execute(proc.block.body, self.ctx)
            proc.state = ProcessState.IDLE
            return

        if ptype is ContinuousProcess:
            lhs_w = self.executor._lhs_width(proc.assign.lhs, self.ctx)
            rhs_val = self.evaluator.eval(proc.assign.rhs, self.ctx, width=lhs_w)
            self.executor._write_target(proc.assign.lhs, rhs_val, self.ctx, immediate=True)
            return

        if ptype is InitialProcess:
            proc.state = ProcessState.ACTIVE
            if proc._coroutine is None:
                proc._coroutine = self.executor.execute_coroutine(proc.block.body, self.ctx)
            try:
                suspend = next(proc._coroutine)
                self._schedule_suspend(proc, suspend)
            except StopIteration:
                proc.state = ProcessState.DONE
            return

        # Handle clock toggle (duck-typed, not a real Process)
        if hasattr(proc, "sig_name") and hasattr(proc, "value"):
            self.ctx.write_signal(proc.sig_name, proc.value)
            return

    def _schedule_suspend(self, proc: Process, suspend: SuspendExecution) -> None:
        """Schedule a process based on its suspension reason (delay or event)."""
        if suspend.delay is not None:
            if suspend.delay > 0:
                self.event_queue.schedule(self.time + suspend.delay, proc)
            else:
                self.event_queue.schedule(self.time, proc)
        elif suspend.events:
            # @(posedge clk), @(negedge clk), @(a or b), etc.
            edge_dict: dict[str, str] = {}
            for edge in suspend.events:
                if isinstance(edge, SensitivityEdge) and isinstance(edge.signal, Identifier):
                    edge_dict[edge.signal.name] = edge.edge if edge.edge in ("posedge", "negedge") else "level"
            if edge_dict:
                self._event_waiting.append((proc, edge_dict))
            else:
                # Empty event list — reschedule next delta
                self.event_queue.schedule(self.time, proc)

    def _run_continuous_assigns(self) -> bool:
        """Re-evaluate all continuous assigns. Returns True if any changed."""
        changed = False
        for proc in self._continuous_procs:
            old = self._read_lhs(proc.assign.lhs)
            lhs_w = self.executor._lhs_width(proc.assign.lhs, self.ctx)
            rhs_val = self.evaluator.eval(proc.assign.rhs, self.ctx, width=lhs_w)
            self.executor._write_target(proc.assign.lhs, rhs_val, self.ctx, immediate=True)
            new = self._read_lhs(proc.assign.lhs)
            if old is not None and new is not None:
                if old.val != new.val or old.mask != new.mask:
                    changed = True
        return changed

    def _run_dirty_continuous_assigns(self, dirty: set[str]) -> bool:
        """Re-evaluate only continuous assigns whose inputs overlap *dirty*.

        This is a targeted version of ``_run_continuous_assigns`` that skips
        assigns whose read-set is disjoint from the set of signals that
        changed.  For designs with many continuous assigns this is a large
        win because most assigns are unaffected by any given state change.
        """
        changed = False
        for proc in self._continuous_procs:
            # proc.sensitivity is the set of signal names read by the RHS.
            if not proc.sensitivity.intersection(dirty):
                continue
            old = self._read_lhs(proc.assign.lhs)
            lhs_w = self.executor._lhs_width(proc.assign.lhs, self.ctx)
            rhs_val = self.evaluator.eval(proc.assign.rhs, self.ctx, width=lhs_w)
            self.executor._write_target(proc.assign.lhs, rhs_val, self.ctx, immediate=True)
            new = self._read_lhs(proc.assign.lhs)
            if old is not None and new is not None:
                if old.val != new.val or old.mask != new.mask:
                    changed = True
                    # Track the output as dirty too so downstream CAs are re-evaluated
                    dirty.update(_lhs_base_names(proc.assign.lhs))
        return changed

    def _read_lhs(self, lhs: Expression) -> Value | None:
        """Read the current value of an LHS target."""
        if isinstance(lhs, Identifier):
            if lhs.hierarchy:
                # Sub-field of a packed struct (e.g. axi_req.ar_valid,
                # axi_req.ar.addr).  The signal in ctx is the ancestor struct,
                # not the sub-field path.  Walk up the hierarchy to find the
                # nearest ancestor that exists in ctx so change detection sees
                # the whole-struct delta.
                full_name = ".".join(lhs.hierarchy) + "." + lhs.name
                v = self.ctx._signals.get(full_name)
                if v is not None:
                    return v
                parts = list(lhs.hierarchy)
                while parts:
                    candidate = ".".join(parts)
                    v = self.ctx._signals.get(candidate)
                    if v is not None:
                        return v
                    parts.pop()
            return self.ctx.read_signal(lhs.name)
        if isinstance(lhs, BitSelect) and isinstance(lhs.target, Identifier):
            name = lhs.target.name
            idx_val = self.evaluator.eval(lhs.index, self.ctx)
            if not idx_val.is_defined:
                return None
            idx = int(idx_val)
            if name in self.ctx._memory_names:
                mem_data, _ = self.ctx._memories[name]
                if 0 <= idx < len(mem_data):
                    return mem_data[idx]
                return None
            # Adjust for non-zero base (e.g. logic [31:1] foo → foo[1] is bit 0)
            idx -= self.ctx._signal_bases.get(name, 0)
            current = self.ctx.read_signal(name)
            return Value(int((current.val >> idx) & 1), width=1)
        if (
            isinstance(lhs, BitSelect)
            and isinstance(lhs.target, BitSelect)
            and isinstance(lhs.target.target, Identifier)
        ):
            name = lhs.target.target.name
            outer_idx_val = self.evaluator.eval(lhs.target.index, self.ctx)
            inner_idx_val = self.evaluator.eval(lhs.index, self.ctx)
            if not outer_idx_val.is_defined or not inner_idx_val.is_defined:
                return None
            if name in self.ctx._memory_names:
                mem_data, _ = self.ctx._memories[name]
                outer_idx = int(outer_idx_val)
                if 0 <= outer_idx < len(mem_data):
                    current = mem_data[outer_idx]
                    inner_idx = int(inner_idx_val)
                    return Value(int((current.val >> inner_idx) & 1), width=1)
            return None
        if isinstance(lhs, RangeSelect) and isinstance(lhs.target, Identifier):
            name = lhs.target.name
            msb_val = self.evaluator.eval(lhs.msb, self.ctx)
            lsb_val = self.evaluator.eval(lhs.lsb, self.ctx)
            if msb_val.is_defined and lsb_val.is_defined:
                current = self.ctx.read_signal(name)
                msb, lsb = int(msb_val), int(lsb_val)
                # Adjust for non-zero base
                base = self.ctx._signal_bases.get(name, 0)
                msb -= base
                lsb -= base
                width = msb - lsb + 1
                val = (current.val >> lsb) & ((1 << width) - 1)
                mask = (current.mask >> lsb) & ((1 << width) - 1)
                return Value(val, width=width, mask=mask)
            return None
        return None

    def _collect_triggered(self, dirty: set[str]) -> list[Process]:
        """Collect processes that should run due to signal changes.

        - COMBINATIONAL always blocks are re-evaluated only when a signal
          in their inferred sensitivity set (RHS reads) has changed.
          This matches IEEE 1364-2005 ``always @*`` semantics.
        - SEQUENTIAL always blocks are triggered when their edge conditions
          (posedge/negedge) are met relative to the start of the time step.
          Each sequential process fires at most once per time step.
        """
        triggered: list[Process] = []
        # Only include combo procs whose sensitivity overlaps dirty signals
        for proc in self._combo_procs:
            if proc.sensitivity & dirty:
                triggered.append(proc)
        # Check sequential procs for edge triggers
        triggered_seq = self._triggered_seq_procs
        for proc in self._seq_procs:
            if proc.state != ProcessState.DONE and id(proc) not in triggered_seq and self._edge_fired(proc):
                triggered_seq.add(id(proc))
                triggered.append(proc)
        return triggered

    def _edge_fired(self, proc: AlwaysProcess) -> bool:
        """Check if any edge condition in a sequential process fired.

        Compares current signal values against ``_prev_signals`` (snapshot
        taken at the start of the current time step).  Uses IEEE 1364-2005
        edge semantics:
          posedge — transition to 1 from 0, x, or z
          negedge — transition to 0 from 1, x, or z
        """
        for sig_name, edge_type in proc.edge_signals.items():
            old = self._prev_signals.get(sig_name)
            if old is None:
                continue
            new = self.ctx.read_signal(sig_name)
            old_bit = old.val & 1
            old_x = (old.mask & 1) != 0
            new_bit = new.val & 1
            new_x = (new.mask & 1) != 0
            if edge_type == "posedge":
                # posedge: transition to 1 from 0, x, or z
                if not new_x and new_bit == 1 and (old_x or old_bit == 0):
                    return True
            elif edge_type == "negedge":
                # negedge: transition to 0 from 1, x, or z
                if not new_x and new_bit == 0 and (old_x or old_bit == 1):
                    return True
        return False

    def _snapshot_signals(self) -> dict[str, Value]:
        """Snapshot current signal values for change detection."""
        return dict(self.ctx._signals)

    def _check_event_waiting(self) -> None:
        """Check event-waiting processes and schedule those whose edges fired."""
        still_waiting: list[tuple[Process, dict[str, str]]] = []
        for proc, edge_dict in self._event_waiting:
            if self._event_edge_fired(edge_dict):
                # Schedule to run at current time (next time step)
                self.event_queue.schedule(self.time, proc)
            else:
                still_waiting.append((proc, edge_dict))
        self._event_waiting = still_waiting

    def _event_edge_fired(self, edge_dict: dict[str, str]) -> bool:
        """Check if any edge in an event edge dict fired this time step."""
        for sig_name, edge_type in edge_dict.items():
            old = self._prev_signals.get(sig_name)
            if old is None:
                continue
            new = self.ctx.read_signal(sig_name)
            old_bit = old.val & 1
            old_x = (old.mask & 1) != 0
            new_bit = new.val & 1
            new_x = (new.mask & 1) != 0
            if edge_type == "posedge":
                if not new_x and new_bit == 1 and (old_x or old_bit == 0):
                    return True
            elif edge_type == "negedge":
                if not new_x and new_bit == 0 and (old_x or old_bit == 1):
                    return True
            elif edge_type == "level":
                # Any change triggers level-sensitive events
                if old.val != new.val or old.mask != new.mask:
                    return True
        return False

    # ── Helpers ──────────────────────────────────────────────────

    def drive_signal(self, name: str, value: Value | int) -> None:
        """Drive a signal from outside (testbench use)."""
        if isinstance(value, int):
            old = self.ctx.read_signal(name)
            value = Value(value, width=old.width)
        if not self._pending_drives:
            # First drive since last settle/run — snapshot pre-drive state for
            # edge detection in settle().
            self._settle_snapshot = self._snapshot_signals()
        self.ctx.write_signal(name, value)
        self._pending_drives.add(name)

    def settle(self) -> None:
        """Propagate pending external drives through combinational logic.

        Runs continuous-assign and combinational-always fixpoint for any signals
        written by ``drive_signal()`` since the last ``settle()`` or ``run()``
        call, without advancing simulation time or consuming events.

        Use this instead of ``run(max_time=sim.time)`` or ``step_eval_now``
        when you want to observe combinational outputs immediately after driving
        inputs.
        """
        if not self._pending_drives:
            return

        drive_dirty: set[str] = set(self._pending_drives)
        self._pending_drives.clear()

        # Use the pre-drive snapshot for edge detection so sequential blocks
        # fire correctly when a driven signal is a clock-like edge signal.
        if self._settle_snapshot:
            self._prev_signals = self._settle_snapshot
            self._settle_snapshot = {}
        self._triggered_seq_procs = set()

        # Propagate driven signals through continuous assigns.
        # _run_dirty_continuous_assigns mutates drive_dirty to include outputs.
        for _ in range(self.delta_limit):
            if not self._run_dirty_continuous_assigns(drive_dirty):
                break

        # Collect and run triggered processes (combo + any edge-triggered seq)
        triggered = self._collect_triggered(drive_dirty)
        if triggered:
            active_dirty = self._run_active_region(triggered)
            # Propagate blocking-assign outputs (e.g. always_comb writing a
            # packed struct) through CAs.  Without this, port-connection CAs
            # like `axi_req_i = axi_req` are never run after a combo block
            # fires, so downstream logic sees stale values.
            for _ in range(self.delta_limit):
                if not self._run_dirty_continuous_assigns(active_dirty):
                    break
            delta = 0
            while active_dirty or self.executor.nba_queue:
                delta += 1
                if delta > self.delta_limit:
                    raise RuntimeError(f"Delta cycle limit ({self.delta_limit}) exceeded during settle()")
                nba_dirty = self.executor.apply_nba(self.ctx)
                if nba_dirty:
                    for _ in range(self.delta_limit):
                        if not self._run_dirty_continuous_assigns(nba_dirty):
                            break
                combined = active_dirty | nba_dirty
                rerun = self._collect_triggered(combined)
                if rerun or nba_dirty:
                    active_dirty = self._run_active_region(rerun)
                    for _ in range(self.delta_limit):
                        if not self._run_dirty_continuous_assigns(active_dirty):
                            break
                else:
                    active_dirty = set()

        # Update snapshot so the next run()/edge detection starts from settled state.
        self._last_run_signals = self._snapshot_signals()

    def read_signal(self, name: str) -> Value:
        """Read a signal value."""
        return self.ctx.read_signal(name)

    def signal_names(self) -> set[str]:
        """Return the set of all signal names in the simulation."""
        return set(self.ctx._signals.keys())

    def schedule_at(self, time: int, proc: Process) -> None:
        """Schedule a process to run at a specific time."""
        self.event_queue.schedule(time, proc)


# ── Free functions ───────────────────────────────────────────────────


def _has_timing(stmt) -> bool:  # noqa: PLR0911
    """Check if a statement tree contains timing controls (#delay, @event)."""
    if stmt is None:
        return False
    from veriforge.model.statements import (  # noqa: PLC0415
        CaseStatement,
        DelayControl,
        EventControl,
        ForeverLoop,
        ForLoop,
        IfStatement,
        RepeatLoop,
        SeqBlock,
        WhileLoop,
    )

    stype = type(stmt)
    if stype in (DelayControl, EventControl):
        return True
    if stype is SeqBlock:
        return any(_has_timing(s) for s in stmt.statements)
    if stype is IfStatement:
        return _has_timing(stmt.then_body) or _has_timing(stmt.else_body)
    if stype is CaseStatement:
        return any(_has_timing(item.body) for item in stmt.items)
    if stype is ForLoop:
        return _has_timing(stmt.body) or _has_timing(stmt.init) or _has_timing(stmt.update)
    if stype in (WhileLoop, RepeatLoop, ForeverLoop):
        return _has_timing(stmt.body)
    return False


def _range_width(r, param_env: dict[str, int] | None = None) -> int:
    """Compute the bit-width from a Range object (or default 1)."""
    if r is None:
        return 1
    # Try fast path: both bounds are bare Literals
    try:
        if isinstance(r.msb, Literal) and isinstance(r.lsb, Literal):
            return int(r.msb.value) - int(r.lsb.value) + 1
    except (TypeError, ValueError):
        pass
    # Fall back to parametric evaluation
    msb = _const_int(r.msb, param_env)
    lsb = _const_int(r.lsb, param_env)
    if msb is not None and lsb is not None:
        return abs(msb - lsb) + 1
    return 1


def _scoped_env(signal_name: str, param_env: dict[str, int]) -> dict[str, int]:
    """Build a param env with unprefixed aliases for a hierarchically-prefixed signal.

    For signal ``"uut.addr"`` with ``param_env = {"uut.IDX_BITS": 5}``,
    returns an env that also contains ``"IDX_BITS": 5`` so that range
    expressions like ``[IDX_BITS-1:0]`` (which use unprefixed identifiers)
    can be evaluated.
    """
    dot = signal_name.rfind(".")
    if dot < 0:
        return param_env
    prefix = signal_name[: dot + 1]  # e.g. "uut."
    local = dict(param_env)
    for k, v in param_env.items():
        if k.startswith(prefix):
            unprefixed = k[len(prefix) :]
            if unprefixed not in local:
                local[unprefixed] = v
    return local


def _lit_int(expr) -> int | None:
    """Extract int value from a Literal expression, or None."""
    if isinstance(expr, Literal):
        try:
            return int(expr.value)
        except (TypeError, ValueError):
            return None
    return None


def _const_int(expr, param_env: dict[str, int] | None = None) -> int | None:
    """Evaluate an expression to an int, handling Literals and constant expressions.

    Falls back to ``_eval_const_expr`` for parametric expressions like ``2**MLEN/4-1``.
    """
    result = _lit_int(expr)
    if result is not None:
        return result
    if expr is None:
        return None
    try:
        from .elaborate import _eval_const_expr  # noqa: PLC0415

        return _eval_const_expr(expr, param_env or {})
    except (ValueError, TypeError):
        return None


def _var_width(var: Variable, param_env: dict[str, int] | None = None) -> int:
    """Compute the bit-width for a Variable, handling integer/real/time types."""
    from veriforge.model.variables import VariableKind  # noqa: PLC0415

    if var.kind == VariableKind.INTEGER:
        return 32
    if var.kind == VariableKind.REAL:
        return 64
    if var.kind == VariableKind.TIME:
        return 64
    if var.kind == VariableKind.BYTE:
        return 8
    if var.kind == VariableKind.SHORTINT:
        return 16
    if var.kind == VariableKind.INT:
        return 32
    if var.kind == VariableKind.LONGINT:
        return 64
    return _range_width(var.width, param_env)


def _collect_reads(expr: Expression) -> set[str]:
    """Walk an expression tree and collect all Identifier names read."""
    reads: set[str] = set()
    _walk_expr_reads(expr, reads)
    return reads


def _walk_expr_reads(expr: Expression, reads: set[str]) -> None:  # noqa: PLR0911
    """Recursive helper — collect identifiers from an expression."""
    if isinstance(expr, Identifier):
        from .elaborate import normalize_struct_access_name  # noqa: PLC0415

        name = expr.name
        if expr.hierarchy:
            name = ".".join(expr.hierarchy) + "." + name
        if name.startswith("__vt_local_for_"):
            return
        reads.add(name)
        reads.add(normalize_struct_access_name(name))
        # Also add all base signals for nested struct field references.
        parts = name.split(".")
        for part in parts:
            if "[" in part and part.endswith("]"):
                index_text = part[part.find("[") + 1 : -1].strip()
                if index_text:
                    try:
                        int(index_text, 0)
                    except ValueError:
                        reads.add(index_text)
        for index in range(1, len(parts)):
            prefix = ".".join(parts[:index])
            reads.add(prefix)
            reads.add(normalize_struct_access_name(prefix))
        stripped_parts = [part.split("[", 1)[0] if "[" in part else part for part in parts]
        stripped_name = ".".join(stripped_parts)
        reads.add(stripped_name)
        reads.add(normalize_struct_access_name(stripped_name))
        for index in range(1, len(stripped_parts)):
            prefix = ".".join(stripped_parts[:index])
            reads.add(prefix)
            reads.add(normalize_struct_access_name(prefix))
        return

    if isinstance(expr, (Literal,)):
        return

    if isinstance(expr, BinaryOp):
        _walk_expr_reads(expr.left, reads)
        _walk_expr_reads(expr.right, reads)
        return

    if isinstance(expr, UnaryOp):
        _walk_expr_reads(expr.operand, reads)
        return

    if isinstance(expr, TernaryOp):
        _walk_expr_reads(expr.condition, reads)
        _walk_expr_reads(expr.true_expr, reads)
        _walk_expr_reads(expr.false_expr, reads)
        return

    if isinstance(expr, Concatenation):
        for part in expr.parts:
            _walk_expr_reads(part, reads)
        return

    if isinstance(expr, Replication):
        _walk_expr_reads(expr.count, reads)
        _walk_expr_reads(expr.value, reads)
        return

    if isinstance(expr, BitSelect):
        _walk_expr_reads(expr.target, reads)
        _walk_expr_reads(expr.index, reads)
        return

    if isinstance(expr, RangeSelect):
        _walk_expr_reads(expr.target, reads)
        _walk_expr_reads(expr.msb, reads)
        _walk_expr_reads(expr.lsb, reads)
        return

    if isinstance(expr, PartSelect):
        _walk_expr_reads(expr.target, reads)
        _walk_expr_reads(expr.base, reads)
        _walk_expr_reads(expr.width, reads)
        return

    if isinstance(expr, FunctionCall):
        for arg in expr.arguments:
            _walk_expr_reads(arg, reads)
        return


def _always_sensitivity(block: AlwaysBlock) -> tuple[set[str], dict[str, str]]:
    """Determine sensitivity set and edge types for an always block.

    Returns:
        (signal_names, edge_dict) where edge_dict maps name → "posedge"/"negedge"
    """
    signals: set[str] = set()
    edges: dict[str, str] = {}

    if block.sensitivity_type == SensitivityType.COMBINATIONAL:
        # @(*) — infer from all reads in the body
        _collect_stmt_reads(block.body, signals)
        _collect_stmt_writes(block.body, signals)
        return signals, edges

    # Explicit sensitivity list
    for edge in block.sensitivity_list:
        if isinstance(edge, SensitivityEdge):
            if isinstance(edge.signal, Identifier):
                name = edge.signal.name
                signals.add(name)
                if edge.edge in ("posedge", "negedge"):
                    edges[name] = edge.edge

    return signals, edges


def _collect_stmt_reads(stmt, reads: set[str]) -> None:  # noqa: PLR0911, PLR0912
    """Walk a statement tree and collect all signal names read in expressions."""
    if stmt is None:
        return

    from veriforge.model.statements import (  # noqa: PLC0415
        BlockingAssign,
        CaseStatement,
        DelayControl,
        EventControl,
        ForeverLoop,
        ForLoop,
        IfStatement,
        NonblockingAssign,
        RepeatLoop,
        SeqBlock,
        SystemTaskCall,
        WhileLoop,
    )

    if isinstance(stmt, (BlockingAssign, NonblockingAssign)):
        _walk_expr_reads(stmt.rhs, reads)
        # Also collect reads from LHS indexing (bit/range select)
        _collect_lhs_index_reads(stmt.lhs, reads)
        return

    if isinstance(stmt, IfStatement):
        _walk_expr_reads(stmt.condition, reads)
        _collect_stmt_reads(stmt.then_body, reads)
        _collect_stmt_reads(stmt.else_body, reads)
        return

    if isinstance(stmt, CaseStatement):
        _walk_expr_reads(stmt.expression, reads)
        for item in stmt.items:
            for val in item.values:
                _walk_expr_reads(val, reads)
            _collect_stmt_reads(item.body, reads)
        return

    if isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            _collect_stmt_reads(s, reads)
        return

    if isinstance(stmt, ForLoop):
        _collect_stmt_reads(stmt.init, reads)
        _walk_expr_reads(stmt.condition, reads)
        _collect_stmt_reads(stmt.update, reads)
        _collect_stmt_reads(stmt.body, reads)
        return

    if isinstance(stmt, WhileLoop):
        _walk_expr_reads(stmt.condition, reads)
        _collect_stmt_reads(stmt.body, reads)
        return

    if isinstance(stmt, (ForeverLoop, RepeatLoop)):
        if hasattr(stmt, "count"):
            _walk_expr_reads(stmt.count, reads)
        _collect_stmt_reads(stmt.body, reads)
        return

    if isinstance(stmt, DelayControl):
        _walk_expr_reads(stmt.delay, reads)
        _collect_stmt_reads(stmt.body, reads)
        return

    if isinstance(stmt, EventControl):
        _collect_stmt_reads(stmt.body, reads)
        return

    if isinstance(stmt, SystemTaskCall):
        for arg in stmt.arguments:
            _walk_expr_reads(arg, reads)
        return


def _collect_lhs_index_reads(lhs: Expression, reads: set[str]) -> None:
    """Collect signal reads from LHS index expressions (not the target itself)."""
    if isinstance(lhs, BitSelect):
        _walk_expr_reads(lhs.index, reads)
    elif isinstance(lhs, RangeSelect):
        _walk_expr_reads(lhs.msb, reads)
        _walk_expr_reads(lhs.lsb, reads)
    elif isinstance(lhs, Concatenation):
        for part in lhs.parts:
            _collect_lhs_index_reads(part, reads)


def _collect_stmt_writes(stmt, reads: set[str]) -> None:  # noqa: PLR0911
    """Remove signals written by the statement tree from inferred reads."""
    if stmt is None:
        return

    from veriforge.model.statements import (  # noqa: PLC0415
        BlockingAssign,
        CaseStatement,
        DelayControl,
        EventControl,
        ForeverLoop,
        ForLoop,
        IfStatement,
        NonblockingAssign,
        RepeatLoop,
        SeqBlock,
        WhileLoop,
    )

    if isinstance(stmt, (BlockingAssign, NonblockingAssign)):
        reads.difference_update(_lhs_base_names(stmt.lhs))
        return

    if isinstance(stmt, IfStatement):
        _collect_stmt_writes(stmt.then_body, reads)
        _collect_stmt_writes(stmt.else_body, reads)
        return

    if isinstance(stmt, CaseStatement):
        for item in stmt.items:
            _collect_stmt_writes(item.body, reads)
        return

    if isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            _collect_stmt_writes(s, reads)
        return

    if isinstance(stmt, ForLoop):
        _collect_stmt_writes(stmt.init, reads)
        _collect_stmt_writes(stmt.update, reads)
        _collect_stmt_writes(stmt.body, reads)
        return

    if isinstance(stmt, WhileLoop):
        _collect_stmt_writes(stmt.body, reads)
        return

    if isinstance(stmt, (ForeverLoop, RepeatLoop, DelayControl, EventControl)):
        _collect_stmt_writes(stmt.body, reads)
        return


def _lhs_base_names(lhs: Expression) -> set[str]:
    """Extract the set of base signal names from an LHS expression."""
    if isinstance(lhs, Identifier):
        name = lhs.name
        if lhs.hierarchy:
            name = ".".join(lhs.hierarchy) + "." + name
        names = {name}
        parts = name.split(".")
        for index in range(1, len(parts)):
            names.add(".".join(parts[:index]))
        return names
    if isinstance(lhs, (BitSelect, RangeSelect, PartSelect)):
        return _lhs_base_names(lhs.target)
    if isinstance(lhs, Concatenation):
        names: set[str] = set()
        for part in lhs.parts:
            names.update(_lhs_base_names(part))
        return names
    return set()

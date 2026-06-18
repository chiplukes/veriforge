"""VM-aware event-driven simulation scheduler.

Mirrors the reference ``Scheduler`` but uses compiled bytecode processes
and the ``Interpreter`` for execution instead of tree-walking
``ExpressionEvaluator`` / ``StatementExecutor``.

Implements the same IEEE 1364-2005 scheduling regions:
  1. Active region  — execute compiled processes
  2. NBA region     — apply scheduled non-blocking updates
  3. Delta check    — if any signal changed → repeat Active region
  4. Advance time   — move to next queued event
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import Identifier

from ..evaluator import EvalContext, ExpressionEvaluator
from ..event_queue import CoroutineMixin, EventQueueMixin, SignalDictBase, TimedEvent
from ..executor import StatementExecutor
from ..value import Value
from .compiler import Compiler, CompiledProcess, ProcessType
from .interpreter import Interpreter, StopSimulation, _format_display

# Try to import the Cython fast interpreter
try:
    from ._interp_fast import cy_execute_batch, CyStopSimulation as _CyStop, CyContext as _CyContext

    # Guard against stale .pyd compiled before setup_wide / other required
    # methods were added.  When the Cython extension is out-of-date the scheduler
    # falls back to pure Python rather than crashing mid-simulation.
    _required_cy_methods = ("setup_wide", "setup_memory")
    _missing = [m for m in _required_cy_methods if not hasattr(_CyContext, m)]
    if _missing:
        import warnings as _warnings

        _warnings.warn(
            f"veriforge.sim.vm._interp_fast is stale (missing: "
            f"{', '.join(_missing)}).  Rebuild with: "
            f"uv sync --reinstall-package veriforge",
            RuntimeWarning,
            stacklevel=2,
        )
        raise ImportError("stale _interp_fast — missing required CyContext methods")

    _HAS_CYTHON = True
except ImportError:
    _HAS_CYTHON = False
    _CyStop = None

# Opt-out: the Cython VM currently has known divergences from the pure-Python
# interpreter (see notes/simulator_engines.md).  Setting this env var forces
# the pure-Python path even when the extension is built.
import os as _os

if _os.environ.get("VERIFORGE_DISABLE_CYTHON_VM") == "1":
    _HAS_CYTHON = False

if TYPE_CHECKING:
    from collections.abc import Callable

    from veriforge.model.design import Module


def _identifier_from_name(name: str) -> Identifier:
    parts = name.split(".")
    if len(parts) == 1:
        return Identifier(name)
    return Identifier(name=parts[-1], hierarchy=parts[:-1])


class VMScheduler(EventQueueMixin, CoroutineMixin):  # cm:6d8a2f
    """Event-driven scheduler using the bytecode VM.

    Provides the same interface as the reference ``Scheduler`` so it can
    be used as a drop-in replacement in ``Simulator``.

    Attributes:
        time:           Current simulation time.
        compiler:       The bytecode compiler (holds signal layout + compiled processes).
        interpreter:    The bytecode interpreter.
        display_output: Collected $display output.
        delta_limit:    Maximum delta cycles per time step.
    """

    __slots__ = (
        "_always_timing_coroutines",
        "_bootstrapped",
        "_combo_procs",
        "_const_c_mask",
        "_const_c_val",
        "_const_c_width",
        "_continuous_procs",
        "_coro_sync_names",
        "_cy_ctx",
        "_event_queue",
        "_event_seq",
        "_initial_coroutines",
        "_initial_procs",
        "_monitor_active",
        "_monitor_prev_vals",
        "_on_time_step",
        "_pending_drives",
        "_prev_sig_mask",
        "_prev_sig_val",
        "_proc_idx",
        "_ref_ctx",
        "_ref_evaluator",
        "_ref_executor",
        "_reverse_sig_map",
        "_seq_procs",
        "_sig_to_combo",
        "_sig_to_cont",
        "_sig_to_procs",
        "_triggered_seq",
        "_use_cython",
        "compiler",
        "ctx",
        "delta_limit",
        "display_output",
        "interpreter",
        "time",
    )

    def __init__(self, *, delta_limit: int = 10_000, force_python: bool = False) -> None:
        self.time: int = 0
        self.compiler = Compiler()
        self.interpreter: Interpreter | None = None
        self.delta_limit = delta_limit
        self.display_output: list[str] = []

        # Process categories (populated during elaborate)
        self._continuous_procs: list[CompiledProcess] = []
        self._combo_procs: list[CompiledProcess] = []
        self._seq_procs: list[CompiledProcess] = []
        self._initial_procs: list[CompiledProcess] = []

        # Signal → processes sensitive to it (all types)
        self._sig_to_procs: dict[int, list[CompiledProcess]] = {}
        # Inverted indices for fast dirty lookup (populated during elaborate)
        self._sig_to_cont: dict[int, list[CompiledProcess]] = {}
        self._sig_to_combo: dict[int, list[CompiledProcess]] = {}

        # Event queue
        self._event_queue: list[TimedEvent] = []
        self._event_seq: int = 0

        # Edge detection state
        self._prev_sig_val: list[int] = []
        self._prev_sig_mask: list[int] = []
        self._triggered_seq: set[int] = set()
        self._pending_drives: set[int] = set()

        # Optional callback fired after each time step completes
        self._on_time_step: Callable[[VMScheduler], None] | None = None

        # $monitor state: (program, sensitivity_sigs) or None
        # The program is a mini-bytecoded program that produces one display line.
        # _monitor_prev_vals stores {sig_id: (val, mask)} for change detection.
        self._monitor_active: tuple[list, set[int]] | None = None
        self._monitor_prev_vals: dict[int, tuple[int, int]] = {}

        # EvalContext wrapper for external signal access (testbench API)
        self.ctx: _VMEvalContext | None = None

        # Cython fast path
        self._use_cython: bool = _HAS_CYTHON and not force_python
        self._const_c_val: list[int] = []
        self._const_c_mask: list[int] = []
        self._const_c_width: list[int] = []
        self._cy_ctx = None
        self._proc_idx: dict[int, int] = {}

        # Fallback for initial blocks with timing controls
        self._ref_evaluator: ExpressionEvaluator | None = None
        self._ref_executor: StatementExecutor | None = None
        self._ref_ctx: EvalContext | None = None
        self._initial_coroutines: dict[int, object] = {}
        self._always_timing_coroutines: dict[int, object] = {}
        # Coroutine sync optimization: proc_id → set of signal names to sync
        self._coro_sync_names: dict[int, set[str]] = {}
        # Reverse signal map: sid → name (built during elaborate)
        self._reverse_sig_map: dict[int, str] = {}

        # Guard: prevent re-executing initial blocks on subsequent run() calls
        self._bootstrapped: bool = False

    # ── Elaboration ──────────────────────────────────────────────

    def elaborate(self, module: Module) -> None:
        """Compile the module and set up the scheduler."""
        # Compile all processes
        self.compiler.compile_module(module)

        # Create interpreter with signal storage from compiler
        self.interpreter = Interpreter(
            self.compiler.sig_val,
            self.compiler.sig_mask,
            self.compiler.sig_width,
            self.compiler.const_pool,
        )

        # Wire memory arrays into interpreter
        if self.compiler.mem_count > 0:
            self.interpreter.mem_val = self.compiler.mem_val
            self.interpreter.mem_mask = self.compiler.mem_mask
            self.interpreter.mem_info = self.compiler.mem_info

        # Wire $readmemh/$readmemb task table
        if self.compiler.readmem_tasks:
            self.interpreter.readmem_tasks = self.compiler.readmem_tasks

        # Wire $fopen task table
        if self.compiler.fopen_tasks:
            self.interpreter.fopen_tasks = self.compiler.fopen_tasks

        # Wire $display/$monitor format string table
        if self.compiler.display_formats:
            self.interpreter.display_formats = self.compiler.display_formats

        # Categorize processes
        for proc in self.compiler.processes:
            if proc.process_type == ProcessType.CONTINUOUS:
                self._continuous_procs.append(proc)
            elif proc.process_type == ProcessType.COMBINATIONAL:
                if proc.has_timing:
                    pass  # Can't have timing in combo — skip (shouldn't happen)
                else:
                    self._combo_procs.append(proc)
            elif proc.process_type == ProcessType.SEQUENTIAL:
                if proc.has_timing:
                    pass  # Will be handled by reference executor at t=0
                else:
                    self._seq_procs.append(proc)
            elif proc.process_type == ProcessType.INITIAL:
                self._initial_procs.append(proc)

            # Build sensitivity index
            for sid in proc.sensitivity:
                if sid not in self._sig_to_procs:
                    self._sig_to_procs[sid] = []
                self._sig_to_procs[sid].append(proc)

        # Build inverted indices for continuous and combo processes
        for proc in self._continuous_procs:
            for sid in proc.sensitivity:
                if sid not in self._sig_to_cont:
                    self._sig_to_cont[sid] = []
                self._sig_to_cont[sid].append(proc)
        for proc in self._combo_procs:
            for sid in proc.sensitivity:
                if sid not in self._sig_to_combo:
                    self._sig_to_combo[sid] = []
                self._sig_to_combo[sid].append(proc)

        # Memory elements >64 bits wide need a wide-memory pool (not yet implemented);
        # fall back to the pure-Python interpreter for such designs.
        if self._use_cython and self.compiler.mem_count > 0:
            if any(info[0] > 64 for info in self.compiler.mem_info):  # noqa: PLR2004
                self._use_cython = False

        # Pre-extract constant pool for Cython fast path
        if self._use_cython:
            self._const_c_val = [c.val for c in self.compiler.const_pool]
            self._const_c_mask = [c.mask for c in self.compiler.const_pool]
            self._const_c_width = [c.width for c in self.compiler.const_pool]

            # Compute narrow/wide split: signals/constants >64 bits get a separate
            # unsigned-word pool to avoid <long long> overflow in CyContext.setup().
            _WIDE_WORDS = 6  # must match DEF WIDE_WORDS in _interp_fast.pyx
            _MASK64 = 0xFFFF_FFFF_FFFF_FFFF
            _SIGN64 = 1 << 63

            def _to_ll(x: int) -> int:
                """Convert Python int to signed-64-bit range (two's complement)."""
                x &= _MASK64
                return x - (1 << 64) if x >= _SIGN64 else x

            narrow_sig_val: list[int] = []
            narrow_sig_mask: list[int] = []
            wide_sig_off: list[int] = []
            wide_sig_vw: list[int] = []
            wide_sig_mw: list[int] = []
            _next_sig_word = 0
            for _i, _w in enumerate(self.compiler.sig_width):
                if _w > 64:
                    wide_sig_off.append(_next_sig_word)
                    _v, _m = self.compiler.sig_val[_i], self.compiler.sig_mask[_i]
                    for _wrd in range(_WIDE_WORDS):
                        wide_sig_vw.append(int(_v >> (64 * _wrd)) & _MASK64)
                        wide_sig_mw.append(int(_m >> (64 * _wrd)) & _MASK64)
                    _next_sig_word += _WIDE_WORDS
                    narrow_sig_val.append(0)
                    narrow_sig_mask.append(0)
                else:
                    wide_sig_off.append(-1)
                    narrow_sig_val.append(_to_ll(self.compiler.sig_val[_i]))
                    narrow_sig_mask.append(_to_ll(self.compiler.sig_mask[_i]))

            narrow_const_val: list[int] = []
            narrow_const_mask: list[int] = []
            wide_const_off: list[int] = []
            wide_const_vw: list[int] = []
            wide_const_mw: list[int] = []
            _next_const_word = 0
            for _c in self.compiler.const_pool:
                if _c.width > 64:
                    wide_const_off.append(_next_const_word)
                    _v, _m = _c.val, _c.mask
                    for _wrd in range(_WIDE_WORDS):
                        wide_const_vw.append(int(_v >> (64 * _wrd)) & _MASK64)
                        wide_const_mw.append(int(_m >> (64 * _wrd)) & _MASK64)
                    _next_const_word += _WIDE_WORDS
                    narrow_const_val.append(0)
                    narrow_const_mask.append(0)
                else:
                    wide_const_off.append(-1)
                    narrow_const_val.append(_to_ll(_c.val))
                    narrow_const_mask.append(_to_ll(_c.mask))

            # Build persistent CyContext (zero-allocation hot loop)
            programs = [p.program for p in self.compiler.processes]
            cy_ctx = _CyContext()
            cy_ctx.setup(
                narrow_sig_val,
                narrow_sig_mask,
                self.compiler.sig_width,
                narrow_const_val,
                narrow_const_mask,
                self._const_c_width,
                programs,
            )

            # Set up wide signal pool if any signals/constants exceed 64 bits
            if _next_sig_word > 0 or _next_const_word > 0:
                cy_ctx.setup_wide(
                    wide_sig_off,
                    wide_sig_vw,
                    wide_sig_mw,
                    wide_const_off,
                    wide_const_vw,
                    wide_const_mw,
                )

            # Set up memory arrays in CyContext (if any)
            if self.compiler.mem_count > 0:
                cy_ctx.setup_memory(
                    self.compiler.mem_val,
                    self.compiler.mem_mask,
                    self.compiler.mem_info,
                )

            self._cy_ctx = cy_ctx
            self._proc_idx = {id(p): i for i, p in enumerate(self.compiler.processes)}

            # Build delta-loop process tables in CyContext
            proc_types: list[int] = []
            for p in self.compiler.processes:
                if p.process_type == ProcessType.CONTINUOUS:
                    proc_types.append(0)
                elif p.process_type == ProcessType.COMBINATIONAL:
                    proc_types.append(1)
                elif p.process_type == ProcessType.SEQUENTIAL:
                    proc_types.append(2)
                else:
                    proc_types.append(3)  # initial

            # Sensitivity CSR: sig → combo/seq proc indices (not continuous)
            sig_sens_lists: list[list[int]] = [[] for _ in range(len(self.compiler.sig_val))]
            for proc in self._combo_procs + self._seq_procs:
                pidx = self._proc_idx[id(proc)]
                for sid in proc.sensitivity:
                    sig_sens_lists[sid].append(pidx)

            # Continuous assign indices + per-proc sensitivity
            cont_idx_list = [self._proc_idx[id(p)] for p in self._continuous_procs]
            cont_sens_lists = [sorted(p.sensitivity) for p in self._continuous_procs]

            # Edge info: per-proc list of (sig_id, edge_type_int)
            proc_edge_lists: list[list[tuple[int, int]]] = [[] for _ in range(len(self.compiler.processes))]
            for proc in self._seq_procs:
                pidx = self._proc_idx[id(proc)]
                for sid, etype in proc.edge_signals.items():
                    proc_edge_lists[pidx].append((sid, 0 if etype == "posedge" else 1))

            cy_ctx.setup_processes(
                proc_types,
                sig_sens_lists,
                cont_idx_list,
                cont_sens_lists,
                proc_edge_lists,
            )

        # Create EvalContext wrapper for testbench signal access
        self.ctx = _VMEvalContext(self.compiler, self._cy_ctx)

        # Build reverse signal map for sync optimization
        self._reverse_sig_map = {sid: name for name, sid in self.compiler.signal_map.items()}

        # Set up reference executor for initial blocks with timing
        self._ref_evaluator = ExpressionEvaluator()
        self._ref_executor = StatementExecutor(self._ref_evaluator)
        self._ref_executor._function_map = {f.name: f for f in module.functions}
        self._ref_executor._task_map = {t.name: t for t in module.tasks}
        self._ref_ctx = EvalContext()
        from ..elaborate import _build_struct_env

        _type_map, struct_signal_map = _build_struct_env(module)
        self._ref_ctx._struct_type_map.update(_type_map)
        self._ref_ctx._struct_types.update(struct_signal_map)
        self._sync_ref_ctx()

    def _sync_ref_ctx(self, names: set[str] | None = None) -> None:  # noqa: PLR0912
        """Sync reference EvalContext from VM signal storage.

        Args:
            names: If provided, only sync these signal names (optimization for
                   coroutines that touch few signals). If None, sync all.
        """
        if self._ref_ctx is None:
            return
        sig_map = self.compiler.signal_map
        sig_width = self.compiler.sig_width
        ref_sigs = self._ref_ctx._signals
        if self._cy_ctx is not None:
            # Read from C arrays (authoritative when Cython delta loop is active)
            if names is not None:
                for name in names:
                    sid = sig_map.get(name)
                    if sid is not None:
                        v, m = self._cy_ctx.read_signal(sid)
                        ref_sigs[name] = Value(v, width=sig_width[sid], mask=m)
            else:
                for name, sid in sig_map.items():
                    v, m = self._cy_ctx.read_signal(sid)
                    ref_sigs[name] = Value(v, width=sig_width[sid], mask=m)
        else:
            sig_val = self.compiler.sig_val
            sig_mask = self.compiler.sig_mask
            if names is not None:
                for name in names:
                    sid = sig_map.get(name)
                    if sid is not None:
                        ref_sigs[name] = Value(sig_val[sid], width=sig_width[sid], mask=sig_mask[sid])
            else:
                for name, sid in sig_map.items():
                    ref_sigs[name] = Value(sig_val[sid], width=sig_width[sid], mask=sig_mask[sid])
        self._sync_mem_to_ref()

    def _sync_from_ref_ctx(self, names: set[str] | None = None) -> None:
        """Sync VM signal storage from reference EvalContext.

        Also marks signals that actually changed as dirty in the interpreter
        so that continuous assigns and edge detection work correctly.

        Args:
            names: If provided, only sync these signal names. If None, sync all.
        """
        if self._ref_ctx is None:
            return
        sig_map = self.compiler.signal_map
        sig_val = self.compiler.sig_val
        sig_mask = self.compiler.sig_mask
        ref_sigs = self._ref_ctx._signals
        dirty = self.interpreter.dirty if self.interpreter else None
        if names is not None:
            for name in names:
                v = ref_sigs.get(name)
                if v is not None:
                    sid = sig_map.get(name)
                    if sid is not None:
                        if sig_val[sid] != v.val or sig_mask[sid] != v.mask:
                            sig_val[sid] = v.val
                            sig_mask[sid] = v.mask
                            if dirty is not None:
                                dirty.add(sid)
        else:
            for name, sid in sig_map.items():
                v = ref_sigs.get(name)
                if v is not None:
                    if sig_val[sid] != v.val or sig_mask[sid] != v.mask:
                        sig_val[sid] = v.val
                        sig_mask[sid] = v.mask
                        if dirty is not None:
                            dirty.add(sid)
        self._sync_mem_from_ref()

    def _sync_mem_to_ref(self) -> None:
        """Copy memory arrays from VM storage into the fallback EvalContext."""
        if self._ref_ctx is None or self.interpreter is None:
            return
        for name, mid in self.compiler.mem_map.items():
            elem_w, depth, base = self.compiler.mem_info[mid]
            mem_data: list[Value] = []
            if self._cy_ctx is not None:
                # Read from CyContext C arrays (authoritative, matches _sync_ref_ctx
                # which also reads from CyContext for signals). Without this, stale
                # Python mem lists cause _sync_mem_from_ref to revert CyContext
                # memories back to X after every coroutine sync.
                for addr in range(depth):
                    v, m = self._cy_ctx.read_mem(mid, addr)
                    mem_data.append(Value(v, width=elem_w, mask=m))
            else:
                for addr in range(depth):
                    flat = base + addr
                    mem_data.append(
                        Value(self.interpreter.mem_val[flat], width=elem_w, mask=self.interpreter.mem_mask[flat])
                    )
            self._ref_ctx._memories[name] = (mem_data, elem_w)
            self._ref_ctx._memory_names.add(name)

    def _sync_mem_from_ref(self) -> None:
        """Copy memory arrays from the fallback EvalContext back into VM storage."""
        if self._ref_ctx is None or self.interpreter is None:
            return
        dirty = self.interpreter.dirty
        for name, mid in self.compiler.mem_map.items():
            entry = self._ref_ctx._memories.get(name)
            if entry is None:
                continue
            mem_data, _elem_w = entry
            elem_w, depth, base = self.compiler.mem_info[mid]
            for addr, val in enumerate(mem_data[:depth]):
                flat = base + addr
                new_val = val.val & ((1 << elem_w) - 1)
                new_mask = val.mask & ((1 << elem_w) - 1)
                if self.interpreter.mem_val[flat] != new_val or self.interpreter.mem_mask[flat] != new_mask:
                    self.interpreter.mem_val[flat] = new_val
                    self.interpreter.mem_mask[flat] = new_mask
                    dirty.add(self.compiler.mem_marker_sigs[mid])

    # ── Main simulation loop ─────────────────────────────────────

    def run(self, *, max_time: int = 1_000_000) -> None:
        """Run the simulation until completion or max_time."""
        interp = self.interpreter
        if interp is None:
            raise RuntimeError("Must call elaborate() before run()")

        if not self._bootstrapped:
            # Execute initial blocks at t=0 BEFORE combo/continuous evaluation
            # so that memory arrays and signals are properly initialized.
            for proc in self._initial_procs:
                if proc.has_timing:
                    # Route to reference executor for suspend/resume support
                    stopped = self._execute_initial_with_timing(proc)
                else:
                    stopped = self._execute_initial_direct(proc)
                if stopped:
                    return

            # Schedule always blocks with timing controls as reference coroutines.
            # These cannot be compiled to bytecode (e.g. "always #5 clk=~clk;").
            for proc in self.compiler.processes:
                if proc.has_timing and proc.process_type in (ProcessType.COMBINATIONAL, ProcessType.SEQUENTIAL):
                    self._schedule_always_with_timing(proc)

            # Wire VCD callback if $dumpvars was executed in an initial block
            self._wire_vcd_from_ref()

            self._bootstrapped = True

        # Bootstrap / re-bootstrap: execute all continuous assigns until stable.
        # This must run on every run() call so that external drive() changes
        # propagate through combinational logic.
        for _ in range(self.delta_limit):
            if self._cy_ctx is not None:
                snap = self._cy_ctx.snapshot_signals()
            else:
                snap = (list(self.compiler.sig_val), list(self.compiler.sig_mask))
            if self.interpreter:
                self.interpreter.dirty.clear()
            self._run_continuous_assigns()
            if self._cy_ctx is not None:
                cur = self._cy_ctx.snapshot_signals()
            else:
                cur = (list(self.compiler.sig_val), list(self.compiler.sig_mask))
            if cur == snap and not (self.interpreter and self.interpreter.dirty):
                break

        # Execute combinational always blocks
        if self._combo_procs:
            self._run_process_list(self._combo_procs)

        # Re-run continuous assigns after combos to propagate changes
        for _ in range(self.delta_limit):
            if self._cy_ctx is not None:
                snap = self._cy_ctx.snapshot_signals()
            else:
                snap = (list(self.compiler.sig_val), list(self.compiler.sig_mask))
            if self.interpreter:
                self.interpreter.dirty.clear()
            self._run_continuous_assigns()
            # Save CA dirty set before _run_process_list clears it.  CAs that
            # update memory arrays (e.g. lrotc_stage depends on bitcnt_partial_q)
            # mark memory-marker signals dirty even when no wire signal changes.
            # Without saving this, the convergence check below would see an
            # empty dirty set and exit one iteration too early, leaving downstream
            # combo procs (butterfly_mask, invbutterfly_result) with stale X values.
            ca_dirty = set(self.interpreter.dirty) if self.interpreter else set()
            if self._combo_procs:
                self._run_process_list(self._combo_procs)
            if self._cy_ctx is not None:
                cur = self._cy_ctx.snapshot_signals()
            else:
                cur = (list(self.compiler.sig_val), list(self.compiler.sig_mask))
            if cur == snap and not (self.interpreter and self.interpreter.dirty) and not ca_dirty:
                break

        # Activate any $monitor registered in initial blocks (snapshot after bootstrap)
        self._check_monitor_activation()

        # Clear bootstrap dirty set so the event loop sees only real event-driven
        # changes. Bootstrap assigns accumulate dirty signals during convergence
        # iterations; without this clear, the first time step's changed set would
        # include all wire signals resolved during bootstrap, inflating
        # run_delta_loop's initial changed set and breaking propagation chains.
        if self.interpreter:
            self.interpreter.dirty.clear()

        # Fire time-step callback at t=0 (after initial blocks + bootstrap)
        # so VCD recording captures the t=0 state.
        if self._on_time_step is not None:
            self._on_time_step(self)

        # Run event loop
        self._run_event_loop(max_time)

        # Finalize VCD writer if active
        if self._ref_executor is not None:
            writer = getattr(self._ref_executor, "_vcd_writer", None)
            if writer is not None:
                writer.finalize()
                self._ref_executor._vcd_writer = None

    def _wire_vcd_from_ref(self) -> None:
        """If the reference executor created a VCD writer, wire it into _on_time_step."""
        if self._on_time_step is not None:
            return
        if self._ref_executor is None:
            return
        writer = getattr(self._ref_executor, "_vcd_writer", None)
        if writer is None:
            return
        self._on_time_step = self._ref_executor.vcd_time_step_callback

    def _run_event_loop(self, max_time: int) -> None:  # noqa: PLR0912
        """Process the event queue until empty or max_time exceeded."""
        while self._event_queue:
            next_time = self._event_queue[0].time
            if next_time > max_time:
                break

            self.time = next_time
            self.interpreter.time = self.time
            if self._cy_ctx is not None:
                self._cy_ctx.set_time(self.time)

            # Snapshot signals for edge detection
            if self._cy_ctx is not None and self._cy_ctx._procs_setup:
                self._cy_ctx.take_snapshot()
                self._cy_ctx.reset_seq_fired()
            elif self._cy_ctx is not None:
                self._prev_sig_val, self._prev_sig_mask = self._cy_ctx.snapshot_signals()
            else:
                self._prev_sig_val = list(self.compiler.sig_val)
                self._prev_sig_mask = list(self.compiler.sig_mask)
            self._triggered_seq = set()

            # Pop all events at this time
            events = self._pop_events_at(next_time)

            # Execute events
            stopped = False
            for event in events:
                if stopped:
                    break
                stopped = self._execute_event(event)

            # Signals changed by events (interpreter.dirty tracks actual changes)
            changed = set(self.interpreter.dirty)
            self.interpreter.dirty.clear()

            # ── Fast path: C delta loop ──
            if self._cy_ctx is not None and self._cy_ctx._procs_setup and changed:
                try:
                    self._cy_ctx.run_delta_loop(list(changed), self.delta_limit)
                except _CyStop:
                    self._drain_cy_display()
                    self._event_queue.clear()
                    stopped = True
                self._drain_cy_display()
            elif changed:
                # ── Fallback: Python delta loop ──
                # Propagate continuous assigns to a fixed point
                self._propagate_continuous(changed)
                changed.update(self.interpreter.dirty)

                # Delta cycle loop
                delta = 0
                while True:
                    delta += 1
                    if delta > self.delta_limit:
                        raise RuntimeError(f"Delta cycle limit ({self.delta_limit}) exceeded at t={self.time}")

                    # Collect triggered based on actual signal changes
                    triggered = self._collect_triggered(changed)
                    if not triggered:
                        break

                    # Run triggered processes
                    self._run_process_list(triggered)

                    # Apply NBAs
                    nba_dirty = self._apply_nbas()

                    # Compute changed signals for next iteration
                    changed = set(self.interpreter.dirty)
                    changed.update(nba_dirty)

                    # Propagate continuous assigns to a fixed point
                    if changed:
                        self.interpreter.dirty.clear()
                        self._propagate_continuous(changed)
                        changed.update(self.interpreter.dirty)

            # Re-fire $monitor if any monitored signal changed
            self._fire_monitors()

            # Fire time-step callback (also fires on the stop iteration so VCD
            # captures the final blocking-assignment state before $finish).
            if self._on_time_step is not None:
                self._on_time_step(self)

            if stopped:
                break

        # Collect any remaining display output
        if self.interpreter:
            self.display_output.extend(self.interpreter.display_output)
            self.interpreter.display_output.clear()

    def run_step(self) -> bool:  # noqa: PLR0912, PLR0915
        """Advance simulation by one time step.

        Pops all events at the next scheduled time, executes them, and runs
        the delta cycle loop to convergence.  Returns True if simulation
        should continue, False if finished or stopped ($finish / empty queue).
        """
        if not self._event_queue:
            return False

        next_time = self._event_queue[0].time
        self.time = next_time
        self.interpreter.time = self.time
        if self._cy_ctx is not None:
            self._cy_ctx.set_time(self.time)

        # Snapshot signals for edge detection
        if self._cy_ctx is not None and self._cy_ctx._procs_setup:
            self._cy_ctx.take_snapshot()
            self._cy_ctx.reset_seq_fired()
        elif self._cy_ctx is not None:
            self._prev_sig_val, self._prev_sig_mask = self._cy_ctx.snapshot_signals()
        else:
            self._prev_sig_val = list(self.compiler.sig_val)
            self._prev_sig_mask = list(self.compiler.sig_mask)
        self._triggered_seq = set()

        # Pop all events at this time
        events = self._pop_events_at(next_time)

        # Execute events
        stopped = False
        for event in events:
            if stopped:
                break
            stopped = self._execute_event(event)

        # Signals changed by events
        changed = set(self.interpreter.dirty)
        self.interpreter.dirty.clear()

        # ── Fast path: C delta loop ──
        if not stopped and self._cy_ctx is not None and self._cy_ctx._procs_setup and changed:
            try:
                self._cy_ctx.run_delta_loop(list(changed), self.delta_limit)
            except _CyStop:
                self._drain_cy_display()
                self._event_queue.clear()
                stopped = True
            self._drain_cy_display()
        elif not stopped and changed:
            # ── Fallback: Python delta loop ──
            self._propagate_continuous(changed)
            changed.update(self.interpreter.dirty)

            delta = 0
            while True:
                delta += 1
                if delta > self.delta_limit:
                    raise RuntimeError(f"Delta cycle limit ({self.delta_limit}) exceeded at t={self.time}")

                triggered = self._collect_triggered(changed)
                if not triggered:
                    break

                self._run_process_list(triggered)
                nba_dirty = self._apply_nbas()
                changed = set(self.interpreter.dirty)
                changed.update(nba_dirty)

                if changed:
                    self.interpreter.dirty.clear()
                    self._propagate_continuous(changed)
                    changed.update(self.interpreter.dirty)

        # Re-fire $monitor if any monitored signal changed
        self._fire_monitors()

        # Fire time-step callback (also fires on the stop iteration so VCD
        # captures the final blocking-assignment state before $finish).
        if self._on_time_step is not None:
            self._on_time_step(self)

        return not stopped

    def _drain_cy_display(self) -> None:
        """Drain display events from the Cython buffer and format them."""
        if self._cy_ctx is None:
            return
        events = self._cy_ctx.drain_display_buffer()
        if not events:
            return
        fmts = self.compiler.display_formats if self.compiler else []
        for fmt_id, _is_monitor, raw_args in events:
            args = [Value(v, width=w, mask=m) for v, m, w in raw_args]
            self.display_output.append(_format_display(args, fmt_id, fmts, self.time))

    def _check_monitor_activation(self) -> None:
        """Check if interpreter activated a new $monitor and register it."""
        mid = self.interpreter.active_monitor_id
        if mid < 0:
            return
        if mid < len(self.compiler.monitor_programs):
            prog, sigs = self.compiler.monitor_programs[mid]
            self._monitor_active = (prog, sigs)
            # Snapshot current values of monitored signals
            self._monitor_prev_vals = {sid: self._read_sig(sid) for sid in sigs}
        self.interpreter.active_monitor_id = -1

    def _read_sig(self, sid: int) -> tuple[int, int]:
        """Read a signal's (val, mask) from CyContext if available, else Python lists."""
        if self._cy_ctx is not None:
            return self._cy_ctx.read_signal(sid)
        return (self.compiler.sig_val[sid], self.compiler.sig_mask[sid])

    def _fire_monitors(self) -> None:
        """Re-fire the active $monitor if any monitored signal changed.

        Called at the end of each time step (after delta convergence).
        Per IEEE 1364: $monitor fires once per time step when any argument
        changes.  Only one $monitor can be active at a time.
        """
        # First, check if any process activated a new monitor
        self._check_monitor_activation()

        if self._monitor_active is None:
            return

        prog, sigs = self._monitor_active
        # Check if any monitored signal changed
        changed = False
        for sid in sigs:
            cur = self._read_sig(sid)
            prev = self._monitor_prev_vals.get(sid)
            if prev is None or prev[0] != cur[0] or prev[1] != cur[1]:
                changed = True
                break

        if not changed:
            return

        # Update prev vals
        self._monitor_prev_vals = {sid: self._read_sig(sid) for sid in sigs}

        # Sync CyContext → Python lists so interpreter sees current values
        if self._cy_ctx is not None:
            self._cy_ctx.sync_signals_to_lists(self.compiler.sig_val, self.compiler.sig_mask)

        # Execute the monitor mini-program
        interp = self.interpreter
        old_dirty = interp.dirty.copy()
        interp.dirty.clear()
        try:
            interp.execute(prog)
        except StopSimulation:
            pass
        self.display_output.extend(interp.display_output)
        interp.display_output.clear()
        interp.dirty = old_dirty  # Monitors don't affect dirty tracking

    def _execute_event(self, event: object) -> bool:
        """Execute a single event. Returns True if simulation should stop."""
        if isinstance(event, tuple) and event[0] == "initial":
            return self._execute_initial(event[1])

        if isinstance(event, tuple) and event[0] == "initial_coro":
            return self._resume_initial_coro(event[1])

        if isinstance(event, tuple) and event[0] == "always_coro":
            return self._resume_always_coro(event[1])

        if isinstance(event, tuple) and event[0] == "clock_toggle":
            sig_name, val = event[1], event[2]
            sid = self.compiler.signal_map.get(sig_name)
            if sid is not None:
                if self._cy_ctx is not None:
                    self._cy_ctx.write_signal(sid, val.val, val.mask)
                else:
                    self.compiler.sig_val[sid] = val.val
                    self.compiler.sig_mask[sid] = val.mask
                self.interpreter.dirty.add(sid)
            return False

        return False

    def _execute_initial(self, proc_idx: int) -> bool:
        """Execute an initial block process.

        Initial blocks may contain timing controls (#delay, @event) that
        require suspend/resume. We use the reference executor's coroutine
        mechanism for these, syncing signal state before and after.
        """
        proc = self._initial_procs[proc_idx]
        return self._execute_initial_direct(proc)

    def _execute_initial_direct(self, proc: CompiledProcess) -> bool:
        """Execute an initial block process directly.

        Returns True if simulation should stop.
        """

        # Sync CyContext → Python lists so interpreter sees current values
        if self._cy_ctx is not None:
            self._cy_ctx.sync_signals_to_lists(self.compiler.sig_val, self.compiler.sig_mask)
            if self.compiler.mem_count > 0:
                self._cy_ctx.sync_mem_to_lists(self.compiler.mem_val, self.compiler.mem_mask)

        # Try VM execution first (works for initial blocks without timing)
        try:
            self.interpreter.dirty.clear()
            self.interpreter.execute(proc.program)
            # Collect display output
            self.display_output.extend(self.interpreter.display_output)
            self.interpreter.display_output.clear()
            # Sync Python lists → CyContext after initial block execution
            if self._cy_ctx is not None:
                self._cy_ctx.sync_signals_from_lists(self.compiler.sig_val, self.compiler.sig_mask)
                if self.compiler.mem_count > 0:
                    self._cy_ctx.sync_mem_from_lists(self.compiler.mem_val, self.compiler.mem_mask)
            return False
        except StopSimulation:
            self.display_output.extend(self.interpreter.display_output)
            self.interpreter.display_output.clear()
            self._event_queue.clear()
            return True

    def _execute_initial_with_timing(self, proc: CompiledProcess) -> bool:
        """Execute an initial block that contains timing controls.

        Delegates to ``CoroutineMixin._run_initial_coro``.
        Returns True if simulation should stop.
        """
        block = proc.source_block
        if block is None:
            return False
        return self._run_initial_coro(block.body, id(proc))

    def _schedule_always_with_timing(self, proc: CompiledProcess) -> None:
        """Start an always block with timing controls as a coroutine.

        Computes targeted sync names for performance, then delegates
        to ``CoroutineMixin._start_always_coro``.
        """
        block = proc.source_block
        if block is None:
            return

        proc_id = id(proc)

        # Compute the set of signal names this coroutine touches (once).
        body_sigs: set[int] = set()
        self.compiler._collect_stmt_signals(block.body, body_sigs)
        rsm = self._reverse_sig_map
        sync_names = {rsm[sid] for sid in body_sigs if sid in rsm}
        self._coro_sync_names[proc_id] = sync_names

        self._start_always_coro(block.body, proc_id)

    # -- CoroutineMixin hooks --------------------------------------------------

    def _coro_sync_in(self, names: set[str] | None = None) -> None:
        self._sync_ref_ctx(names)

    def _coro_sync_out(self, names: set[str] | None = None) -> None:
        if self._cy_ctx is not None and names is None:
            # Freshen Python lists from CyContext so _sync_from_ref_ctx compares
            # against current signal values, not stale X-initialized Python lists.
            # Without this, X→0 wire transitions flagged as dirty inflate the
            # initial changed set and break _propagate_cont_assigns dedup.
            self._cy_ctx.sync_signals_to_lists(self.compiler.sig_val, self.compiler.sig_mask)
            if self.compiler.mem_count > 0:
                self._cy_ctx.sync_mem_to_lists(self.compiler.mem_val, self.compiler.mem_mask)
        self._sync_from_ref_ctx(names)
        self._sync_cy_from_vm(names)

    def _coro_post_resume(self) -> None:
        self._wire_vcd_from_ref()
        if self._ref_executor is not None:
            self.display_output.extend(self._ref_executor.display_output)
            self._ref_executor.display_output.clear()

    def _coro_get_sync_names(self, proc_id: int) -> set[str] | None:
        return self._coro_sync_names.get(proc_id)

    def _sync_cy_from_vm(self, names: set[str] | None = None) -> None:
        """Sync CyContext from VM Python lists (after reference executor writes).

        If ``names`` is provided, only those signals are written back to the
        CyContext C arrays — this avoids overwriting signals that were updated
        by the C delta loop but not reflected in the (now-stale) Python lists.
        """
        if self._cy_ctx is not None:
            if names is None:
                self._cy_ctx.sync_signals_from_lists(self.compiler.sig_val, self.compiler.sig_mask)
            else:
                sig_map = self.compiler.signal_map
                sig_val = self.compiler.sig_val
                sig_mask = self.compiler.sig_mask
                for name in names:
                    sid = sig_map.get(name)
                    if sid is not None:
                        self._cy_ctx.write_signal(sid, sig_val[sid], sig_mask[sid])
            if self.compiler.mem_count > 0:
                self._cy_ctx.sync_mem_from_lists(self.compiler.mem_val, self.compiler.mem_mask)

    def _run_continuous_assigns(self) -> None:
        """Execute all continuous assigns."""
        if not self._continuous_procs:
            return

        if self._cy_ctx is not None:
            indices = [self._proc_idx[id(p)] for p in self._continuous_procs]
            try:
                _nba, dirty_set = self._cy_ctx.execute_procs(indices)
            except _CyStop:
                self._drain_cy_display()
                return
            self._drain_cy_display()
            self.interpreter.dirty.update(dirty_set)
        elif self._use_cython:
            programs = [p.program for p in self._continuous_procs]
            try:
                nba_list, dirty_set = cy_execute_batch(
                    programs,
                    self.compiler.sig_val,
                    self.compiler.sig_mask,
                    self.compiler.sig_width,
                    self._const_c_val,
                    self._const_c_mask,
                    self._const_c_width,
                    self.time,
                )
            except _CyStop:
                return
            self.interpreter.dirty.update(dirty_set)
            for sid, val, mask in nba_list:
                self.interpreter.nba_queue.append((sid, Value(val, width=self.compiler.sig_width[sid], mask=mask)))
        else:
            for proc in self._continuous_procs:
                try:
                    self.interpreter.execute(proc.program)
                except StopSimulation:
                    pass

    def _propagate_continuous(self, dirty: set[int]) -> None:
        """Run continuous assigns iteratively until no signal value changes.

        ``dirty`` is mutated in-place to accumulate every sid whose value
        changed during propagation (callers rely on this for downstream
        bookkeeping such as event scheduling).
        """
        work = set(dirty)
        for _ in range(self.delta_limit):
            if not work:
                break
            self.interpreter.dirty.clear()
            self._run_dirty_continuous_assigns(work)
            work = set(self.interpreter.dirty)
            # Only the actually-changed sids should propagate further. The
            # `dirty` accumulator preserves the union for callers, but the
            # worklist must be replaced (not unioned) so we don't re-fire
            # sensitive procs whose triggers haven't changed this iteration.
            dirty.update(work)

    def _run_dirty_continuous_assigns(self, dirty: set[int]) -> None:
        """Re-run continuous assigns whose inputs overlap the dirty set."""
        # Use inverted index: for each dirty signal, collect sensitive continuous procs
        seen: set[int] = set()
        procs_to_run: list[CompiledProcess] = []
        sig_to_cont = self._sig_to_cont
        for sid in dirty:
            procs = sig_to_cont.get(sid)
            if procs is not None:
                for p in procs:
                    pid = id(p)
                    if pid not in seen:
                        seen.add(pid)
                        procs_to_run.append(p)
        if not procs_to_run:
            return

        if self._cy_ctx is not None:
            indices = [self._proc_idx[id(p)] for p in procs_to_run]
            try:
                _nba, new_dirty = self._cy_ctx.execute_procs(indices)
            except _CyStop:
                self._drain_cy_display()
                return
            self._drain_cy_display()
            self.interpreter.dirty.update(new_dirty)
        elif self._use_cython:
            programs = [p.program for p in procs_to_run]
            try:
                nba_list, new_dirty = cy_execute_batch(
                    programs,
                    self.compiler.sig_val,
                    self.compiler.sig_mask,
                    self.compiler.sig_width,
                    self._const_c_val,
                    self._const_c_mask,
                    self._const_c_width,
                    self.time,
                )
            except _CyStop:
                return
            # Merge results into interpreter state so caller can read dirty
            self.interpreter.dirty.update(new_dirty)
            for sid, val, mask in nba_list:
                self.interpreter.nba_queue.append((sid, Value(val, width=self.compiler.sig_width[sid], mask=mask)))
        else:
            for proc in procs_to_run:
                try:
                    self.interpreter.execute(proc.program)
                except StopSimulation:
                    pass

    def _run_process_list(self, procs: list[CompiledProcess]) -> None:
        """Execute a list of compiled processes."""
        interp = self.interpreter
        interp.dirty.clear()

        if self._cy_ctx is not None:
            indices = [self._proc_idx[id(p)] for p in procs]
            try:
                _nba, dirty_set = self._cy_ctx.execute_procs(indices)
            except _CyStop:
                self._drain_cy_display()
                self._event_queue.clear()
                raise StopSimulation() from None
            self._drain_cy_display()
            interp.dirty.update(dirty_set)
        elif self._use_cython:
            programs = [p.program for p in procs]
            try:
                nba_list, dirty_set = cy_execute_batch(
                    programs,
                    self.compiler.sig_val,
                    self.compiler.sig_mask,
                    self.compiler.sig_width,
                    self._const_c_val,
                    self._const_c_mask,
                    self._const_c_width,
                    self.time,
                )
            except _CyStop:
                self._event_queue.clear()
                raise StopSimulation() from None
            interp.dirty.update(dirty_set)
            for sid, val, mask in nba_list:
                interp.nba_queue.append((sid, Value(val, width=self.compiler.sig_width[sid], mask=mask)))
        else:
            for proc in procs:
                try:
                    interp.execute(proc.program)
                except StopSimulation:
                    self.display_output.extend(interp.display_output)
                    interp.display_output.clear()
                    self._event_queue.clear()
                    raise

            # Collect display output
            self.display_output.extend(interp.display_output)
            interp.display_output.clear()

    def _apply_nbas(self) -> set[int]:
        """Apply non-blocking assignment updates. Returns set of changed signal IDs."""
        if self._cy_ctx is not None:
            return self._cy_ctx.apply_nbas()

        changed: set[int] = set()
        interp = self.interpreter
        sig_val = self.compiler.sig_val
        sig_mask = self.compiler.sig_mask

        for item in interp.nba_queue:
            if isinstance(item, tuple) and len(item) == 4:
                # Struct field NBA: (sig_id, msb, lsb, field_val) — apply lazily
                sig_id, msb, lsb, field_val = item
                sig_w = self.compiler.sig_width[sig_id]
                current = Value(sig_val[sig_id], width=sig_w, mask=sig_mask[sig_id])
                updated = current.set_range(msb, lsb, field_val)
                if sig_val[sig_id] != updated.val or sig_mask[sig_id] != updated.mask:
                    sig_val[sig_id] = updated.val
                    sig_mask[sig_id] = updated.mask
                    changed.add(sig_id)
            elif isinstance(item, tuple) and len(item) == 2:
                sig_id, val = item
                v_val, v_mask = val.val, val.mask
                if sig_val[sig_id] != v_val or sig_mask[sig_id] != v_mask:
                    sig_val[sig_id] = v_val
                    sig_mask[sig_id] = v_mask
                    changed.add(sig_id)

        interp.nba_queue.clear()

        # Apply memory NBAs
        from ..value import _mask_for_width

        mem_val = self.compiler.mem_val if self.compiler.mem_count > 0 else []
        mem_mask = self.compiler.mem_mask if self.compiler.mem_count > 0 else []
        mem_info = self.compiler.mem_info if self.compiler.mem_count > 0 else []
        mem_marker_sigs = self.compiler.mem_marker_sigs if self.compiler.mem_count > 0 else []
        for mem_id, addr, val in interp.nba_mem_queue:
            if mem_id < len(mem_info):
                ew, depth, base = mem_info[mem_id]
                if 0 <= addr < depth:
                    flat = base + addr
                    wmask = _mask_for_width(ew)
                    mem_val[flat] = val.val & wmask & ~val.mask
                    mem_mask[flat] = val.mask & wmask
                    # Mark memory marker signal as changed so combo processes re-fire
                    if mem_id < len(mem_marker_sigs):
                        changed.add(mem_marker_sigs[mem_id])
        interp.nba_mem_queue.clear()

        # Apply memory range NBAs (partial byte-lane writes)
        for mem_id, addr, msb, lsb, val in interp.nba_mem_range_queue:
            if mem_id < len(mem_info):
                ew, depth, base = mem_info[mem_id]
                if 0 <= addr < depth:
                    flat = base + addr
                    wmask = _mask_for_width(ew)
                    current = Value(mem_val[flat] & wmask, width=ew, mask=mem_mask[flat] & wmask)
                    updated = current.set_range(msb, lsb, val)
                    mem_val[flat] = updated.val & wmask & ~updated.mask
                    mem_mask[flat] = updated.mask & wmask
                    if mem_id < len(mem_marker_sigs):
                        changed.add(mem_marker_sigs[mem_id])
        interp.nba_mem_range_queue.clear()

        return changed

    def _collect_triggered(self, changed: set[int]) -> list[CompiledProcess]:
        """Collect processes that should re-execute due to signal changes.

        Only includes combinational processes whose sensitivity set
        intersects the actually-changed signals, and sequential processes
        whose edge conditions fired.
        """
        triggered: list[CompiledProcess] = []

        # Combinational: use inverted index for O(|changed|) lookup
        seen: set[int] = set()
        sig_to_combo = self._sig_to_combo
        for sid in changed:
            procs = sig_to_combo.get(sid)
            if procs is not None:
                for p in procs:
                    pid = id(p)
                    if pid not in seen:
                        seen.add(pid)
                        triggered.append(p)

        # Check sequential processes for edge triggers
        for proc in self._seq_procs:
            pid = id(proc)
            if pid not in self._triggered_seq and self._edge_fired(proc):
                self._triggered_seq.add(pid)
                triggered.append(proc)

        return triggered

    def _edge_fired(self, proc: CompiledProcess) -> bool:
        """Check if any edge condition in a sequential process fired."""
        for sig_id, edge_type in proc.edge_signals.items():
            old_val = self._prev_sig_val[sig_id]
            old_mask = self._prev_sig_mask[sig_id]

            if self._cy_ctx is not None:
                new_val, new_mask = self._cy_ctx.read_signal(sig_id)
            else:
                new_val = self.compiler.sig_val[sig_id]
                new_mask = self.compiler.sig_mask[sig_id]

            old_bit = old_val & 1
            old_x = (old_mask & 1) != 0
            new_bit = new_val & 1
            new_x = (new_mask & 1) != 0

            if edge_type == "posedge":
                if not new_x and new_bit == 1 and (old_x or old_bit == 0):
                    return True
            elif edge_type == "negedge":
                if not new_x and new_bit == 0 and (old_x or old_bit == 1):
                    return True

        return False

    # ── Signal access (Scheduler-compatible API) ─────────────────

    def drive_signal(self, name: str, value: Value | int) -> None:
        """Drive a signal from outside (testbench use)."""
        sid = self.compiler.signal_map.get(name)
        if sid is not None:
            if isinstance(value, int):
                value = Value(value, width=self.compiler.sig_width[sid])
            if not self._pending_drives:
                # First drive in this batch — snapshot pre-drive state for edge detection in settle().
                if self._cy_ctx is not None and self._cy_ctx._procs_setup:
                    self._cy_ctx.take_snapshot()
                    self._cy_ctx.reset_seq_fired()
                else:
                    self._prev_sig_val = list(self.compiler.sig_val)
                    self._prev_sig_mask = list(self.compiler.sig_mask)
                self._triggered_seq = set()
            if self._cy_ctx is not None:
                self._cy_ctx.write_signal(sid, value.val, value.mask)
            else:
                self.compiler.sig_val[sid] = value.val
                self.compiler.sig_mask[sid] = value.mask
            self._pending_drives.add(sid)
            if self.interpreter is not None:
                self.interpreter.dirty.add(sid)
            return
        if "[" in name and name.endswith("]"):
            from ..value import _mask_for_width

            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self.compiler.mem_map.get(mem_name)
            if mid is None:
                return
            ew, depth, base = self.compiler.mem_info[mid]
            idx = int(name[bracket + 1 : -1])
            if not 0 <= idx < depth:
                return
            if isinstance(value, int):
                value = Value(value, width=ew)
            flat = base + idx
            wmask = _mask_for_width(ew)
            self.compiler.mem_val[flat] = value.val & wmask & ~value.mask
            self.compiler.mem_mask[flat] = value.mask & wmask
            if self.interpreter is not None and mid < len(self.compiler.mem_marker_sigs):
                self.interpreter.dirty.add(self.compiler.mem_marker_sigs[mid])
            if self._cy_ctx is not None and self.compiler.mem_count > 0:
                self._cy_ctx.sync_mem_from_lists(self.compiler.mem_val, self.compiler.mem_mask)

    def settle(self) -> None:
        """Propagate pending external drives through combinational logic at the current time."""
        if not self._pending_drives:
            return
        changed = set(self._pending_drives)
        self._pending_drives.clear()

        if self._cy_ctx is not None and self._cy_ctx._procs_setup:
            try:
                self._cy_ctx.run_delta_loop(list(changed), self.delta_limit)
            except _CyStop:
                self._drain_cy_display()
                return
            self._drain_cy_display()
        elif changed:
            if self.interpreter:
                self.interpreter.dirty.clear()
            self._propagate_continuous(changed)
            if self.interpreter:
                changed.update(self.interpreter.dirty)

            delta = 0
            while True:
                delta += 1
                if delta > self.delta_limit:
                    raise RuntimeError(f"Delta cycle limit ({self.delta_limit}) exceeded during settle()")
                triggered = self._collect_triggered(changed)
                if not triggered:
                    break
                self._run_process_list(triggered)
                nba_dirty = self._apply_nbas()
                changed = set(self.interpreter.dirty) if self.interpreter else set()
                changed.update(nba_dirty)
                if changed:
                    if self.interpreter:
                        self.interpreter.dirty.clear()
                    self._propagate_continuous(changed)
                    if self.interpreter:
                        changed.update(self.interpreter.dirty)

    def read_signal(self, name: str) -> Value:
        """Read a signal value.  Supports ``"MEM[idx]"`` syntax."""
        sid = self.compiler.signal_map.get(name)
        if sid is not None:
            if self._cy_ctx is not None:
                v, m = self._cy_ctx.read_signal(sid)
                return Value(v, width=self.compiler.sig_width[sid], mask=m)
            return Value(
                self.compiler.sig_val[sid],
                width=self.compiler.sig_width[sid],
                mask=self.compiler.sig_mask[sid],
            )
        # Try memory array element: "MEM[idx]"
        if "[" in name:
            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self.compiler.mem_map.get(mem_name)
            if mid is not None and name.endswith("]"):
                ew, depth, base = self.compiler.mem_info[mid]
                idx = int(name[bracket + 1 : -1])
                if 0 <= idx < depth:
                    if self._cy_ctx is not None and self.compiler.mem_count > 0:
                        v, m = self._cy_ctx.read_mem(mid, idx)
                        return Value(v, width=ew, mask=m)
                    return Value(self.compiler.mem_val[base + idx], width=ew, mask=self.compiler.mem_mask[base + idx])
        if "." in name and self._ref_ctx is not None and self._ref_evaluator is not None:
            self._sync_ref_ctx()
            return self._ref_evaluator.eval(_identifier_from_name(name), self._ref_ctx)
        return Value.x(1)

    def signal_names(self) -> set[str]:
        """Return the set of all signal names in the simulation."""
        names = set(self.compiler.signal_map.keys())
        # Include memory array elements as individual signals
        for mem_name, mid in self.compiler.mem_map.items():
            _ew, depth, _base = self.compiler.mem_info[mid]
            for idx in range(depth):
                names.add(f"{mem_name}[{idx}]")
        return names

    def schedule_at(self, time: int, proc: object) -> None:
        """Schedule a process to run at a specific time (clock events)."""
        self._schedule_event(time, proc)


# ── EvalContext wrapper ──────────────────────────────────────────────


class _VMEvalContext:
    """Wraps VM signal storage to present an EvalContext-like interface.

    This is needed because the testbench code (SignalHandle, Clock, etc.)
    accesses signals through the scheduler's ctx attribute.
    """

    __slots__ = ("_compiler", "_cy_ctx", "_signals")

    def __init__(self, compiler: Compiler, cy_ctx=None) -> None:
        self._compiler = compiler
        self._cy_ctx = cy_ctx
        # Provide a dict-like _signals for compatibility with testbench code
        self._signals = _VMSignalDict(compiler, cy_ctx)

    def read_signal(self, name: str) -> Value:
        """Read a signal by name.  Supports ``"MEM[idx]"`` syntax."""
        sid = self._compiler.signal_map.get(name)
        if sid is not None:
            if self._cy_ctx is not None:
                v, m = self._cy_ctx.read_signal(sid)
                return Value(v, width=self._compiler.sig_width[sid], mask=m)
            return Value(
                self._compiler.sig_val[sid],
                width=self._compiler.sig_width[sid],
                mask=self._compiler.sig_mask[sid],
            )
        # Try memory array element: "MEM[idx]"
        if "[" in name:
            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self._compiler.mem_map.get(mem_name)
            if mid is not None and name.endswith("]"):
                ew, depth, base = self._compiler.mem_info[mid]
                idx = int(name[bracket + 1 : -1])
                if 0 <= idx < depth:
                    if self._cy_ctx is not None and self._compiler.mem_count > 0:
                        v, m = self._cy_ctx.read_mem(mid, idx)
                        return Value(v, width=ew, mask=m)
                    return Value(self._compiler.mem_val[base + idx], width=ew, mask=self._compiler.mem_mask[base + idx])
        return Value.x(1)

    def write_signal(self, name: str, value: Value) -> None:
        """Write a signal by name."""
        sid = self._compiler.signal_map.get(name)
        if sid is not None:
            if self._cy_ctx is not None:
                self._cy_ctx.write_signal(sid, value.val, value.mask)
            else:
                self._compiler.sig_val[sid] = value.val
                self._compiler.sig_mask[sid] = value.mask
            return
        if "[" in name and name.endswith("]"):
            from ..value import _mask_for_width

            bracket = name.index("[")
            mem_name = name[:bracket]
            mid = self._compiler.mem_map.get(mem_name)
            if mid is None:
                return
            ew, depth, base = self._compiler.mem_info[mid]
            idx = int(name[bracket + 1 : -1])
            if not 0 <= idx < depth:
                return
            flat = base + idx
            wmask = _mask_for_width(ew)
            self._compiler.mem_val[flat] = value.val & wmask & ~value.mask
            self._compiler.mem_mask[flat] = value.mask & wmask
            if self._cy_ctx is not None and self._compiler.mem_count > 0:
                self._cy_ctx.sync_mem_from_lists(self._compiler.mem_val, self._compiler.mem_mask)


class _VMSignalDict(SignalDictBase):
    """Dict-like wrapper over VM signal arrays for testbench compatibility.

    The testbench code accesses ``ctx._signals`` as a dict. This wrapper
    translates dict operations to VM array operations.
    """

    __slots__ = ("_compiler", "_cy_ctx")

    def __init__(self, compiler: Compiler, cy_ctx=None) -> None:
        self._compiler = compiler
        self._cy_ctx = cy_ctx

    def _sig_map(self) -> dict[str, int]:
        return self._compiler.signal_map

    def _read_sid(self, sid: int) -> tuple[int, int, int]:
        if self._cy_ctx is not None:
            v, m = self._cy_ctx.read_signal(sid)
            return (v, m, self._compiler.sig_width[sid])
        return (self._compiler.sig_val[sid], self._compiler.sig_mask[sid], self._compiler.sig_width[sid])

    def _write_sid(self, sid: int, val: int, mask: int) -> None:
        if self._cy_ctx is not None:
            self._cy_ctx.write_signal(sid, val, mask)
        else:
            self._compiler.sig_val[sid] = val
            self._compiler.sig_mask[sid] = mask

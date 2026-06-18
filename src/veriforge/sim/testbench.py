"""Testbench API for the Verilog simulation engine.

Provides:
  - SignalHandle: read/write proxy to a simulation signal
  - Clock: built-in clock generator
  - Simulator: top-level entry point (elaborate, fork, run, settle)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.design import Design, Module

from .elaborate import (
    check_signed_declarations,
    flatten_module,
    materialize_process_locals,
    resolve_sv_imports,
    _resolve_typedef_widths,
)
from .scheduler import Scheduler
from .value import Value

if TYPE_CHECKING:
    from collections.abc import Callable

    from .compiled.compiled_scheduler import CompiledScheduler
    from .vm.vm_scheduler import VMScheduler


# ── Signal Handle ────────────────────────────────────────────────────


class SignalHandle:  # cm:2e7d3b
    """Runtime handle to a signal in the simulation.

    Provides read/write access to the simulation signal state.
    """

    __slots__ = ("_name", "_sched", "_width")

    def __init__(self, name: str, sched: Scheduler | VMScheduler | CompiledScheduler, width: int = 1) -> None:
        self._name = name
        self._sched = sched
        self._width = width

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self) -> Value:
        """Read current signal value."""
        return self._sched.read_signal(self._name)

    @value.setter
    def value(self, new_val: Value | int | str) -> None:
        """Drive signal from testbench."""
        if isinstance(new_val, int):
            new_val = Value(new_val, width=self._width)
        elif isinstance(new_val, str):
            new_val = Value.from_verilog(new_val)
        self._sched.drive_signal(self._name, new_val)

    @property
    def width(self) -> int:
        return self._width

    def __repr__(self) -> str:
        return f"Signal({self._name} = {self.value})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SignalHandle):
            return self._name == other._name
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._name)


# ── Clock ────────────────────────────────────────────────────────────


class Clock:  # cm:4d1b6e
    """Built-in clock generator utility.

    Generates a periodic clock signal by scheduling drive events
    on the given signal.

    Args:
        signal:  SignalHandle for the clock.
        period:  Full clock period in time units.
        duty:    Duty cycle (fraction of period that clock is high).
    """

    __slots__ = ("high_time", "low_time", "signal")

    def __init__(self, signal: SignalHandle, *, period: int, duty: float = 0.5) -> None:
        self.signal = signal
        self.high_time = max(1, int(period * duty))
        self.low_time = max(1, period - self.high_time)

    def __repr__(self) -> str:
        return f"Clock({self.signal.name}, period={self.high_time + self.low_time})"


# ── Simulator ────────────────────────────────────────────────────────


class Simulator:  # cm:a5c8f4
    """Top-level simulation entry point.

    Wraps the scheduler and provides a testbench-friendly API:
      - signal(name) → SignalHandle
      - fork(Clock(...)) to start a clock
      - run(test_fn) to run a test function

    Args:
        module:  The elaborated Verilog module to simulate.
        engine:  Simulation engine to use.
                 ``"reference"`` (default) — tree-walking evaluator/executor.
                 ``"vm"`` — bytecode VM, pure-Python interpreter.
                 ``"vm-fast"`` — bytecode VM, Cython interpreter (falls back to pure-Python if unavailable).
    """

    __slots__ = ("_clocks", "_engine", "_module", "_sched", "_signal_cache")

    def __init__(
        self,
        module: Module,
        *,
        engine: str = "reference",
        design: Design | None = None,
        delta_limit: int = 10_000,
    ) -> None:
        self._engine = engine
        self._signal_cache: dict[str, SignalHandle] = {}
        self._clocks: list[Clock] = []

        # Flatten hierarchy if instances or generate blocks are present
        if module.generate_blocks or module.instances:
            # Resolve SV package imports before flattening so that package
            # constants are available during generate elaboration.
            resolve_sv_imports(module, design)
            _resolve_typedef_widths(module)
            module = flatten_module(module, design=design)
        self._module = module

        # Resolve SV package imports on the flattened module (picks up any
        # remaining imports from inlined submodules).
        resolve_sv_imports(module, design)
        _resolve_typedef_widths(module)
        materialize_process_locals(module)
        check_signed_declarations(module)

        if engine in ("vm", "vm-fast"):
            from .vm.vm_scheduler import VMScheduler as _VMSched

            self._sched: Scheduler | VMScheduler | CompiledScheduler = _VMSched(
                force_python=(engine == "vm"),
                delta_limit=delta_limit,
            )
        elif engine == "compiled":
            from .compiled.compiled_scheduler import CompiledScheduler as _CSched

            self._sched = _CSched(delta_limit=delta_limit)
        elif engine == "reference":
            self._sched = Scheduler(delta_limit=delta_limit)
        else:
            raise ValueError(f"Unknown engine: {engine!r}. Use 'reference', 'vm', 'vm-fast', or 'compiled'.")

        # Elaborate
        if engine == "compiled" and design is not None:
            self._sched.elaborate(module, source_files=design.source_files)
        else:
            self._sched.elaborate(module)

    @property
    def time(self) -> int:
        """Current simulation time."""
        return self._sched.time

    @property
    def display_output(self) -> list[str]:
        """Collected $display output."""
        # Flush any pending $write buffer from the executor
        sched = self._sched
        executor = getattr(sched, "executor", None) or getattr(sched, "_ref_executor", None)
        if executor is not None and executor._write_buffer:
            sched.display_output.append(executor._write_buffer)
            executor._write_buffer = ""
        # Flush compiled engine write buffer (CompiledScheduler)
        wb = getattr(sched, "_write_buffer", None)
        if wb:
            sched.display_output.append(wb)
            sched._write_buffer = ""
        return sched.display_output

    def signal(self, name: str) -> SignalHandle:
        """Get a handle to a signal by name.

        Raises:
            KeyError: If the signal name does not exist.
        """
        if name in self._signal_cache:
            return self._signal_cache[name]
        all_names = self._all_signal_names()
        if name not in all_names and not self._can_resolve_struct_signal(name):
            import difflib

            close = difflib.get_close_matches(name, all_names, n=5, cutoff=0.5)
            msg = f"Signal '{name}' not found."
            if close:
                msg += f" Did you mean: {', '.join(close)}?"
            raise KeyError(msg)
        sig = self._sched.read_signal(name)
        handle = SignalHandle(name, self._sched, width=sig.width)
        self._signal_cache[name] = handle
        return handle

    def _runtime_contexts(self) -> list[object]:
        return [
            ctx
            for ctx in (getattr(self._sched, "ctx", None), getattr(self._sched, "_ref_ctx", None))
            if ctx is not None
        ]

    def _runtime_memory_names(self) -> set[str]:
        memory_names: set[str] = set()
        for ctx in self._runtime_contexts():
            ctx_memory_names = getattr(ctx, "_memory_names", None)
            if ctx_memory_names is not None:
                memory_names.update(ctx_memory_names)
            ctx_memories = getattr(ctx, "_memories", None)
            if ctx_memories is not None:
                memory_names.update(ctx_memories.keys())

        compiler = getattr(self._sched, "compiler", None)
        memory_names.update(getattr(compiler, "mem_map", {}).keys())
        memory_names.update(getattr(self._sched, "_mem_map", {}).keys())
        codegen = getattr(self._sched, "_codegen", None)
        if codegen is not None:
            memory_names.update(getattr(codegen, "mem_map", {}).keys())
        return memory_names

    def _runtime_memory_element_names(self) -> set[str]:
        element_names: set[str] = set()
        for ctx in self._runtime_contexts():
            ctx_memories = getattr(ctx, "_memories", None)
            if ctx_memories is None:
                continue
            for memory_name, (data, _elem_width) in ctx_memories.items():
                for index in range(len(data)):
                    element_names.add(f"{memory_name}[{index}]")

        compiler = getattr(self._sched, "compiler", None)
        compiler_mem_map = getattr(compiler, "mem_map", {})
        compiler_mem_info = getattr(compiler, "mem_info", [])
        for memory_name, memory_id in compiler_mem_map.items():
            if memory_id < len(compiler_mem_info):
                depth = compiler_mem_info[memory_id][1]
                for index in range(depth):
                    element_names.add(f"{memory_name}[{index}]")

        sched_mem_map = getattr(self._sched, "_mem_map", {})
        codegen = getattr(self._sched, "_codegen", None)
        codegen_mem_info = getattr(codegen, "mem_info", []) if codegen is not None else []
        for memory_name, memory_id in sched_mem_map.items():
            if memory_id < len(codegen_mem_info):
                depth = codegen_mem_info[memory_id][1]
                for index in range(depth):
                    element_names.add(f"{memory_name}[{index}]")

        return element_names

    def _struct_field_names(self, base_names: set[str]) -> set[str]:
        from .elaborate import normalize_struct_access_name

        field_names: set[str] = set()
        seen_layouts: set[tuple[str, str]] = set()

        def add_fields(visible_name: str, layout_name: str, struct_types: dict[str, object]) -> None:
            key = (visible_name, layout_name)
            if key in seen_layouts:
                return
            seen_layouts.add(key)
            layout = struct_types.get(layout_name)
            if layout is None:
                return
            for field_name in layout.fields:
                visible_field = f"{visible_name}.{field_name}"
                field_names.add(visible_field)
                nested_layout_name = f"{layout_name}.{field_name}"
                if nested_layout_name in struct_types:
                    add_fields(visible_field, nested_layout_name, struct_types)

        for ctx in self._runtime_contexts():
            struct_types = getattr(ctx, "_struct_types", None)
            if not struct_types:
                continue
            for base_name in base_names:
                layout_name = base_name if base_name in struct_types else normalize_struct_access_name(base_name)
                if layout_name in struct_types:
                    add_fields(base_name, layout_name, struct_types)

        return field_names

    def _all_signal_names(self) -> set[str]:
        names = set(self._sched.signal_names())
        names.update(self._runtime_memory_element_names())
        names.update(self._struct_field_names(names))
        return names

    def _can_resolve_struct_signal(self, name: str) -> bool:
        from .elaborate import resolve_struct_storage_access

        memory_names = self._runtime_memory_names()
        for ctx in self._runtime_contexts():
            struct_types = getattr(ctx, "_struct_types", None)
            signals = getattr(ctx, "_signals", None)
            if struct_types is None or signals is None:
                continue
            if resolve_struct_storage_access(name, struct_types, signals, memory_names) is not None:
                return True
        return False

    def signals(self, pattern: str | None = None) -> list[str]:
        """Return a sorted list of all signal names in the simulation.

        Args:
            pattern: Optional prefix filter. Only signals whose name starts
                     with *pattern* are returned.
        """
        names = self._all_signal_names()
        if pattern is not None:
            names = {n for n in names if n.startswith(pattern)}
        return sorted(names)

    def hierarchy(self) -> dict[str, str]:
        """Return a mapping of instance paths to module names.

        Example::

            {"u1": "inverter", "u_mid": "middle", "u_mid.u_leaf": "leaf"}
        """
        return dict(self._module.hierarchy_map)

    def fork(self, clock: Clock) -> None:
        """Start a clock generator as a background process."""
        self._clocks.append(clock)

    def run(
        self,
        test_fn: Callable[[Simulator], None] | None = None,
        *,
        max_time: int = 1_000_000,
    ) -> None:
        """Run the simulation up to *max_time*.

        If *test_fn* is provided, it is called synchronously with the
        Simulator instance before the event loop runs, allowing
        imperative-style test setup (drive signals, assert results).
        """
        # Run clocks by scheduling their events
        for clock in self._clocks:
            self._schedule_clock_events(clock, max_time)

        # Run test setup
        if test_fn is not None:
            test_fn(self)

        # Run the event loop
        self._sched.run(max_time=max_time)

    def run_step(self, *, max_time: int = 1_000_000) -> bool:
        """Run one time step of the simulation.

        Returns True if simulation can continue (events remain),
        False if finished or stopped.
        """
        if self._engine == "reference":
            return self._sched.run_step()
        if self._engine in ("vm", "vm-fast"):
            from .vm.vm_scheduler import VMScheduler as _VMSched

            sched = self._sched
            assert isinstance(sched, _VMSched)  # noqa: S101
            return sched.run_step()
        if self._engine == "compiled":
            from .compiled.compiled_scheduler import CompiledScheduler as _CSched

            sched = self._sched
            assert isinstance(sched, _CSched)  # noqa: S101
            return sched.run_step()
        raise NotImplementedError(f"run_step() not supported for engine {self._engine!r}")

    def drive(self, name: str, value: Value | int) -> None:
        """Drive a signal by name (convenience method)."""
        self._sched.drive_signal(name, value)

    def settle(self) -> None:
        """Propagate pending external drives through combinational logic.

        Runs continuous-assign and combinational-always fixpoint for all
        signals written by ``drive()`` since the last ``settle()`` or
        ``run()`` call, without advancing simulation time or consuming
        events.  Use this to observe combinational outputs immediately
        after driving inputs without needing a fake clock edge.
        """
        self._sched.settle()

    def read(self, name: str) -> Value:
        """Read a signal by name (convenience method)."""
        return self._sched.read_signal(name)

    def batch_run(
        self,
        cycles: int,
        clock_name: str,
        clock_period: int = 10,
        events: list[tuple[int, str, int]] | None = None,
    ) -> int:
        """Run *cycles* full clock cycles in batch mode (compiled engine only).

        Args:
            cycles: Number of full clock cycles to execute.
            clock_name: Name of the clock signal to toggle.
            clock_period: Period of one full clock cycle in time units.
            events: Optional list of ``(cycle, signal_name, value)`` tuples.
                Applied before the posedge of the given cycle.  Must be
                sorted by cycle number.

        Returns:
            Number of cycles actually completed.

        Raises:
            NotImplementedError: If the engine is not ``"compiled"``.
        """
        if self._engine != "compiled":
            raise NotImplementedError(f"batch_run() requires engine='compiled', got {self._engine!r}")
        from .compiled.compiled_scheduler import CompiledScheduler as _CSched

        sched = self._sched
        assert isinstance(sched, _CSched)  # noqa: S101
        return sched.batch_run(cycles, clock_name, clock_period, events=events)

    def _schedule_clock_events(self, clock: Clock, max_time: int) -> None:
        """Pre-schedule clock toggle events up to max_time."""
        t = 0
        sig_name = clock.signal.name
        w = clock.signal.width

        if self._engine in ("vm", "vm-fast", "compiled"):
            sched = self._sched
            # Initialize signal to 0
            sched.drive_signal(sig_name, Value(0, width=w))
            while t <= max_time:
                sched.schedule_at(t, ("clock_toggle", sig_name, Value(1, width=w)))
                t += clock.high_time
                sched.schedule_at(t, ("clock_toggle", sig_name, Value(0, width=w)))
                t += clock.low_time
        else:
            # Reference engine: schedule via event_queue
            self._sched.drive_signal(sig_name, Value(1, width=w))
            self._sched.ctx.write_signal(sig_name, Value(0, width=w))
            while t <= max_time:
                self._sched.event_queue.schedule(t, _ClockToggle(sig_name, Value(1, width=w)))
                t += clock.high_time
                self._sched.event_queue.schedule(t, _ClockToggle(sig_name, Value(0, width=w)))
                t += clock.low_time

            # Reset initial drive
            self._sched.ctx.write_signal(sig_name, Value(0, width=w))


class _ClockToggle:
    """Lightweight process-like object for clock toggle events."""

    __slots__ = ("sig_name", "state", "value")

    def __init__(self, sig_name: str, value: Value) -> None:
        self.sig_name = sig_name
        self.value = value
        self.state = None  # duck-type Process

    @property
    def id(self) -> int:
        return -1  # special ID for clock processes

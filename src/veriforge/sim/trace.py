"""Reusable simulation tracing helpers.

One VCD implementation serves both ``attach_vcd`` and ``$dumpvars``:
``VcdTraceSession`` writes the header and initial values, then after every
time step asks a *poller* which traced signals changed and writes only those.

On the compiled engine the poller is ``CompiledSim.trace_poll()``: change
detection runs in C over just the traced items, so a time step costs time in
proportion to the traced signals that changed, and untraced signals cost
nothing. Other engines use a Python poller that compares raw ``(val, mask)``
pairs (no per-signal string formatting).

See notes/plans/vcd_enhancement.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

from .elaborate import is_synthesized_local_name
from .severity import DisplayLog
from .value import Value
from .vcd import VcdWriter, _VcdSignal

if TYPE_CHECKING:
    import io
    from collections.abc import Callable, Iterable

    from .testbench import Simulator

_MEM_ELEMENT_RE = re.compile(r"^(.*)\[(\d+)\]$")


def _split_hierarchy(name: str) -> tuple[str, str]:
    """Split a hierarchical signal name into (scope, leaf_name)."""
    if "." in name:
        parts = name.rsplit(".", 1)
        return parts[0], parts[1]
    return "top", name


def _glob(pattern: str) -> str:
    """fnmatch pattern with ``[`` escaped, so generate indices in names
    (``gen_lane[3].q``) match literally."""
    return pattern.replace("[", "[[]")


def select_signals(  # noqa: PLR0913
    names: Iterable[str],
    *,
    scopes: Iterable[str] | None = None,
    depth: int = 0,
    signals: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    memories: bool | Iterable[str] | None = None,
) -> list[str]:
    """Pick the signals to trace from *names* (flattened hierarchical names,
    e.g. ``gen_lane[2].u_lane.q``; memory elements as ``mem[5]``).

    - *scopes*: instance paths; a signal is kept if it lives in one of them
      or below. *depth* limits how far below (0 = any depth, 1 = the scope's
      own signals only), as in ``$dumpvars``.
    - *signals*: glob patterns a kept signal must match (any of them).
    - *exclude*: glob patterns that drop a signal.
    - *memories*: memory elements are kept if True, dropped if False, or kept
      when the memory's name matches one of the given glob patterns. None
      means True when nothing else is selected (everything) and False when
      *scopes* or *signals* narrow the selection.
    Globs are fnmatch patterns with ``[`` literal.
    """
    scope_list = [s.strip(".") for s in scopes] if scopes is not None else None
    sig_globs = [_glob(p) for p in signals] if signals is not None else None
    excl_globs = [_glob(p) for p in exclude] if exclude is not None else []
    if memories is None:
        memories = scope_list is None and sig_globs is None
    mem_globs = None if isinstance(memories, bool) else [_glob(p) for p in memories]

    def in_scope(name: str) -> bool:
        if scope_list is None:
            return True
        for scope in scope_list:
            if scope == "":
                rest = name
            elif name.startswith(scope + "."):
                rest = name[len(scope) + 1 :]
            else:
                continue
            if depth <= 0 or rest.count(".") < depth:
                return True
        return False

    picked = []
    for name in names:
        if is_synthesized_local_name(name):
            continue
        m = _MEM_ELEMENT_RE.match(name)
        if m is not None:
            if mem_globs is not None:
                if not any(fnmatchcase(m.group(1), g) for g in mem_globs):
                    continue
            elif not memories:
                continue
        if not in_scope(name):
            continue
        if sig_globs is not None and not any(fnmatchcase(name, g) for g in sig_globs):
            continue
        if any(fnmatchcase(name, g) for g in excl_globs):
            continue
        picked.append(name)
    return sorted(picked)


class _TimeStepCallbackChain:
    __slots__ = ("callbacks",)

    def __init__(self, callbacks) -> None:
        self.callbacks = list(callbacks)

    def __call__(self, sched) -> None:
        for callback in tuple(self.callbacks):
            callback(sched)


class TimeStepCallbackHandle:
    """Disposable registration for scheduler time-step callbacks."""

    __slots__ = ("_callback", "_closed", "_sched")

    def __init__(self, sched, callback) -> None:
        self._sched = sched
        self._callback = callback
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        current = self._sched._on_time_step
        if current is self._callback:
            self._sched._on_time_step = None
        elif isinstance(current, _TimeStepCallbackChain):
            current.callbacks = [callback for callback in current.callbacks if callback is not self._callback]
            if not current.callbacks:
                self._sched._on_time_step = None
            elif len(current.callbacks) == 1:
                self._sched._on_time_step = current.callbacks[0]
        self._closed = True

    def __enter__(self) -> TimeStepCallbackHandle:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def register_time_step_callback(sched, callback) -> TimeStepCallbackHandle:
    """Register a scheduler callback without clobbering existing listeners."""

    current = sched._on_time_step
    if current is None:
        sched._on_time_step = callback
    elif isinstance(current, _TimeStepCallbackChain):
        current.callbacks.append(callback)
    else:
        sched._on_time_step = _TimeStepCallbackChain([current, callback])
    return TimeStepCallbackHandle(sched, callback)


class _PythonPoller:
    """Change detection by reading each traced signal (reference/vm/vm-fast,
    and anything the compiled engine can't map to its arrays)."""

    def __init__(self, sched, names: list[str], indices: list[int]) -> None:
        self._read = sched.read_signal
        self._names = names
        self._indices = indices
        self._last = []
        for name in names:
            v = self._read(name)
            self._last.append((v.val, v.mask))

    def poll(self) -> list[tuple[int, int, int]]:
        out = []
        last = self._last
        for k, name in enumerate(self._names):
            v = self._read(name)
            cur = (v.val, v.mask)
            if cur != last[k]:
                last[k] = cur
                out.append((self._indices[k], v.val, v.mask))
        return out


class TraceHub:
    """Per-scheduler change detection shared by every trace session.

    Holds the union of all sessions' signals and polls it once per time
    step: on the compiled engine through ``CompiledSim.trace_poll()`` (and,
    inside ``batch_run``, the C-side change recorder -- see
    ``CompiledScheduler.batch_run``), elsewhere with ``_PythonPoller``.
    Each change goes to the sessions tracing that signal.
    """

    def __init__(self, sched) -> None:
        self._sched = sched
        self._names: list[str] = []
        self._index: dict[str, int] = {}
        self._subs: list[list[tuple[_TraceSink, int]]] = []  # per hub index: (sink, sink-local index)
        self._sinks: list[_TraceSink] = []
        self._c_index: list[int] = []  # C trace slot -> hub index
        # The only sink, when it traces exactly the hub's names in hub
        # order (the common case): changes go to it unregrouped.
        self._direct: _TraceSink | None = None
        self._py: _PythonPoller | None = None
        self._sim = None
        self._handle = register_time_step_callback(sched, self._on_time_step)

    @property
    def compiled_sim(self):
        """The ``CompiledSim`` doing C-side detection, if any (batch_run
        recording uses it)."""
        return self._sim

    def subscribe(self, sink: _TraceSink, names: list[str]) -> None:
        """Start delivering changes of *names* to *sink*; ``names[k]``'s
        changes arrive as sink-local index ``k``."""
        # Changes since the last poll belong to the existing subscribers --
        # deliver them before the trace set is rebuilt (which re-reads the
        # current values as the new baseline).
        self.dispatch(self._sched.time, self._poll())
        for k, name in enumerate(names):
            i = self._index.get(name)
            if i is None:
                i = len(self._names)
                self._index[name] = i
                self._names.append(name)
                self._subs.append([])
            self._subs[i].append((sink, k))
        self._sinks.append(sink)
        self._update_direct()
        self._rebuild()

    def unsubscribe(self, sink: _TraceSink) -> None:
        self._sinks = [s for s in self._sinks if s is not sink]
        for subs in self._subs:
            subs[:] = [(s, k) for s, k in subs if s is not sink]
        self._update_direct()
        if not self._sinks:
            self._handle.close()
            if self._sim is not None:
                self._sim.trace_set([])
            if getattr(self._sched, "_trace_hub", None) is self:
                self._sched._trace_hub = None

    def _update_direct(self) -> None:
        self._direct = None
        if len(self._sinks) == 1:
            sink = self._sinks[0]
            if all(subs == [(sink, i)] for i, subs in enumerate(self._subs)):
                self._direct = sink

    def _rebuild(self) -> None:
        sched = self._sched
        sim = getattr(sched, "_sim", None)
        signal_map = getattr(sched, "_signal_map", None)
        codegen = getattr(sched, "_codegen", None)
        self._sim = None
        self._c_index = []
        if sim is None or signal_map is None or not hasattr(sim, "trace_set"):
            self._py = _PythonPoller(sched, self._names, list(range(len(self._names))))
            return
        mem_map = codegen.mem_map if codegen is not None else {}
        specs: list[tuple[int, ...]] = []
        py_names: list[str] = []
        py_index: list[int] = []
        for i, name in enumerate(self._names):
            sid = signal_map.get(name)
            spec: tuple[int, ...] | None = (0, sid) if sid is not None else None
            if spec is None:
                m = _MEM_ELEMENT_RE.match(name)
                mid = mem_map.get(m.group(1)) if m is not None else None
                if mid is not None and m is not None:
                    spec = (1, mid, int(m.group(2)))
            if spec is None:
                py_names.append(name)
                py_index.append(i)
            else:
                specs.append(spec)
                self._c_index.append(i)
        sim.trace_set(specs)
        self._sim = sim
        self._py = _PythonPoller(sched, py_names, py_index) if py_names else None

    def _poll(self) -> list[tuple[int, int, int]]:
        if not self._names:
            return []
        out: list[tuple[int, int, int]] = []
        if self._sim is not None:
            c_index = self._c_index
            out = [(c_index[i], v, m) for i, v, m in self._sim.trace_poll()]
        if self._py is not None:
            out.extend(self._py.poll())
        return out

    def dispatch(self, time: int, changes: list[tuple[int, int, int]]) -> None:
        """Deliver hub-index ``changes`` at *time* to the subscribed sinks."""
        if not changes:
            return
        if self._direct is not None:
            self._direct.on_changes(time, changes)
            return
        per_sink: dict[int, tuple[_TraceSink, list[tuple[int, int, int]]]] = {}
        subs = self._subs
        for i, v, m in changes:
            for sink, k in subs[i]:
                entry = per_sink.get(id(sink))
                if entry is None:
                    entry = per_sink[id(sink)] = (sink, [])
                entry[1].append((k, v, m))
        for sink, sink_changes in per_sink.values():
            sink.on_changes(time, sink_changes)

    def dispatch_recorded(self, records: list[tuple[int, int, int, int]]) -> None:
        """Deliver C-recorded ``(time, c_slot, val, mask)`` changes (from
        ``batch_run``), grouped by time."""
        if not records:
            return
        c_index = self._c_index
        batch: list[tuple[int, int, int]] = []
        cur = records[0][0]
        for t, slot, v, m in records:
            if t != cur:
                self.dispatch(cur, batch)
                batch = []
                cur = t
            batch.append((c_index[slot], v, m))
        self.dispatch(cur, batch)

    def _on_time_step(self, sched) -> None:
        self.dispatch(sched.time, self._poll())


def trace_hub(sched) -> TraceHub:
    """The scheduler's trace hub (created on first use)."""
    hub = getattr(sched, "_trace_hub", None)
    if hub is None:
        hub = TraceHub(sched)
        sched._trace_hub = hub
    return hub


class _TraceSink:
    """Receives a hub's changes (sink-local indices)."""

    def on_changes(self, time: int, changes: list[tuple[int, int, int]]) -> None:
        raise NotImplementedError


class VcdTraceSession(_TraceSink):
    """Records signal changes on a scheduler to a VCD sink, one block per
    time step that has changes. Used by ``attach_vcd`` and ``$dumpvars``.

    *start*/*stop* restrict the dump to a time window (the initial values
    are written when the window opens); ``pause``/``resume`` (and
    ``$dumpoff``/``$dumpon``) suspend it; *limit* stops it once the file
    reaches that many characters (``$dumplimit``). The session keeps
    receiving changes while not writing, so it always knows current values.
    """

    def __init__(  # noqa: PLR0913
        self,
        sched,
        output: str | Path | io.TextIOBase,
        *,
        signal_names: Iterable[str],
        timescale: str = "1ns",
        root_scope: str = "top",
        start: int | None = None,
        stop: int | None = None,
        limit: int | None = None,
    ) -> None:
        self._sched = sched
        self._writer = VcdWriter(str(output) if isinstance(output, Path) else output, timescale=timescale)
        self._signal_names = sorted(n for n in signal_names if not is_synthesized_local_name(n))
        self._start = start
        self._stop = stop
        self._limit = limit
        self._paused = False
        self._limited = False
        self._closed = False

        hub = trace_hub(sched)
        # Deliver anything pending to existing sessions first, so this
        # session's initial values are this moment's values.
        hub.dispatch(sched.time, hub._poll())
        initial = {name: sched.read_signal(name) for name in self._signal_names}
        for signal_name in self._signal_names:
            scope, leaf = _split_hierarchy(signal_name)
            scope = root_scope if scope == "top" else f"{root_scope}.{scope}"
            self._writer.add_signal(signal_name, width=initial[signal_name].width, scope=scope, vcd_name=leaf)
        self._writer.write_header()
        self._cur = [(initial[name].val, initial[name].mask) for name in self._signal_names]
        self._sigs = [self._writer.signal_info(name) for name in self._signal_names]
        self._started = start is None or start <= sched.time
        if self._started:
            self._writer.write_initial(initial, time=sched.time)
        self._hub = hub
        hub.subscribe(self, self._signal_names)

    def _values(self) -> list[tuple[_VcdSignal, int, int]]:
        return [(sig, v, m) for sig, (v, m) in zip(self._sigs, self._cur, strict=True)]

    def _writing(self, time: int) -> bool:
        return self._started and not self._paused and not self._limited and (self._stop is None or time <= self._stop)

    def _check_limit(self) -> None:
        if self._limit is not None and not self._limited and self._writer.bytes_written() >= self._limit:
            self._limited = True
            self._writer.write_comment("$dumplimit reached")

    def on_changes(self, time: int, changes: list[tuple[int, int, int]]) -> None:
        cur = self._cur
        if not self._started and self._start is not None and time >= self._start:
            # The window opens: the values going in are the ones that held at
            # `start` (no step since the previous one changed anything).
            if time > self._start:
                self._writer.write_section("dumpvars", self._start, self._values())
            for k, v, m in changes:
                cur[k] = (v, m)
            if time == self._start:
                self._writer.write_section("dumpvars", time, self._values())
                self._started = True
                return
            self._started = True
        else:
            for k, v, m in changes:
                cur[k] = (v, m)
        if self._writing(time):
            sigs = self._sigs
            self._writer.write_changes(time, [(sigs[k], v, m) for k, v, m in changes])
            self._check_limit()

    def _sync(self) -> int:
        """Deliver pending changes; return the current time."""
        now = self._sched.time
        self._hub.dispatch(now, self._hub._poll())
        return now

    def pause(self) -> None:
        """Stop writing (``$dumpoff``): every signal is dumped as x."""
        now = self._sync()
        if self._writing(now):
            self._writer.write_section("dumpoff", now, None)
        self._paused = True

    def resume(self) -> None:
        """Start writing again (``$dumpon``), with every signal's current value."""
        now = self._sync()
        was_paused = self._paused
        self._paused = False
        if was_paused and self._writing(now):
            self._writer.write_section("dumpon", now, self._values())

    def dump_all(self) -> None:
        """Write every signal's current value (``$dumpall``)."""
        now = self._sync()
        if self._writing(now):
            self._writer.write_section("dumpall", now, self._values())

    @property
    def signal_names(self) -> list[str]:
        """The traced signal names."""
        return list(self._signal_names)

    def flush(self) -> None:
        if not self._closed:
            self._writer.flush()

    def close(self) -> None:
        if self._closed:
            return
        now = self._sync()
        if not self._started and self._start is not None and now >= self._start:
            self._writer.write_section("dumpvars", self._start, self._values())
        self._hub.unsubscribe(self)
        self._writer.finalize()
        self._closed = True

    def __enter__(self) -> VcdTraceSession:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def attach_vcd(  # cm:5b5a9e  # noqa: PLR0913
    sim: Simulator,
    output: str | Path | io.TextIOBase,
    *,
    timescale: str = "1ns",
    signal_names: Iterable[str] | None = None,
    scopes: Iterable[str] | None = None,
    depth: int = 0,
    signals: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    memories: bool | Iterable[str] | None = None,
    start: int | None = None,
    stop: int | None = None,
    limit: int | None = None,
) -> VcdTraceSession:
    """Attach VCD tracing to an existing simulator.

    The returned session records initial values immediately, then appends value
    changes after each completed time step until the session is closed.

    What to trace: an explicit *signal_names* list, or a selection over all
    signals -- *scopes*/*depth*, *signals*/*exclude* glob patterns, and
    *memories* (see ``select_signals``). With no selection, everything is
    traced, memory elements included.

    *start*/*stop* limit the dump to that time window; *limit* stops it at
    that many characters. The session also has ``pause()``/``resume()``/
    ``dump_all()``.
    """
    sched = sim._sched
    if signal_names is None:
        signal_names = select_signals(
            sched.signal_names(), scopes=scopes, depth=depth, signals=signals, exclude=exclude, memories=memories
        )
    root_scope: str = getattr(sim._module, "name", "top")
    return VcdTraceSession(
        sched,
        output,
        signal_names=signal_names,
        timescale=timescale,
        root_scope=root_scope,
        start=start,
        stop=stop,
        limit=limit,
    )


_EDGE_FUNCS = {
    "$rose": "rose",
    "rose": "rose",
    "$fell": "fell",
    "fell": "fell",
    "$changed": "changed",
    "changed": "changed",
}


class TriggerExpr:
    """A trigger condition: a Verilog expression over (flattened) signal
    names, e.g. ``"u_dp.err && u_dp.state == 3"`` or
    ``"$rose(gen_lane[2].u.frame_start)"``. ``$rose``/``$fell``/
    ``$changed`` (also without ``$``) compare with the previous step's value.

    It is evaluated in Python on the trace hub's change stream, only at
    steps where one of its signals changed -- the only times its value can
    change -- so it works the same on every engine and inside
    ``batch_run``, at a cost proportional to its signals' activity.
    """

    def __init__(self, text: str) -> None:
        from veriforge.model.expressions import Expression, FunctionCall, Identifier  # noqa: PLC0415
        from veriforge.transforms.tree_to_model import tree_to_design  # noqa: PLC0415
        from veriforge.verilog_parser import verilog_parser  # noqa: PLC0415

        from .evaluator import ExpressionEvaluator  # noqa: PLC0415

        tree = verilog_parser(start="source_text").build_tree(f"module __trigger; wire __t = ({text}); endmodule")
        module = tree_to_design(tree, source_file=None).modules[0]
        assigns = list(module.continuous_assigns)
        if len(assigns) != 1:
            raise ValueError(f"could not parse trigger expression {text!r}")
        self.text = text
        self.signals: list[str] = []
        self._edges: list[tuple[str, str, str]] = []  # (synthetic name, kind, signal)

        def name_of(ident: Identifier) -> str:
            return ".".join([*ident.hierarchy, ident.name]) if ident.hierarchy else ident.name

        def rewrite(node: Expression) -> Expression:
            if isinstance(node, FunctionCall) and node.name in _EDGE_FUNCS:
                if len(node.arguments) != 1 or not isinstance(node.arguments[0], Identifier):
                    raise ValueError(f"{node.name}() takes one signal name, in trigger {text!r}")
                sig = name_of(node.arguments[0])
                if sig not in self.signals:
                    self.signals.append(sig)
                synth = f"__trig{len(self._edges)}"
                self._edges.append((synth, _EDGE_FUNCS[node.name], sig))
                return Identifier(synth)
            if isinstance(node, Identifier):
                sig = name_of(node)
                if sig not in self.signals:
                    self.signals.append(sig)
                return Identifier(sig)
            for slot in getattr(type(node), "__slots__", ()):
                child = getattr(node, slot, None)
                if isinstance(child, Expression):
                    setattr(node, slot, rewrite(child))
                elif isinstance(child, list):
                    setattr(node, slot, [rewrite(c) if isinstance(c, Expression) else c for c in child])
            return node

        self._expr = rewrite(assigns[0].rhs)
        self._eval = ExpressionEvaluator()

    def holds(self, cur: dict[str, Value], prev: dict[str, Value]) -> bool:
        """The condition's value (x counts as false) for current values
        *cur*, with *prev* the previous step's values (edge functions)."""
        from .evaluator import EvalContext  # noqa: PLC0415

        values = dict(cur)
        for synth, kind, sig in self._edges:
            now, before = cur[sig], prev[sig]
            if kind == "changed":
                hit = (now.val, now.mask) != (before.val, before.mask)
            else:
                want_now, want_before = (1, 0) if kind == "rose" else (0, 1)
                hit = (
                    not now.mask & 1
                    and not before.mask & 1
                    and (now.val & 1) == want_now
                    and (before.val & 1) == want_before
                )
            values[synth] = Value(1 if hit else 0, width=1)
        result = self._eval.eval(self._expr, EvalContext(values))
        return result.mask == 0 and result.val != 0


def _capture_path(pattern: str, n: int) -> str:
    """Output path of capture *n*: ``{n}`` in *pattern* is replaced, else
    ``_NNN`` goes before the suffix (``cap.vcd`` -> ``cap_000.vcd``)."""
    if "{n}" in pattern:
        return pattern.replace("{n}", f"{n:03d}")
    p = Path(pattern)
    return str(p.with_name(f"{p.stem}_{n:03d}{p.suffix}"))


class CaptureSession(_TraceSink):
    """VCD capture around trigger events: keeps the last *pre* time units of
    changes in memory and writes nothing until a trigger fires; then writes
    that window plus the next *post* time units to a new file
    (``_capture_path``), and re-arms, up to *max_captures* files.

    Triggers: a condition (*trigger*: a ``TriggerExpr`` string, or a
    callable ``f(time, values) -> bool`` over a dict of the traced and
    *watch* signals' ``Value``s, called at each step where one of them
    changed), ``trigger()`` from testbench code, an HDL ``$error`` (with
    *on_error*), and -- with *on_failure* -- an HDL ``$fatal`` or an
    exception from ``run()``/``run_step()``/``batch_run()``/
    ``run_cycles()``/``settle()`` or leaving a ``with`` block. A failure
    writes the window up to the failure time and closes it (no post).

    The starting values of a window are reconstructed by undoing the
    buffered changes (each change keeps its old value), so no periodic
    snapshots are stored. Memory use is bounded by *pre* time units of
    traced-signal activity.
    """

    def __init__(  # noqa: PLR0913
        self,
        sched,
        path: str | Path,
        *,
        signal_names: Iterable[str],
        pre: int,
        post: int,
        trigger: str | Callable[[int, dict[str, Value]], bool] | None = None,
        watch: Iterable[str] = (),
        max_captures: int = 1,
        on_failure: bool = True,
        on_error: bool = True,
        timescale: str = "1ns",
        root_scope: str = "top",
    ) -> None:
        self._sched = sched
        self._on_error = on_error
        self._pattern = str(path)
        self._timescale = timescale
        self._root = root_scope
        self._pre = pre
        self._post = post
        self._max = max_captures
        self._on_failure = on_failure
        self._vcd_names = sorted(n for n in signal_names if not is_synthesized_local_name(n))
        self._expr = TriggerExpr(trigger) if isinstance(trigger, str) else None
        self._fn = trigger if callable(trigger) else None
        extra: list[str] = []
        for name in [*(self._expr.signals if self._expr else []), *watch]:
            if name not in self._vcd_names and name not in extra:
                extra.append(name)
        all_signals = set(sched.signal_names())
        missing = [n for n in extra if n not in all_signals]
        if missing:
            raise ValueError(f"trigger/watch signals not found: {missing}")
        self._names = [*self._vcd_names, *extra]
        self._n_vcd = len(self._vcd_names)
        self.files: list[str] = []
        self.triggers: list[tuple[int, str]] = []  # (time, reason) of each capture

        hub = trace_hub(sched)
        hub.dispatch(sched.time, hub._poll())
        initial = [sched.read_signal(name) for name in self._names]
        self._widths = [v.width for v in initial]
        self._cur = [(v.val, v.mask) for v in initial]
        self._attach_time = sched.time
        self._ring: list[tuple[int, int, int, int, int, int]] = []  # (time, k, old v, old m, new v, new m)
        self._writer: VcdWriter | None = None
        self._sigs: list[_VcdSignal] = []
        self._capture_end: int | None = None
        self._cond = self._eval_at(sched.time, {}) if (self._expr or self._fn) else False
        self._closed = False
        self._failed: BaseException | None = None
        self._pending: list[tuple[int, str, str]] = []  # queued $error/$fatal events
        self._hub = hub
        hub.subscribe(self, self._names)
        log = getattr(sched, "display_output", None)
        if isinstance(log, DisplayLog):
            log.listeners.append(self._on_severity)

    # -- trigger evaluation ---------------------------------------------

    def _values(self, cur: list[tuple[int, int]]) -> dict[str, Value]:
        return {n: Value(v, width=w, mask=m) for n, (v, m), w in zip(self._names, cur, self._widths, strict=True)}

    # -- change stream ----------------------------------------------------

    def on_changes(self, time: int, changes: list[tuple[int, int, int]]) -> None:
        if self._pending:
            self._take_pending(time, fatal_at_upto=False)
        if self._capture_end is not None and time > self._capture_end:
            self._finish_capture()
        cur = self._cur
        old: dict[int, tuple[int, int]] = {}
        for k, v, m in changes:
            old[k] = cur[k]
            if k < self._n_vcd:
                ov, om = cur[k]
                self._ring.append((time, k, ov, om, v, m))
            cur[k] = (v, m)
        if self._writer is not None:
            sigs = self._sigs
            vcd_changes = [(sigs[k], v, m) for k, v, m in changes if k < self._n_vcd]
            if vcd_changes:
                self._writer.write_changes(time, vcd_changes)
        if self._pending:
            self._take_pending(time)
        if self._expr is not None or self._fn is not None:
            cond = self._eval_at(time, old)
            fired = cond and not self._cond
            self._cond = cond
            if fired and self._writer is None and len(self.files) < self._max:
                self._start_capture(time, f"trigger {self._expr.text if self._expr else 'callable'}")
        self._trim(time)

    def _eval_at(self, time: int, old: dict[int, tuple[int, int]]) -> bool:
        """The trigger condition now; *old* holds the pre-step values of the
        signals that just changed (for $rose/$fell/$changed)."""
        cur = self._values(self._cur)
        if self._expr is not None:
            prev = list(self._cur)
            for k, before in old.items():
                prev[k] = before
            return self._expr.holds(cur, self._values(prev))
        return bool(self._fn(time, cur)) if self._fn is not None else False

    def _trim(self, time: int) -> None:
        if self._writer is not None:
            return
        horizon = time - self._pre
        ring = self._ring
        cut = 0
        while cut < len(ring) and ring[cut][0] < horizon:
            cut += 1
        if cut:
            del ring[:cut]

    # -- captures -----------------------------------------------------------

    def _start_capture(self, time: int, reason: str, *, post: int | None = None) -> None:
        start = max(time - self._pre, self._attach_time)
        end = time + (self._post if post is None else post)
        # Values at `start`: undo every buffered change after it.
        state = list(self._cur)
        for t, k, ov, om, _nv, _nm in reversed(self._ring):
            if t <= start:
                break
            state[k] = (ov, om)
        path = _capture_path(self._pattern, len(self.files))
        writer = VcdWriter(path, timescale=self._timescale)
        for name, w in zip(self._vcd_names, self._widths, strict=False):
            scope, leaf = _split_hierarchy(name)
            scope = self._root if scope == "top" else f"{self._root}.{scope}"
            writer.add_signal(name, width=w, scope=scope, vcd_name=leaf)
        writer.write_header()
        writer.write_comment(f"capture {len(self.files)}: {reason} at {time}")
        self._sigs = [writer.signal_info(name) for name in self._vcd_names]
        writer.write_section("dumpvars", start, [(self._sigs[k], *state[k]) for k in range(self._n_vcd)])
        block_t = start
        block: list[tuple[_VcdSignal, int, int]] = []
        for t, k, _ov, _om, nv, nm in self._ring:
            if t <= start:
                continue
            if t > end:
                # Already-delivered changes past the window (a severity
                # event reported after batch_run's records, see _on_severity).
                break
            if t != block_t and block:
                writer.write_changes(block_t, block)
                block = []
            block_t = t
            block.append((self._sigs[k], nv, nm))
        if block:
            writer.write_changes(block_t, block)
        self._writer = writer
        self.files.append(path)
        self.triggers.append((time, reason))
        self._capture_end = end

    def _finish_capture(self) -> None:
        if self._writer is not None:
            self._writer.finalize()
        self._writer = None
        self._capture_end = None
        # Keep the buffer (trimmed to `pre` as time advances): a re-armed
        # trigger soon after still gets its pre-window.

    def _sync(self) -> int:
        now = self._sched.time
        self._hub.dispatch(now, self._hub._poll())
        if self._pending:
            self._take_pending(now)
        return now

    def trigger(self, reason: str = "manual trigger") -> None:
        """Capture around the current time (from testbench code, e.g. a
        scoreboard mismatch)."""
        now = self._sync()
        if self._writer is None and len(self.files) < self._max:
            self._start_capture(now, reason)

    def failure(self, exc: BaseException) -> None:
        """Write the window up to now and close it (no post window)."""
        if not self._on_failure or self._closed or exc is self._failed:
            return
        self._failed = exc
        try:
            now = self._sync()
        except Exception:  # noqa: BLE001 -- the simulation is already failing
            now = self._sched.time
        self._fail_at(now, f"failure: {type(exc).__name__}: {exc}")

    def _fail_at(self, time: int, reason: str) -> None:
        if self._writer is None:
            if len(self.files) >= self._max:
                return
            self._start_capture(time, reason, post=0)
        else:
            self._writer.write_comment(f"{reason} at {time}")
        self._finish_capture()

    def _on_severity(self, time: int, severity: str, message: str) -> None:
        """Queue a ``$error`` (with *on_error*: starts a capture) or
        ``$fatal`` (with *on_failure*: writes the window up to it). Events
        arrive while the engine drains display output, which can be before
        or after the changes around them reach this session; they take
        effect in time order from the change stream (``_take_pending``), so
        the buffer holds exactly the history up to the event."""
        if (severity == "FATAL" and self._on_failure) or (severity == "ERROR" and self._on_error):
            self._pending.append((time, severity, message))

    def _take_pending(self, upto: int, *, fatal_at_upto: bool = True) -> None:
        """Act on queued severity events at or before *upto*. With
        *fatal_at_upto* False (called before the changes at *upto* are
        applied) a ``$fatal`` exactly at *upto* waits, so its window includes
        the state at the failure; a ``$error`` there starts its capture
        before them (they are then written live)."""
        while self._pending:
            t, severity, message = self._pending[0]
            if t > upto or (t == upto and severity == "FATAL" and not fatal_at_upto):
                break
            self._pending.pop(0)
            if self._capture_end is not None and t > self._capture_end:
                self._finish_capture()
            if severity == "FATAL":
                self._fail_at(t, f"$fatal: {message}")
            elif self._writer is None and len(self.files) < self._max:
                self._start_capture(t, f"$error: {message}")

    def close(self) -> None:
        if self._closed:
            return
        self._sync()
        self._finish_capture()
        self._hub.unsubscribe(self)
        log = getattr(self._sched, "display_output", None)
        if isinstance(log, DisplayLog) and self._on_severity in log.listeners:
            log.listeners.remove(self._on_severity)
        self._closed = True

    def __enter__(self) -> CaptureSession:
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        if exc is not None:
            self.failure(exc)
        self.close()


def attach_capture(  # noqa: PLR0913
    sim: Simulator,
    path: str | Path,
    *,
    pre: int,
    post: int = 0,
    trigger: str | Callable[[int, dict[str, Value]], bool] | None = None,
    watch: Iterable[str] = (),
    max_captures: int = 1,
    on_failure: bool = True,
    on_error: bool = True,
    timescale: str = "1ns",
    signal_names: Iterable[str] | None = None,
    scopes: Iterable[str] | None = None,
    depth: int = 0,
    signals: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    memories: bool | Iterable[str] | None = None,
) -> CaptureSession:
    """Capture VCD windows around triggers or failures (see
    ``CaptureSession``). *pre*/*post* are time units before/after the
    trigger; signal selection is as for ``attach_vcd``."""
    sched = sim._sched
    if signal_names is None:
        signal_names = select_signals(
            sched.signal_names(), scopes=scopes, depth=depth, signals=signals, exclude=exclude, memories=memories
        )
    return CaptureSession(
        sched,
        path,
        signal_names=signal_names,
        pre=pre,
        post=post,
        trigger=trigger,
        watch=watch,
        max_captures=max_captures,
        on_failure=on_failure,
        on_error=on_error,
        timescale=timescale,
        root_scope=getattr(sim._module, "name", "top"),
    )


def notify_failure(sched, exc: BaseException) -> None:
    """Tell every capture session on *sched* that the simulation failed."""
    hub = getattr(sched, "_trace_hub", None)
    if hub is None:
        return
    for sink in list(hub._sinks):
        if isinstance(sink, CaptureSession):
            sink.failure(exc)


@dataclass(frozen=True)
class DumpRequest:
    """A ``$dumpvars`` call, recorded by the executor and turned into a
    ``VcdTraceSession`` by the scheduler (``start_dump``)."""

    filename: str
    level: int = 0
    scopes: tuple[str, ...] = ()


def start_dump(sched, executor) -> None:
    """Apply pending ``$dumpvars`` / ``$dumpoff`` / ``$dumpon`` /
    ``$dumpall`` / ``$dumpflush`` / ``$dumplimit`` calls recorded on
    *executor*. Called by each scheduler after an initial block (or a
    resumed timed one) has run, so values are those of that time."""
    request: DumpRequest | None = getattr(executor, "_dump_request", None)
    if request is not None:
        executor._dump_request = None
        if executor._dump_session is not None:
            executor._dump_session.close()
        all_names = sched.signal_names()
        scopes = None
        if request.scopes:
            # Verilog scope arguments start at the top module (`tb.u_dut`);
            # flattened names don't include it. Drop that leading segment
            # when the scope as written matches nothing (`tb` alone =
            # everything).
            scopes = []
            for scope in request.scopes:
                if not any(n.startswith(scope + ".") for n in all_names):
                    scope = scope.split(".", 1)[1] if "." in scope else ""
                scopes.append(scope)
        names = select_signals(all_names, scopes=scopes, depth=request.level, memories=False)
        executor._dump_session = VcdTraceSession(sched, request.filename, signal_names=names)
    ctl = getattr(executor, "_dump_ctl", None)
    if not ctl:
        return
    session: VcdTraceSession | None = executor._dump_session
    ops = list(ctl)
    ctl.clear()
    if session is None:
        return
    for op, arg in ops:
        if op == "$dumpoff":
            session.pause()
        elif op == "$dumpon":
            session.resume()
        elif op == "$dumpall":
            session.dump_all()
        elif op == "$dumpflush":
            session.flush()
        elif op == "$dumplimit":
            session._limit = arg
            session._check_limit()


def flush_dump(executor) -> None:
    """Flush a ``$dumpvars`` session's file (end of each ``run()``)."""
    session = getattr(executor, "_dump_session", None)
    if session is not None:
        session.flush()

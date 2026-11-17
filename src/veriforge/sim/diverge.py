"""First-divergence locator: where do two simulations of one design first disagree, and why.

Records (or loads) a value trace of every signal from two runs -- two
veriforge engines driven by the same random stimulus, or two VCD files from
any simulators -- finds the first time each signal differs, then walks back
through the drivers of a diverging signal in the flattened design to the
earliest one whose inputs all still agreed. That driver (an ``assign``, an
``always`` block, a port connection) is where the two runs part.

Rules of the walk:

* a combinational driver (``assign``, ``always @*``) is explained by an input
  that diverged at the same time or earlier;
* an edge-triggered driver (``always @(posedge clk)``) only by an input that
  diverged strictly earlier -- the edge sampled the old values;
* among explaining inputs, the walk follows the one that diverged first.

Usage (CLI)::

    uv run -m veriforge.sim.diverge design.v --top t --engines reference,compiled
    uv run -m veriforge.sim.diverge design.v --top t --vcd a.vcd b.vcd

Usage (Python)::

    stim = Stimulus(cycles=50, seed=1)
    a = record_trace(design, "t", "reference", stim)
    b = record_trace(design, "t", "compiled", stim)
    report = locate_divergence(a, b, design, "t")
    print(report.format())

See notes/simulation/debug.md ("Locating a divergence").
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .value import Value

if TYPE_CHECKING:
    from ..model.design import Design, Module

_CLOCK_PATTERNS = ("clk", "clock", "clk_i", "clk_in", "i_clk", "*_clk", "clk_*")


# ── Stimulus ─────────────────────────────────────────────────────────


# cm:7d1e4b
@dataclass
class Stimulus:
    """Deterministic random input stimulus, cycle by cycle.

    Each input's value at a cycle depends only on (seed, input name, cycle),
    so removing an input -- as the reducer does -- leaves every other input's
    stimulus unchanged.

    Attributes:
        cycles: Number of clock cycles.
        seed: Random seed.
        clocks: Clock input names; ``None`` detects them by name (``clk``,
            ``clock``, ``*_clk``...). A design without a clock is still
            sampled once per "cycle".
        resets: ``{name: (active_value, cycles)}`` -- hold *name* at
            *active_value* for the first *cycles* cycles, then at its inverse
            (bit-wise, over the port width).
        x_prob: Probability that an input value has random x bits.
    """

    cycles: int = 32
    seed: int = 0
    clocks: tuple[str, ...] | None = None
    resets: dict[str, tuple[int, int]] = field(default_factory=dict)
    x_prob: float = 0.0

    def clock_names(self, inputs: list[str]) -> list[str]:
        if self.clocks is not None:
            return [c for c in self.clocks if c in inputs]
        return [n for n in inputs if any(fnmatch.fnmatchcase(n.lower(), p) for p in _CLOCK_PATTERNS)]

    def value(self, name: str, width: int, cycle: int) -> Value:
        if name in self.resets:
            active, n = self.resets[name]
            full = (1 << width) - 1
            return Value(active & full if cycle < n else ~active & full, width=width)
        h = hashlib.blake2b(f"{self.seed}:{name}:{cycle}".encode(), digest_size=16).digest()
        bits = int.from_bytes(h * ((width + 127) // 128), "little")
        val = bits & ((1 << width) - 1)
        mask = 0
        if self.x_prob and (h[0] / 256.0) < self.x_prob:
            mask = (bits >> width) & ((1 << width) - 1) or 1
        return Value(val, width=width, mask=mask)


# ── Traces ───────────────────────────────────────────────────────────


@dataclass
class Trace:
    """Value changes of named signals: ``changes[name] = [(time, bits), ...]``.

    *bits* is an MSB-first string over ``01x`` (z is folded into x, as the
    engines represent both with one encoding). *labels* names sample times
    (``"cycle 3 posedge"``) when the trace was recorded in-process.
    """

    changes: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    labels: dict[int, str] = field(default_factory=dict)
    source: str = ""
    error: str | None = None  # the run stopped with this exception

    def record(self, name: str, time: int, bits: str) -> None:
        ch = self.changes.setdefault(name, [])
        if not ch or ch[-1][1] != bits:
            ch.append((time, bits))

    def value_at(self, name: str, time: int) -> str | None:
        """Value of *name* after all changes at or before *time*."""
        result = None
        for t, v in self.changes.get(name, ()):
            if t > time:
                break
            result = v
        return result

    def value_before(self, name: str, time: int) -> str | None:
        """Value of *name* after all changes strictly before *time*."""
        return self.value_at(name, time - 1)

    def label(self, time: int) -> str:
        return self.labels.get(time, f"t={time}")


def value_bits(value: Value) -> str:
    """MSB-first ``01x`` string of a simulator value."""
    w = max(value.width, 1)
    val = format(value.val, f"0{w}b")[-w:]
    if not value.mask:
        return val
    mask = format(value.mask, f"0{w}b")[-w:]
    return "".join("x" if m == "1" else v for v, m in zip(val, mask, strict=True))


def _traced_names(names: list[str]) -> list[str]:
    """Drop engine-internal names and bare memory names (their elements are traced)."""
    keep = [n for n in names if not n.split(".")[-1].startswith("__")]
    bases = {n.rsplit("[", 1)[0] for n in keep if n.endswith("]")}
    return [n for n in keep if n not in bases or n.endswith("]")]


# cm:4b8f2a
def record_trace(  # noqa: PLR0912
    design: Design,
    top: str,
    engine: str,
    stimulus: Stimulus,
    *,
    signals: list[str] | None = None,
) -> Trace:
    """Simulate *top* on *engine* under *stimulus* and record every signal.

    Inputs start at 0 and time-0 start-up runs (``initial`` blocks, up to
    their first delay); simulation time then stays at 0 -- each cycle is
    driven and settled, like the fuzzer's harness. Each cycle has three
    sample times: ``3c`` after the cycle's inputs are driven and settled,
    ``3c+1`` after the clock rises, ``3c+2`` after it falls. *signals* limits
    the recording (default: every signal except engine-internal ones); names
    the design does not have are skipped. An exception during the run is recorded in
    ``Trace.error``; samples taken before it are kept. Elaboration errors
    (the ``Simulator`` constructor) propagate.
    """
    from .testbench import Simulator

    module = design.get_module(top)
    if module is None:
        raise ValueError(f"no module {top!r} in the design")
    sim = Simulator(module, engine=engine, design=design)
    trace = Trace(source=engine)
    all_names = sim.signals()
    if signals is None:
        names = _traced_names(all_names)
    else:
        have = set(all_names)
        names = [n for n in signals if n in have]
    inputs = [p.name for p in module.input_ports()]
    clocks = stimulus.clock_names(inputs)
    data_inputs = [n for n in inputs if n not in clocks]
    widths = {n: max(sim.read(n).width, 1) for n in inputs}

    def sample(time: int, label: str) -> None:
        trace.labels[time] = label
        for n in names:
            trace.record(n, time, value_bits(sim.read(n)))

    try:
        for n in inputs:
            sim.drive(n, Value(0, width=widths[n]))
        # Time-0 start-up: `initial` blocks (and their t=0 effects) run only
        # when the scheduler runs; settle() alone never starts them. Later
        # delays inside them are never reached -- time does not advance.
        sim.run(max_time=0)
        for cycle in range(stimulus.cycles):
            for n in data_inputs:
                sim.drive(n, stimulus.value(n, widths[n], cycle))
            sim.settle()
            sample(3 * cycle, f"cycle {cycle} inputs")
            if not clocks:
                continue
            for c in clocks:
                sim.drive(c, Value(1, width=widths[c]))
            sim.settle()
            sample(3 * cycle + 1, f"cycle {cycle} posedge")
            for c in clocks:
                sim.drive(c, Value(0, width=widths[c]))
            sim.settle()
            sample(3 * cycle + 2, f"cycle {cycle} negedge")
    except Exception as exc:  # noqa: BLE001 -- the failure is part of the result
        trace.error = f"{type(exc).__name__}: {exc}"
    return trace


def trace_from_vcd(text: str, *, source: str = "") -> Trace:
    """Load a VCD file as a ``Trace`` with full hierarchical names (``tb.dut.u.q``).

    Unlike ``vcd_compare.parse_vcd``, every name sharing a VCD identifier
    (Icarus aliases connected ports) gets the changes. Values are left-extended
    to the declared width per the VCD rules; z is folded into x.
    """
    trace = Trace(source=source)
    ident_names: dict[str, list[tuple[str, int]]] = {}
    scope: list[str] = []
    time = 0
    tokens = iter(text.split())
    for tok in tokens:
        if tok == "$scope":
            next(tokens)  # kind
            scope.append(next(tokens).replace("(", "[").replace(")", "]"))
            next(tokens)  # $end
        elif tok == "$upscope":
            scope.pop()
            next(tokens)
        elif tok == "$var":
            next(tokens)  # kind
            width = int(next(tokens))
            ident = next(tokens)
            name = next(tokens)
            rest = next(tokens)
            while rest != "$end":
                rest = next(tokens)
            full = ".".join([*scope, name])
            ident_names.setdefault(ident, []).append((full, width))
        elif tok in ("$date", "$version", "$timescale", "$comment"):
            for rest in tokens:
                if rest == "$end":
                    break
        elif tok.startswith("#"):
            time = int(tok[1:])
        elif tok[0] in "01xXzZ" and len(tok) > 1 and tok[1:] in ident_names:
            _vcd_change(trace, ident_names[tok[1:]], time, tok[0])
        elif tok[0] in "bB":
            ident = next(tokens)
            if ident in ident_names:
                _vcd_change(trace, ident_names[ident], time, tok[1:])
        elif tok[0] in "rR":
            ident = next(tokens)
            if ident in ident_names:
                for full, _w in ident_names[ident]:
                    trace.record(full, time, "r" + tok[1:])
    return trace


def _vcd_change(trace: Trace, names: list[tuple[str, int]], time: int, raw: str) -> None:
    raw = raw.lower().replace("z", "x")
    for full, width in names:
        v = raw
        if len(v) < width:
            v = (v[0] if v[0] == "x" else "0") * (width - len(v)) + v
        trace.record(full, time, v[-width:])


def align_trace(trace: Trace, known: set[str]) -> Trace:
    """Strip the scope prefix (``tb.dut.``) that best maps *trace*'s names onto *known* names."""
    best_prefix, best = "", sum(1 for n in trace.changes if n in known)
    prefixes: set[str] = set()
    for n in trace.changes:
        parts = n.split(".")
        for i in range(1, len(parts)):
            prefixes.add(".".join(parts[:i]) + ".")
    for p in prefixes:
        hits = sum(1 for n in trace.changes if n.startswith(p) and n[len(p) :] in known)
        if hits > best:
            best_prefix, best = p, hits
    out = Trace(labels=trace.labels, source=trace.source, error=trace.error)
    for n, ch in trace.changes.items():
        if n.startswith(best_prefix):
            out.changes[n[len(best_prefix) :]] = ch
    return out


# ── Comparison ───────────────────────────────────────────────────────


def _bits_match(a: str, b: str, ignore_x: bool) -> bool:
    if a == b:
        return True
    if not ignore_x or len(a) != len(b):
        return False
    return all(x == y or x == "x" or y == "x" for x, y in zip(a, b, strict=True))


@dataclass
class FirstDiff:
    time: int
    a: str | None
    b: str | None


# cm:9e3c71
def first_divergences(a: Trace, b: Trace, *, ignore_x: bool = False) -> dict[str, FirstDiff]:
    """First time each signal present in both traces differs."""
    out: dict[str, FirstDiff] = {}
    for name in a.changes.keys() & b.changes.keys():
        ca, cb = a.changes[name], b.changes[name]
        times = sorted({t for t, _ in ca} | {t for t, _ in cb})
        ia = ib = -1
        for t in times:
            while ia + 1 < len(ca) and ca[ia + 1][0] <= t:
                ia += 1
            while ib + 1 < len(cb) and cb[ib + 1][0] <= t:
                ib += 1
            va = ca[ia][1] if ia >= 0 else None
            vb = cb[ib][1] if ib >= 0 else None
            if va is None or vb is None:
                continue
            if not _bits_match(va, vb, ignore_x):
                out[name] = FirstDiff(t, va, vb)
                break
    return out


# ── Driver graph ─────────────────────────────────────────────────────


@dataclass
class DriverInfo:
    """One process that writes a signal in the flattened design."""

    kind: str  # "assign" | "port" | "always" | "initial"
    clocked: bool
    writes: set[str]
    reads: set[str]
    where: str  # "file:line" or "" for synthesized port wiring
    text: str

    def describe(self) -> str:
        where = f" at {self.where}" if self.where else ""
        return f"{self.kind}{where}"


def _base(name: str) -> str:
    """Signal a traced name belongs to: ``mem[3]`` -> ``mem``, ``u.q`` -> ``u.q``."""
    while name.endswith("]") and "[" in name:
        name = name[: name.rindex("[")]
    return name


def _display_path(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except ValueError:
        return path


def _source_lines(loc, cache: dict[str, list[str]], limit: int = 6) -> str:
    if not loc or not loc.file or not loc.line:
        return ""
    lines = cache.get(loc.file)
    if lines is None:
        try:
            lines = Path(loc.file).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        cache[loc.file] = lines
    end = max(loc.end_line or loc.line, loc.line)
    chunk = lines[loc.line - 1 : min(end, loc.line - 1 + limit)]
    text = "\n".join(chunk).strip("\n")
    if end - loc.line + 1 > limit:
        text += "\n    ..."
    return text


# cm:2c6d90
class DriverGraph:
    """Which processes write which signals, in the flattened, lowered design."""

    def __init__(self, drivers: list[DriverInfo]) -> None:
        self.drivers = drivers
        self.by_signal: dict[str, list[DriverInfo]] = {}
        for d in drivers:
            for w in d.writes:
                self.by_signal.setdefault(w, []).append(d)

    @classmethod
    def from_design(cls, design: Design, top: str) -> DriverGraph:
        from ..codegen.verilog_emitter import emit_expression
        from ..model.statements import BlockingAssign, NonblockingAssign, TaskEnable
        from .scheduler import _collect_reads, _collect_stmt_reads, _lhs_base_names
        from .testbench import Simulator

        design = copy.deepcopy(design)
        module = design.get_module(top)
        if module is None:
            raise ValueError(f"no module {top!r} in the design")
        flat: Module = Simulator(module, engine="reference", design=design)._module
        src_cache: dict[str, list[str]] = {}
        drivers: list[DriverInfo] = []

        def where(node) -> str:
            loc = node.loc
            return f"{_display_path(loc.file)}:{loc.line}" if loc and loc.file and loc.line else ""

        for ca in flat.continuous_assigns:
            writes = _lhs_base_names(ca.lhs)
            reads = _collect_reads(ca.rhs)
            text = _source_lines(ca.loc, src_cache) or f"{emit_expression(ca.lhs)} = {emit_expression(ca.rhs)}"
            kind = "assign" if where(ca) else "port"
            drivers.append(DriverInfo(kind, False, writes, reads, where(ca), text))
        for kind, blocks in (("always", flat.always_blocks), ("initial", flat.initial_blocks)):
            for blk in blocks:
                blk_writes: set[str] = set()
                for node in blk.body.walk():
                    if isinstance(node, (BlockingAssign, NonblockingAssign)):
                        blk_writes |= _lhs_base_names(node.lhs)
                    elif isinstance(node, TaskEnable):
                        for arg in node.arguments:
                            blk_writes |= _lhs_base_names(arg)
                blk_reads: set[str] = set()
                _collect_stmt_reads(blk.body, blk_reads)
                clocked = any(
                    getattr(e, "edge", None) in ("posedge", "negedge")
                    for e in getattr(blk, "sensitivity_list", None) or []
                )
                text = _source_lines(blk.loc, src_cache) or kind
                drivers.append(DriverInfo(kind, clocked, blk_writes, blk_reads, where(blk), text))
        return cls(drivers)

    def drivers_of(self, name: str) -> list[DriverInfo]:
        return self.by_signal.get(name) or self.by_signal.get(_base(name), [])


# ── Locator ──────────────────────────────────────────────────────────


@dataclass
class Step:
    signal: str
    diff: FirstDiff
    drivers: list[DriverInfo]


@dataclass
class DivergenceReport:
    """Result of ``locate_divergence``."""

    a: Trace
    b: Trace
    diffs: dict[str, FirstDiff]
    chain: list[Step]  # from the starting signal back to the root
    root_inputs: list[tuple[str, str | None, str | None]]  # root driver inputs: (name, a, b)
    untraced_inputs: list[str]
    outputs: list[str]

    @property
    def diverged(self) -> bool:
        return bool(self.diffs) or (self.a.error != self.b.error)

    @property
    def root(self) -> Step | None:
        return self.chain[-1] if self.chain else None

    def format(self) -> str:  # noqa: PLR0912
        a, b = self.a.source or "A", self.b.source or "B"
        out: list[str] = []
        for tr in (self.a, self.b):
            if tr.error:
                out.append(f"{tr.source or '?'} stopped with {tr.error}")
        if not self.diffs:
            out.append(
                f"No divergence between {a} and {b} on {len(self.a.changes.keys() & self.b.changes.keys())} common signals."
            )
            return "\n".join(out)
        first = min(self.diffs.values(), key=lambda d: d.time)
        n_out = [o for o in self.outputs if o in self.diffs]
        out.append(
            f"{len(self.diffs)} signals diverge; the first at {self.a.label(first.time)}. "
            f"Diverging outputs: {', '.join(sorted(n_out, key=lambda o: self.diffs[o].time)) or 'none'}."
        )
        out.append("")
        out.append(f"Trace back (values {a} / {b}):")
        for st in self.chain:
            d = st.diff
            drivers_desc = ", ".join(x.describe() for x in st.drivers) or "no driver (input or undriven)"
            out.append(f"  {st.signal:<24} {self.a.label(d.time):<20} {_show(d.a)} / {_show(d.b)}   <- {drivers_desc}")
        root = self.root
        assert root is not None
        out.append("")
        if not root.drivers:
            out.append(f"Root: {root.signal} has no driver in the design; it diverges at its source.")
        else:
            out.append(f"Root: {root.signal} first diverges at {self.a.label(root.diff.time)}, but every traced input")
            edge = all(d.clocked for d in root.drivers)
            out.append(f"of its driver agreed {'before' if edge else 'at'} that time:")
            for name, va, vb in self.root_inputs:
                out.append(f"  {name:<24} {_show(va)}" + ("" if va == vb else f" / {_show(vb)}"))
            if self.untraced_inputs:
                out.append(f"  (not in both traces: {', '.join(sorted(self.untraced_inputs))})")
            for drv in root.drivers:
                out.append("")
                out.append(f"Driver: {drv.describe()}{'  [edge-triggered]' if drv.clocked else ''}")
                out.extend("    " + line for line in drv.text.splitlines())
        return "\n".join(out)


def _show(bits: str | None) -> str:
    if bits is None:
        return "-"
    if len(bits) > 8 and "x" not in bits and not bits.startswith("r"):
        return f"{len(bits)}'h{int(bits, 2):x}"
    return f"{len(bits)}'b{bits}" if not bits.startswith("r") else bits


# cm:5f0a3d
def locate_divergence(  # noqa: PLR0912, PLR0913
    a: Trace,
    b: Trace,
    design: Design,
    top: str,
    *,
    start: str | None = None,
    ignore_x: bool = False,
    graph: DriverGraph | None = None,
) -> DivergenceReport:
    """Find where traces *a* and *b* of *design* part, walking back from *start*.

    *start* defaults to the first-diverging output port of *top* (or, when no
    output diverges, the first-diverging signal). Trace names must match the
    flattened design's (``u.q``, ``g[1].t``); use ``align_trace`` for VCDs.
    """
    module = design.get_module(top)
    outputs = [p.name for p in module.output_ports()] if module else []
    diffs = first_divergences(a, b, ignore_x=ignore_x)
    report = DivergenceReport(a, b, diffs, [], [], [], outputs)
    if not diffs:
        return report
    graph = graph or DriverGraph.from_design(design, top)
    traced = a.changes.keys() & b.changes.keys()
    # Traced names of each signal (a memory's elements).
    by_base: dict[str, list[str]] = {}
    for n in traced:
        by_base.setdefault(_base(n), []).append(n)

    if start is None:
        out_diffs = [o for o in outputs if o in diffs]
        pool = out_diffs or list(diffs)
        start = min(pool, key=lambda n: (diffs[n].time, n))
    elif start not in diffs:
        raise ValueError(f"signal {start!r} does not diverge")

    cur = start
    seen: set[str] = set()
    while True:
        seen.add(cur)
        d = diffs[cur]
        drivers = graph.drivers_of(cur)
        report.chain.append(Step(cur, d, drivers))
        best: tuple[int, str] | None = None
        for drv in drivers:
            for r in drv.reads:
                for name in by_base.get(r, ()) if r in by_base else ():
                    if name == cur or name in seen or name not in diffs:
                        continue
                    t = diffs[name].time
                    if (t < d.time) if drv.clocked else (t <= d.time):
                        if best is None or (t, name) < best:
                            best = (t, name)
        if best is None:
            break
        cur = best[1]

    root = report.chain[-1]
    inputs: set[str] = set()
    untraced: set[str] = set()
    for drv in root.drivers:
        for r in drv.reads:
            names = by_base.get(r)
            if names:
                inputs.update(names)
            elif r in graph.by_signal and not any(n.startswith((r + ".", r + "[")) for n in traced):
                # (Not an instance or struct prefix of traced names: `u` of `u.q`.)
                untraced.add(r)
    when = root.diff.time - 1 if all(x.clocked for x in root.drivers) and root.drivers else root.diff.time
    for name in sorted(inputs - {root.signal}):
        report.root_inputs.append((name, a.value_at(name, when), b.value_at(name, when)))
    report.untraced_inputs = sorted(untraced)
    return report


# ── Design loading and CLI ───────────────────────────────────────────


def load_design(
    files: list[str | Path],
    *,
    preprocess: bool = False,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
) -> Design:
    """Parse and link *files* into one design."""
    from ..project import parse_files

    return parse_files(files, preprocess=preprocess, defines=defines, include_paths=include_paths)


def parse_resets(specs: list[str]) -> dict[str, tuple[int, int]]:
    """``["rst=1:2", "rst_n=0"]`` -> ``{"rst": (1, 2), "rst_n": (0, 2)}``."""
    out: dict[str, tuple[int, int]] = {}
    for spec in specs:
        m = re.fullmatch(r"([\w.$]+)=(\d+)(?::(\d+))?", spec)
        if not m:
            raise ValueError(f"bad --reset {spec!r}: expected NAME=VALUE[:CYCLES]")
        out[m.group(1)] = (int(m.group(2), 0), int(m.group(3) or 2))
    return out


def add_design_arguments(ap: argparse.ArgumentParser) -> None:
    """Design-loading and stimulus options shared by the diverge and reduce CLIs."""
    ap.add_argument("files", nargs="+", help="Verilog/SystemVerilog sources")
    ap.add_argument("--top", required=True, help="top module")
    ap.add_argument("--preprocess", action="store_true", help="run the preprocessor")
    ap.add_argument("-D", dest="defines", action="append", default=[], help="NAME[=VALUE] define")
    ap.add_argument("-I", dest="includes", action="append", default=[], help="include directory")
    ap.add_argument("--cycles", type=int, default=32, help="clock cycles of random stimulus (default 32)")
    ap.add_argument("--seed", type=int, default=0, help="stimulus seed (default 0)")
    ap.add_argument("--clock", action="append", help="clock input (repeatable; default: detect by name)")
    ap.add_argument("--reset", action="append", default=[], help="NAME=VALUE[:CYCLES] reset held for CYCLES (2)")
    ap.add_argument("--x-prob", type=float, default=0.0, help="probability of x bits in an input value")
    ap.add_argument("--ignore-x", action="store_true", help="an x bit in either run matches anything")


def stimulus_from_args(args: argparse.Namespace) -> Stimulus:
    return Stimulus(
        cycles=args.cycles,
        seed=args.seed,
        clocks=tuple(args.clock) if args.clock else None,
        resets=parse_resets(args.reset),
        x_prob=args.x_prob,
    )


def design_from_args(args: argparse.Namespace) -> Design:
    defines = dict(d.split("=", 1) if "=" in d else (d, "") for d in args.defines)
    return load_design(
        args.files,
        preprocess=args.preprocess or bool(defines) or bool(args.includes),
        defines=defines,
        include_paths=args.includes,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m veriforge.sim.diverge",
        description="Find where two simulations of a design first disagree, and the driver responsible.",
    )
    add_design_arguments(ap)
    ap.add_argument(
        "--engines", default="reference,compiled", help="two engines to compare (default reference,compiled)"
    )
    ap.add_argument("--vcd", nargs=2, metavar=("A", "B"), help="compare two VCD files instead of running engines")
    ap.add_argument("--signal", help="walk back from this signal (default: the first-diverging output)")
    args = ap.parse_args(argv)

    design = design_from_args(args)
    if args.vcd:
        known = set(_traced_names(_flat_signal_names(design, args.top)))
        traces = [
            align_trace(trace_from_vcd(Path(p).read_text(encoding="utf-8", errors="replace"), source=p), known)
            for p in args.vcd
        ]
    else:
        engines = [e.strip() for e in args.engines.split(",")]
        if len(engines) != 2:
            ap.error("--engines takes exactly two engines")
        stim = stimulus_from_args(args)
        traces = [record_trace(design, args.top, e, stim) for e in engines]
    report = locate_divergence(traces[0], traces[1], design, args.top, start=args.signal, ignore_x=args.ignore_x)
    print(report.format())
    return 1 if report.diverged else 0


def _flat_signal_names(design: Design, top: str) -> list[str]:
    from .testbench import Simulator

    design = copy.deepcopy(design)
    module = design.get_module(top)
    if module is None:
        raise SystemExit(f"no module {top!r} in the design")
    return Simulator(module, engine="reference", design=design).signals()


if __name__ == "__main__":
    sys.exit(main())

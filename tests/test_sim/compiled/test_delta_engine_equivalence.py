"""A/B equivalence of the compiled engine's two ``delta_loop`` engines.

The scan engine (default) and the queue engine (``VERIFORGE_DELTA_ENGINE=queue``)
run the same processes in the same rank order every delta iteration, except
that the queue engine skips *redundant reruns of pure processes* -- reruns on
inputs unchanged since the process last ran, which are no-ops (see
notes/plans/work_queue_delta_engine.md, "Stage 2 design decisions" and
"Redundant-rerun elimination"). A skipped no-op dirties nothing, so
equivalence stays directly checkable: on every step, both engines must produce
identical signal values AND an identical ``delta_loop`` iteration count
(``CompiledSim.step()``'s return value). Identical iteration counts are what
guarantee ``DELTA_LIMIT``, ``DELTA_CONV_CHECK_START`` and the value-convergence
detector behave identically under both engines -- and they would expose a
"pure" process whose rerun was not actually a no-op.

Designs are chosen for the risky surfaces: continuous-assign chains declared
out of dependency order, combinational always blocks, a combinational block
that writes a memory it also reads (the memory-marker / value-convergence
path), wide (multi-word) signals, derived clocks, negedge/async-reset
processes, constant drivers (empty sensitivity), convergent combinational
feedback, a genuine combinational cycle (must still hit the delta limit), and
randomized modules from the cross-engine differential generators.
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .. import test_differential as td
from .. import test_differential_statements as tds

_REPO = Path(__file__).resolve().parents[3]


def _load_benchmarks_module(name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / "benchmarks" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── harness ──────────────────────────────────────────────────────────


def _build(source: str, mode: str, monkeypatch, *, top: str = "t", delta_limit: int = 10_000) -> Simulator:
    monkeypatch.setenv("VERIFORGE_DELTA_ENGINE", mode)
    design = td._parse_design(source)
    module = next(m for m in design.modules if m.name == top)
    return Simulator(module, engine="compiled", design=design, delta_limit=delta_limit)


def _trace(sim: Simulator, steps: list[dict[str, Value]]) -> list[tuple[int, tuple]]:
    """Per step: snapshot, apply drives, step(); record (iteration count,
    every signal's (val, mask))."""
    sched = sim._sched
    csim = sched._sim
    smap = sched._signal_map
    names = sorted(smap)
    out = []
    for drives in steps:
        csim.snapshot()
        for name, value in drives.items():
            # Width-aware raw drive (two's-complement for <=64-bit sids); no
            # scheduler snapshot bookkeeping, unlike sim.drive().
            sched._sim_drive_signal(smap[name], value.val & ~value.mask, value.mask)
        deltas = csim.step()
        out.append((deltas, tuple(csim.read_wide(smap[n]) for n in names)))
    return out


def _assert_same(scan: list, queue: list, names: list[str], label: str) -> None:
    assert len(scan) == len(queue)
    for i, ((d_s, st_s), (d_q, st_q)) in enumerate(zip(scan, queue, strict=True)):
        if d_s != d_q:
            pytest.fail(f"{label}: step {i}: iteration count differs: scan={d_s} queue={d_q}")
        if st_s != st_q:
            diffs = [f"{n}: scan={a} queue={b}" for n, a, b in zip(names, st_s, st_q, strict=True) if a != b]
            pytest.fail(f"{label}: step {i}: signal values differ:\n  " + "\n  ".join(diffs[:20]))


def _rand_value(rng: random.Random, width: int, *, x_prob: float = 0.05) -> Value:
    if rng.random() < x_prob:
        return Value(0, width=width, mask=(1 << width) - 1)
    return Value(rng.getrandbits(width), width=width)


def _clocked_steps(inputs: dict[str, int], n_cycles: int, seed: int, *, clk: str = "clk") -> list[dict[str, Value]]:
    """Per cycle: drive random inputs (clock low), clock high, clock low."""
    rng = random.Random(seed)
    steps: list[dict[str, Value]] = [{clk: Value(0, width=1), **{n: Value(0, width=w) for n, w in inputs.items()}}]
    for _ in range(n_cycles):
        steps.append({n: _rand_value(rng, w) for n, w in inputs.items()})
        steps.append({clk: Value(1, width=1)})
        steps.append({clk: Value(0, width=1)})
    return steps


def _comb_steps(inputs: dict[str, int], n: int, seed: int) -> list[dict[str, Value]]:
    rng = random.Random(seed)
    return [{n_: _rand_value(rng, w) for n_, w in inputs.items()} for _ in range(n)]


def _ab(source: str, steps: list[dict[str, Value]], monkeypatch, *, label: str, top: str = "t") -> list:
    scan_sim = _build(source, "scan", monkeypatch, top=top)
    queue_sim = _build(source, "queue", monkeypatch, top=top)
    names = sorted(scan_sim._sched._signal_map)
    assert names == sorted(queue_sim._sched._signal_map)
    scan, queue = _trace(scan_sim, steps), _trace(queue_sim, steps)
    _assert_same(scan, queue, names, label)
    return scan


# ── hand-picked designs ──────────────────────────────────────────────

# Out-of-dependency-order continuous assigns (the mismatch_10096 shape) feeding
# a register, plus a combinational block downstream of the chain.
_OUT_OF_ORDER = """
module t(input clk, input [7:0] a, input [7:0] b, output reg [7:0] r, output reg [7:0] y);
  wire [7:0] o10, o11, o12, o13;
  assign o10 = ~o12;
  assign o11 = o10 + b;
  assign o13 = o11 ^ o12;
  assign o12 = {a[3:0], a[7:4]};
  always @(*) y = o13 & {8{a[0]}};
  always @(posedge clk) r <= o11 ^ o10;
endmodule
"""

# Combinational blocks that clear-then-rewrite a memory they also read: each
# run changes the data twice (net unchanged once settled), toggling the
# memory's marker signal and re-triggering the block itself, so delta_loop
# only stops via the value-convergence check (DELTA_CONV_CHECK_START) -- the
# pattern the convergence check's marker-forcing comment describes. (A block
# that just writes the same settled value doesn't re-trigger at all.)
_COMBO_MEMORY = """
module t(input clk, input [7:0] a, output reg [7:0] r, output [7:0] y);
  reg [7:0] m [0:3];
  always @(*) begin
    m[0] = 8'd0;
    m[0] = a;
    m[1] = m[0] + 8'd1;
  end
  assign y = m[1];
  always @(posedge clk) r <= y;
endmodule
"""

_COMBO_MEMORY_DYNAMIC = """
module t(input clk, input [7:0] a, output reg [7:0] r, output [7:0] y);
  reg [7:0] m [0:3];
  integer i;
  always @(*) begin
    for (i = 0; i < 4; i = i + 1) m[i] = 8'd0;
    m[a[1:0]] = a + m[0];
  end
  assign y = m[1] ^ m[2];
  always @(posedge clk) r <= y;
endmodule
"""

_WIDE = """
module t(input clk, input [63:0] a, input [63:0] b, output reg [191:0] r, output [127:0] w);
  wire [127:0] cat = {a, b};
  wire [127:0] sh = cat << 3;
  assign w = sh ^ {b, a};
  always @(posedge clk) r <= {w, a};
endmodule
"""

# A clock derived through two continuous-assign hops: its edge is only visible
# to the seq process's edge check in a later delta iteration.
_DERIVED_CLOCK = """
module t(input clk, input en, input [7:0] d, output reg [7:0] q, output reg [7:0] q2);
  wire g1 = clk & en;
  wire g2 = g1;
  always @(posedge g2) q <= d;
  always @(posedge clk) q2 <= q + d;
endmodule
"""

# Constant drivers (empty sensitivity -> run every iteration), async reset,
# and a negedge process.
_CONST_ASYNC_NEGEDGE = """
module t(input clk, input rst_n, input [3:0] d, output reg [3:0] q, output reg [3:0] qn,
         output [3:0] k, output [3:0] z);
  assign k = 4'd9;
  assign z = k ^ q;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 4'd0; else q <= d + k;
  always @(negedge clk) qn <= q;
endmodule
"""

# Convergent combinational feedback: an SR latch of cross-coupled NORs.
_SR_LATCH = """
module t(input s, input r, output q, output qn);
  assign q = ~(r | qn);
  assign qn = ~(s | q);
endmodule
"""

# Continuous assigns that read a combinational block's output. Combos are
# ranked after every continuous assign, so these readers rank *below* their
# writer: under the queue engine's redundant-rerun elimination they are pure
# readers that must be re-run next iteration (the `_pnext` path), not skipped.
_CONT_READS_COMBO = """
module t(input clk, input [7:0] a, input [7:0] b, output reg [7:0] r, output [7:0] z);
  reg [7:0] y;
  wire [7:0] u = a ^ b;
  always @(*) y = u + 8'd3;
  wire [7:0] v = y ^ a;
  assign z = v + y;
  always @(posedge clk) r <= z;
endmodule
"""

# A genuine combinational cycle: a three-inverter ring, enabled by `en`.
_RING_OSCILLATOR = """
module t(input en, output a);
  wire b, c;
  assign a = ~(c & en);
  assign b = ~a;
  assign c = ~b;
endmodule
"""


def test_out_of_order_cont_chain(monkeypatch):
    steps = _clocked_steps({"a": 8, "b": 8}, 40, seed=1)
    _ab(_OUT_OF_ORDER, steps, monkeypatch, label="out_of_order")


@pytest.mark.parametrize("source", [_COMBO_MEMORY, _COMBO_MEMORY_DYNAMIC], ids=["static", "dynamic"])
def test_combo_memory_value_convergence_path(source, monkeypatch):
    steps = _clocked_steps({"a": 8}, 20, seed=2)
    scan = _ab(source, steps, monkeypatch, label="combo_memory")
    # Guard that this design really exercises the value-convergence path
    # (DELTA_CONV_CHECK_START = 16) -- otherwise the test would silently stop
    # covering it, as an earlier version of this design did.
    assert max(d for d, _state in scan) >= 16


def test_wide_signals(monkeypatch):
    steps = _clocked_steps({"a": 64, "b": 64}, 30, seed=3)
    _ab(_WIDE, steps, monkeypatch, label="wide")


def test_derived_clock(monkeypatch):
    steps = _clocked_steps({"en": 1, "d": 8}, 40, seed=4)
    _ab(_DERIVED_CLOCK, steps, monkeypatch, label="derived_clock")


def test_constant_driver_async_reset_negedge(monkeypatch):
    rng = random.Random(5)
    steps: list[dict[str, Value]] = [{"clk": Value(0, width=1), "rst_n": Value(0, width=1), "d": Value(0, width=4)}]
    for _ in range(40):
        steps.append({"d": _rand_value(rng, 4), "rst_n": Value(int(rng.random() > 0.15), width=1)})
        steps.append({"clk": Value(1, width=1)})
        steps.append({"clk": Value(0, width=1)})
    _ab(_CONST_ASYNC_NEGEDGE, steps, monkeypatch, label="const_async_negedge")


def test_cont_reads_combo_output(monkeypatch):
    steps = _clocked_steps({"a": 8, "b": 8}, 40, seed=6)
    _ab(_CONT_READS_COMBO, steps, monkeypatch, label="cont_reads_combo")


def test_sr_latch_convergent_feedback(monkeypatch):
    seq = [(1, 0), (0, 0), (0, 1), (0, 0), (1, 0), (1, 1), (0, 0), (0, 1), (1, 0), (0, 0)]
    steps = [{"s": Value(s, width=1), "r": Value(r, width=1)} for s, r in seq]
    _ab(_SR_LATCH, steps, monkeypatch, label="sr_latch")


@pytest.mark.parametrize("mode", ["scan", "queue"])
def test_ring_oscillator_hits_delta_limit(mode, monkeypatch):
    """A genuine combinational cycle must still raise the delta-limit error --
    never silently "converge" -- under either engine."""
    sim = _build(_RING_OSCILLATOR, mode, monkeypatch, delta_limit=200)
    csim = sim._sched._sim
    en = sim._sched._signal_map["en"]
    csim.snapshot()
    csim.drive(en, 0, 0)
    csim.step()  # en=0: the ring is broken (a = 1) and settles
    csim.snapshot()
    csim.drive(en, 1, 0)
    with pytest.raises(RuntimeError, match="Delta cycle limit"):
        csim.step()


def test_ring_oscillator_settled_steps_match(monkeypatch):
    # The enabled-oscillation step itself errors (covered above); the
    # disabled, settling steps must match exactly.
    steps = [{"en": Value(0, width=1)}, {"en": Value(0, width=1)}]
    _ab(_RING_OSCILLATOR, steps, monkeypatch, label="ring_disabled")


def test_benchmark_dut(monkeypatch):
    bench = _load_benchmarks_module("benchmark")
    steps: list[dict[str, Value]] = [{"clk": Value(0, width=1), "rst": Value(1, width=1)}]
    for i in range(60):
        if i == 3:
            steps.append({"rst": Value(0, width=1)})
        steps.append({"clk": Value(1, width=1)})
        steps.append({"clk": Value(0, width=1)})
    _ab(bench.BENCH_DUT, steps, monkeypatch, label="benchmark_dut", top="bench")


@pytest.mark.parametrize(("n_lanes", "active"), [(16, 3), (16, 16), (24, 1)])
def test_wide_bench(n_lanes, active, monkeypatch):
    gen = _load_benchmarks_module("wide_bench_gen")
    source = gen.make_wide_bench(n_lanes, active)
    steps: list[dict[str, Value]] = [{"clk": Value(0, width=1), "rst": Value(1, width=1)}]
    for i in range(30):
        if i == 2:
            steps.append({"rst": Value(0, width=1)})
        steps.append({"clk": Value(1, width=1)})
        steps.append({"clk": Value(0, width=1)})
    _ab(source, steps, monkeypatch, label=f"wide_bench({n_lanes},{active})", top="bench")


# ── batch_run path ───────────────────────────────────────────────────


@pytest.mark.parametrize("source", [_OUT_OF_ORDER, _DERIVED_CLOCK, _COMBO_MEMORY], ids=["ooo", "derived", "combomem"])
def test_batch_run_equivalence(source, monkeypatch):
    """batch_run (the C-level cycle loop: Stage 1's first-cycle settle, the
    negedge skip, per-cycle delta_loop calls) must end in identical state."""
    finals = []
    for mode in ("scan", "queue"):
        sim = _build(source, mode, monkeypatch)
        smap = sim._sched._signal_map
        csim = sim._sched._sim
        for name, width in (("a", 8), ("b", 8), ("en", 1), ("d", 8)):
            if name in smap:
                sim._sched._sim_drive_signal(smap[name], 0x5A & ((1 << width) - 1), 0)
        ran = sim.batch_run(25, "clk")
        finals.append((ran, tuple(csim.read_wide(smap[n]) for n in sorted(smap))))
    assert finals[0] == finals[1]


# ── randomized modules (cross-engine differential generators) ────────


@pytest.mark.parametrize("batch_idx", range(min(6, len(td._BATCHES))))
def test_random_expression_modules(batch_idx, monkeypatch):
    source = td._build_batch_module(td._BATCHES[batch_idx])
    inputs = {name: width for name, width, _signed in td.FIXED_SIGNALS}
    steps = _clocked_steps(inputs, 12, seed=100 + batch_idx)
    _ab(source, steps, monkeypatch, label=f"diff_expr[{batch_idx}]")


@pytest.mark.parametrize("batch_idx", range(min(6, len(tds._BATCHES))))
def test_random_statement_modules(batch_idx, monkeypatch):
    source = tds._build_batch_module(tds._BATCHES[batch_idx])
    inputs = {name: width for name, width, _signed in td.FIXED_SIGNALS}
    steps = _clocked_steps(inputs, 12, seed=200 + batch_idx)
    _ab(source, steps, monkeypatch, label=f"diff_stmt[{batch_idx}]")


# ── rerun-purity classification ──────────────────────────────────────

_PURITY_MODULE = """
module t(input [7:0] a, input [7:0] b, output [7:0] p_plain, output [7:0] p_sys,
         output [7:0] i_func, output [7:0] i_const, output [3:0] p_slice);
  reg [7:0] mem [0:3];
  function [7:0] f(input [7:0] x);
    f = x + 8'd1;
  endfunction
  assign p_plain = a ^ b;
  assign p_sys = $unsigned(a) + $signed(b);
  assign i_func = f(a);
  assign i_const = 8'd5;
  assign p_slice[3:0] = a[3:0];
  assign mem[0] = a;
endmodule
"""


def test_rerun_purity_classification():
    """Only processes whose rerun on unchanged inputs is a provable no-op may
    skip it: plain signal targets with pure RHS. Memory targets (marker
    toggles), user-function calls (function-internal writes), and constant
    drivers (empty sensitivity) keep the exact scan-engine schedule."""
    from veriforge.sim.compiled.codegen import CythonCodegen

    design = td._parse_design(_PURITY_MODULE)
    cg = CythonCodegen()
    cg.generate(design.modules[0])
    # Each process writes exactly one signal here; map it back via its body.
    pure_by_target = {}
    names = {sid: name for name, sid in cg._signal_map.items()}
    for idx, (_sens, body) in enumerate(cg._processes):
        text = "\n".join(body)
        targets = [names[sid] for sid in names if f"mark_dirty(c, {sid})" in text]
        for t in targets:
            pure_by_target[t] = idx in cg._pure_conts
    assert pure_by_target["p_plain"] is True
    assert pure_by_target["p_sys"] is True
    assert pure_by_target["p_slice"] is True
    assert pure_by_target["i_func"] is False
    assert pure_by_target["i_const"] is False
    # The memory-target assign is impure too (its body toggles a marker).
    mem_procs = [i for i, (_s, body) in enumerate(cg._processes) if any("mem_" in line for line in body)]
    assert mem_procs
    assert not any(i in cg._pure_conts for i in mem_procs)

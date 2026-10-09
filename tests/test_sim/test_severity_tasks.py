"""``$info``/``$warning``/``$error``/``$fatal`` and ``$time``/``%t`` across
engines (found 2026-10-08 while adding HDL-error capture triggers).

Before: reference and compiled ignored all four severity tasks; vm printed
``$error``/``$warning``/``$info`` like ``$display`` and ignored ``$fatal``.
``%t`` printed 0 (reference) or the current time, without consuming its
argument, on every engine -- shifting every later argument -- and ``$time``
in a timed initial block (run through the reference executor) was 0 on
vm/vm-fast/compiled. Expected outputs below are Icarus Verilog's (first
line of each severity message; the ``Time:``/``Scope:`` line is omitted).
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.trace import attach_capture
from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _sim(src: str, engine: str, *, clock: bool = False) -> Simulator:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file="sev.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    if clock:
        sim.fork(Clock(sim.signal("clk"), period=10))
    return sim


_SEVERITY = """module t;
  reg [7:0] x = 8'h2a;
  initial begin
    #5 $info("info %0d", x);
    #5 $warning("warn %h", x);
    #5 $error("err %0d", x);
    $error;
    #5 $fatal(1, "fatal %0d", x);
    $display("after fatal");
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_severity_tasks(engine):
    sim = _sim(_SEVERITY, engine)
    sim.run(max_time=100)
    assert list(sim.display_output) == [
        "INFO: sev.v:4: info 42",
        "WARNING: sev.v:5: warn 2a",
        "ERROR: sev.v:6: err 42",
        "ERROR: sev.v:7: ",
        "FATAL: sev.v:8: fatal 42",
    ]
    assert [(t, s) for t, s, _m in sim.severity_events] == [
        (5, "INFO"),
        (10, "WARNING"),
        (15, "ERROR"),
        (15, "ERROR"),
        (20, "FATAL"),
    ]
    assert sim.time == 20  # $fatal ends the simulation


_CLOCKED = """module t(input clk);
  reg [7:0] c = 0;
  always @(posedge clk) begin
    c <= c + 1;
    if (c == 3) $error("c reached %0d at %0t", c, $time);
    if (c == 5) $fatal(0, "stop");
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_severity_in_clocked_process(engine):
    sim = _sim(_CLOCKED, engine, clock=True)
    sim.run(max_time=200)
    assert list(sim.display_output) == ["ERROR: sev.v:5: c reached 3 at 30", "FATAL: sev.v:6: stop"]
    assert [(t, s) for t, s, _m in sim.severity_events] == [(30, "ERROR"), (50, "FATAL")]
    assert sim.time == 50


_TIME_FORMATS = """module t;
  reg [31:0] r;
  initial begin
    #7 r = $time;
    $display("[%t] [%0t] [%5t] r=%0d", $time, $time, $time, r);
    $display("[%t]", 42);
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_time_and_percent_t(engine):
    sim = _sim(_TIME_FORMATS, engine)
    sim.run(max_time=50)
    assert list(sim.display_output) == ["[                   7] [7] [    7] r=7", "[                  42]"]


_CAP = """module t(input clk);
  reg [7:0] c = 0;
  always @(posedge clk) begin
    c <= c + 1;
    if (c == 12) $error("boom");
    if (c == 30) $fatal(1, "dead");
  end
endmodule
"""


def _times(path) -> list[int]:
    return [int(line[1:]) for line in open(path).read().splitlines() if line.startswith("#")]


@pytest.mark.parametrize("engine", ENGINES)
def test_capture_on_hdl_error_and_fatal(engine, tmp_path):
    sim = _sim(_CAP, engine, clock=True)
    sim.run(max_time=0)
    with attach_capture(sim, tmp_path / "e.vcd", pre=40, post=20, max_captures=2) as cap:
        sim.run(max_time=1000)
    assert [(t, r.split(":")[0]) for t, r in cap.triggers] == [(120, "$error"), (300, "$fatal")]
    err, fatal = cap.files
    assert min(_times(err)) == 80 and max(_times(err)) <= 140
    assert min(_times(fatal)) == 260 and max(_times(fatal)) == 300
    assert "$fatal: sev.v:6: dead" in open(fatal).read()


@pytest.mark.skipif("compiled" not in ENGINES, reason="compiled engine unavailable")
def test_capture_on_hdl_error_inside_batch(tmp_path):
    """In batch_run the $error is reported after the chunk's changes are
    recorded; the capture still gets exactly the window around it."""
    a = _sim(_CAP, "compiled", clock=True)
    with attach_capture(a, tmp_path / "a.vcd", pre=40, post=20, on_failure=False) as ca:
        a.run(max_time=250)
    b = _sim(_CAP, "compiled", clock=True)
    with attach_capture(b, tmp_path / "b.vcd", pre=40, post=20, on_failure=False) as cb:
        b.run_cycles(25)
    assert ca.triggers == cb.triggers
    body = [line for line in open(ca.files[0]).read().splitlines() if not line.startswith(("$date", "$comment"))]
    body_b = [line for line in open(cb.files[0]).read().splitlines() if not line.startswith(("$date", "$comment"))]
    assert body == body_b


_FINISH = """module t(input clk);
  reg [7:0] c = 0, after = 0, other = 0, comb = 0, nb2 = 0;
  always @(posedge clk) begin c <= c + 1; if (c == 5) begin $finish; after = 1; end end
  always @(posedge clk) begin other = other + 1; nb2 <= nb2 + 1; end
  always @(c) comb = c * 2;
endmodule
"""
# Icarus: the calling process stops at $finish (after=0); the time step
# still completes -- the other posedge process runs, NBAs land, combinational
# logic settles -- and then the simulation ends.
_FINISH_EXPECTED = {"c": 6, "after": 0, "other": 6, "nb2": 6, "comb": 12}


@pytest.mark.parametrize("engine", ENGINES)
def test_finish_completes_the_time_step(engine):
    sim = _sim(_FINISH, engine, clock=True)
    sim.run(max_time=200)
    assert {n: sim.read(n).val for n in _FINISH_EXPECTED} == _FINISH_EXPECTED
    assert sim.time == 50


@pytest.mark.parametrize("engine", [e for e in ENGINES if e in ("vm-fast", "compiled")])
def test_batch_finish_counts_completed_cycles(engine):
    if engine == "vm-fast" and not _HAS_CYTHON:
        pytest.skip("vm-fast batch execution requires the native VM extension")
    """A cycle cut short by $finish isn't counted; time stays at that edge."""
    sim = _sim(_FINISH, engine, clock=True)
    assert sim.run_cycles(20) == 5
    assert {n: sim.read(n).val for n in _FINISH_EXPECTED} == _FINISH_EXPECTED
    assert sim.time == 50

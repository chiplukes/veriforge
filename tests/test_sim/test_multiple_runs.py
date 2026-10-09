"""Calling ``run()`` several times with a forked clock (found 2026-10-08).

- ``Simulator.run()`` rescheduled every forked ``Clock`` from t=0 on each
  call, so a second ``run()`` replayed already-simulated edges with time
  going backwards (all engines).
- compiled: ``run()``'s event loop left the clock toggle's pre-edge snapshot
  marked as pending, so a ``drive()`` between ``run()`` calls reused it and
  the next ``run()`` re-detected the last posedge -- every posedge process
  fired twice (the extra firing seeing the newly driven value).
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.trace import register_time_step_callback
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_SRC = """
module t(input clk, input x, output reg [7:0] n, output reg [7:0] nx);
  initial begin n = 0; nx = 0; end
  always @(posedge clk) begin n <= n + 1; if (x) nx <= nx + 1; end
endmodule
"""


def _sim(engine: str) -> Simulator:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_SRC), source_file=None)
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("x", 0)
    sim.fork(Clock(sim.signal("clk"), period=10))
    return sim


@pytest.mark.parametrize("engine", ENGINES)
def test_second_run_continues_the_clock(engine):
    sim = _sim(engine)
    sim.run(max_time=25)
    seen: list[int] = []
    register_time_step_callback(sim._sched, lambda s: seen.append(s.time))
    sim.run(max_time=45)
    assert seen
    assert min(seen) >= 25
    assert seen == sorted(seen)
    assert sim.read("n").val == 5  # posedges at 0, 10, 20, 30, 40


@pytest.mark.parametrize("engine", ENGINES)
def test_drive_between_runs_does_not_refire_last_posedge(engine):
    sim = _sim(engine)
    sim.run(max_time=40)  # ends right after the posedge at 40
    assert sim.read("n").val == 5
    sim.drive("x", 1)
    sim.run(max_time=80)
    assert sim.read("n").val == 9
    assert sim.read("nx").val == 4  # posedges at 50, 60, 70, 80 only

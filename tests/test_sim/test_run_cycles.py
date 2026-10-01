"""Tests for ``Simulator.run_cycles()`` -- the interactive-but-fast batch-run
wrapper for the compiled engine (see notes/plans/compiled_engine_perf_2026-09.md,
"step-path API").

``run_cycles()`` is a thin convenience layer over the existing, already
C-level ``batch_run()``: it infers the clock name/period from the sole
forked ``Clock``, initializes the clock signal and runs any pending t=0
``initial`` blocks on first use, and is meant to be called repeatedly,
interleaved with ``drive()``/``read()``/``settle()``, by a testbench that
wants to make decisions every N cycles instead of every single edge.

The key correctness question these tests target: stimulus chosen
*reactively* -- based on a value read back mid-run -- must produce exactly
the same result whether driven through ``run_cycles()`` chunks or through
the classic per-edge ``run_step()`` loop (the existing, well-established
interactive pattern -- see ``TestMultibitCondition._run_clocked`` in
``tests/test_sim/compiled/test_execution.py`` for the same
``_schedule_clock_events`` + ``run_step()`` idiom used as the oracle here).
"""

from __future__ import annotations

import pytest

from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value


_ACCUM_SRC = """\
module accum(
    input  wire        clk,
    input  wire        rst,
    input  wire [7:0]  load_val,
    input  wire         load_en,
    output reg  [15:0] acc
);
    always @(posedge clk) begin
        if (rst)
            acc <= 16'd0;
        else if (load_en)
            acc <= acc + {8'd0, load_val};
        else
            acc <= acc + 16'd1;
    end
endmodule
"""


def _parse():
    p = verilog_parser(start="source_text")
    tree = p.build_tree(_ACCUM_SRC)
    return tree_to_design(tree).modules[0]


def _decide(total: int) -> int:
    """Deterministic decision function: the next chunk's load value depends
    on the running accumulator total read back after the previous chunk --
    exactly the kind of stimulus a precomputed batch_run() event table
    cannot express, since it depends on the DUT's own runtime state.
    """
    return 3 if (total % 2 == 0) else 10


class TestRunCyclesReactiveStimulus:
    def test_display_output_after_batch_run(self):
        source = 'module batch_display(input clk); always @(posedge clk) $display("tick"); endmodule'
        mod = tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]
        sim = Simulator(mod, engine="compiled")
        sim.drive("clk", 0)

        assert sim.batch_run(3, "clk") == 3
        assert sim.display_output == ["tick", "tick", "tick"]
        assert sim.display_output == ["tick", "tick", "tick"]

    def test_run_cycles_matches_run_step_with_reactive_decisions(self):
        chunk = 4
        checkpoints = 6
        clock_period = 10

        # Oracle: same engine, driven one edge at a time via the
        # established _schedule_clock_events()+run_step() idiom.
        sim_step = Simulator(_parse(), engine="compiled")
        clk_step = Clock(sim_step.signal("clk"), period=clock_period)
        sim_step.fork(clk_step)
        max_time = (2 + checkpoints * chunk + 2) * clock_period
        sim_step.schedule_clock(clk_step, max_time)
        sim_step.drive("rst", Value(1, width=1))
        sim_step.drive("load_en", Value(0, width=1))

        def run_edges(n_cycles: int) -> None:
            for _ in range(n_cycles * 2):  # posedge + negedge per cycle
                assert sim_step.run_step()

        run_edges(2)
        sim_step.drive("rst", Value(0, width=1))

        results_step: list[int] = []
        total = 0
        for _ in range(checkpoints):
            load_val = _decide(total)
            sim_step.drive("load_en", Value(1, width=1))
            sim_step.drive("load_val", Value(load_val, width=8))
            run_edges(chunk)
            total = sim_step.read("acc").val
            results_step.append(total)

        # Candidate: run_cycles(), same reactive decision logic.
        sim = Simulator(_parse(), engine="compiled")
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=clock_period))
        sim.drive("rst", Value(1, width=1))
        sim.drive("load_en", Value(0, width=1))
        sim.run_cycles(2)  # reset for 2 cycles (also bootstraps + inits clk)
        sim.drive("rst", Value(0, width=1))

        results_cand: list[int] = []
        total = 0
        for _ in range(checkpoints):
            load_val = _decide(total)
            sim.drive("load_en", Value(1, width=1))
            sim.drive("load_val", Value(load_val, width=8))
            sim.run_cycles(chunk)
            total = sim.read("acc").val
            results_cand.append(total)

        assert results_cand == results_step

    def test_run_cycles_returns_completed_cycle_count(self):
        sim = Simulator(_parse(), engine="compiled")
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))
        sim.drive("rst", Value(1, width=1))
        sim.drive("load_en", Value(0, width=1))
        completed = sim.run_cycles(5)
        assert completed == 5

    def test_run_cycles_requires_compiled_engine(self):
        sim = Simulator(_parse(), engine="reference")
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))
        with pytest.raises(NotImplementedError):
            sim.run_cycles(5)

    def test_run_cycles_infers_clock_from_sole_forked_clock(self):
        sim = Simulator(_parse(), engine="compiled")
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=20))
        sim.drive("rst", Value(1, width=1))
        sim.drive("load_en", Value(0, width=1))
        sim.run_cycles(3)
        assert sim.read("rst").val == 1

    def test_run_cycles_requires_explicit_clock_with_zero_or_multiple_forks(self):
        sim = Simulator(_parse(), engine="compiled")
        with pytest.raises(ValueError):
            sim.run_cycles(5)

    def test_run_cycles_matches_batch_run_for_static_stimulus(self):
        """With no reactive decisions (static reset then free-run), the new
        wrapper's results must match calling the lower-level batch_run()
        directly with an equivalent event table.
        """
        sim_a = Simulator(_parse(), engine="compiled")
        clk_a = sim_a.signal("clk")
        sim_a.fork(Clock(clk_a, period=10))
        sim_a.drive("rst", Value(1, width=1))
        sim_a.drive("load_en", Value(0, width=1))
        sim_a.run_cycles(3)
        sim_a.drive("rst", Value(0, width=1))
        sim_a.run_cycles(20)

        sim_b = Simulator(_parse(), engine="compiled")
        sim_b.drive("rst", Value(1, width=1))
        sim_b.drive("clk", Value(0, width=1))
        sim_b.drive("load_en", Value(0, width=1))
        sim_b.batch_run(3, "clk", clock_period=10)
        sim_b.drive("rst", Value(0, width=1))
        sim_b.batch_run(20, "clk", clock_period=10)

        assert sim_a.read("acc").val == sim_b.read("acc").val

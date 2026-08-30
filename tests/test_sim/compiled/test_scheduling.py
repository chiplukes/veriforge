"""Compiled engine: delta-cycle scheduling and dirty-marking.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestPhase5Scheduler:
    """Test batch_run through CompiledScheduler."""

    def test_scheduler_batch_run(self):
        """CompiledScheduler.batch_run runs the counter correctly."""
        sim = Simulator(_make_counter(), engine="compiled")
        sim.drive("rst", Value(1, width=1))
        sim.drive("clk", Value(0, width=1))
        sim._sched._sim.snapshot()
        sim.drive("clk", Value(1, width=1))
        sim._sched._sim.step()

        sim.drive("rst", Value(0, width=1))
        sim._sched._sim.snapshot()
        sim.drive("clk", Value(0, width=1))
        sim._sched._sim.step()

        sim.batch_run(5, "clk", clock_period=10)

        v = sim.read("count")
        assert v.val == 5
        assert sim.time == 50

    def test_simulator_batch_run_not_compiled(self):
        """batch_run raises NotImplementedError for non-compiled engines."""
        sim = Simulator(_make_counter(), engine="vm")
        with pytest.raises(NotImplementedError, match="compiled"):
            sim.batch_run(10, "clk")

    def test_scheduler_batch_run_bad_clock(self):
        """batch_run raises ValueError for unknown clock signal."""
        sim = Simulator(_make_counter(), engine="compiled")
        with pytest.raises(ValueError, match="nonexistent"):
            sim.batch_run(10, "nonexistent")


class TestDirtyMarkingRegression:
    """Bug regression: compiled engine unconditionally marked signals dirty."""

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]  # noqa: RUF012

    def test_stable_combo_cross(self):
        """Stable combinational logic should produce identical results across engines."""
        for eng in self.ENGINES:
            sim = Simulator(_make_stable_combo(), engine=eng)
            sim.drive("a", Value(1, width=1))
            sim.run(max_time=0)

            assert int(sim.read("y")) == 1, f"{eng}: y should be 1"
            assert int(sim.read("z")) == 1, f"{eng}: z should be 1"

            # Change input
            sim.drive("a", Value(0, width=1))
            sim.run(max_time=10)

            assert int(sim.read("y")) == 0, f"{eng}: y should be 0 after a=0"
            assert int(sim.read("z")) == 0, f"{eng}: z should be 0 after a=0"


def _parse(src: str):
    from veriforge.transforms.tree_to_model import tree_to_design
    from veriforge.verilog_parser import verilog_parser

    tree = verilog_parser(start="source_text").build_tree(src)
    design = tree_to_design(tree, source_file="test.v")
    return next(m for m in design.modules if m.name == "t")


class TestClockSignalReadWithinItsOwnTriggeredBody:
    """Bug regression: reading a posedge/negedge TRIGGER signal from within
    the very body it triggered gave its stale PRE-edge value in the compiled
    engine, instead of the value that actually caused the edge.

    Root cause: `_seq_body_to_sv_reads` (`sim/compiled/_gen_sections.py`)
    rewrites ordinary signal reads inside a seq process body to read from
    `sv[]`/`sm[]` (a snapshot taken BEFORE the edge, so other registers'
    NBA-race-free pre-edge values are visible) -- correct for every signal
    EXCEPT the process's own edge-trigger signal(s), whose value has, by
    definition, already genuinely transitioned to the new state by the time
    the triggered body runs. This exception already existed for negedge
    sensitivity signals (async reset inputs, where reading the stale
    pre-transition value inside `if (!rst_n)` would wrongly skip the reset
    branch) but was never extended to the ordinary POSEDGE clock case --
    confirmed directly: `always @(posedge clk) o <= clk;` gave `o <= 0`
    (sv[]'s stale pre-edge value) instead of the correct `o <= 1`, on
    `compiled` only (`reference`/`vm`/`vm-fast` use a separate old-value
    slot just for edge detection and never shared this gap).
    """

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]  # noqa: RUF012

    def test_bare_clk_read_in_triggering_block(self):
        mod = _parse("""
            module t (input clk, output reg [2:0] o);
              always @(posedge clk) begin
                o <= clk;
              end
            endmodule
        """)
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            assert int(sim.read("o")) == 1, f"{eng}: o should capture clk's new (1) value at its own posedge"

    def test_clk_read_inside_concat_in_triggering_block(self):
        """Same bug, but with the trigger signal buried inside a larger
        expression (a concat) rather than read bare -- confirms the fix
        isn't accidentally scoped to only the trivial single-identifier RHS
        shape.
        """
        mod = _parse("""
            module t (input clk, output reg [2:0] o);
              always @(posedge clk) begin
                o <= {2'b0, clk};
              end
            endmodule
        """)
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            assert int(sim.read("o")) == 1, f"{eng}: o should capture clk's new (1) value at its own posedge"

    def test_non_trigger_signal_still_uses_pre_edge_snapshot(self):
        """Guard against an overcorrection: an ORDINARY (non-trigger) signal
        toggled in lockstep with clk must still read its pre-edge snapshot
        value where that's the correct NBA-race-free semantics -- this bug's
        fix must not turn into "never use sv[] at all".
        """
        mod = _parse("""
            module t (input clk, input d, output reg [2:0] o);
              always @(posedge clk) begin
                o <= d;
              end
            endmodule
        """)
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            sim.drive("clk", Value(0, width=1))
            sim.drive("d", Value(1, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            assert int(sim.read("o")) == 1, f"{eng}: o should capture d's driven value at the posedge"

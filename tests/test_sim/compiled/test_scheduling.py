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

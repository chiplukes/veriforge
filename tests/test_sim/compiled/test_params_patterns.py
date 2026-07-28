"""Compiled engine: parameter resolution and assignment-pattern fallback.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestParameterResolutionRegression:
    """Bug regression: parameters were resolving to 0 in compiled engine."""

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]  # noqa: RUF012

    def test_fsm_parameter_states_cross(self):
        """FSM with parameter-encoded states should cycle correctly across all engines."""
        for eng in self.ENGINES:
            sim = Simulator(_make_fsm_with_params(), engine=eng)
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim.drive("state", Value(0, width=2))  # start at IDLE

            # Run 3 full clock cycles (period=10 each, so 30 time units)
            sim.run(max_time=30)
            state_final = int(sim.read("state"))

            # After 3 cycles: IDLE→RUNNING→DONE→IDLE, should be back to 0
            assert state_final == 0, f"{eng}: expected IDLE=0 after 3 cycles, got {state_final}"


class TestAssignmentPatternFallback:
    """Regression: unresolved named assignment patterns must not silently emit zero."""

    def test_named_assignment_pattern_without_layout_raises(self):
        cg = CythonCodegen()
        expr = AssignmentPattern(named_pairs=[("foo", Literal(1, width=1, original_text="1'b1"))])

        with pytest.raises(NotImplementedError, match="assignment pattern"):
            cg._emit_assignment_pattern(expr, 1)

    def test_named_assignment_pattern_mask_without_layout_raises(self):
        cg = CythonCodegen()
        expr = AssignmentPattern(
            named_pairs=[("foo", Literal("x", width=1, base="b", is_x=True, original_text="1'bx"))]
        )

        with pytest.raises(NotImplementedError, match="assignment pattern"):
            cg._emit_assignment_pattern_mask(expr, 1)

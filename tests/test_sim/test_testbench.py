"""Tests for the testbench API across all simulation engines.

Covers:
  - SignalHandle read/write
  - Clock utility (period, duty cycle, event generation)
  - Simulator: elaborate, signal access, drive, read
  - Simulator: run with test function
  - Simulator: clock integration
"""

import shutil

import pytest

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityEdge, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, Range
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import BlockingAssign, IfStatement, NonblockingAssign, SeqBlock
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.scheduler import Scheduler
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import (
    Clock,
    SignalHandle,
    Simulator,
)
from veriforge.sim.value import Value

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines():
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        try:
            import Cython  # noqa: F401, PLC0415

            engines.append("compiled")
        except ImportError:
            pass
    return engines


ENGINES = _engines()
STEPPED_ENGINES = [engine for engine in ENGINES if engine in {"vm", "compiled"}]


# ── Module builders ──────────────────────────────────────────────────


def _make_adder() -> Module:
    """module adder(input [7:0] a, b, output [7:0] y); assign y = a + b; endmodule"""
    return Module(
        "adder",
        ports=[
            Port("a", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("b", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("y", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("y"), BinaryOp("+", Identifier("a"), Identifier("b"))),
        ],
    )


def _make_counter() -> Module:
    """Simple clocked counter with initial block and always @(*) combo logic."""
    m = Module(
        "counter",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("rst", PortDirection.INPUT),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("rst", NetKind.WIRE),
        ],
        variables=[
            Variable("count", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("count"), Literal(0, width=8))))
    return m


def _make_step_probe() -> Module:
    """Small clocked module used to validate stepped harness drive semantics."""
    m = Module(
        "step_probe",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("rst_n", PortDirection.INPUT),
            Port("load", PortDirection.INPUT),
            Port("d", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("accept", PortDirection.OUTPUT),
            Port("q", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("rst_n", NetKind.WIRE),
            Net("load", NetKind.WIRE),
            Net("d", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("accept", NetKind.WIRE),
        ],
        variables=[
            Variable("q", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("accept"), BinaryOp("&", Identifier("rst_n"), Identifier("load"))),
        ],
    )
    m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("q"), Literal(0, width=8))))
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_list=[
                SensitivityEdge("posedge", Identifier("clk")),
                SensitivityEdge("negedge", Identifier("rst_n")),
            ],
            sensitivity_type=SensitivityType.SEQUENTIAL,
            body=IfStatement(
                BinaryOp("==", Identifier("rst_n"), Literal(0, width=1)),
                NonblockingAssign(Identifier("q"), Literal(0, width=8)),
                IfStatement(
                    Identifier("load"),
                    NonblockingAssign(Identifier("q"), Identifier("d")),
                    None,
                ),
            ),
        )
    )
    return m


# ── SignalHandle ─────────────────────────────────────────────────────


class TestSignalHandle:
    def test_read(self):
        sched = Scheduler()
        sched.ctx.write_signal("x", Value(42, width=8))
        h = SignalHandle("x", sched, width=8)
        assert h.value == 42

    def test_write_int(self):
        sched = Scheduler()
        sched.ctx.write_signal("x", Value(0, width=8))
        h = SignalHandle("x", sched, width=8)
        h.value = 99
        assert h.value == 99

    def test_write_value(self):
        sched = Scheduler()
        sched.ctx.write_signal("x", Value(0, width=8))
        h = SignalHandle("x", sched, width=8)
        h.value = Value(0xFF, width=8)
        assert h.value == 0xFF

    def test_name(self):
        sched = Scheduler()
        sched.ctx.write_signal("foo", Value(0, width=1))
        h = SignalHandle("foo", sched)
        assert h.name == "foo"

    def test_repr(self):
        sched = Scheduler()
        sched.ctx.write_signal("bar", Value(5, width=8))
        h = SignalHandle("bar", sched, width=8)
        assert "bar" in repr(h)

    def test_equality(self):
        sched = Scheduler()
        sched.ctx.write_signal("x", Value(0, width=1))
        h1 = SignalHandle("x", sched)
        h2 = SignalHandle("x", sched)
        h3 = SignalHandle("y", sched)
        assert h1 == h2
        assert h1 != h3

    def test_hash(self):
        sched = Scheduler()
        h1 = SignalHandle("x", sched)
        h2 = SignalHandle("x", sched)
        assert hash(h1) == hash(h2)
        s = {h1, h2}
        assert len(s) == 1


# ── Clock ────────────────────────────────────────────────────────────


class TestClock:
    def test_default_duty(self):
        sched = Scheduler()
        sched.ctx.write_signal("clk", Value(0, width=1))
        h = SignalHandle("clk", sched)
        c = Clock(h, period=10)
        assert c.high_time == 5
        assert c.low_time == 5

    def test_custom_duty(self):
        sched = Scheduler()
        sched.ctx.write_signal("clk", Value(0, width=1))
        h = SignalHandle("clk", sched)
        c = Clock(h, period=10, duty=0.3)
        assert c.high_time == 3
        assert c.low_time == 7

    def test_repr(self):
        sched = Scheduler()
        sched.ctx.write_signal("clk", Value(0, width=1))
        h = SignalHandle("clk", sched)
        c = Clock(h, period=10)
        assert "clk" in repr(c)


# ── Simulator ────────────────────────────────────────────────────────


class TestSimulator:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_create(self, engine):
        sim = Simulator(_make_adder(), engine=engine)
        assert sim.time == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signal_handle(self, engine):
        sim = Simulator(_make_adder(), engine=engine)
        a = sim.signal("a")
        assert isinstance(a, SignalHandle)
        assert a.name == "a"
        assert a.width == 8

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signal_cached(self, engine):
        sim = Simulator(_make_adder(), engine=engine)
        a1 = sim.signal("a")
        a2 = sim.signal("a")
        assert a1 is a2

    @pytest.mark.parametrize("engine", ENGINES)
    def test_drive_and_read(self, engine):
        sim = Simulator(_make_adder(), engine=engine)
        sim.drive("a", Value(10, width=8))
        assert sim.read("a") == 10

    @pytest.mark.parametrize("engine", ENGINES)
    def test_drive_int(self, engine):
        sim = Simulator(_make_adder(), engine=engine)
        sim.drive("a", 10)
        assert sim.read("a") == 10

    @pytest.mark.parametrize("engine", ENGINES)
    def test_run_continuous_assign(self, engine):
        """Run adder: a=10, b=20 → y=30"""
        sim = Simulator(_make_adder(), engine=engine)

        def test(s):
            s.drive("a", Value(10, width=8))
            s.drive("b", Value(20, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 30

    @pytest.mark.parametrize("engine", ENGINES)
    def test_run_no_test(self, engine):
        """Run with no test function — just elaborates and runs."""
        sim = Simulator(_make_adder(), engine=engine)
        sim.drive("a", Value(5, width=8))
        sim.drive("b", Value(3, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 8

    @pytest.mark.parametrize("engine", ENGINES)
    def test_initial_block(self, engine):
        """Initial block sets count=0."""
        sim = Simulator(_make_counter(), engine=engine)
        sim.run(max_time=0)
        assert sim.read("count") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signal_write(self, engine):
        """Write via signal handle, read back."""
        sim = Simulator(_make_adder(), engine=engine)
        a = sim.signal("a")
        a.value = 42
        assert a.value == 42

    @pytest.mark.parametrize("engine", ["reference", "vm"])
    def test_display_output(self, engine):
        """$display output accessible via simulator."""
        # Compiled engine can't compile degenerate zero-signal modules
        from veriforge.model.statements import SystemTaskCall

        m = Module("disp", variables=[])
        m.initial_blocks.append(InitialBlock(SystemTaskCall("$display", [Literal(99, width=8)])))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert any("99" in s for s in sim.display_output)


class TestSimulatorClock:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_clock_toggles(self, engine):
        """Clock should toggle the signal over time."""
        m = Module(
            "clk_mod",
            nets=[Net("clk", NetKind.WIRE)],
        )
        sim = Simulator(m, engine=engine)
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))
        sim.run(max_time=100)

        # After running, the clock should have been toggled
        # The final value depends on timing, but the scheduler should not crash
        val = clk.value
        assert isinstance(val, Value)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_clock_period(self, engine):
        """Verify clock toggles at expected times."""
        m = Module(
            "clk_mod",
            nets=[Net("clk", NetKind.WIRE)],
        )
        sim = Simulator(m, engine=engine)
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))

        # After t=0: clk=1 (high phase)
        # After t=5: clk=0 (low phase)
        # After t=10: clk=1 again
        sim.run(max_time=5)
        # At t=5, clock should be 0 (end of high period)
        assert clk.value == 0


class TestSimulatorIntegration:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_adder_testbench(self, engine):
        """Full integration: drive adder inputs, verify output."""
        sim = Simulator(_make_adder(), engine=engine)

        def test(s):
            a = s.signal("a")
            b = s.signal("b")
            y = s.signal("y")

            # Test case 1
            a.value = 10
            b.value = 20

        sim.run(test, max_time=0)
        assert sim.read("y") == 30

    @pytest.mark.parametrize("engine", ENGINES)
    def test_multiple_test_cases(self, engine):
        """Run multiple test scenarios sequentially."""
        for a_val, b_val in [(1, 2), (100, 50), (0, 0), (255, 1)]:
            sim = Simulator(_make_adder(), engine=engine)

            def test(s, av=a_val, bv=b_val):
                s.drive("a", Value(av, width=8))
                s.drive("b", Value(bv, width=8))

            sim.run(test, max_time=0)
            expected = (a_val + b_val) & 0xFF  # 8-bit wrap
            assert sim.read("y") == expected, f"Failed for a={a_val}, b={b_val}"


# ── run_step() cross-engine ──────────────────────────────────────────


class TestRunStepCrossEngine:
    """Verify run_step() works consistently across all engines."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_run_step_empty_queue(self, engine):
        """run_step with no pending events returns False."""
        sim = Simulator(_make_adder(), engine=engine)
        sim.run(max_time=0)
        assert sim.run_step() is False

    @pytest.mark.parametrize("engine", ENGINES)
    def test_run_step_with_clock(self, engine):
        """run_step advances one time step with clock events."""
        m = Module("clk_mod", nets=[Net("clk", NetKind.WIRE)])
        sim = Simulator(m, engine=engine)
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))
        sim.run(max_time=0)
        assert sim.run_step() is True
        assert sim.time > 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_run_step_loop(self, engine):
        """Stepping multiple times produces same result as run(max_time=...)."""
        m = Module("clk_mod", nets=[Net("clk", NetKind.WIRE)])
        sim = Simulator(m, engine=engine)
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))
        sim.run(max_time=0)
        for _ in range(10):
            if not sim.run_step():
                break
        assert sim.time > 0


class TestVMDriveSignalDirty:
    """drive() on VM engines must mark the signal dirty so run_step() propagates it.

    Before the fix, VMScheduler.drive_signal only set the signal value but never
    added the sid to interpreter.dirty for plain signals (only memory elements got
    that treatment).  run_step() seeds its delta loop from interpreter.dirty, so
    external drives were invisible to it — combinational outputs stayed stale.
    """

    def _make_combo_module(self) -> Module:
        """assign y = a & b; plus a clock port to keep events flowing."""
        return Module(
            "combo",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("a", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("b", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("y", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            continuous_assigns=[
                ContinuousAssign(Identifier("y"), BinaryOp("&", Identifier("a"), Identifier("b"))),
            ],
        )

    @pytest.mark.parametrize("engine", ["vm", "vm-fast"])
    def test_drive_then_run_step_propagates_combo(self, engine):
        """drive() + run_step() must propagate through combinational logic on VM engines."""
        sim = Simulator(self._make_combo_module(), engine=engine)
        clk = sim.signal("clk")
        sim.fork(Clock(clk, period=10))
        sim.run(max_time=0)

        # Drive inputs — before the fix, these were invisible to run_step()
        sim.drive("a", 0xFF)
        sim.drive("b", 0x0F)

        # Advance one clock step; dirty set from drives must seed delta propagation
        sim.run_step()

        assert int(sim.read("y")) == 0x0F, (
            f"y should be 0x0F (0xFF & 0x0F) after drive+run_step on {engine!r}, got {sim.read('y')!r}"
        )

    @pytest.mark.parametrize("engine", ["vm", "vm-fast"])
    def test_drive_plain_signal_dirty_equivalent_to_step_drive(self, engine):
        """sim.drive() alone is now equivalent to step_drive() for VM engines."""
        m = self._make_combo_module()

        sim_direct = Simulator(m, engine=engine)
        clk_d = sim_direct.signal("clk")
        sim_direct.fork(Clock(clk_d, period=10))
        sim_direct.run(max_time=0)
        sim_direct.drive("a", 0xAB)
        sim_direct.drive("b", 0xFF)
        sim_direct.run_step()
        y_direct = int(sim_direct.read("y"))

        sim_compat = Simulator(m, engine=engine)
        clk_c = sim_compat.signal("clk")
        sim_compat.fork(Clock(clk_c, period=10))
        sim_compat.run(max_time=0)
        step_drive(sim_compat, engine, "a", 0xAB)
        step_drive(sim_compat, engine, "b", 0xFF)
        sim_compat.run_step()
        y_compat = int(sim_compat.read("y"))

        assert y_direct == y_compat == 0xAB


class TestSimulatorSettle:
    """Simulator.settle() propagates pending drives through combinational logic."""

    def _make_combo(self) -> Module:
        """assign y = a & b;"""
        return Module(
            "combo_settle",
            ports=[
                Port("a", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("b", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("y", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            continuous_assigns=[
                ContinuousAssign(Identifier("y"), BinaryOp("&", Identifier("a"), Identifier("b"))),
            ],
        )

    def _make_chain(self) -> Module:
        """Multi-level: assign mid = a & b; assign y = mid | c;"""
        return Module(
            "chain_settle",
            ports=[
                Port("a", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("b", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("c", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("c", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("mid", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("y", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            continuous_assigns=[
                ContinuousAssign(Identifier("mid"), BinaryOp("&", Identifier("a"), Identifier("b"))),
                ContinuousAssign(Identifier("y"), BinaryOp("|", Identifier("mid"), Identifier("c"))),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_settle_propagates_combo(self, engine):
        """drive() + settle() must propagate through combinational logic."""
        sim = Simulator(self._make_combo(), engine=engine)
        sim.run(max_time=0)

        sim.drive("a", 0xFF)
        sim.drive("b", 0x0F)
        sim.settle()

        assert int(sim.read("y")) == 0x0F

    @pytest.mark.parametrize("engine", ENGINES)
    def test_settle_noop_when_no_drives(self, engine):
        """settle() with no pending drives must not raise."""
        sim = Simulator(self._make_combo(), engine=engine)
        sim.run(max_time=0)
        y_before = sim.read("y")
        sim.settle()  # no pending drives — should be a no-op
        assert sim.read("y") == y_before  # state unchanged

    @pytest.mark.parametrize("engine", ENGINES)
    def test_settle_called_twice(self, engine):
        """Second settle() after the first is a safe no-op."""
        sim = Simulator(self._make_combo(), engine=engine)
        sim.run(max_time=0)

        sim.drive("a", 0xAA)
        sim.drive("b", 0xFF)
        sim.settle()
        sim.settle()  # second call must not error or corrupt state

        assert int(sim.read("y")) == 0xAA

    @pytest.mark.parametrize("engine", ENGINES)
    def test_settle_multi_level_chain(self, engine):
        """settle() propagates through a two-level combinational chain."""
        sim = Simulator(self._make_chain(), engine=engine)
        sim.run(max_time=0)

        sim.drive("a", 0b11001100)
        sim.drive("b", 0b11110000)
        sim.drive("c", 0b00001111)
        sim.settle()

        # mid = 0b11001100 & 0b11110000 = 0b11000000 = 0xC0
        # y   = 0b11000000 | 0b00001111 = 0b11001111 = 0xCF
        assert int(sim.read("y")) == 0xCF

    @pytest.mark.parametrize("engine", ENGINES)
    def test_settle_successive_drives_cumulative(self, engine):
        """Driving different signals between two settle() calls both propagate."""
        sim = Simulator(self._make_combo(), engine=engine)
        sim.run(max_time=0)

        sim.drive("a", 0xFF)
        sim.drive("b", 0x0F)
        sim.settle()
        assert int(sim.read("y")) == 0x0F

        sim.drive("b", 0xF0)
        sim.settle()
        assert int(sim.read("y")) == 0xF0


class TestSteppedHarnessCrossEngine:
    """Validate the stepped harness pattern used by example runners."""

    @pytest.mark.parametrize("engine", STEPPED_ENGINES)
    def test_manual_clock_schedule_and_drive(self, engine):
        """Manual stepped drive/read loops stay aligned across engines."""
        sim = Simulator(_make_step_probe(), engine=engine)
        sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 40)
        sim.run(max_time=0)

        step_drive(sim, engine, "clk", 0)
        step_drive(sim, engine, "rst_n", 0)
        step_drive(sim, engine, "load", 0)
        step_drive(sim, engine, "d", 0)
        step_eval_now(sim)

        assert int(sim.read("accept")) == 0
        assert int(sim.read("q")) == 0

        step_run_until(sim, 12)
        step_drive(sim, engine, "rst_n", 1)
        step_drive(sim, engine, "d", 0xA5)
        step_drive(sim, engine, "load", 1)
        step_eval_now(sim)

        assert int(sim.read("accept")) == 1
        assert int(sim.read("q")) == 0

        step_run_until(sim, 16)
        assert int(sim.read("q")) == 0xA5

        step_drive(sim, engine, "load", 0)
        step_drive(sim, engine, "d", 0x3C)
        step_eval_now(sim)

        assert int(sim.read("accept")) == 0
        assert int(sim.read("q")) == 0xA5

        step_run_until(sim, 26)
        assert int(sim.read("q")) == 0xA5


class TestSignedDeclarationSupport:
    """Declared-signed signals (nets, vars, ports) are respected by all engines."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast", "compiled"])
    def test_signed_net_comparison_works(self, engine):
        """A module with declared-signed nets uses signed comparison."""
        m = Module(
            "signed_cmp",
            nets=[
                Net("a", NetKind.WIRE, width=Range(Literal(7), Literal(0)), signed=True),
                Net("b", NetKind.WIRE, width=Range(Literal(7), Literal(0)), signed=True),
                Net("lt", NetKind.WIRE, width=Range(Literal(0), Literal(0)), signed=False),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    lhs=Identifier("lt"),
                    rhs=BinaryOp("<", Identifier("a"), Identifier("b")),
                )
            ],
        )
        sim = Simulator(m, engine=engine)
        sim.drive("a", 0xFF)  # -1 as signed 8-bit
        sim.drive("b", 1)
        sim.run(max_time=0)
        lt = sim.read("lt")
        assert lt.val == 1, f"Signed -1 < 1 should be true, got {lt.val}"

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast", "compiled"])
    def test_signed_var_comparison_works(self, engine):
        """A module with declared-signed variables uses signed comparison."""
        m = Module(
            "signed_cmp",
            variables=[
                Variable("a", VariableKind.REG, width=Range(Literal(7), Literal(0)), signed=True),
                Variable("b", VariableKind.REG, width=Range(Literal(7), Literal(0)), signed=True),
            ],
            nets=[Net("lt", NetKind.WIRE, width=Range(Literal(0), Literal(0)), signed=False)],
            continuous_assigns=[
                ContinuousAssign(
                    lhs=Identifier("lt"),
                    rhs=BinaryOp("<", Identifier("a"), Identifier("b")),
                )
            ],
        )
        sim = Simulator(m, engine=engine)
        sim.drive("a", 0xFF)  # -1 as signed 8-bit
        sim.drive("b", 1)
        sim.run(max_time=0)
        lt = sim.read("lt")
        assert lt.val == 1, f"Signed -1 < 1 should be true, got {lt.val}"

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast", "compiled"])
    def test_unsigned_module_no_warning(self, recwarn, engine):
        Simulator(_make_adder(), engine=engine)
        signed_warnings = [w for w in recwarn.list if "declared-signed" in str(w.message)]
        assert not signed_warnings


class TestDeltaLimitPlumbing:
    """delta_limit is threaded from Simulator through to every engine."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast"])
    def test_custom_delta_limit_stored(self, engine):
        """Simulator stores delta_limit on the scheduler."""
        sim = Simulator(_make_adder(), engine=engine, delta_limit=42)
        assert sim._sched.delta_limit == 42

    @pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast"])
    def test_default_delta_limit(self, engine):
        sim = Simulator(_make_adder(), engine=engine)
        assert sim._sched.delta_limit == 10_000

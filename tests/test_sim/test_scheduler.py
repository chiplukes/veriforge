"""Tests for the simulation scheduler.

Covers:
  - EventQueue ordering and pop
  - Elaboration (signal initialization, process creation)
  - Continuous assign evaluation + propagation
  - Always block (combinational) re-evaluation
  - Initial block execution
  - Delta cycles (combinational chain)
  - NBA semantics in scheduler context
  - $finish halts simulation
  - Edge-triggered always blocks
  - Max-time limit
"""

import pytest

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, Range
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    IfStatement,
    NonblockingAssign,
    SeqBlock,
    SensitivityEdge,
    SystemTaskCall,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.scheduler import (
    AlwaysProcess,
    ContinuousProcess,
    EventQueue,
    InitialProcess,
    Process,
    Scheduler,
    _collect_reads,
    _range_width,
)
from veriforge.sim.value import Value


# ── EventQueue ───────────────────────────────────────────────────────


class TestEventQueue:
    def test_empty(self):
        q = EventQueue()
        assert q.is_empty()
        assert q.peek_time() is None
        assert len(q) == 0

    def test_schedule_and_pop(self):
        q = EventQueue()
        p1 = Process()
        p2 = Process()
        q.schedule(10, p1)
        q.schedule(10, p2)
        q.schedule(20, Process())

        assert q.peek_time() == 10
        assert len(q) == 3

        procs = q.pop_at(10)
        assert len(procs) == 2
        assert p1 in procs
        assert p2 in procs

        assert q.peek_time() == 20
        assert len(q) == 1

    def test_ordering(self):
        q = EventQueue()
        p5 = Process()
        p3 = Process()
        p1 = Process()
        q.schedule(5, p5)
        q.schedule(3, p3)
        q.schedule(1, p1)

        assert q.pop_at(1) == [p1]
        assert q.pop_at(3) == [p3]
        assert q.pop_at(5) == [p5]
        assert q.is_empty()

    def test_pop_at_wrong_time(self):
        q = EventQueue()
        q.schedule(10, Process())
        assert q.pop_at(5) == []
        assert len(q) == 1  # still there


# ── Helper functions ─────────────────────────────────────────────────


class TestHelpers:
    def test_range_width_none(self):
        assert _range_width(None) == 1

    def test_range_width_literal(self):
        r = Range(Literal(7, width=32), Literal(0, width=32))
        assert _range_width(r) == 8

    def test_collect_reads_simple(self):
        expr = BinaryOp("+", Identifier("a"), Identifier("b"))
        assert _collect_reads(expr) == {"a", "b"}

    def test_collect_reads_literal(self):
        expr = Literal(42, width=8)
        assert _collect_reads(expr) == set()

    def test_collect_reads_nested(self):
        expr = BinaryOp(
            "&",
            BinaryOp("+", Identifier("a"), Identifier("b")),
            Identifier("c"),
        )
        assert _collect_reads(expr) == {"a", "b", "c"}


# ── Module helpers ───────────────────────────────────────────────────


def _make_simple_module() -> Module:
    """Create: module top(input [7:0] a, b, output [7:0] y); assign y = a + b; endmodule"""
    m = Module(
        "top",
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
    return m


def _make_chain_module() -> Module:
    """Create: assign b = a; assign c = b;  (2-stage combinational chain)"""
    m = Module(
        "chain",
        nets=[
            Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("c", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("b"), Identifier("a")),
            ContinuousAssign(Identifier("c"), Identifier("b")),
        ],
    )
    return m


def _make_always_comb_module() -> Module:
    """Create: always @(*) y = a & b;"""
    m = Module(
        "comb",
        nets=[
            Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("y", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.always_blocks.append(
        AlwaysBlock(
            BlockingAssign(Identifier("y"), BinaryOp("&", Identifier("a"), Identifier("b"))),
            sensitivity_type=SensitivityType.COMBINATIONAL,
        )
    )
    return m


def _make_initial_module() -> Module:
    """Create: initial begin x = 0; y = 42; end"""
    m = Module(
        "init_mod",
        variables=[
            Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Variable("y", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(Identifier("x"), Literal(0, width=8)),
                    BlockingAssign(Identifier("y"), Literal(42, width=8)),
                ]
            )
        )
    )
    return m


# ── Elaboration ──────────────────────────────────────────────────────


class TestElaboration:
    def test_signal_init_to_x(self):
        sched = Scheduler()
        m = _make_simple_module()
        sched.elaborate(m)
        # All signals initialized to x
        for name in ["a", "b", "y"]:
            v = sched.read_signal(name)
            assert v.width == 8
            assert not v.is_defined  # x

    def test_continuous_processes_created(self):
        sched = Scheduler()
        sched.elaborate(_make_simple_module())
        assert len(sched._continuous_procs) == 1

    def test_always_processes_created(self):
        sched = Scheduler()
        sched.elaborate(_make_always_comb_module())
        assert len(sched._always_procs) == 1

    def test_initial_processes_created(self):
        sched = Scheduler()
        sched.elaborate(_make_initial_module())
        assert len(sched._initial_procs) == 1

    def test_sensitivity_registered(self):
        sched = Scheduler()
        sched.elaborate(_make_simple_module())
        # "a" and "b" should have the continuous proc registered
        assert "a" in sched._sig_to_procs
        assert "b" in sched._sig_to_procs
        assert len(sched._sig_to_procs["a"]) == 1
        assert len(sched._sig_to_procs["b"]) == 1


# ── Continuous Assign ────────────────────────────────────────────────


class TestContinuousAssign:
    def test_basic_eval(self):
        """assign y = a + b; with a=5, b=3 → y=8"""
        sched = Scheduler()
        sched.elaborate(_make_simple_module())

        # Drive inputs
        sched.drive_signal("a", Value(5, width=8))
        sched.drive_signal("b", Value(3, width=8))

        # Run continuous assigns
        sched._run_continuous_assigns()

        assert sched.read_signal("y") == 8

    def test_chain_propagation(self):
        """assign b = a; assign c = b; → drive a=42, after eval c=42"""
        sched = Scheduler()
        sched.elaborate(_make_chain_module())

        sched.drive_signal("a", Value(42, width=8))

        # First eval: a→b
        sched._run_continuous_assigns()
        # Second eval: b→c (chain propagation)
        sched._run_continuous_assigns()

        assert sched.read_signal("b") == 42
        assert sched.read_signal("c") == 42


# ── Initial Block ────────────────────────────────────────────────────


class TestInitialBlock:
    def test_initial_execution(self):
        sched = Scheduler()
        sched.elaborate(_make_initial_module())
        sched.run(max_time=100)

        assert sched.read_signal("x") == 0
        assert sched.read_signal("y") == 42

    def test_initial_with_display(self):
        """initial $display(42);"""
        m = Module("disp_mod", variables=[])
        m.initial_blocks.append(InitialBlock(SystemTaskCall("$display", [Literal(42, width=8)])))
        sched = Scheduler()
        sched.elaborate(m)
        sched.run(max_time=100)
        assert any("42" in s for s in sched.display_output)


# ── Always Combinational ─────────────────────────────────────────────


class TestAlwaysCombinational:
    def test_comb_evaluation(self):
        """always @(*) y = a & b; with a=0xFF, b=0x0F → y=0x0F"""
        sched = Scheduler()
        sched.elaborate(_make_always_comb_module())

        sched.drive_signal("a", Value(0xFF, width=8))
        sched.drive_signal("b", Value(0x0F, width=8))

        # Execute the always block
        proc = sched._always_procs[0]
        sched._execute_process(proc)

        assert sched.read_signal("y") == 0x0F


# ── $finish ──────────────────────────────────────────────────────────


class TestFinish:
    def test_finish_stops_simulation(self):
        """initial begin x = 1; $finish; x = 2; end → x should be 1"""
        m = Module(
            "fin_mod",
            variables=[
                Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("x"), Literal(1, width=8)),
                        SystemTaskCall("$finish"),
                        BlockingAssign(Identifier("x"), Literal(2, width=8)),
                    ]
                )
            )
        )
        sched = Scheduler()
        sched.elaborate(m)
        sched.run(max_time=100)
        assert sched.read_signal("x") == 1


# ── NBA in scheduled context ─────────────────────────────────────────


class TestNbaScheduler:
    def test_nba_applied_after_active(self):
        """initial begin x <= 42; end → after run, x=42"""
        m = Module(
            "nba_mod",
            variables=[
                Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(InitialBlock(NonblockingAssign(Identifier("x"), Literal(42, width=8))))
        sched = Scheduler()
        sched.elaborate(m)
        sched.run(max_time=100)
        assert sched.read_signal("x") == 42


# ── Delay control ────────────────────────────────────────────────────


class TestDelayScheduling:
    def test_delay_in_initial(self):
        """initial begin x = 1; #10 x = 2; end
        After run: x=1 at t<10, x should change at t=10.
        Our executor raises SuspendExecution on #10, so x stays at 1
        and the process gets rescheduled. But the body after the delay
        is not yet supported (needs continuation). For now, verify that
        x gets set to 1 and the scheduler doesn't crash.
        """
        m = Module(
            "delay_mod",
            variables=[
                Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("x"), Literal(1, width=8)),
                        DelayControl(Literal(10, width=32)),
                    ]
                )
            )
        )
        sched = Scheduler()
        sched.elaborate(m)
        sched.run(max_time=100)
        # x should be 1 (delay suspends before any further statement)
        assert sched.read_signal("x") == 1

    def test_timed_events_advance(self):
        """Scheduler should advance time to the delay event."""
        sched = Scheduler()
        m = Module("adv_mod", variables=[])
        m.initial_blocks.append(InitialBlock(DelayControl(Literal(50, width=32))))
        sched.elaborate(m)
        sched.run(max_time=100)
        # Time should have advanced to 50
        assert sched.time >= 50


# ── Drive + continuous assign integration ────────────────────────────


class TestDriveIntegration:
    def test_drive_and_run(self):
        """Drive inputs, run simulation, read outputs."""
        sched = Scheduler()
        sched.elaborate(_make_simple_module())

        sched.drive_signal("a", Value(10, width=8))
        sched.drive_signal("b", Value(20, width=8))
        sched.run(max_time=0)

        assert sched.read_signal("y") == 30

    def test_drive_integer(self):
        """drive_signal accepts plain int."""
        sched = Scheduler()
        sched.elaborate(_make_simple_module())
        sched.drive_signal("a", 10)
        assert sched.read_signal("a") == 10


# ── Edge sensitivity tracking ────────────────────────────────────────


class TestEdgeSensitivity:
    def test_sequential_always_edges(self):
        """always @(posedge clk) q <= d; — should have edge info."""
        m = Module(
            "seq_mod",
            nets=[
                Net("clk", NetKind.WIRE),
                Net("d", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            variables=[
                Variable("q", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.always_blocks.append(
            AlwaysBlock(
                NonblockingAssign(Identifier("q"), Identifier("d")),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            )
        )
        sched = Scheduler()
        sched.elaborate(m)

        assert len(sched._always_procs) == 1
        proc = sched._always_procs[0]
        assert "clk" in proc.sensitivity
        assert proc.edge_signals.get("clk") == "posedge"

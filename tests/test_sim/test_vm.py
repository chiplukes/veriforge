"""Tests for the bytecode VM simulation engine.

Covers:
  - Compiler: signal registration, constant pool, expression/statement compilation
  - Interpreter: instruction execution for all opcode categories
  - VMScheduler: elaboration, continuous assign, combo always, sequential always
  - Cross-validation: same design run through reference and VM engines → same result
  - Simulator integration: engine="vm" parameter
"""

import pytest
import warnings

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import BinaryOp, BitSelect, Concatenation, Identifier, Literal, Range, UnaryOp
from veriforge.model.expressions import PartSelect, RangeSelect, Replication, TernaryOp
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    DisableStatement,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    SensitivityEdge,
    SystemTaskCall,
    WhileLoop,
)
from veriforge.model.expressions import FunctionCall
from veriforge.model.expressions import StringLiteral
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.testbench import Clock, SignalHandle, Simulator
from veriforge.sim.value import Value
from veriforge.sim.vm.compiler import CompiledProcess, Compiler, ProcessType
from veriforge.sim.vm.interpreter import Interpreter, StopSimulation, _format_display
from veriforge.sim.vm.opcodes import Op, instr
from veriforge.sim.vm.vm_scheduler import VMScheduler


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


def _make_inverter() -> Module:
    """module inv(input [7:0] a, output [7:0] y); assign y = ~a; endmodule"""
    return Module(
        "inv",
        ports=[
            Port("a", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("y", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("y"), UnaryOp("~", Identifier("a"))),
        ],
    )


def _make_mux() -> Module:
    """module mux(input sel, input [7:0] a, b, output reg [7:0] y);
    always @(*)
      if (sel) y = a; else y = b;
    endmodule"""
    m = Module(
        "mux",
        ports=[
            Port("sel", PortDirection.INPUT),
            Port("a", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("b", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("sel", NetKind.WIRE),
            Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("y", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=IfStatement(
                Identifier("sel"),
                BlockingAssign(Identifier("y"), Identifier("a")),
                BlockingAssign(Identifier("y"), Identifier("b")),
            ),
        )
    )
    return m


def _make_counter() -> Module:
    """Simple clocked counter: always @(posedge clk) count <= count + 1;"""
    m = Module(
        "counter",
        ports=[
            Port("clk", PortDirection.INPUT),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
        ],
        variables=[
            Variable("count", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    # Initial block: count = 0
    m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("count"), Literal(0, width=8))))
    # Always block: @(posedge clk) count <= count + 1
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
            body=NonblockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
    )
    return m


def _make_display() -> Module:
    """Module with $display in initial block."""
    m = Module("disp", variables=[])
    m.initial_blocks.append(InitialBlock(SystemTaskCall("$display", [Literal(42, width=8)])))
    return m


# ── Compiler Tests ───────────────────────────────────────────────────


class TestCompiler:
    def test_signal_registration(self):
        """Compiler assigns integer IDs to all signals."""
        c = Compiler()
        c.compile_module(_make_adder())
        assert "a" in c.signal_map
        assert "b" in c.signal_map
        assert "y" in c.signal_map
        assert len(c.signal_map) == 3
        # IDs are distinct non-negative ints
        ids = set(c.signal_map.values())
        assert len(ids) == 3
        assert all(i >= 0 for i in ids)

    def test_signal_widths(self):
        """Signal widths are captured correctly."""
        c = Compiler()
        c.compile_module(_make_adder())
        for name in ("a", "b", "y"):
            sid = c.signal_map[name]
            assert c.sig_width[sid] == 8

    def test_continuous_assign_compiled(self):
        """Continuous assigns produce compiled processes."""
        c = Compiler()
        c.compile_module(_make_adder())
        ca_procs = [p for p in c.processes if p.process_type == ProcessType.CONTINUOUS]
        assert len(ca_procs) == 1

    def test_always_block_compiled(self):
        """Always @(*) produces a combinational compiled process."""
        c = Compiler()
        c.compile_module(_make_mux())
        combo = [p for p in c.processes if p.process_type == ProcessType.COMBINATIONAL]
        assert len(combo) == 1

    def test_sequential_block_compiled(self):
        """Always @(posedge clk) produces a sequential compiled process."""
        c = Compiler()
        c.compile_module(_make_counter())
        seq = [p for p in c.processes if p.process_type == ProcessType.SEQUENTIAL]
        assert len(seq) == 1
        # Should have edge_signals for clk
        proc = seq[0]
        assert len(proc.edge_signals) > 0

    def test_initial_block_compiled(self):
        """Initial blocks produce compiled processes."""
        c = Compiler()
        c.compile_module(_make_counter())
        init = [p for p in c.processes if p.process_type == ProcessType.INITIAL]
        assert len(init) == 1

    def test_constant_pool(self):
        """Literals are added to the constant pool."""
        c = Compiler()
        c.compile_module(_make_adder())
        # The adder has no explicit literals, but the counter does
        c2 = Compiler()
        c2.compile_module(_make_counter())
        assert len(c2.const_pool) > 0

    def test_program_has_proc_end(self):
        """Every compiled program ends with PROC_END."""
        c = Compiler()
        c.compile_module(_make_adder())
        for proc in c.processes:
            assert proc.program[-1][0] == Op.PROC_END

    def test_sensitivity_for_combo(self):
        """Combinational always block has sensitivity to its input signals."""
        c = Compiler()
        c.compile_module(_make_mux())
        combo = [p for p in c.processes if p.process_type == ProcessType.COMBINATIONAL]
        assert len(combo) == 1
        proc = combo[0]
        # Should be sensitive to sel, a, b (all inputs read in the body)
        sensitive_names = {name for name, sid in c.signal_map.items() if sid in proc.sensitivity}
        assert "sel" in sensitive_names
        assert "a" in sensitive_names
        assert "b" in sensitive_names


# ── Interpreter Tests ────────────────────────────────────────────────


class TestInterpreter:
    def _make_interp(self, n_sigs: int = 4, consts: list | None = None) -> Interpreter:
        """Create an interpreter with n_sigs signals, all 8-bit."""
        sig_val = [0] * n_sigs
        sig_mask = [0] * n_sigs
        sig_width = [8] * n_sigs
        const_pool = consts or []
        return Interpreter(sig_val, sig_mask, sig_width, const_pool)

    def test_load_store_sig(self):
        """LOAD_SIG / STORE_SIG round trip."""
        interp = self._make_interp()
        interp.sig_val[0] = 42
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.STORE_SIG, 1),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[1] == 42

    def test_load_const(self):
        """LOAD_CONST pushes a constant value."""
        c = Value(77, width=8)
        interp = self._make_interp(consts=[c])
        program = [
            instr(Op.LOAD_CONST, 0),
            instr(Op.STORE_SIG, 0),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[0] == 77

    def test_add(self):
        """ADD two signal values."""
        interp = self._make_interp()
        interp.sig_val[0] = 10
        interp.sig_val[1] = 20
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.LOAD_SIG, 1),
            instr(Op.ADD),
            instr(Op.STORE_SIG, 2),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[2] == 30

    def test_sub(self):
        """SUB two values."""
        interp = self._make_interp()
        interp.sig_val[0] = 50
        interp.sig_val[1] = 20
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.LOAD_SIG, 1),
            instr(Op.SUB),
            instr(Op.STORE_SIG, 2),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[2] == 30

    def test_bit_not(self):
        """BIT_NOT inverts all bits."""
        interp = self._make_interp()
        interp.sig_val[0] = 0x0F  # 00001111
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.BIT_NOT),
            instr(Op.STORE_SIG, 1),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[1] == 0xF0  # 11110000 in 8-bit

    def test_cmp_eq_true(self):
        """CMP_EQ pushes 1 when equal."""
        interp = self._make_interp()
        interp.sig_val[0] = 42
        interp.sig_val[1] = 42
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.LOAD_SIG, 1),
            instr(Op.CMP_EQ),
            instr(Op.STORE_SIG, 2),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[2] == 1

    def test_cmp_eq_false(self):
        """CMP_EQ pushes 0 when not equal."""
        interp = self._make_interp()
        interp.sig_val[0] = 42
        interp.sig_val[1] = 99
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.LOAD_SIG, 1),
            instr(Op.CMP_EQ),
            instr(Op.STORE_SIG, 2),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[2] == 0

    def test_jump_if_zero(self):
        """JUMP_IF_ZERO branches when TOS is zero."""
        c_one = Value(1, width=8)
        interp = self._make_interp(consts=[c_one])
        interp.sig_val[0] = 0  # condition = 0 → branch taken
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.JUMP_IF_ZERO, 4),  # jump to LOAD_CONST
            instr(Op.LOAD_CONST, 0),  # skipped: store 1
            instr(Op.STORE_SIG, 1),
            instr(Op.PROC_END),  # index 4: end
        ]
        interp.execute(program)
        # Signal 1 should still be 0 (the store was skipped)
        assert interp.sig_val[1] == 0

    def test_jump_if_zero_no_branch(self):
        """JUMP_IF_ZERO falls through when TOS is nonzero."""
        c_one = Value(99, width=8)
        interp = self._make_interp(consts=[c_one])
        interp.sig_val[0] = 1  # condition = 1 → no branch
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.JUMP_IF_ZERO, 4),
            instr(Op.LOAD_CONST, 0),  # not skipped: store 99
            instr(Op.STORE_SIG, 1),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert interp.sig_val[1] == 99

    def test_nba(self):
        """NBA_SIG queues a non-blocking update (does not change signal immediately)."""
        c = Value(55, width=8)
        interp = self._make_interp(consts=[c])
        program = [
            instr(Op.LOAD_CONST, 0),
            instr(Op.NBA_SIG, 0),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        # Signal should NOT have changed yet
        assert interp.sig_val[0] == 0
        # NBA queue should have the pending update
        assert len(interp.nba_queue) == 1
        assert interp.nba_queue[0] == (0, c)

    def test_dirty_tracking(self):
        """STORE_SIG marks signal as dirty only when value changes."""
        c = Value(42, width=8)
        interp = self._make_interp(consts=[c])
        interp.dirty.clear()
        # Store to a signal that doesn't have 42
        program = [
            instr(Op.LOAD_CONST, 0),
            instr(Op.STORE_SIG, 0),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert 0 in interp.dirty

        # Now store same value again
        interp.dirty.clear()
        interp.execute(program)
        assert 0 not in interp.dirty  # no change → not dirty

    def test_sys_display(self):
        """SYS_DISPLAY collects output."""
        c = Value(123, width=8)
        interp = self._make_interp(consts=[c])
        program = [
            instr(Op.LOAD_CONST, 0),
            instr(Op.SYS_DISPLAY, 1),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        assert len(interp.display_output) == 1
        assert "123" in interp.display_output[0]

    def test_sys_finish(self):
        """SYS_FINISH raises StopSimulation."""
        interp = self._make_interp()
        program = [
            instr(Op.SYS_FINISH),
        ]
        with pytest.raises(StopSimulation):
            interp.execute(program)

    def test_resize(self):
        """RESIZE changes width of value."""
        interp = self._make_interp()
        interp.sig_val[0] = 0xFF
        interp.sig_width[0] = 8
        program = [
            instr(Op.LOAD_SIG, 0),
            instr(Op.RESIZE, 4),
            instr(Op.STORE_SIG, 1),
            instr(Op.PROC_END),
        ]
        interp.execute(program)
        # 0xFF truncated to 4 bits = 0x0F
        assert interp.sig_val[1] == 0x0F


# ── VMScheduler Tests ────────────────────────────────────────────────


class TestVMScheduler:
    def test_elaborate(self):
        """VMScheduler.elaborate() creates interpreter and processes."""
        sched = VMScheduler()
        sched.elaborate(_make_adder())
        assert sched.interpreter is not None
        assert len(sched.compiler.processes) > 0

    def test_continuous_assign(self):
        """Continuous assign: a=10, b=20 → y=30."""
        sched = VMScheduler()
        sched.elaborate(_make_adder())
        sched.drive_signal("a", Value(10, width=8))
        sched.drive_signal("b", Value(20, width=8))
        sched.run(max_time=0)
        assert sched.read_signal("y") == 30

    def test_continuous_assign_chain(self):
        """Two chained assigns: assign a = in; assign b = a;"""
        m = Module(
            "chain",
            nets=[
                Net("inp", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("a", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Net("b", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            continuous_assigns=[
                ContinuousAssign(Identifier("a"), Identifier("inp")),
                ContinuousAssign(Identifier("b"), Identifier("a")),
            ],
        )
        sched = VMScheduler()
        sched.elaborate(m)
        sched.drive_signal("inp", Value(77, width=8))
        sched.run(max_time=0)
        assert sched.read_signal("a") == 77
        assert sched.read_signal("b") == 77

    def test_combo_always(self):
        """Combinational always @(*) mux."""
        sched = VMScheduler()
        sched.elaborate(_make_mux())
        sched.drive_signal("sel", Value(1, width=1))
        sched.drive_signal("a", Value(42, width=8))
        sched.drive_signal("b", Value(99, width=8))
        sched.run(max_time=0)
        assert sched.read_signal("y") == 42

    def test_combo_always_sel0(self):
        """Mux with sel=0 selects b."""
        sched = VMScheduler()
        sched.elaborate(_make_mux())
        sched.drive_signal("sel", Value(0, width=1))
        sched.drive_signal("a", Value(42, width=8))
        sched.drive_signal("b", Value(99, width=8))
        sched.run(max_time=0)
        assert sched.read_signal("y") == 99

    def test_initial_block(self):
        """Initial block sets count=0."""
        sched = VMScheduler()
        sched.elaborate(_make_counter())
        sched.run(max_time=0)
        assert sched.read_signal("count") == 0

    def test_display_output(self):
        """$display in initial block."""
        sched = VMScheduler()
        sched.elaborate(_make_display())
        sched.run(max_time=0)
        assert any("42" in s for s in sched.display_output)

    def test_drive_and_read(self):
        """Drive and read signals via scheduler API."""
        sched = VMScheduler()
        sched.elaborate(_make_adder())
        sched.drive_signal("a", Value(5, width=8))
        assert sched.read_signal("a") == 5

    def test_drive_int(self):
        """Drive signal with plain int."""
        sched = VMScheduler()
        sched.elaborate(_make_adder())
        sched.drive_signal("a", 7)
        assert sched.read_signal("a") == 7

    def test_read_unknown_signal(self):
        """Reading unknown signal returns x."""
        sched = VMScheduler()
        sched.elaborate(_make_adder())
        v = sched.read_signal("nonexistent")
        assert not v.is_defined


# ── Simulator VM Engine Tests ────────────────────────────────────────


class TestSimulatorVM:
    def test_create_vm(self):
        """Simulator with engine='vm' (pure-Python) creates successfully."""
        sim = Simulator(_make_adder(), engine="vm")
        assert sim.time == 0

    def test_create_vm_fast(self):
        """Simulator with engine='vm-fast' (Cython or pure-Python fallback) creates successfully."""
        sim = Simulator(_make_adder(), engine="vm-fast")
        assert sim.time == 0

    def test_invalid_engine(self):
        """Invalid engine name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown engine"):
            Simulator(_make_adder(), engine="invalid")

    def test_adder(self):
        """VM engine: a=10, b=20 → y=30."""
        sim = Simulator(_make_adder(), engine="vm")

        def test(s):
            s.drive("a", Value(10, width=8))
            s.drive("b", Value(20, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 30

    def test_adder_multi(self):
        """VM engine: multiple test cases."""
        for a, b in [(0, 0), (1, 2), (100, 50), (255, 1)]:
            sim = Simulator(_make_adder(), engine="vm")

            def test(s, av=a, bv=b):
                s.drive("a", Value(av, width=8))
                s.drive("b", Value(bv, width=8))

            sim.run(test, max_time=0)
            expected = (a + b) & 0xFF
            assert sim.read("y") == expected, f"Failed for a={a}, b={b}"

    def test_inverter(self):
        """VM engine: ~a."""
        sim = Simulator(_make_inverter(), engine="vm")

        def test(s):
            s.drive("a", Value(0x0F, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 0xF0

    def test_mux_sel1(self):
        """VM engine: mux sel=1 → output = a."""
        sim = Simulator(_make_mux(), engine="vm")

        def test(s):
            s.drive("sel", Value(1, width=1))
            s.drive("a", Value(42, width=8))
            s.drive("b", Value(99, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 42

    def test_mux_sel0(self):
        """VM engine: mux sel=0 → output = b."""
        sim = Simulator(_make_mux(), engine="vm")

        def test(s):
            s.drive("sel", Value(0, width=1))
            s.drive("a", Value(42, width=8))
            s.drive("b", Value(99, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 99

    def test_initial_block(self):
        """VM engine: initial block sets count=0."""
        sim = Simulator(_make_counter(), engine="vm")
        sim.run(max_time=0)
        assert sim.read("count") == 0

    def test_display(self):
        """VM engine: $display output collected."""
        sim = Simulator(_make_display(), engine="vm")
        sim.run(max_time=0)
        assert any("42" in s for s in sim.display_output)

    def test_signal_handle(self):
        """VM engine: SignalHandle works."""
        sim = Simulator(_make_adder(), engine="vm")
        a = sim.signal("a")
        assert isinstance(a, SignalHandle)
        a.value = 55
        assert a.value == 55

    def test_run_step_basic(self):
        """VM engine: run_step() advances one time step."""
        sim = Simulator(_make_counter(), engine="vm")
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=0)
        # Step through first few clock edges
        result = sim.run_step()
        assert result is True  # simulation continues


# ── Cross-Validation Tests ──────────────────────────────────────────


class TestCrossValidation:
    """Run the same design through all engines and compare results."""

    def _run_both(self, module_fn, setup_fn, signals_to_check, max_time=0):
        """Run module through all engines with same setup, compare signals."""
        # Reference engine
        sim_ref = Simulator(module_fn(), engine="reference")
        setup_fn(sim_ref)
        sim_ref.run(max_time=max_time)

        for engine in ("vm", "vm-fast"):
            sim_e = Simulator(module_fn(), engine=engine)
            setup_fn(sim_e)
            sim_e.run(max_time=max_time)

            for sig in signals_to_check:
                ref_val = sim_ref.read(sig)
                e_val = sim_e.read(sig)
                assert ref_val == e_val, f"[{engine}] Signal {sig} mismatch: reference={ref_val}, {engine}={e_val}"

    def test_adder_cross(self):
        """Cross-validate adder."""

        def setup(s):
            s.drive("a", Value(37, width=8))
            s.drive("b", Value(19, width=8))

        self._run_both(_make_adder, setup, ["y"])

    def test_inverter_cross(self):
        """Cross-validate inverter."""

        def setup(s):
            s.drive("a", Value(0xAA, width=8))

        self._run_both(_make_inverter, setup, ["y"])

    def test_mux_cross_sel1(self):
        """Cross-validate mux sel=1."""

        def setup(s):
            s.drive("sel", Value(1, width=1))
            s.drive("a", Value(111, width=8))
            s.drive("b", Value(222, width=8))

        self._run_both(_make_mux, setup, ["y"])

    def test_mux_cross_sel0(self):
        """Cross-validate mux sel=0."""

        def setup(s):
            s.drive("sel", Value(0, width=1))
            s.drive("a", Value(111, width=8))
            s.drive("b", Value(222, width=8))

        self._run_both(_make_mux, setup, ["y"])

    def test_initial_cross(self):
        """Cross-validate initial block."""

        def setup(s):
            pass

        self._run_both(_make_counter, setup, ["count"])

    def test_adder_sweep_cross(self):
        """Cross-validate adder across many input combinations."""
        for a_val in [0, 1, 127, 128, 255]:
            for b_val in [0, 1, 127, 128, 255]:

                def setup(s, av=a_val, bv=b_val):
                    s.drive("a", Value(av, width=8))
                    s.drive("b", Value(bv, width=8))

                self._run_both(_make_adder, setup, ["y"])


# ── Module builders for bug-fix tests ────────────────────────────────


def _make_concat_lhs() -> Module:
    """module m(input [7:0] x, output [3:0] hi, lo);
    assign {hi, lo} = x;
    endmodule
    Tests concatenation LHS decomposition.
    """
    return Module(
        "concat_lhs",
        ports=[
            Port("x", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("hi", PortDirection.OUTPUT, width=Range(Literal(3, width=32), Literal(0, width=32))),
            Port("lo", PortDirection.OUTPUT, width=Range(Literal(3, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("x", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("hi", NetKind.WIRE, width=Range(Literal(3, width=32), Literal(0, width=32))),
            Net("lo", NetKind.WIRE, width=Range(Literal(3, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(
                Concatenation([Identifier("hi"), Identifier("lo")]),
                Identifier("x"),
            ),
        ],
    )


def _make_concat_lhs_3way() -> Module:
    """assign {a, b, c} = x; where a=2-bit, b=3-bit, c=3-bit, x=8-bit."""
    return Module(
        "concat_lhs_3",
        ports=[
            Port("x", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("a", PortDirection.OUTPUT, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Port("b", PortDirection.OUTPUT, width=Range(Literal(2, width=32), Literal(0, width=32))),
            Port("c", PortDirection.OUTPUT, width=Range(Literal(2, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("x", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("a", NetKind.WIRE, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Net("b", NetKind.WIRE, width=Range(Literal(2, width=32), Literal(0, width=32))),
            Net("c", NetKind.WIRE, width=Range(Literal(2, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(
                Concatenation([Identifier("a"), Identifier("b"), Identifier("c")]),
                Identifier("x"),
            ),
        ],
    )


def _make_repeat_loop() -> Module:
    """module m; reg [7:0] count; initial begin count = 0; repeat(5) count = count + 1; end endmodule
    After initial block, count should be 5.
    """
    m = Module(
        "repeat_test",
        variables=[
            Variable("count", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(Identifier("count"), Literal(0, width=8)),
                    RepeatLoop(
                        Literal(5, width=32),
                        BlockingAssign(
                            Identifier("count"),
                            BinaryOp("+", Identifier("count"), Literal(1, width=8)),
                        ),
                    ),
                ]
            )
        )
    )
    return m


def _make_repeat_loop_zero() -> Module:
    """repeat(0) should not execute body."""
    m = Module(
        "repeat_zero",
        variables=[
            Variable("count", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(Identifier("count"), Literal(42, width=8)),
                    RepeatLoop(
                        Literal(0, width=32),
                        BlockingAssign(Identifier("count"), Literal(99, width=8)),
                    ),
                ]
            )
        )
    )
    return m


def _make_clog2_test() -> Module:
    """assign y = $clog2(x);"""
    return Module(
        "clog2_test",
        ports=[
            Port("x", PortDirection.INPUT, width=Range(Literal(31, width=32), Literal(0, width=32))),
            Port("y", PortDirection.OUTPUT, width=Range(Literal(31, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("x", NetKind.WIRE, width=Range(Literal(31, width=32), Literal(0, width=32))),
            Net("y", NetKind.WIRE, width=Range(Literal(31, width=32), Literal(0, width=32))),
        ],
        continuous_assigns=[
            ContinuousAssign(
                Identifier("y"),
                FunctionCall("$clog2", [Identifier("x")], is_system=True),
            ),
        ],
    )


def _make_casex_test() -> Module:
    """module casex_test(input [3:0] sel, output reg [7:0] y);
    always @(*) casex (sel)
      4'b1xxx: y = 1;
      4'b01xx: y = 2;
      default: y = 0;
    endcase
    endmodule
    We test with known values to see if don't-care matching works.
    """
    m = Module(
        "casex_test",
        ports=[
            Port("sel", PortDirection.INPUT, width=Range(Literal(3, width=32), Literal(0, width=32))),
            Port("y", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("sel", NetKind.WIRE, width=Range(Literal(3, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("y", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    # casex: sel bits 3=1,xxx → y=1; sel bits 3:2=01,xx → y=2; default → y=0
    # We use original_text so that the compiler calls Value.from_verilog,
    # which correctly sets the x-mask bits for don't-care positions.
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=CaseStatement(
                "casex",
                Identifier("sel"),
                [
                    CaseItem(
                        [Literal(0b1000, width=4, original_text="4'b1xxx")],
                        BlockingAssign(Identifier("y"), Literal(1, width=8)),
                    ),
                    CaseItem(
                        [Literal(0b0100, width=4, original_text="4'b01xx")],
                        BlockingAssign(Identifier("y"), Literal(2, width=8)),
                    ),
                    CaseItem(
                        None,
                        BlockingAssign(Identifier("y"), Literal(0, width=8)),
                        is_default=True,
                    ),
                ],
            ),
        )
    )
    return m


# ── Bug Fix Tests ────────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestConcatLHS:
    """Test concatenation LHS decomposition (was: only storing to first part)."""

    def test_concat_lhs_split(self, engine):
        """assign {hi, lo} = 8'hA5  →  hi=0xA, lo=0x5."""
        sim = Simulator(_make_concat_lhs(), engine=engine)

        def test(s):
            s.drive("x", Value(0xA5, width=8))

        sim.run(test, max_time=0)
        assert sim.read("hi") == 0xA, f"hi should be 0xA, got {sim.read('hi'):#x}"
        assert sim.read("lo") == 0x5, f"lo should be 0x5, got {sim.read('lo'):#x}"

    def test_concat_lhs_all_zeros(self, engine):
        """{hi, lo} = 0 → both zero."""
        sim = Simulator(_make_concat_lhs(), engine=engine)

        def test(s):
            s.drive("x", Value(0, width=8))

        sim.run(test, max_time=0)
        assert sim.read("hi") == 0
        assert sim.read("lo") == 0

    def test_concat_lhs_all_ones(self, engine):
        """{hi, lo} = 0xFF → both 0xF."""
        sim = Simulator(_make_concat_lhs(), engine=engine)

        def test(s):
            s.drive("x", Value(0xFF, width=8))

        sim.run(test, max_time=0)
        assert sim.read("hi") == 0xF
        assert sim.read("lo") == 0xF

    def test_concat_lhs_3way(self, engine):
        """assign {a[1:0], b[2:0], c[2:0]} = 8'b11_101_011 →  a=3, b=5, c=3."""
        sim = Simulator(_make_concat_lhs_3way(), engine=engine)

        def test(s):
            # 0b11_101_011 = 0xEB
            s.drive("x", Value(0b11101011, width=8))

        sim.run(test, max_time=0)
        assert sim.read("c") == 0b011, f"c={sim.read('c'):#05b}"
        assert sim.read("b") == 0b101, f"b={sim.read('b'):#05b}"
        assert sim.read("a") == 0b11, f"a={sim.read('a'):#04b}"


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestRepeatLoop:
    """Test repeat loop (was: only executing body once)."""

    def test_repeat_5(self, engine):
        """repeat(5) count = count + 1  →  count == 5."""
        sim = Simulator(_make_repeat_loop(), engine=engine)
        sim.run(max_time=0)
        assert sim.read("count") == 5, f"count should be 5, got {sim.read('count')}"

    def test_repeat_zero(self, engine):
        """repeat(0) should not execute body at all."""
        sim = Simulator(_make_repeat_loop_zero(), engine=engine)
        sim.run(max_time=0)
        assert sim.read("count") == 42, f"count should stay 42, got {sim.read('count')}"


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestClog2:
    """Test $clog2 opcode (was: no computation)."""

    def test_clog2_values(self, engine):
        """$clog2: standard test vectors."""
        for inp, expected in [
            (0, 0),
            (1, 0),
            (2, 1),
            (3, 2),
            (4, 2),
            (5, 3),
            (7, 3),
            (8, 3),
            (9, 4),
            (16, 4),
            (17, 5),
            (256, 8),
        ]:
            sim = Simulator(_make_clog2_test(), engine=engine)

            def test(s, v=inp):
                s.drive("x", Value(v, width=32))

            sim.run(test, max_time=0)
            result = sim.read("y")
            assert result == expected, f"$clog2({inp}) should be {expected}, got {result}"


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestCasexCasez:
    """Test casex matching (was: using CMP_EQ instead of don't-care)."""

    def test_casex_match_first(self, engine):
        """sel=0b1010 should match 4'b1xxx → y=1."""
        sim = Simulator(_make_casex_test(), engine=engine)

        def test(s):
            s.drive("sel", Value(0b1010, width=4))

        sim.run(test, max_time=0)
        assert sim.read("y") == 1

    def test_casex_match_second(self, engine):
        """sel=0b0110 should match 4'b01xx → y=2."""
        sim = Simulator(_make_casex_test(), engine=engine)

        def test(s):
            s.drive("sel", Value(0b0110, width=4))

        sim.run(test, max_time=0)
        assert sim.read("y") == 2

    def test_casex_match_default(self, engine):
        """sel=0b0010 should hit default → y=0."""
        sim = Simulator(_make_casex_test(), engine=engine)

        def test(s):
            s.drive("sel", Value(0b0010, width=4))

        sim.run(test, max_time=0)
        assert sim.read("y") == 0


# ── Memory array module builders ─────────────────────────────────────


def _make_mem_read_write() -> Module:
    """Module with reg [7:0] mem [0:3] and always @(*) out = mem[addr].

    Has an initial block that writes mem[0]=10, mem[1]=20, mem[2]=30, mem[3]=40
    using blocking assigns, and a combo block that reads mem[addr].
    """
    m = Module(
        "mem_test",
        ports=[
            Port("addr", PortDirection.INPUT, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Port("out", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("addr", NetKind.WIRE, width=Range(Literal(1, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Variable(
                "mem",
                VariableKind.REG,
                width=Range(Literal(7, width=32), Literal(0, width=32)),
                dimensions=[Range(Literal(0, width=32), Literal(3, width=32))],
            ),
        ],
    )
    # Initial block: mem[0]=10; mem[1]=20; mem[2]=30; mem[3]=40;
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(
                        BitSelect(Identifier("mem"), Literal(0, width=2)),
                        Literal(10, width=8),
                    ),
                    BlockingAssign(
                        BitSelect(Identifier("mem"), Literal(1, width=2)),
                        Literal(20, width=8),
                    ),
                    BlockingAssign(
                        BitSelect(Identifier("mem"), Literal(2, width=2)),
                        Literal(30, width=8),
                    ),
                    BlockingAssign(
                        BitSelect(Identifier("mem"), Literal(3, width=2)),
                        Literal(40, width=8),
                    ),
                ]
            )
        )
    )
    # always @(*) out = mem[addr];
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("out"),
                BitSelect(Identifier("mem"), Identifier("addr")),
            ),
        )
    )
    return m


def _make_mem_nba() -> Module:
    """Module with reg [7:0] mem [0:3] and clocked NBA writes.

    always @(posedge clk) mem[addr] <= data_in;
    always @(*) out = mem[rd_addr];
    """
    m = Module(
        "mem_nba",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("addr", PortDirection.INPUT, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Port("data_in", PortDirection.INPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Port("rd_addr", PortDirection.INPUT, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Port("out", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("addr", NetKind.WIRE, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Net("data_in", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Net("rd_addr", NetKind.WIRE, width=Range(Literal(1, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Variable(
                "mem",
                VariableKind.REG,
                width=Range(Literal(7, width=32), Literal(0, width=32)),
                dimensions=[Range(Literal(0, width=32), Literal(3, width=32))],
            ),
        ],
    )
    # always @(posedge clk) mem[addr] <= data_in;
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
            body=NonblockingAssign(
                BitSelect(Identifier("mem"), Identifier("addr")),
                Identifier("data_in"),
            ),
        )
    )
    # always @(*) out = mem[rd_addr];
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("out"),
                BitSelect(Identifier("mem"), Identifier("rd_addr")),
            ),
        )
    )
    return m


# ── Memory array tests ──────────────────────────────────────────────


class TestMemoryArrayCompiler:
    """Test memory array compilation."""

    def test_memory_registered(self):
        """Memory arrays should be tracked in mem_map, not signal_map."""
        c = Compiler()
        c.compile_module(_make_mem_read_write())
        assert "mem" in c.mem_map
        assert "mem" not in c.signal_map
        assert c.mem_count == 1

    def test_memory_info(self):
        """mem_info should have (elem_width=8, depth=4, base_addr=0)."""
        c = Compiler()
        c.compile_module(_make_mem_read_write())
        ew, depth, base = c.mem_info[0]
        assert ew == 8
        assert depth == 4
        assert base == 0

    def test_memory_storage_size(self):
        """Flat memory storage should have 4 elements (8-bit each)."""
        c = Compiler()
        c.compile_module(_make_mem_read_write())
        assert len(c.mem_val) == 4
        assert len(c.mem_mask) == 4

    def test_memory_opcodes_generated(self):
        """Compiler should emit LOAD_MEM and STORE_MEM for memory access."""
        c = Compiler()
        c.compile_module(_make_mem_read_write())

        # Check that LOAD_MEM is in the combo process (out = mem[addr])
        combo_procs = [p for p in c.processes if p.process_type == ProcessType.COMBINATIONAL]
        assert len(combo_procs) == 1
        ops = [instr[0] for instr in combo_procs[0].program]
        assert Op.LOAD_MEM in ops

        # Check that STORE_MEM is in the initial process (mem[i] = ...)
        init_procs = [p for p in c.processes if p.process_type == ProcessType.INITIAL]
        assert len(init_procs) == 1
        ops = [instr[0] for instr in init_procs[0].program]
        assert Op.STORE_MEM in ops


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestMemoryArraySim:
    """Test memory array simulation."""

    def test_mem_init_and_read(self, engine):
        """Initial block writes + combo read should produce correct output."""
        sim = Simulator(_make_mem_read_write(), engine=engine)

        def test(s):
            s.drive("addr", Value(0, width=2))

        sim.run(test, max_time=0)
        assert sim.read("out") == 10

    def test_mem_read_all_addresses(self, engine):
        """Read each memory address after initial write."""
        expected = {0: 10, 1: 20, 2: 30, 3: 40}
        for addr, exp_val in expected.items():
            sim = Simulator(_make_mem_read_write(), engine=engine)

            def test(s, a=addr):
                s.drive("addr", Value(a, width=2))

            sim.run(test, max_time=0)
            assert sim.read("out") == exp_val, f"mem[{addr}] expected {exp_val}"


# ── Item 1: Loop iteration limit ────────────────────────────────────


def _make_infinite_loop() -> Module:
    """Module with initial block: while(1) x = x + 1; (infinite loop)."""
    m = Module(
        "inf_loop",
        ports=[],
        variables=[
            Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.initial_blocks.append(
        InitialBlock(
            WhileLoop(
                condition=Literal(1, width=1),
                body=BlockingAssign(
                    Identifier("x"),
                    BinaryOp("+", Identifier("x"), Literal(1, width=8)),
                ),
            )
        )
    )
    return m


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestLoopIterationLimit:
    """Item 1: Backward-jump loop iteration limit."""

    def test_infinite_loop_raises(self, engine):
        """An infinite while(1) loop should raise RuntimeError."""
        sim = Simulator(_make_infinite_loop(), engine=engine)
        with pytest.raises(RuntimeError, match="Loop exceeded"):
            sim.run(lambda s: None, max_time=0)

    def test_finite_loop_completes(self, engine):
        """A finite for-loop should complete without error."""
        m = Module(
            "fin_loop",
            ports=[
                Port("out", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Variable("i", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        # initial begin out = 0; for (i=0; i<10; i=i+1) out = out + 1; end
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("out"), Literal(0, width=8)),
                        ForLoop(
                            init=BlockingAssign(Identifier("i"), Literal(0, width=8)),
                            condition=BinaryOp("<", Identifier("i"), Literal(10, width=8)),
                            update=BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal(1, width=8))),
                            body=BlockingAssign(
                                Identifier("out"),
                                BinaryOp("+", Identifier("out"), Literal(1, width=8)),
                            ),
                        ),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: None, max_time=0)
        assert sim.read("out") == 10


# ── Item 2: $bits compile-time fix ──────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestBitsFix:
    """Item 2: $bits should return compile-time width."""

    def test_bits_8bit_signal(self, engine):
        """$bits(x) where x is 8-bit should yield 8."""
        m = Module(
            "bits_test",
            ports=[
                Port("out", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        # initial out = $bits(x);
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    FunctionCall("$bits", [Identifier("x")]),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: None, max_time=0)
        assert sim.read("out") == 8

    def test_bits_1bit_signal(self, engine):
        """$bits(x) where x is 1-bit should yield 1."""
        m = Module(
            "bits1_test",
            ports=[
                Port("out", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
                Variable("x", VariableKind.REG),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    FunctionCall("$bits", [Identifier("x")]),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: None, max_time=0)
        assert sim.read("out") == 1


# ── Item 3: $random ─────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestRandom:
    """Item 3: $random should return a 32-bit value."""

    def test_random_returns_value(self, engine):
        """$random should produce a valid 32-bit value."""
        m = Module(
            "rand_test",
            ports=[
                Port("out", PortDirection.OUTPUT, width=Range(Literal(31, width=32), Literal(0, width=32))),
            ],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(31, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    FunctionCall("$random", []),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: None, max_time=0)
        val = sim.read("out")
        # Value should be a valid integer (not x/z) and fit in 32 bits
        assert val is not None
        assert isinstance(val, Value)
        assert val.is_defined
        assert 0 <= val.val < 2**32

    def test_random_opcode_emitted(self, engine):
        """Compiler should emit FUNC_RANDOM for $random."""
        m = Module(
            "rand_test",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(31, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    FunctionCall("$random", []),
                )
            )
        )
        c = Compiler()
        c.compile_module(m)
        init_procs = [p for p in c.processes if p.process_type == ProcessType.INITIAL]
        ops = [i[0] for i in init_procs[0].program]
        assert Op.FUNC_RANDOM in ops


# ── Item 4: Initial blocks with #delay ──────────────────────────────


def _make_initial_with_delay() -> Module:
    """Module: initial begin x = 0; #10 x = 1; #10 x = 2; end"""
    m = Module(
        "init_delay",
        ports=[
            Port("x", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
    )
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(Identifier("x"), Literal(0, width=8)),
                    DelayControl(
                        delay=Literal(10, width=32),
                        body=BlockingAssign(Identifier("x"), Literal(1, width=8)),
                    ),
                    DelayControl(
                        delay=Literal(10, width=32),
                        body=BlockingAssign(Identifier("x"), Literal(2, width=8)),
                    ),
                ]
            )
        )
    )
    return m


class TestInitialWithDelay:
    """Item 4: Initial blocks with #delay should work via reference fallback."""

    def test_has_timing_flag(self):
        """Compiler should set has_timing=True for initial blocks with #delay."""
        c = Compiler()
        c.compile_module(_make_initial_with_delay())
        init_procs = [p for p in c.processes if p.process_type == ProcessType.INITIAL]
        assert len(init_procs) == 1
        assert init_procs[0].has_timing is True

    def test_initial_delay_values(self):
        """Signal should change at the scheduled times."""
        m = _make_initial_with_delay()
        sched = VMScheduler()
        sched.elaborate(m)

        # Capture x at different times
        results = {}

        def on_step(s):
            results[s.time] = s.compiler.sig_val[s.compiler.signal_map["x"]]

        sched._on_time_step = on_step
        sched.run(max_time=100)

        # At t=10, x should be 1; at t=20, x should be 2
        assert results.get(10) == 1, f"x@10={results.get(10)}"
        assert results.get(20) == 2, f"x@20={results.get(20)}"


# ── Item 5: Always blocks with timing ───────────────────────────────


def _make_always_with_timing() -> Module:
    """Module with always #5 clk = ~clk; (clock generator pattern)."""
    m = Module(
        "always_timing",
        ports=[
            Port("clk", PortDirection.OUTPUT),
        ],
        variables=[
            Variable("clk", VariableKind.REG),
        ],
    )
    # Initial clk = 0;
    m.initial_blocks.append(
        InitialBlock(
            BlockingAssign(Identifier("clk"), Literal(0, width=1)),
        )
    )
    # always #5 clk = ~clk;
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=DelayControl(
                delay=Literal(5, width=32),
                body=BlockingAssign(Identifier("clk"), UnaryOp("~", Identifier("clk"))),
            ),
        )
    )
    return m


class TestAlwaysWithTiming:
    """Item 5: Always blocks with timing controls should use coroutine fallback."""

    def test_has_timing_flag_always(self):
        """Compiler should set has_timing=True for always blocks with #delay."""
        c = Compiler()
        c.compile_module(_make_always_with_timing())
        always_procs = [p for p in c.processes if p.process_type in (ProcessType.COMBINATIONAL, ProcessType.SEQUENTIAL)]
        assert any(p.has_timing for p in always_procs)

    def test_always_timing_clock_toggles(self):
        """always #5 clk=~clk should toggle at t=5,10,15,..."""
        m = _make_always_with_timing()
        sched = VMScheduler()
        sched.elaborate(m)

        results = {}

        def on_step(s):
            results[s.time] = s.compiler.sig_val[s.compiler.signal_map["clk"]]

        sched._on_time_step = on_step
        sched.run(max_time=30)

        # clk starts at 0, toggles at t=5 (→1), t=10 (→0), t=15 (→1), t=20 (→0), t=25 (→1)
        assert results.get(5) == 1, f"clk@5={results.get(5)}"
        assert results.get(10) == 0, f"clk@10={results.get(10)}"
        assert results.get(15) == 1, f"clk@15={results.get(15)}"
        assert results.get(20) == 0, f"clk@20={results.get(20)}"


# ── Item 6: $readmemh / $readmemb ──────────────────────────────────


def _make_readmemh_module(filename: str) -> Module:
    """Module with reg [7:0] mem [0:3]; initial $readmemh(filename, mem); out=mem[addr]."""
    m = Module(
        "readmemh_test",
        ports=[
            Port("addr", PortDirection.INPUT, width=Range(Literal(1, width=32), Literal(0, width=32))),
            Port("out", PortDirection.OUTPUT, width=Range(Literal(7, width=32), Literal(0, width=32))),
        ],
        nets=[
            Net("addr", NetKind.WIRE, width=Range(Literal(1, width=32), Literal(0, width=32))),
        ],
        variables=[
            Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            Variable(
                "mem",
                VariableKind.REG,
                width=Range(Literal(7, width=32), Literal(0, width=32)),
                dimensions=[Range(Literal(0, width=32), Literal(3, width=32))],
            ),
        ],
    )
    # initial $readmemh(filename, mem);
    m.initial_blocks.append(
        InitialBlock(
            SystemTaskCall("$readmemh", [StringLiteral(filename), Identifier("mem")]),
        )
    )
    # always @(*) out = mem[addr];
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("out"),
                BitSelect(Identifier("mem"), Identifier("addr")),
            ),
        )
    )
    return m


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestReadmem:
    """Item 6: $readmemh and $readmemb system tasks."""

    def test_readmemh_opcode(self, engine):
        """Compiler should emit SYS_READMEM for $readmemh."""
        c = Compiler()
        c.compile_module(_make_readmemh_module("test.hex"))
        init_procs = [p for p in c.processes if p.process_type == ProcessType.INITIAL]
        ops = [i[0] for i in init_procs[0].program]
        assert Op.SYS_READMEM in ops
        assert len(c.readmem_tasks) == 1
        fname, mid, is_hex = c.readmem_tasks[0]
        assert fname == "test.hex"
        assert is_hex is True

    def test_readmemh_loads_data(self, tmp_path, engine):
        """$readmemh should load hex values into memory."""
        hex_file = tmp_path / "data.hex"
        hex_file.write_text("0A\n14\n1E\n28\n")

        m = _make_readmemh_module(str(hex_file))
        sim = Simulator(m, engine=engine)

        expected = {0: 0x0A, 1: 0x14, 2: 0x1E, 3: 0x28}
        for addr, exp_val in expected.items():
            sim = Simulator(m, engine=engine)

            def test(s, a=addr):
                s.drive("addr", Value(a, width=2))

            sim.run(test, max_time=0)
            assert sim.read("out") == exp_val, f"mem[{addr}] expected {exp_val:#x}"

    def test_readmemb_opcode(self, engine):
        """Compiler should emit SYS_READMEM for $readmemb with is_hex=False."""
        m = Module(
            "readmemb_test",
            ports=[],
            variables=[
                Variable(
                    "mem",
                    VariableKind.REG,
                    width=Range(Literal(7, width=32), Literal(0, width=32)),
                    dimensions=[Range(Literal(0, width=32), Literal(3, width=32))],
                ),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SystemTaskCall("$readmemb", [StringLiteral("data.bin"), Identifier("mem")]),
            )
        )
        c = Compiler()
        c.compile_module(m)
        assert len(c.readmem_tasks) == 1
        fname, mid, is_hex = c.readmem_tasks[0]
        assert fname == "data.bin"
        assert is_hex is False

    def test_readmemh_with_address_spec(self, tmp_path, engine):
        """$readmemh should handle @addr specifications."""
        hex_file = tmp_path / "data_addr.hex"
        hex_file.write_text("@02\nAA\nBB\n")

        m = _make_readmemh_module(str(hex_file))
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("addr", Value(2, width=2))

        sim.run(test, max_time=0)
        assert sim.read("out") == 0xAA

    def test_readmemh_missing_file(self, engine):
        """$readmemh with missing file should raise FileNotFoundError."""
        m = _make_readmemh_module("nonexistent_file.hex")
        sim = Simulator(m, engine=engine)
        with pytest.raises(FileNotFoundError):
            sim.run(lambda s: s.drive("addr", Value(0, width=2)), max_time=0)


# ── TernaryOp x-condition merge Tests ────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestTernaryXMerge:
    """Test that ternary with x-condition merges true/false values bitwise."""

    def test_ternary_defined_cond_true(self, engine):
        """cond=1 → result = true_expr."""
        m = Module(
            "tern_true",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        # initial out = 1 ? 8'hAA : 8'h55;
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    TernaryOp(Literal(1, width=1), Literal(0xAA, width=8), Literal(0x55, width=8)),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("out") == 0xAA

    def test_ternary_defined_cond_false(self, engine):
        """cond=0 → result = false_expr."""
        m = Module(
            "tern_false",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    TernaryOp(Literal(0, width=1), Literal(0xAA, width=8), Literal(0x55, width=8)),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("out") == 0x55

    def test_ternary_opcode_emitted(self, engine):
        """Compiler emits TERNARY opcode for TernaryOp."""
        m = Module(
            "tern_op",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("out"),
                    TernaryOp(Literal(1, width=1), Literal(0xAA, width=8), Literal(0x55, width=8)),
                )
            )
        )
        c = Compiler()
        c.compile_module(m)
        opcodes = [op for op, _, _ in c.processes[0].program]
        assert Op.TERNARY in opcodes


# ── DisableStatement Tests ───────────────────────────────────────────


class TestDisableStatement:
    """Test that DisableStatement raises NotImplementedError during compilation."""

    def test_disable_raises(self):
        """Compiler should raise NotImplementedError for disable statement."""
        m = Module(
            "disable_test",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("out"), Literal(1, width=8)),
                        DisableStatement("myblock"),
                    ]
                )
            )
        )
        c = Compiler()
        with pytest.raises(NotImplementedError, match="disable"):
            c.compile_module(m)


# ── PartSelect LHS Tests ────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestPartSelectLHS:
    """Test part-select on LHS of assignments."""

    def test_partselect_plus_colon(self, engine):
        """reg [7:0] out; initial out[2 +: 4] = 4'hA → bits [5:2] = 0xA."""
        m = Module(
            "ps_plus",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("out"), Literal(0, width=8)),
                        BlockingAssign(
                            PartSelect(Identifier("out"), Literal(2, width=32), Literal(4, width=32), "+:"),
                            Literal(0xA, width=4),
                        ),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        # 0xA = 4'b1010 in bits [5:2] → 0b00101000 = 0x28
        assert sim.read("out") == 0x28

    def test_partselect_minus_colon(self, engine):
        """reg [7:0] out; initial out[5 -: 4] = 4'hB → bits [5:2] = 0xB."""
        m = Module(
            "ps_minus",
            ports=[],
            variables=[
                Variable("out", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("out"), Literal(0, width=8)),
                        BlockingAssign(
                            PartSelect(Identifier("out"), Literal(5, width=32), Literal(4, width=32), "-:"),
                            Literal(0xB, width=4),
                        ),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        # 0xB = 4'b1011 in bits [5:2] → 0b00101100 = 0x2C
        assert sim.read("out") == 0x2C


# ── $display Format String Tests ─────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestDisplayFormatStrings:
    """Test $display with Verilog format strings."""

    def test_format_display_no_fmt(self, engine):
        """No format string: values joined with spaces."""
        args = [Value(10, width=8), Value(20, width=8)]
        result = _format_display(args, 0, [], 0)
        assert result == "10 20"

    def test_format_display_hex(self, engine):
        """Format string with %h."""
        args = [Value(255, width=8)]
        result = _format_display(args, 1, ["val=%h"], 0)
        assert result == "val=ff"

    def test_format_display_decimal(self, engine):
        """Format string with %d."""
        args = [Value(42, width=8)]
        result = _format_display(args, 1, ["%d"], 0)
        assert result == "42"

    def test_format_display_binary(self, engine):
        """Format string with %b."""
        args = [Value(5, width=4)]
        result = _format_display(args, 1, ["%b"], 0)
        assert result == "101"

    def test_format_display_time(self, engine):
        """Format string with %t."""
        args = []
        result = _format_display(args, 1, ["time=%t"], 100)
        assert "100" in result

    def test_format_display_percent(self, engine):
        """Escaped %% produces literal %."""
        result = _format_display([], 1, ["100%%"], 0)
        assert result == "100%"

    def test_display_with_format_string_sim(self, engine):
        """$display with format string in simulation."""
        m = Module("disp_fmt", variables=[])
        m.initial_blocks.append(
            InitialBlock(SystemTaskCall("$display", [StringLiteral("val=%d"), Literal(42, width=8)]))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert any("val=42" in s for s in sim.display_output)

    def test_display_multiple_args_no_format(self, engine):
        """$display with multiple args, no format string."""
        m = Module("disp_multi", variables=[])
        m.initial_blocks.append(InitialBlock(SystemTaskCall("$display", [Literal(10, width=8), Literal(20, width=8)])))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert any("10" in s and "20" in s for s in sim.display_output)

    def test_format_display_width_hex(self, engine):
        """Format %08x pads hex to 8 chars with zeros."""
        args = [Value(0xAB, width=32)]
        result = _format_display(args, 1, ["%08x"], 0)
        assert result == "000000ab"

    def test_format_display_width_decimal(self, engine):
        """Format %4d pads decimal to 4 chars with spaces."""
        args = [Value(42, width=8)]
        result = _format_display(args, 1, ["%4d"], 0)
        assert result == "  42"

    def test_format_display_width_zero_pad_decimal(self, engine):
        """Format %04d pads decimal to 4 chars with zeros."""
        args = [Value(7, width=8)]
        result = _format_display(args, 1, ["%04d"], 0)
        assert result == "0007"

    def test_format_display_mixed_width_hex(self, engine):
        """PicoRV32-style: two %08x specifiers."""
        args = [Value(0x1000, width=32), Value(0x3FC00093, width=32)]
        result = _format_display(args, 1, ["ifetch 0x%08x: 0x%08x"], 0)
        assert result == "ifetch 0x00001000: 0x3fc00093"

    def test_format_display_zero_suppress_no_width(self, engine):
        """Format %0d (no width) is just decimal without padding."""
        args = [Value(42, width=16)]
        result = _format_display(args, 1, ["%0d"], 0)
        assert result == "42"


# ── $monitor Tests ───────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestMonitorOpcode:
    """Test that $monitor compiles and executes."""

    def test_monitor_opcode_emitted(self, engine):
        """Compiler emits SYS_MONITOR for $monitor call."""
        m = Module("mon_test", variables=[])
        m.initial_blocks.append(InitialBlock(SystemTaskCall("$monitor", [Literal(99, width=8)])))
        c = Compiler()
        c.compile_module(m)
        opcodes = [op for op, _, _ in c.processes[0].program]
        assert Op.SYS_MONITOR in opcodes

    def test_monitor_produces_output(self, engine):
        """$monitor produces display output."""
        m = Module(
            "mon_out",
            ports=[],
            variables=[
                Variable("x", VariableKind.REG, width=Range(Literal(7, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        SystemTaskCall("$monitor", [Identifier("x")]),
                        BlockingAssign(Identifier("x"), Literal(77, width=8)),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        # Monitor should have produced at least one output
        assert len(sim.display_output) > 0


# ── run_step Tests ───────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestRunStep:
    """Test the run_step() API for single time-step advancement."""

    def test_run_step_empty_queue(self, engine):
        """run_step with no events returns False."""
        sim = Simulator(_make_adder(), engine=engine)
        sim.run(max_time=0)  # elaborate + bootstrap
        # After run with max_time=0, queue should be empty
        result = sim.run_step()
        assert result is False

    def test_run_step_with_clock(self, engine):
        """run_step() works with clock events."""
        sim = Simulator(_make_counter(), engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=0)
        # Step through some events
        stepped = sim.run_step()
        assert stepped is True

    def test_run_step_counter_advances(self, engine):
        """Counter increments through run_step calls."""
        m = _make_counter()
        sched = VMScheduler()
        sched.elaborate(m)
        # Manually schedule clock events: posedge at 0,10,20,...; negedge at 5,15,...
        for t in range(0, 100, 10):
            sched.schedule_at(t, ("clock_toggle", "clk", Value(1, width=1)))
            sched.schedule_at(t + 5, ("clock_toggle", "clk", Value(0, width=1)))
        # Bootstrap: run initial blocks and combinational at t=0
        sched.run(max_time=0)
        count_after_t0 = int(sched.read_signal("count"))
        # Now step forward one-at-a-time
        for _ in range(6):
            if not sched.run_step():
                break
        count_after_steps = int(sched.read_signal("count"))
        # At least one more posedge should have incremented the counter
        assert count_after_steps > count_after_t0


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: arithmetic (MUL, DIV, MOD, POW)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestArithmeticOps:
    """Test MUL, DIV, MOD, POW opcodes via continuous assigns."""

    @staticmethod
    def _make_binop_module(name: str, op: str) -> Module:
        """module <name>(input [7:0] a, b, output [7:0] y); assign y = a <op> b; endmodule"""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        return Module(
            name,
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("b", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("b", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), BinaryOp(op, Identifier("a"), Identifier("b")))],
        )

    def test_mul(self, engine):
        m = self._make_binop_module("mul_mod", "*")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(6, width=8)), s.drive("b", Value(7, width=8))), max_time=0)
        assert int(sim.read("y")) == 42

    def test_div(self, engine):
        m = self._make_binop_module("div_mod", "/")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(42, width=8)), s.drive("b", Value(7, width=8))), max_time=0)
        assert int(sim.read("y")) == 6

    def test_div_by_zero(self, engine):
        m = self._make_binop_module("divz", "/")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(42, width=8)), s.drive("b", Value(0, width=8))), max_time=0)
        assert sim.read("y").is_x

    def test_mod(self, engine):
        m = self._make_binop_module("mod_mod", "%")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(10, width=8)), s.drive("b", Value(3, width=8))), max_time=0)
        assert int(sim.read("y")) == 1

    def test_pow(self, engine):
        m = self._make_binop_module("pow_mod", "**")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(2, width=8)), s.drive("b", Value(5, width=8))), max_time=0)
        assert int(sim.read("y")) == 32


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: bitwise (AND, OR, XOR, XNOR, SHL, SHR, ASHL, ASHR)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestBitwiseOps:
    """Test bitwise operators via continuous assigns."""

    @staticmethod
    def _make_binop_module(name: str, op: str) -> Module:
        w = Range(Literal(7, width=32), Literal(0, width=32))
        return Module(
            name,
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("b", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("b", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), BinaryOp(op, Identifier("a"), Identifier("b")))],
        )

    def test_bit_and(self, engine):
        m = self._make_binop_module("band", "&")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xAA, width=8)), s.drive("b", Value(0x0F, width=8))), max_time=0)
        assert int(sim.read("y")) == 0x0A

    def test_bit_or(self, engine):
        m = self._make_binop_module("bor", "|")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xA0, width=8)), s.drive("b", Value(0x05, width=8))), max_time=0)
        assert int(sim.read("y")) == 0xA5

    def test_bit_xor(self, engine):
        m = self._make_binop_module("bxor", "^")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xFF, width=8)), s.drive("b", Value(0x0F, width=8))), max_time=0)
        assert int(sim.read("y")) == 0xF0

    def test_bit_xnor(self, engine):
        m = self._make_binop_module("bxnor", "~^")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xFF, width=8)), s.drive("b", Value(0x0F, width=8))), max_time=0)
        assert int(sim.read("y")) == 0x0F

    def test_shl(self, engine):
        m = self._make_binop_module("shl", "<<")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0x03, width=8)), s.drive("b", Value(2, width=8))), max_time=0)
        assert int(sim.read("y")) == 0x0C

    def test_shr(self, engine):
        m = self._make_binop_module("shr", ">>")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xC0, width=8)), s.drive("b", Value(2, width=8))), max_time=0)
        assert int(sim.read("y")) == 0x30

    def test_ashl(self, engine):
        m = self._make_binop_module("ashl", "<<<")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0x03, width=8)), s.drive("b", Value(2, width=8))), max_time=0)
        assert int(sim.read("y")) == 0x0C

    def test_ashr(self, engine):
        m = self._make_binop_module("ashr", ">>>")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xC0, width=8)), s.drive("b", Value(2, width=8))), max_time=0)
        # 0xC0 = 11000000, arithmetic shift right 2 → 11110000 = 0xF0
        assert int(sim.read("y")) == 0xF0


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: comparison (NE, LE, GT, GE, CASE_EQ, CASE_NE)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestComparisonOps:
    """Test comparison operators."""

    @staticmethod
    def _make_cmp_module(name: str, op: str) -> Module:
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        return Module(
            name,
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("b", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w1),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=w),
                Net("b", NetKind.WIRE, width=w),
                Net("y", NetKind.WIRE, width=w1),
            ],
            continuous_assigns=[ContinuousAssign(Identifier("y"), BinaryOp(op, Identifier("a"), Identifier("b")))],
        )

    def test_ne_true(self, engine):
        m = self._make_cmp_module("ne_t", "!=")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(3, width=8))), max_time=0)
        assert int(sim.read("y")) == 1

    def test_ne_false(self, engine):
        m = self._make_cmp_module("ne_f", "!=")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(5, width=8))), max_time=0)
        assert int(sim.read("y")) == 0

    def test_le(self, engine):
        m = self._make_cmp_module("le", "<=")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(5, width=8))), max_time=0)
        assert int(sim.read("y")) == 1

    def test_gt(self, engine):
        m = self._make_cmp_module("gt", ">")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(6, width=8)), s.drive("b", Value(5, width=8))), max_time=0)
        assert int(sim.read("y")) == 1

    def test_ge(self, engine):
        m = self._make_cmp_module("ge", ">=")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(6, width=8))), max_time=0)
        assert int(sim.read("y")) == 0

    def test_case_eq(self, engine):
        m = self._make_cmp_module("ceq", "===")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(5, width=8))), max_time=0)
        assert int(sim.read("y")) == 1

    def test_case_ne(self, engine):
        m = self._make_cmp_module("cne", "!==")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(5, width=8))), max_time=0)
        assert int(sim.read("y")) == 0


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: logical (LOG_AND, LOG_OR, LOG_NOT)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestLogicalOps:
    """Test logical operators."""

    def test_log_and(self, engine):
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        m = Module(
            "land",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("b", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w1),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("b", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w1)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), BinaryOp("&&", Identifier("a"), Identifier("b")))],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(5, width=8)), s.drive("b", Value(0, width=8))), max_time=0)
        assert int(sim.read("y")) == 0

    def test_log_or(self, engine):
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        m = Module(
            "lor",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("b", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w1),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("b", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w1)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), BinaryOp("||", Identifier("a"), Identifier("b")))],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0, width=8)), s.drive("b", Value(3, width=8))), max_time=0)
        assert int(sim.read("y")) == 1

    def test_log_not(self, engine):
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        m = Module(
            "lnot",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w1),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w1)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), UnaryOp("!", Identifier("a")))],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0, width=8)), max_time=0)
        assert int(sim.read("y")) == 1


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: unary (NEG, UPLUS)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestUnaryOps:
    """Test unary +/- ops."""

    def test_neg(self, engine):
        w = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "neg",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), UnaryOp("-", Identifier("a")))],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(5, width=8)), max_time=0)
        # -5 in 8-bit two's complement = 251
        assert int(sim.read("y")) == 251

    def test_uplus(self, engine):
        w = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "uplus",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), UnaryOp("+", Identifier("a")))],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(42, width=8)), max_time=0)
        assert int(sim.read("y")) == 42


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: reduction ops (RED_AND, RED_OR, RED_XOR, etc.)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestReductionOps:
    """Test all 6 reduction operators."""

    @staticmethod
    def _make_reduce_module(name: str, op: str) -> Module:
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        return Module(
            name,
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w1),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w1)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), UnaryOp(op, Identifier("a")))],
        )

    def test_red_and_true(self, engine):
        m = self._make_reduce_module("randt", "&")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0xFF, width=8)), max_time=0)
        assert int(sim.read("y")) == 1

    def test_red_and_false(self, engine):
        m = self._make_reduce_module("randf", "&")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0xFE, width=8)), max_time=0)
        assert int(sim.read("y")) == 0

    def test_red_or_true(self, engine):
        m = self._make_reduce_module("rort", "|")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0x01, width=8)), max_time=0)
        assert int(sim.read("y")) == 1

    def test_red_or_false(self, engine):
        m = self._make_reduce_module("rorf", "|")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0x00, width=8)), max_time=0)
        assert int(sim.read("y")) == 0

    def test_red_xor(self, engine):
        m = self._make_reduce_module("rxor", "^")
        sim = Simulator(m, engine=engine)
        # 0x07 = 00000111 → 3 ones → parity 1
        sim.run(lambda s: s.drive("a", Value(0x07, width=8)), max_time=0)
        assert int(sim.read("y")) == 1

    def test_red_nand(self, engine):
        m = self._make_reduce_module("rnand", "~&")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0xFF, width=8)), max_time=0)
        assert int(sim.read("y")) == 0

    def test_red_nor(self, engine):
        m = self._make_reduce_module("rnor", "~|")
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0x00, width=8)), max_time=0)
        assert int(sim.read("y")) == 1

    def test_red_xnor(self, engine):
        m = self._make_reduce_module("rxnor", "~^")
        sim = Simulator(m, engine=engine)
        # 0x07 = 00000111 → 3 ones → parity 1 → xnor = 0
        sim.run(lambda s: s.drive("a", Value(0x07, width=8)), max_time=0)
        assert int(sim.read("y")) == 0


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: bit-select, range-select, concat, replicate
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestExpressionOps:
    """Test BIT_SELECT, RANGE_SELECT, CONCAT, REPLICATE opcodes on the RHS."""

    def test_bit_select_rhs(self, engine):
        """assign y = a[3];"""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        m = Module(
            "bsel",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w1),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w1)],
            continuous_assigns=[ContinuousAssign(Identifier("y"), BitSelect(Identifier("a"), Literal(3, width=32)))],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("a", Value(0x08, width=8)), max_time=0)
        assert int(sim.read("y")) == 1

    def test_range_select_rhs(self, engine):
        """assign y = a[5:2];"""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        w4 = Range(Literal(3, width=32), Literal(0, width=32))
        m = Module(
            "rsel",
            ports=[
                Port("a", PortDirection.INPUT, width=w),
                Port("y", PortDirection.OUTPUT, width=w4),
            ],
            nets=[Net("a", NetKind.WIRE, width=w), Net("y", NetKind.WIRE, width=w4)],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    RangeSelect(Identifier("a"), Literal(5, width=32), Literal(2, width=32)),
                )
            ],
        )
        sim = Simulator(m, engine=engine)
        # a = 0xAC = 10101100, [5:2] = 1011 = 0xB
        sim.run(lambda s: s.drive("a", Value(0xAC, width=8)), max_time=0)
        assert int(sim.read("y")) == 0x0B

    def test_concat_rhs(self, engine):
        """assign y = {a, b};"""
        w4 = Range(Literal(3, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "cat",
            ports=[
                Port("a", PortDirection.INPUT, width=w4),
                Port("b", PortDirection.INPUT, width=w4),
                Port("y", PortDirection.OUTPUT, width=w8),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=w4),
                Net("b", NetKind.WIRE, width=w4),
                Net("y", NetKind.WIRE, width=w8),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    Concatenation([Identifier("a"), Identifier("b")]),
                )
            ],
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: (s.drive("a", Value(0xA, width=4)), s.drive("b", Value(0x5, width=4))), max_time=0)
        assert int(sim.read("y")) == 0xA5

    def test_replicate_rhs(self, engine):
        """assign y = {4{a}};"""
        w2 = Range(Literal(1, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "rep",
            ports=[
                Port("a", PortDirection.INPUT, width=w2),
                Port("y", PortDirection.OUTPUT, width=w8),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=w2),
                Net("y", NetKind.WIRE, width=w8),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    Replication(Literal(4, width=32), Identifier("a")),
                )
            ],
        )
        sim = Simulator(m, engine=engine)
        # a = 2'b10, {4{2'b10}} = 10101010 = 0xAA
        sim.run(lambda s: s.drive("a", Value(0x2, width=2)), max_time=0)
        assert int(sim.read("y")) == 0xAA


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: STORE_BIT, NBA_BIT, NBA_RANGE
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestBitNBAOps:
    """Test bit-level and range-level NBA stores."""

    def test_store_bit(self, engine):
        """initial begin x = 0; x[3] = 1'b1; end  → x == 8."""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "stbit",
            ports=[],
            variables=[Variable("x", VariableKind.REG, width=w)],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("x"), Literal(0, width=8)),
                        BlockingAssign(
                            BitSelect(Identifier("x"), Literal(3, width=32)),
                            Literal(1, width=1),
                        ),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("x")) == 8

    def test_nba_bit(self, engine):
        """always @(posedge clk) x[0] <= 1'b1; — via Clock driver."""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "nbabit",
            ports=[Port("clk", PortDirection.INPUT)],
            nets=[Net("clk", NetKind.WIRE)],
            variables=[Variable("x", VariableKind.REG, width=w)],
        )
        m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("x"), Literal(0, width=8))))
        m.always_blocks.append(
            AlwaysBlock(
                body=NonblockingAssign(
                    BitSelect(Identifier("x"), Literal(0, width=32)),
                    Literal(1, width=1),
                ),
                sensitivity_type=SensitivityType.SEQUENTIAL,
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            )
        )
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=15)
        assert int(sim.read("x")) == 1

    def test_nba_range(self, engine):
        """always @(posedge clk) x[2 +: 4] <= 4'hA; — via Clock driver."""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "nbarange",
            ports=[Port("clk", PortDirection.INPUT)],
            nets=[Net("clk", NetKind.WIRE)],
            variables=[Variable("x", VariableKind.REG, width=w)],
        )
        m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("x"), Literal(0, width=8))))
        m.always_blocks.append(
            AlwaysBlock(
                body=NonblockingAssign(
                    PartSelect(Identifier("x"), Literal(2, width=32), Literal(4, width=32), "+:"),
                    Literal(0xA, width=4),
                ),
                sensitivity_type=SensitivityType.SEQUENTIAL,
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            )
        )
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=15)
        # x[5:2] = 0xA = 1010, so x = 00101000 = 0x28
        assert int(sim.read("x")) == 0x28


# ══════════════════════════════════════════════════════════════════════
# Opcode coverage: SYS_TIME
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestSysTime:
    """Test $time system function."""

    def test_sys_time_at_zero(self, engine):
        """initial $display("%0d", $time); → should output '0'."""
        w = Range(Literal(31, width=32), Literal(0, width=32))
        m = Module(
            "systime",
            ports=[],
            variables=[Variable("t", VariableKind.REG, width=w)],
        )
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("t"),
                    FunctionCall("$time", []),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("t")) == 0

    def test_realtime(self, engine):
        """$realtime should behave identically to $time (integer time)."""
        w = Range(Literal(31, width=32), Literal(0, width=32))
        m = Module(
            "realtime_mod",
            ports=[],
            variables=[Variable("t", VariableKind.REG, width=w)],
        )
        m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("t"), FunctionCall("$realtime", []))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("t")) == 0

    def test_stime(self, engine):
        """$stime should return time as 32-bit value."""
        w = Range(Literal(31, width=32), Literal(0, width=32))
        m = Module(
            "stime_mod",
            ports=[],
            variables=[Variable("t", VariableKind.REG, width=w)],
        )
        m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("t"), FunctionCall("$stime", []))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("t")) == 0


# ══════════════════════════════════════════════════════════════════════
# x/z Propagation Tests
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestXZPropagation:
    """Test 4-state (x/z) value propagation through the VM pipeline."""

    # Helper: create a module that initializes regs and computes y via continuous assign
    @staticmethod
    def _make_xz_module(assign_rhs, *, a_init=None, b_init=None, width=8):
        """Build a module with regs a,b, wire y, assign y = <assign_rhs(a,b)>.

        If a_init/b_init is None, the reg stays at its initial x value.
        """
        w = Range(Literal(width - 1, width=32), Literal(0, width=32))
        m = Module(
            "xzmod",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w)],
            variables=[
                Variable("a", VariableKind.REG, width=w),
                Variable("b", VariableKind.REG, width=w),
            ],
        )
        # Optional initialization
        stmts = []
        if a_init is not None:
            stmts.append(BlockingAssign(Identifier("a"), a_init))
        if b_init is not None:
            stmts.append(BlockingAssign(Identifier("b"), b_init))
        if stmts:
            m.initial_blocks.append(InitialBlock(SeqBlock(stmts) if len(stmts) > 1 else stmts[0]))
        # Continuous assign for result
        m.continuous_assigns.append(ContinuousAssign(Identifier("y"), assign_rhs))
        return m

    # ── Arithmetic with x ────────────────────────────────────────

    def test_add_x_propagates(self, engine):
        """x + 5 → x."""
        m = self._make_xz_module(
            BinaryOp("+", Identifier("a"), Literal(5, width=8)),
            b_init=Literal(5, width=8),  # b unused but set for clarity
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_x, f"Expected x, got {y}"

    def test_sub_x_propagates(self, engine):
        """x - 3 → x."""
        m = self._make_xz_module(
            BinaryOp("-", Identifier("a"), Literal(3, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_mul_x_propagates(self, engine):
        """x * 2 → x."""
        m = self._make_xz_module(
            BinaryOp("*", Identifier("a"), Literal(2, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_div_by_zero_produces_x(self, engine):
        """5 / 0 → x."""
        m = self._make_xz_module(
            BinaryOp("/", Literal(5, width=8), Literal(0, width=8)),
            a_init=Literal(5, width=8),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_mod_x_propagates(self, engine):
        """x % 3 → x."""
        m = self._make_xz_module(
            BinaryOp("%", Identifier("a"), Literal(3, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_pow_x_propagates(self, engine):
        """x ** 2 → x."""
        m = self._make_xz_module(
            BinaryOp("**", Identifier("a"), Literal(2, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    # ── Bitwise with x: 4-state semantics ────────────────────────

    def test_bitwise_and_x_with_zero(self, engine):
        """x & 0 → 0 (any bit ANDed with 0 is 0)."""
        m = self._make_xz_module(
            BinaryOp("&", Identifier("a"), Literal(0, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 0, f"Expected 0, got {y}"

    def test_bitwise_and_x_with_ones(self, engine):
        """x & 0xFF → x (any bit ANDed with 1 keeps its x state)."""
        m = self._make_xz_module(
            BinaryOp("&", Identifier("a"), Literal(0xFF, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_bitwise_or_x_with_ones(self, engine):
        """x | 0xFF → 0xFF (any bit ORed with 1 is 1)."""
        m = self._make_xz_module(
            BinaryOp("|", Identifier("a"), Literal(0xFF, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 0xFF, f"Expected 0xFF, got {y}"

    def test_bitwise_or_x_with_zero(self, engine):
        """x | 0 → x."""
        m = self._make_xz_module(
            BinaryOp("|", Identifier("a"), Literal(0, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_bitwise_xor_x_propagates(self, engine):
        """x ^ anything → x."""
        m = self._make_xz_module(
            BinaryOp("^", Identifier("a"), Literal(0x55, width=8)),
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_bitwise_not_x(self, engine):
        """~x → x."""
        w = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "notx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w)],
            variables=[Variable("a", VariableKind.REG, width=w)],
        )
        m.continuous_assigns.append(ContinuousAssign(Identifier("y"), UnaryOp("~", Identifier("a"))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    # ── Comparison with x ────────────────────────────────────────

    def test_eq_x_produces_x(self, engine):
        """x == 5 → x(1)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "eqx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("==", Identifier("a"), Literal(5, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_ne_x_produces_x(self, engine):
        """x != 5 → x(1)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "nex",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("!=", Identifier("a"), Literal(5, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_lt_x_produces_x(self, engine):
        """x < 5 → x(1)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "ltx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("<", Identifier("a"), Literal(5, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    # ── Case equality with x ────────────────────────────────────

    def test_case_eq_x_matches_x(self, engine):
        """x === x → 1 (case equality compares x bits)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "ceqx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8), Variable("b", VariableKind.REG, width=w8)],
        )
        # a and b both uninitialized → both x
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("===", Identifier("a"), Identifier("b")))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 1, f"Expected 1, got {y}"

    def test_case_eq_x_vs_value(self, engine):
        """x === 5 → 0 (x bits don't match defined bits)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "ceqxv",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("===", Identifier("a"), Literal(5, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 0, f"Expected 0, got {y}"

    def test_case_ne_x_vs_value(self, engine):
        """x !== 5 → 1."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "cnex",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("!==", Identifier("a"), Literal(5, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 1, f"Expected 1, got {y}"

    # ── Logical with x (short-circuit) ───────────────────────────

    def test_logical_and_x_with_zero(self, engine):
        """x && 0 → 0 (short-circuit: anything && 0 = 0)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "landx0",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("&&", Identifier("a"), Literal(0, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 0, f"Expected 0, got {y}"

    def test_logical_or_x_with_one(self, engine):
        """x || 1 → 1 (short-circuit: anything || 1 = 1)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "lorx1",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("||", Identifier("a"), Literal(1, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 1, f"Expected 1, got {y}"

    def test_logical_and_x_with_one(self, engine):
        """x && 1 → x (can't short-circuit)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "landx1",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("&&", Identifier("a"), Literal(1, width=8)))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_logical_not_x(self, engine):
        """!x → x."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "lnotx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(ContinuousAssign(Identifier("y"), UnaryOp("!", Identifier("a"))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    # ── Reduction with x ─────────────────────────────────────────

    def test_reduce_and_x(self, engine):
        """&x → x."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "randx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(ContinuousAssign(Identifier("y"), UnaryOp("&", Identifier("a"))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_reduce_or_x(self, engine):
        """| x → x (all bits unknown)."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "rorx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w1)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(ContinuousAssign(Identifier("y"), UnaryOp("|", Identifier("a"))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    # ── Ternary with x condition ─────────────────────────────────

    def test_ternary_x_condition_merges(self, engine):
        """x ? 0xFF : 0x00 → x (merges both branches)."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "ternx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(
                Identifier("y"),
                TernaryOp(Identifier("a"), Literal(0xFF, width=8), Literal(0x00, width=8)),
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    def test_ternary_x_condition_same_value(self, engine):
        """x ? 0xAA : 0xAA → 0xAA (both branches same ⇒ defined)."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "ternxs",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(
                Identifier("y"),
                TernaryOp(Identifier("a"), Literal(0xAA, width=8), Literal(0xAA, width=8)),
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        y = sim.read("y")
        assert y.is_defined and int(y) == 0xAA, f"Expected 0xAA, got {y}"

    # ── Unary with x ─────────────────────────────────────────────

    def test_neg_x(self, engine):
        """-x → x."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "negx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(ContinuousAssign(Identifier("y"), UnaryOp("-", Identifier("a"))))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x

    # ── x literal in initial block ───────────────────────────────

    def test_x_literal_value(self, engine):
        """Assigning an x literal preserves x state."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module("xlit", ports=[], variables=[Variable("a", VariableKind.REG, width=w8)])
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("a"),
                    Literal(0, width=8, is_x=True),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("a").is_x

    def test_partial_x_literal(self, engine):
        """4'b1x0x → val=8, mask=5, defined bits preserved."""
        w = Range(Literal(3, width=32), Literal(0, width=32))
        m = Module("pxlit", ports=[], variables=[Variable("a", VariableKind.REG, width=w)])
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    Identifier("a"),
                    Literal(0, width=4, original_text="4'b1x0x"),
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        a = sim.read("a")
        assert a.is_x
        assert a.val == 0b1000  # bit 3 = 1, others 0 or x
        assert a.mask == 0b0101  # bits 0 and 2 are x

    # ── Shift with x amount ──────────────────────────────────────

    def test_shl_x_amount(self, engine):
        """5 << x → x."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "shlx",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[Variable("a", VariableKind.REG, width=w8)],
        )
        m.continuous_assigns.append(
            ContinuousAssign(Identifier("y"), BinaryOp("<<", Literal(5, width=8), Identifier("a")))
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y").is_x


# ══════════════════════════════════════════════════════════════════════
# Memory Write Triggers Combo Re-evaluation
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestMemoryComboReeval:
    """Test that memory writes trigger combinational process re-evaluation."""

    def test_blocking_mem_write_triggers_combo(self, engine):
        """initial mem[0] = 42; assign y = mem[0]; → y == 42."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        dim = Range(Literal(3, width=32), Literal(0, width=32))
        m = Module(
            "memcombo",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[
                Variable("mem", VariableKind.REG, width=w8, dimensions=[dim]),
            ],
        )
        # initial mem[0] = 42;
        m.initial_blocks.append(
            InitialBlock(
                BlockingAssign(
                    BitSelect(Identifier("mem"), Literal(0, width=32)),
                    Literal(42, width=8),
                )
            )
        )
        # assign y = mem[0];
        m.continuous_assigns.append(
            ContinuousAssign(
                Identifier("y"),
                BitSelect(Identifier("mem"), Literal(0, width=32)),
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("y")) == 42

    def test_nba_mem_write_triggers_combo(self, engine):
        """always @(posedge clk) mem[1] <= data; assign y = mem[1]; → y == 99."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        dim = Range(Literal(3, width=32), Literal(0, width=32))
        m = Module(
            "memcombo_nba",
            ports=[Port("clk", PortDirection.INPUT)],
            nets=[Net("clk", NetKind.WIRE), Net("y", NetKind.WIRE, width=w8)],
            variables=[
                Variable("data", VariableKind.REG, width=w8),
                Variable("mem", VariableKind.REG, width=w8, dimensions=[dim]),
            ],
        )
        # initial data = 99;
        m.initial_blocks.append(InitialBlock(BlockingAssign(Identifier("data"), Literal(99, width=8))))
        # always @(posedge clk) mem[1] <= data;
        m.always_blocks.append(
            AlwaysBlock(
                body=NonblockingAssign(
                    BitSelect(Identifier("mem"), Literal(1, width=32)),
                    Identifier("data"),
                ),
                sensitivity_type=SensitivityType.SEQUENTIAL,
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            )
        )
        # assign y = mem[1];
        m.continuous_assigns.append(
            ContinuousAssign(
                Identifier("y"),
                BitSelect(Identifier("mem"), Literal(1, width=32)),
            )
        )
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=15)
        assert int(sim.read("y")) == 99

    def test_mem_write_combo_always_reeval(self, engine):
        """Combo always block reading memory re-fires on memory write."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        dim = Range(Literal(1, width=32), Literal(0, width=32))
        m = Module(
            "memcawb",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[
                Variable("mem", VariableKind.REG, width=w8, dimensions=[dim]),
            ],
        )
        # initial begin mem[0] = 10; mem[1] = 20; end
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(BitSelect(Identifier("mem"), Literal(0, width=32)), Literal(10, width=8)),
                        BlockingAssign(BitSelect(Identifier("mem"), Literal(1, width=32)), Literal(20, width=8)),
                    ]
                )
            )
        )
        # assign y = mem[0] + mem[1];
        m.continuous_assigns.append(
            ContinuousAssign(
                Identifier("y"),
                BinaryOp(
                    "+",
                    BitSelect(Identifier("mem"), Literal(0, width=32)),
                    BitSelect(Identifier("mem"), Literal(1, width=32)),
                ),
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("y")) == 30


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestMonitorRefire:
    """$monitor re-fire semantics: fires at end of timestep when any arg changes."""

    def _make_monitor_module(self):
        """Build a module with a clock driving a counter, and $monitor on the counter.

        module monitor_test;
          reg clk;
          reg [7:0] count;
          initial begin
            clk = 0;
            count = 0;
            $monitor(count);
          end
          always @(posedge clk) count <= count + 1;
        endmodule
        """
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "monitor_test",
            ports=[],
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, width=w1),
                Variable("count", VariableKind.REG, width=w8),
            ],
        )
        # initial begin clk = 0; count = 0; $monitor(count); end
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("clk"), Literal(0, width=1)),
                        BlockingAssign(Identifier("count"), Literal(0, width=8)),
                        SystemTaskCall("$monitor", [Identifier("count")]),
                    ]
                )
            )
        )
        # always @(posedge clk) count <= count + 1;
        m.always_blocks.append(
            AlwaysBlock(
                NonblockingAssign(
                    Identifier("count"),
                    BinaryOp("+", Identifier("count"), Literal(1, width=8)),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            )
        )
        return m

    def test_monitor_fires_on_initial(self, engine):
        """$monitor fires immediately when first called (initial block)."""
        m = self._make_monitor_module()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        # The initial block sets count=0 and calls $monitor(count)
        # which should print once at t=0
        output = sim.display_output
        assert len(output) >= 1, f"Expected at least 1 monitor output, got {output}"
        # First line should show count=0
        assert "0" in output[0]

    def test_monitor_refires_on_change(self, engine):
        """$monitor re-fires at end of timestep when monitored signal changes."""
        m = self._make_monitor_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        # Run for 25 time units: posedge at t=5,15,25 → count becomes 1,2,3
        sim.run(max_time=25)
        output = sim.display_output
        # Should have:
        # t=0: initial $monitor prints count=0
        # t=5: count changes 0→1, monitor re-fires
        # t=15: count changes 1→2, monitor re-fires
        # t=25: count changes 2→3, monitor re-fires
        # That's 4 monitor outputs total
        assert len(output) >= 4, f"Expected >= 4 monitor outputs, got {len(output)}: {output}"

    def test_monitor_no_refire_if_unchanged(self, engine):
        """$monitor does not re-fire at timestep where monitored signal is unchanged."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "mon_nochange",
            ports=[],
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, width=w1),
                Variable("val", VariableKind.REG, width=w8),
            ],
        )
        # initial begin clk = 0; val = 42; $monitor(val); end
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("clk"), Literal(0, width=1)),
                        BlockingAssign(Identifier("val"), Literal(42, width=8)),
                        SystemTaskCall("$monitor", [Identifier("val")]),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=25)
        output = sim.display_output
        # 'val' never changes so $monitor should only fire once (the initial invocation)
        # Clock toggles shouldn't cause re-fire since val is not changing
        assert len(output) == 1, f"Expected exactly 1 monitor output (no re-fire), got {len(output)}: {output}"

    def test_monitor_with_format_string(self, engine):
        """$monitor with format string refires correctly."""
        w1 = Range(Literal(0, width=32), Literal(0, width=32))
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "mon_fmt",
            ports=[],
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, width=w1),
                Variable("cnt", VariableKind.REG, width=w8),
            ],
        )
        # initial begin clk=0; cnt=0; $monitor("cnt=%0d", cnt); end
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(Identifier("clk"), Literal(0, width=1)),
                        BlockingAssign(Identifier("cnt"), Literal(0, width=8)),
                        SystemTaskCall("$monitor", [StringLiteral("cnt=%0d"), Identifier("cnt")]),
                    ]
                )
            )
        )
        # always @(posedge clk) cnt <= cnt + 1;
        m.always_blocks.append(
            AlwaysBlock(
                NonblockingAssign(
                    Identifier("cnt"),
                    BinaryOp("+", Identifier("cnt"), Literal(1, width=8)),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            )
        )
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=15)
        output = sim.display_output
        # t=0: cnt=0, t=5: cnt=1, t=15: cnt=2
        assert len(output) >= 3, f"Expected >= 3, got {len(output)}: {output}"
        assert "cnt=0" in output[0]
        assert "cnt=1" in output[1]
        assert "cnt=2" in output[2]


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestParBlockAndHierarchical:
    """ParBlock sequential warning and hierarchical identifier error."""

    def test_parblock_emits_warning(self, engine):
        """ParBlock compiles as sequential and emits a warning."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "parmod",
            ports=[],
            nets=[],
            variables=[
                Variable("a", VariableKind.REG, width=w8),
                Variable("b", VariableKind.REG, width=w8),
            ],
        )
        # initial fork a = 1; b = 2; join
        m.initial_blocks.append(
            InitialBlock(
                ParBlock(
                    [
                        BlockingAssign(Identifier("a"), Literal(1, width=8)),
                        BlockingAssign(Identifier("b"), Literal(2, width=8)),
                    ]
                )
            )
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sim = Simulator(m, engine=engine)
            sim.run(max_time=0)
        par_warns = [x for x in w if "ParBlock" in str(x.message)]
        assert len(par_warns) >= 1, f"Expected ParBlock warning, got: {[str(x.message) for x in w]}"
        # Still works — sequential execution sets a=1, b=2
        assert int(sim.read("a")) == 1
        assert int(sim.read("b")) == 2

    def test_hierarchical_identifier_accepted(self, engine):
        """Hierarchical identifier (e.g. inst.sig) is accepted for flattened hierarchy support."""
        w8 = Range(Literal(7, width=32), Literal(0, width=32))
        m = Module(
            "hiermod",
            ports=[],
            nets=[Net("y", NetKind.WIRE, width=w8)],
            variables=[
                Variable("x", VariableKind.REG, width=w8),
            ],
        )
        # assign y = inst.sub_sig  (hierarchical reference — valid after flattening)
        m.continuous_assigns.append(
            ContinuousAssign(
                Identifier("y"),
                Identifier("inst.sub_sig"),
            )
        )
        # Should no longer raise — dotted names are valid for flattened hierarchy
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)


# ══════════════════════════════════════════════════════════════════════
# File I/O Tests
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestFileIO:
    """Test $fopen, $fclose, $fdisplay, $fwrite, $feof system tasks."""

    def test_fopen_fdisplay_fclose(self, tmp_path, engine):
        """$fopen → $fdisplay → $fclose should create a file with content."""
        outfile = str(tmp_path / "test_out.txt")
        w = Range(Literal(31, width=32), Literal(0, width=32))
        m = Module(
            "fio_mod",
            ports=[],
            variables=[
                Variable("fd", VariableKind.INTEGER, width=w),
                Variable("x", VariableKind.REG, width=w),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    statements=[
                        BlockingAssign(Identifier("x"), Literal(42, width=32)),
                        BlockingAssign(
                            Identifier("fd"), FunctionCall("$fopen", [StringLiteral(outfile), StringLiteral("w")])
                        ),
                        SystemTaskCall("$fdisplay", [Identifier("fd"), StringLiteral("%0d"), Identifier("x")]),
                        SystemTaskCall("$fclose", [Identifier("fd")]),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        content = (tmp_path / "test_out.txt").read_text()
        assert "42" in content

    def test_fwrite_no_newline(self, tmp_path, engine):
        """$fwrite should write without trailing newline."""
        outfile = str(tmp_path / "test_fwrite.txt")
        w = Range(Literal(31, width=32), Literal(0, width=32))
        m = Module(
            "fw_mod",
            ports=[],
            variables=[
                Variable("fd", VariableKind.INTEGER, width=w),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    statements=[
                        BlockingAssign(
                            Identifier("fd"), FunctionCall("$fopen", [StringLiteral(outfile), StringLiteral("w")])
                        ),
                        SystemTaskCall("$fwrite", [Identifier("fd"), StringLiteral("hello")]),
                        SystemTaskCall("$fclose", [Identifier("fd")]),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        content = (tmp_path / "test_fwrite.txt").read_text()
        assert content == "hello"

    def test_feof(self, tmp_path, engine):
        """$feof should return 1 after reading past end of file."""
        infile = tmp_path / "test_in.txt"
        infile.write_text("")  # empty file
        w = Range(Literal(31, width=32), Literal(0, width=32))
        m = Module(
            "feof_mod",
            ports=[],
            variables=[
                Variable("fd", VariableKind.INTEGER, width=w),
                Variable("eof_flag", VariableKind.REG, width=Range(Literal(0, width=32), Literal(0, width=32))),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    statements=[
                        BlockingAssign(
                            Identifier("fd"), FunctionCall("$fopen", [StringLiteral(str(infile)), StringLiteral("r")])
                        ),
                        BlockingAssign(Identifier("eof_flag"), FunctionCall("$feof", [Identifier("fd")])),
                        SystemTaskCall("$fclose", [Identifier("fd")]),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert int(sim.read("eof_flag")) == 1

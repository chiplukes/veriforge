"""Tests for VCD waveform output.

Covers:
    - Shared attach_vcd helper
  - VCD identifier generation
  - Value-to-VCD conversion (1-bit, multi-bit, x/z)
  - VCD header format
  - Signal registration
  - Value change recording
  - Deduplication (no-change suppression)
  - $dumpvars initial section
  - String and file output
"""

import io

from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, Range
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import IfStatement, NonblockingAssign, SensitivityEdge
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.trace import attach_vcd
from veriforge.sim.vcd import VcdWriter, _make_id, _value_to_vcd
from veriforge.sim.value import Value


def _make_trace_probe() -> Module:
    module = Module(
        "trace_probe",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("rst_n", PortDirection.INPUT),
            Port("q", PortDirection.OUTPUT),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("rst_n", NetKind.WIRE),
        ],
        variables=[
            Variable("q", VariableKind.REG),
        ],
    )
    module.initial_blocks.append(InitialBlock(NonblockingAssign(Identifier("q"), Literal(0, width=1))))
    module.always_blocks.append(
        AlwaysBlock(
            sensitivity_list=[
                SensitivityEdge("posedge", Identifier("clk")),
                SensitivityEdge("negedge", Identifier("rst_n")),
            ],
            sensitivity_type=SensitivityType.SEQUENTIAL,
            body=IfStatement(
                BinaryOp("==", Identifier("rst_n"), Literal(0, width=1)),
                NonblockingAssign(Identifier("q"), Literal(0, width=1)),
                NonblockingAssign(Identifier("q"), BinaryOp("^", Identifier("q"), Literal(1, width=1))),
            ),
        )
    )
    return module


# ── Identifier generation ────────────────────────────────────────────


class TestMakeId:
    def test_first_ids(self):
        assert _make_id(1) == '"'  # chr(33+1) = '"'
        assert _make_id(0) == "!"  # chr(33+0) = '!'

    def test_unique(self):
        ids = {_make_id(i) for i in range(200)}
        assert len(ids) == 200  # all unique


# ── Value conversion ─────────────────────────────────────────────────


class TestValueToVcd:
    def test_single_bit_zero(self):
        assert _value_to_vcd(Value(0, width=1), 1) == "0"

    def test_single_bit_one(self):
        assert _value_to_vcd(Value(1, width=1), 1) == "1"

    def test_single_bit_x(self):
        assert _value_to_vcd(Value.x(1), 1) == "x"

    def test_multi_bit(self):
        assert _value_to_vcd(Value(0b1010, width=4), 4) == "1010"

    def test_multi_bit_x(self):
        # Value with some x bits
        v = Value(0b10, width=4, mask=0b0100)  # bit 2 is x
        result = _value_to_vcd(v, 4)
        assert result == "0x10"

    def test_8_bit_value(self):
        assert _value_to_vcd(Value(0xFF, width=8), 8) == "11111111"

    def test_all_x(self):
        result = _value_to_vcd(Value.x(4), 4)
        assert result == "xxxx"


# ── VCD Writer ───────────────────────────────────────────────────────


class TestVcdWriter:
    def test_header(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("clk", width=1)
        w.add_signal("count", width=8, scope="counter")
        w.write_header()

        output = buf.getvalue()
        assert "$date" in output
        assert "$version" in output
        assert "$timescale 1ns $end" in output
        assert "$var wire 1" in output
        assert "clk" in output
        assert "$var wire 8" in output
        assert "count" in output
        assert "$enddefinitions $end" in output

    def test_value_changes(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("clk", width=1)
        w.write_header()

        w.set_time(0)
        w.change("clk", Value(0, width=1))
        w.set_time(5)
        w.change("clk", Value(1, width=1))
        w.set_time(10)
        w.change("clk", Value(0, width=1))

        output = buf.getvalue()
        assert "#0" in output
        assert "#5" in output
        assert "#10" in output

    def test_multi_bit_change(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("bus", width=8)
        w.write_header()

        w.set_time(0)
        w.change("bus", Value(0xAB, width=8))

        output = buf.getvalue()
        assert "b10101011" in output

    def test_deduplication(self):
        """Same value should not be written twice."""
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("clk", width=1)
        w.write_header()

        w.set_time(0)
        w.change("clk", Value(1, width=1))
        w.set_time(5)
        w.change("clk", Value(1, width=1))  # same value — should be suppressed

        output = buf.getvalue()
        # Should only have one "1" change, not two
        lines = output.strip().split("\n")
        # Count lines that start with "1" (the value for 1-bit signal)
        value_lines = [line for line in lines if line.startswith("1") and not line.startswith("$")]
        assert len(value_lines) == 1

    def test_unknown_signal_ignored(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("clk", width=1)
        w.write_header()
        w.set_time(0)
        w.change("nonexistent", Value(0, width=1))  # should not crash

    def test_dumpvars(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("clk", width=1)
        w.add_signal("data", width=8)
        w.write_header()
        w.write_initial({"clk": Value(0, width=1), "data": Value(0xFF, width=8)})

        output = buf.getvalue()
        assert "$dumpvars" in output
        assert "$end" in output

    def test_dump_all(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("a", width=1)
        w.add_signal("b", width=1)
        w.write_header()
        w.dump_all(0, {"a": Value(0, width=1), "b": Value(1, width=1)})

        output = buf.getvalue()
        assert "#0" in output

    def test_context_manager(self):
        buf = io.StringIO()
        with VcdWriter(buf, timescale="1ns") as w:
            w.add_signal("x", width=1)
            w.write_header()
            w.set_time(0)
            w.change("x", Value(1, width=1))
        # Should not raise

    def test_scope_grouping(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("a", width=1, scope="mod1")
        w.add_signal("b", width=1, scope="mod2")
        w.write_header()

        output = buf.getvalue()
        assert "$scope module mod1" in output
        assert "$scope module mod2" in output
        assert output.count("$upscope") == 2

    def test_x_value_in_vcd(self):
        buf = io.StringIO()
        w = VcdWriter(buf, timescale="1ns")
        w.add_signal("sig", width=1)
        w.write_header()
        w.set_time(0)
        w.change("sig", Value.x(1))

        output = buf.getvalue()
        # x value should appear
        lines = output.strip().split("\n")
        x_lines = [line for line in lines if line.startswith("x")]
        assert len(x_lines) >= 1


class TestAttachVcd:
    def test_records_transitions_and_restores_callback(self):
        sim = Simulator(_make_trace_probe(), engine="reference")
        sim.drive("rst_n", 0)
        sim.run(max_time=0)
        sim.fork(Clock(sim.signal("clk"), period=10))

        callback_times = []

        def _existing_callback(sched) -> None:
            callback_times.append(sched.time)

        sim._sched._on_time_step = _existing_callback
        sim.drive("rst_n", 1)

        buf = io.StringIO()
        with attach_vcd(sim, buf, signal_names=["clk", "rst_n", "q"]):
            sim.run(max_time=25)

        output = buf.getvalue()
        assert "$dumpvars" in output
        assert " q $end" in output
        assert "#5" in output
        assert "#10" in output
        assert callback_times
        assert sim._sched._on_time_step is _existing_callback

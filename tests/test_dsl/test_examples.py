"""DSL examples — simple, complete designs that also serve as tests.

Each test builds a module with the DSL, emits Verilog, and simulates to verify
correct behavior. These are realistic hardware designs, not unit-test fragments.
"""

from __future__ import annotations

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl import Module, cat, mux, posedge
from veriforge.sim import Clock, Simulator


# ===================================================================
# Example 1: 8-bit up-counter with synchronous reset
# ===================================================================


class TestCounter:
    """8-bit counter: increments on posedge clk, resets to 0 when rst is high."""

    def _build(self) -> Module:
        m = Module("counter")
        clk = m.input("clk")
        rst = m.input("rst")
        count = m.output_reg("count", width=8)

        with m.always(posedge(clk)):
            with m.if_(rst):
                count <<= 0
            with m.else_():
                count <<= count + 1

        return m

    def test_emit(self):
        m = self._build()
        v = emit_module(m.build())
        assert "module counter" in v
        assert "input clk" in v
        assert "input rst" in v
        assert "output reg [7:0] count" in v
        assert "always @(posedge clk)" in v
        assert "count <= 0" in v
        assert "count <= count + 1" in v

    def test_simulate(self):
        m = self._build()
        module = m.build()
        sim = Simulator(module)
        sim.fork(Clock(sim.signal("clk"), period=10))

        def test(s):
            s.drive("rst", 1)

        sim.run(test, max_time=25)  # posedges with rst=1 → count stays 0
        assert sim.read("count") == 0


# ===================================================================
# Example 2: FSM — traffic light controller
# ===================================================================


class TestFSM:
    """3-state FSM: GREEN → YELLOW → RED → GREEN.

    Transitions happen every cycle. Outputs one-hot encoded.
    """

    def _build(self) -> Module:
        m = Module("traffic_light")
        clk = m.input("clk")
        rst = m.input("rst")
        green = m.output_reg("green")
        yellow = m.output_reg("yellow")
        red = m.output_reg("red")
        state = m.reg("state", width=2)

        # State encoding
        S_GREEN = 0
        S_YELLOW = 1
        S_RED = 2

        # State register
        with m.always(posedge(clk)):
            with m.if_(rst):
                state <<= S_GREEN
            with m.else_():
                with m.case(state) as c:
                    with c.when(S_GREEN):
                        state <<= S_YELLOW
                    with c.when(S_YELLOW):
                        state <<= S_RED
                    with c.when(S_RED):
                        state <<= S_GREEN
                    with c.default():
                        state <<= S_GREEN

        # Output decode
        with m.always():
            green @= 0
            yellow @= 0
            red @= 0
            with m.case(state) as c:
                with c.when(S_GREEN):
                    green @= 1
                with c.when(S_YELLOW):
                    yellow @= 1
                with c.when(S_RED):
                    red @= 1
                with c.default():
                    pass

        return m

    def test_emit(self):
        m = self._build()
        v = emit_module(m.build())
        assert "module traffic_light" in v
        assert "reg [1:0] state" in v
        assert "case (state)" in v

    def test_simulate(self):
        m = self._build()
        module = m.build()

        # After reset: should be in GREEN state
        sim = Simulator(module)
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("rst", 1)
        sim.run(max_time=15)  # one posedge with rst=1
        assert sim.read("green") == 1  # after reset → GREEN


# ===================================================================
# Example 3: 4-bit ALU
# ===================================================================


class TestALU:
    """4-bit ALU with 4 operations: ADD, SUB, AND, OR.

    op=0 → ADD, op=1 → SUB, op=2 → AND, op=3 → OR.
    """

    def _build(self) -> Module:
        m = Module("alu")
        a = m.input("a", width=4)
        b = m.input("b", width=4)
        op = m.input("op", width=2)
        result = m.output_reg("result", width=4)

        with m.always():
            with m.case(op) as c:
                with c.when(0):
                    result @= a + b
                with c.when(1):
                    result @= a - b
                with c.when(2):
                    result @= a & b
                with c.when(3):
                    result @= a | b
                with c.default():
                    result @= 0

        return m

    def test_emit(self):
        m = self._build()
        v = emit_module(m.build())
        assert "module alu" in v
        assert "input [3:0] a" in v
        assert "case (op)" in v
        assert "result = a + b" in v
        assert "result = a - b" in v
        assert "result = a & b" in v

    def test_simulate(self):
        m = self._build()

        def check(a_val, b_val, op_val, expected):
            sim = Simulator(m.build())
            sim.drive("a", a_val)
            sim.drive("b", b_val)
            sim.drive("op", op_val)
            sim.run(lambda s: None, max_time=10)
            got = sim.read("result") & 0xF  # 4-bit mask
            assert got == (expected & 0xF), f"op={op_val}: {a_val},{b_val} → {got} (expected {expected & 0xF})"

        check(3, 5, 0, 8)  # ADD
        check(7, 2, 1, 5)  # SUB
        check(0xF, 0x3, 2, 0x3)  # AND
        check(0xA, 0x5, 3, 0xF)  # OR


# ===================================================================
# Example 4: Shift register (serial-in, parallel-out)
# ===================================================================


class TestShiftRegister:
    """8-bit serial-in parallel-out shift register.

    On each posedge clk, shifts left and inserts din at bit 0.
    """

    def _build(self) -> Module:
        m = Module("shift_reg")
        clk = m.input("clk")
        rst = m.input("rst")
        din = m.input("din")
        dout = m.output_reg("dout", width=8)

        with m.always(posedge(clk)):
            with m.if_(rst):
                dout <<= 0
            with m.else_():
                dout <<= cat(dout[6:0], din)

        return m

    def test_emit(self):
        m = self._build()
        v = emit_module(m.build())
        assert "module shift_reg" in v
        assert "output reg [7:0] dout" in v
        assert "{dout[6:0], din}" in v

    def test_simulate(self):
        m = self._build()
        module = m.build()
        sim = Simulator(module)
        sim.fork(Clock(sim.signal("clk"), period=10))

        def test(s):
            s.drive("rst", 0)
            s.drive("din", 1)

        # din=1 from t=0, first posedge at t=0 loads din into bit0
        sim.run(test, max_time=5)
        # After one posedge, dout[0] should be 1
        assert (sim.read("dout") & 1) == 1


# ===================================================================
# Example 5: Parameterized mux (Python loop = generate)
# ===================================================================


class TestParameterizedMux:
    """N-input mux built with a Python loop (equivalent to generate).

    Demonstrates how Python replaces Verilog generate constructs.
    """

    def _build(self, n_inputs: int = 4, width: int = 8) -> Module:
        import math

        sel_width = max(1, math.ceil(math.log2(n_inputs)))

        m = Module("mux_n")
        sel = m.input("sel", width=sel_width)
        inputs = [m.input(f"in{i}", width=width) for i in range(n_inputs)]
        y = m.output_reg("y", width=width)

        with m.always():
            with m.case(sel) as c:
                for i, inp in enumerate(inputs):
                    with c.when(i):
                        y @= inp
                with c.default():
                    y @= 0

        return m

    def test_emit_4_input(self):
        m = self._build(4)
        v = emit_module(m.build())
        assert "module mux_n" in v
        assert "input [1:0] sel" in v
        for i in range(4):
            assert f"input [7:0] in{i}" in v

    def test_simulate(self):
        m = self._build(4)
        sim = Simulator(m.build())
        sim.drive("in0", 10)
        sim.drive("in1", 20)
        sim.drive("in2", 30)
        sim.drive("in3", 40)

        sim.drive("sel", 0)
        sim.run(lambda s: None, max_time=10)
        assert sim.read("y") == 10

        sim = Simulator(m.build())
        sim.drive("in0", 10)
        sim.drive("in1", 20)
        sim.drive("in2", 30)
        sim.drive("in3", 40)
        sim.drive("sel", 2)
        sim.run(lambda s: None, max_time=10)
        assert sim.read("y") == 30


# ===================================================================
# Example 6: Bidirectional counter (up/down)
# ===================================================================


class TestUpDownCounter:
    """8-bit up/down counter.

    When up_down=1, counts up; when up_down=0, counts down.
    Demonstrates mux() in expressions.
    """

    def _build(self) -> Module:
        m = Module("updown_counter")
        clk = m.input("clk")
        rst = m.input("rst")
        up_down = m.input("up_down")
        count = m.output_reg("count", width=8)

        with m.always(posedge(clk)):
            with m.if_(rst):
                count <<= 0
            with m.else_():
                count <<= mux(up_down, count + 1, count - 1)

        return m

    def test_emit(self):
        m = self._build()
        v = emit_module(m.build())
        assert "module updown_counter" in v
        assert "up_down ? count + 1 : count - 1" in v

    def test_simulate(self):
        m = self._build()
        module = m.build()
        sim = Simulator(module)
        sim.fork(Clock(sim.signal("clk"), period=10))

        def test(s):
            s.drive("rst", 1)
            s.drive("up_down", 1)

        sim.run(test, max_time=25)  # posedges with rst=1 → count stays 0
        assert sim.read("count") == 0

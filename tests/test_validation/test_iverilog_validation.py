"""3rd-party validation: compare all our simulation engines against Icarus Verilog.

For each test case we:
  1. Write a self-contained Verilog module (with stimulus in initial blocks).
  2. Compile + run through iverilog/vvp → produce a VCD file.
  3. Parse the same Verilog → model → run through each of our engines → produce VCD.
  4. Parse all VCD files and compare signal values at each time step.

Requirements:
  - Icarus Verilog (``iverilog`` + ``vvp``) reachable by one of:
    a) On the system PATH (works on any OS).
    b) Environment variables ``IVERILOG`` and ``VVP`` pointing to the executables.
    c) Well-known Windows install dirs (``C:\\iverilog\\bin``,
       ``C:\\Program Files\\Icarus Verilog\\bin``, etc.).

All test cases use ``$dumpfile`` / ``$dumpvars`` so iverilog produces VCD.
Our simulator ignores those system tasks and records VCD via the scheduler's
time-step callback.

Engines tested: reference, vm, vm-fast (when built), compiled (when toolchain present).
"""

from __future__ import annotations

import logging

import pytest

from veriforge.sim.cosim import IcarusCosim, available_engines, find_icarus

log = logging.getLogger(__name__)

# Skip all tests if iverilog is not available
pytestmark = pytest.mark.skipif(
    find_icarus("iverilog") is None or find_icarus("vvp") is None,
    reason="Icarus Verilog (iverilog/vvp) not found",
)

_ENGINES = available_engines()


# ── Helpers ──────────────────────────────────────────────────────────


def _validate(
    verilog_src: str,
    *,
    max_time: int = 1000,
    signals: list[str] | None = None,
    ignore_signals: set[str] | None = None,
) -> list[str]:
    """Run all available engines against Icarus and return all differences.

    Returns a flat list of difference strings tagged with the engine name,
    e.g. ``["[vm] t=10 signal y: got 0 want 1"]``.  Empty = all match.
    """
    cosim = IcarusCosim(verilog_src=verilog_src)
    results = cosim.run_all_engines(
        max_time=max_time,
        signals=signals,
        ignore_signals=ignore_signals,
    )
    return [diff for result in results.values() for diff in result.diffs]


# ══════════════════════════════════════════════════════════════════════
# Test Cases
# ══════════════════════════════════════════════════════════════════════


class TestCombinationalLogic:
    """Validate combinational (continuous assign) logic."""

    def test_adder(self):
        """assign y = a + b with two test vectors."""
        verilog = """\
module test_adder;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a + b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_adder);
        a = 8'd10; b = 8'd20;
        #10;
        a = 8'd100; b = 8'd50;
        #10;
        a = 8'd255; b = 8'd1;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_bitwise_ops(self):
        """AND, OR, XOR, NOT."""
        verilog = """\
module test_bitwise;
    reg [7:0] a, b;
    wire [7:0] y_and, y_or, y_xor, y_not;
    assign y_and = a & b;
    assign y_or  = a | b;
    assign y_xor = a ^ b;
    assign y_not = ~a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_bitwise);
        a = 8'hAA; b = 8'h55;
        #10;
        a = 8'hFF; b = 8'h0F;
        #10;
        a = 8'h00; b = 8'hFF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_and", "y_or", "y_xor", "y_not"])
        assert not diffs, "\n".join(diffs)

    def test_shift_ops(self):
        """Left and right shifts."""
        verilog = """\
module test_shift;
    reg [7:0] a;
    wire [7:0] y_shl, y_shr;
    assign y_shl = a << 2;
    assign y_shr = a >> 3;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_shift);
        a = 8'hAA;
        #10;
        a = 8'h0F;
        #10;
        a = 8'hFF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_shl", "y_shr"])
        assert not diffs, "\n".join(diffs)

    def test_ternary(self):
        """Conditional (ternary) operator."""
        verilog = """\
module test_ternary;
    reg sel;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = sel ? a : b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_ternary);
        sel = 0; a = 8'd10; b = 8'd20;
        #10;
        sel = 1;
        #10;
        a = 8'd99;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_concatenation(self):
        """Concatenation operator."""
        verilog = """\
module test_concat;
    reg [3:0] hi, lo;
    wire [7:0] y;
    assign y = {hi, lo};

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_concat);
        hi = 4'hA; lo = 4'h5;
        #10;
        hi = 4'hF; lo = 4'h0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["hi", "lo", "y"])
        assert not diffs, "\n".join(diffs)

    def test_reduction_ops(self):
        """Reduction AND, OR, XOR."""
        verilog = """\
module test_reduction;
    reg [7:0] a;
    wire y_and, y_or, y_xor;
    assign y_and = &a;
    assign y_or  = |a;
    assign y_xor = ^a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_reduction);
        a = 8'hFF;
        #10;
        a = 8'h00;
        #10;
        a = 8'hA5;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_and", "y_or", "y_xor"])
        assert not diffs, "\n".join(diffs)

    def test_comparison_ops(self):
        """Comparison: ==, !=, <, >, <=, >=."""
        verilog = """\
module test_compare;
    reg [7:0] a, b;
    wire eq, ne, lt, gt, le, ge;
    assign eq = (a == b);
    assign ne = (a != b);
    assign lt = (a < b);
    assign gt = (a > b);
    assign le = (a <= b);
    assign ge = (a >= b);

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_compare);
        a = 8'd10; b = 8'd10;
        #10;
        a = 8'd5; b = 8'd10;
        #10;
        a = 8'd20; b = 8'd10;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "eq", "ne", "lt", "gt", "le", "ge"])
        assert not diffs, "\n".join(diffs)

    def test_arithmetic(self):
        """Subtraction, multiplication, modulo."""
        verilog = """\
module test_arith;
    reg [7:0] a, b;
    wire [7:0] y_sub, y_mul, y_mod;
    assign y_sub = a - b;
    assign y_mul = a * b;
    assign y_mod = a % b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_arith);
        a = 8'd100; b = 8'd30;
        #10;
        a = 8'd7; b = 8'd3;
        #10;
        a = 8'd0; b = 8'd1;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_sub", "y_mul", "y_mod"])
        assert not diffs, "\n".join(diffs)


class TestSequentialLogic:
    """Validate clocked (sequential) logic."""

    def test_dff(self):
        """Simple D flip-flop."""
        verilog = """\
module test_dff;
    reg clk, d;
    reg q;

    always @(posedge clk)
        q <= d;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_dff);
        clk = 0; d = 0; q = 0;
        #5; clk = 1;
        #5; clk = 0; d = 1;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0; d = 0;
        #5; clk = 1;
        #5; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "d", "q"])
        assert not diffs, "\n".join(diffs)

    def test_counter(self):
        """4-bit synchronous counter with reset."""
        verilog = """\
module test_counter;
    reg clk, rst;
    reg [3:0] count;

    always @(posedge clk) begin
        if (rst)
            count <= 4'd0;
        else
            count <= count + 4'd1;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_counter);
        clk = 0; rst = 1; count = 4'd0;
        #5; clk = 1;
        #5; clk = 0; rst = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "rst", "count"])
        assert not diffs, "\n".join(diffs)

    def test_shift_register(self):
        """4-bit shift register."""
        verilog = """\
module test_shiftreg;
    reg clk, din;
    reg [3:0] sr;

    always @(posedge clk)
        sr <= {sr[2:0], din};

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_shiftreg);
        clk = 0; din = 0; sr = 4'd0;
        #5; din = 1; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; din = 0; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "din", "sr"])
        assert not diffs, "\n".join(diffs)


class TestInitialBlocks:
    """Validate initial block execution with delays."""

    def test_sequential_delays(self):
        """Multiple assignments with delays."""
        verilog = """\
module test_delays;
    reg [7:0] a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_delays);
        a = 8'd0;
        #10; a = 8'd10;
        #10; a = 8'd20;
        #10; a = 8'd30;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a"])
        assert not diffs, "\n".join(diffs)

    def test_if_else(self):
        """If/else in initial block."""
        verilog = """\
module test_ifelse;
    reg [7:0] a, y;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_ifelse);
        a = 8'd5;
        if (a > 8'd3)
            y = 8'd1;
        else
            y = 8'd0;
        #10;
        a = 8'd2;
        if (a > 8'd3)
            y = 8'd1;
        else
            y = 8'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y"])
        assert not diffs, "\n".join(diffs)

    def test_case_statement(self):
        """Case statement in initial block."""
        verilog = """\
module test_case;
    reg [1:0] sel;
    reg [7:0] y;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_case);
        sel = 2'd0; y = 8'd0;
        case (sel)
            2'd0: y = 8'd10;
            2'd1: y = 8'd20;
            2'd2: y = 8'd30;
            default: y = 8'd0;
        endcase
        #10;
        sel = 2'd2;
        case (sel)
            2'd0: y = 8'd10;
            2'd1: y = 8'd20;
            2'd2: y = 8'd30;
            default: y = 8'd0;
        endcase
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "y"])
        assert not diffs, "\n".join(diffs)

    def test_for_loop(self):
        """For loop filling a register sequentially."""
        verilog = """\
module test_for;
    reg [7:0] sum;
    integer i;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_for);
        sum = 8'd0;
        for (i = 0; i < 5; i = i + 1) begin
            sum = sum + 8'd1;
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sum"], ignore_signals={"i"})
        assert not diffs, "\n".join(diffs)


class TestMixedLogic:
    """Validate combinations of continuous assigns and behavioral blocks."""

    def test_combo_with_initial(self):
        """Continuous assign driven by initial block changes."""
        verilog = """\
module test_combo_init;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a + b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_combo_init);
        a = 8'd0; b = 8'd0;
        #10; a = 8'd5;
        #10; b = 8'd3;
        #10; a = 8'd10; b = 8'd10;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_always_combo(self):
        """Combinational always block (always @(*))."""
        verilog = """\
module test_always_combo;
    reg [7:0] a, b;
    reg [7:0] y;

    always @(*) begin
        y = a + b;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_always_combo);
        a = 8'd0; b = 8'd0;
        #10; a = 8'd10;
        #10; b = 8'd20;
        #10; a = 8'd100; b = 8'd50;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_mux_2to1(self):
        """2:1 multiplexer via continuous assign."""
        verilog = """\
module test_mux;
    reg sel;
    reg [7:0] d0, d1;
    wire [7:0] y;
    assign y = sel ? d1 : d0;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_mux);
        sel = 0; d0 = 8'd10; d1 = 8'd20;
        #10; sel = 1;
        #10; d1 = 8'd99;
        #10; sel = 0;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "d0", "d1", "y"])
        assert not diffs, "\n".join(diffs)


class TestBitSelect:
    """Validate bit and range select operations."""

    def test_bit_select_read(self):
        """Read individual bits."""
        verilog = """\
module test_bitsel;
    reg [7:0] a;
    wire b0, b7;
    assign b0 = a[0];
    assign b7 = a[7];

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_bitsel);
        a = 8'hA5;
        #10;
        a = 8'h81;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b0", "b7"])
        assert not diffs, "\n".join(diffs)

    def test_range_select(self):
        """Range select: a[3:0], a[7:4]."""
        verilog = """\
module test_rangesel;
    reg [7:0] a;
    wire [3:0] lo, hi;
    assign lo = a[3:0];
    assign hi = a[7:4];

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_rangesel);
        a = 8'hAB;
        #10;
        a = 8'h12;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "lo", "hi"])
        assert not diffs, "\n".join(diffs)


class TestEdgeCases:
    """Validate edge cases and boundary conditions."""

    def test_zero_delay_assign(self):
        """Multiple assigns at time zero."""
        verilog = """\
module test_zero;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a & b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_zero);
        a = 8'hFF;
        b = 8'hAA;
        #1; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"], max_time=1)
        assert not diffs, "\n".join(diffs)

    def test_overflow_8bit(self):
        """8-bit overflow: 255 + 1 = 0."""
        verilog = """\
module test_overflow;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a + b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_overflow);
        a = 8'd255; b = 8'd1;
        #10;
        a = 8'd200; b = 8'd200;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_unary_minus(self):
        """Unary minus (two's complement)."""
        verilog = """\
module test_uminus;
    reg [7:0] a;
    wire [7:0] y;
    assign y = -a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_uminus);
        a = 8'd1;
        #10;
        a = 8'd127;
        #10;
        a = 8'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y"])
        assert not diffs, "\n".join(diffs)

    def test_logical_ops(self):
        """Logical AND, OR, NOT."""
        verilog = """\
module test_logical;
    reg [7:0] a, b;
    wire y_and, y_or, y_not;
    assign y_and = a && b;
    assign y_or  = a || b;
    assign y_not = !a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_logical);
        a = 8'd0; b = 8'd0;
        #10;
        a = 8'd1; b = 8'd0;
        #10;
        a = 8'd0; b = 8'd5;
        #10;
        a = 8'd3; b = 8'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_and", "y_or", "y_not"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — expressions
# ══════════════════════════════════════════════════════════════════════


class TestExtendedExpressions:
    """Validate expression types not covered by the basic tests."""

    def test_divide(self):
        """Integer division."""
        verilog = """\
module test_div;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a / b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_div);
        a = 8'd100; b = 8'd10;
        #10;
        a = 8'd7; b = 8'd2;
        #10;
        a = 8'd255; b = 8'd3;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_power(self):
        """Power operator (**)."""
        verilog = """\
module test_power;
    reg [15:0] a, b;
    wire [15:0] y;
    assign y = a ** b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_power);
        a = 16'd2; b = 16'd8;
        #10;
        a = 16'd3; b = 16'd4;
        #10;
        a = 16'd5; b = 16'd2;
        #10;
        a = 16'd10; b = 16'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_xnor(self):
        """Bitwise XNOR (~^)."""
        verilog = """\
module test_xnor;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a ~^ b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_xnor);
        a = 8'hAA; b = 8'h55;
        #10;
        a = 8'hFF; b = 8'hFF;
        #10;
        a = 8'h0F; b = 8'hF0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_reduction_nand_nor_xnor(self):
        """Reduction NAND, NOR, XNOR."""
        verilog = """\
module test_reduce_ext;
    reg [7:0] a;
    wire y_nand, y_nor, y_xnor;
    assign y_nand = ~&a;
    assign y_nor  = ~|a;
    assign y_xnor = ~^a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_reduce_ext);
        a = 8'hFF;
        #10;
        a = 8'h00;
        #10;
        a = 8'hA5;
        #10;
        a = 8'h01;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_nand", "y_nor", "y_xnor"])
        assert not diffs, "\n".join(diffs)

    def test_replication(self):
        """Replication operator {N{expr}}."""
        verilog = """\
module test_repl;
    reg [3:0] a;
    wire [7:0] y2;
    wire [15:0] y4;
    assign y2 = {2{a}};
    assign y4 = {4{a}};

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_repl);
        a = 4'hA;
        #10;
        a = 4'h5;
        #10;
        a = 4'hF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y2", "y4"])
        assert not diffs, "\n".join(diffs)

    def test_case_equality(self):
        """Case equality (===) and inequality (!==) — both sides known."""
        verilog = """\
module test_case_eq;
    reg [7:0] a, b;
    wire eq, ne;
    assign eq = (a === b);
    assign ne = (a !== b);

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_case_eq);
        a = 8'd10; b = 8'd10;
        #10;
        a = 8'd10; b = 8'd20;
        #10;
        a = 8'd0; b = 8'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "eq", "ne"])
        assert not diffs, "\n".join(diffs)

    def test_arithmetic_shift(self):
        """Arithmetic shifts (<<<, >>>)."""
        verilog = """\
module test_ashift;
    reg signed [7:0] a;
    wire signed [7:0] y_sra;
    wire [7:0] y_sla;
    assign y_sra = a >>> 2;
    assign y_sla = a <<< 2;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_ashift);
        a = 8'sb1111_0000;
        #10;
        a = 8'sb0000_1111;
        #10;
        a = 8'sb1000_0001;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_sra", "y_sla"])
        assert not diffs, "\n".join(diffs)

    def test_unary_plus(self):
        """Unary plus (identity)."""
        verilog = """\
module test_uplus;
    reg [7:0] a;
    wire [7:0] y;
    assign y = +a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_uplus);
        a = 8'd42;
        #10;
        a = 8'd0;
        #10;
        a = 8'd255;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y"])
        assert not diffs, "\n".join(diffs)

    def test_mixed_width_arithmetic(self):
        """Arithmetic between different widths — width extension rules."""
        verilog = """\
module test_mixwidth;
    reg [3:0] a;
    reg [7:0] b;
    wire [7:0] y_add, y_sub;
    wire [11:0] y_mul;
    assign y_add = a + b;
    assign y_sub = b - a;
    assign y_mul = a * b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_mixwidth);
        a = 4'd15; b = 8'd200;
        #10;
        a = 4'd1; b = 8'd1;
        #10;
        a = 4'd8; b = 8'd32;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_add", "y_sub", "y_mul"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — statements and control flow
# ══════════════════════════════════════════════════════════════════════


class TestExtendedStatements:
    """Validate statement types not covered by the basic tests."""

    def test_while_loop(self):
        """While loop in initial block."""
        verilog = """\
module test_while;
    reg [7:0] count;
    integer i;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_while);
        count = 8'd0;
        i = 0;
        while (i < 6) begin
            count = count + 8'd1;
            i = i + 1;
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["count"], ignore_signals={"i"})
        assert not diffs, "\n".join(diffs)

    def test_repeat_loop(self):
        """Repeat loop in initial block."""
        verilog = """\
module test_repeat;
    reg [7:0] val;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_repeat);
        val = 8'd0;
        repeat (4) begin
            val = val + 8'd10;
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["val"])
        assert not diffs, "\n".join(diffs)

    def test_forever_with_finish(self):
        """Forever loop ended by $finish (delay-based timing only)."""
        verilog = """\
module test_forever;
    reg clk;
    reg [3:0] count;

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_forever);
        count = 4'd0;
        #10;
        count = 4'd1;
        #10;
        count = 4'd2;
        #10;
        count = 4'd3;
        #3;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "count"])
        assert not diffs, "\n".join(diffs)

    def test_nested_if_else(self):
        """Deeply nested if/else chains."""
        verilog = """\
module test_nested_if;
    reg [7:0] a, y;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_nested_if);

        a = 8'd5;
        if (a < 8'd3)
            y = 8'd0;
        else if (a < 8'd6)
            y = 8'd1;
        else if (a < 8'd10)
            y = 8'd2;
        else
            y = 8'd3;
        #10;

        a = 8'd1;
        if (a < 8'd3)
            y = 8'd0;
        else if (a < 8'd6)
            y = 8'd1;
        else if (a < 8'd10)
            y = 8'd2;
        else
            y = 8'd3;
        #10;

        a = 8'd8;
        if (a < 8'd3)
            y = 8'd0;
        else if (a < 8'd6)
            y = 8'd1;
        else if (a < 8'd10)
            y = 8'd2;
        else
            y = 8'd3;
        #10;

        a = 8'd100;
        if (a < 8'd3)
            y = 8'd0;
        else if (a < 8'd6)
            y = 8'd1;
        else if (a < 8'd10)
            y = 8'd2;
        else
            y = 8'd3;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y"])
        assert not diffs, "\n".join(diffs)

    def test_case_default(self):
        """Case with default fallthrough."""
        verilog = """\
module test_case_def;
    reg [2:0] sel;
    reg [7:0] y;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_case_def);

        sel = 3'd0; #1;
        case (sel)
            3'd0: y = 8'd10;
            3'd1: y = 8'd20;
            3'd2: y = 8'd30;
            default: y = 8'd99;
        endcase
        #10;

        sel = 3'd5; #1;
        case (sel)
            3'd0: y = 8'd10;
            3'd1: y = 8'd20;
            3'd2: y = 8'd30;
            default: y = 8'd99;
        endcase
        #10;

        sel = 3'd2; #1;
        case (sel)
            3'd0: y = 8'd10;
            3'd1: y = 8'd20;
            3'd2: y = 8'd30;
            default: y = 8'd99;
        endcase
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "y"])
        assert not diffs, "\n".join(diffs)

    def test_nested_for_loops(self):
        """Nested for loops."""
        verilog = """\
module test_nested_for;
    reg [7:0] result;
    integer i, j;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_nested_for);
        result = 8'd0;
        for (i = 0; i < 3; i = i + 1) begin
            for (j = 0; j < 3; j = j + 1) begin
                result = result + 8'd1;
            end
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["result"], ignore_signals={"i", "j"})
        assert not diffs, "\n".join(diffs)

    def test_multiple_initial_blocks(self):
        """Multiple initial blocks driving different signals."""
        verilog = """\
module test_multi_init;
    reg [7:0] a, b;
    wire [7:0] sum;
    assign sum = a + b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_multi_init);
        a = 8'd0;
        #10; a = 8'd5;
        #10; a = 8'd10;
        #10;
    end

    initial begin
        b = 8'd0;
        #15; b = 8'd3;
        #10; b = 8'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "sum"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — LHS targets
# ══════════════════════════════════════════════════════════════════════


class TestLhsTargets:
    """Validate assignment to bit/range/concat LHS targets."""

    def test_bit_select_lhs(self):
        """Write to individual bits."""
        verilog = """\
module test_bitsel_lhs;
    reg [7:0] data;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_bitsel_lhs);
        data = 8'd0;
        #10; data[0] = 1'b1;
        #10; data[7] = 1'b1;
        #10; data[3] = 1'b1;
        #10; data[0] = 1'b0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["data"])
        assert not diffs, "\n".join(diffs)

    def test_range_select_lhs(self):
        """Write to range selects."""
        verilog = """\
module test_rangesel_lhs;
    reg [7:0] data;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_rangesel_lhs);
        data = 8'd0;
        #10; data[3:0] = 4'hA;
        #10; data[7:4] = 4'h5;
        #10; data[3:0] = 4'hF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["data"])
        assert not diffs, "\n".join(diffs)

    def test_concat_lhs(self):
        """Concatenation as LHS target."""
        verilog = """\
module test_concat_lhs;
    reg [3:0] hi, lo;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_concat_lhs);
        hi = 4'd0; lo = 4'd0;
        #10; {hi, lo} = 8'hAB;
        #10; {hi, lo} = 8'h12;
        #10; {hi, lo} = 8'hFF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["hi", "lo"])
        assert not diffs, "\n".join(diffs)

    def test_nba_bit_select(self):
        """Non-blocking assign with bit select LHS."""
        verilog = """\
module test_nba_bitsel;
    reg clk;
    reg [7:0] data;

    always @(posedge clk) begin
        data[0] <= ~data[0];
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_nba_bitsel);
        clk = 0; data = 8'd0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "data"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — sequential logic patterns
# ══════════════════════════════════════════════════════════════════════


class TestExtendedSequential:
    """Validate more complex sequential patterns."""

    def test_dff_with_enable(self):
        """D flip-flop with clock enable."""
        verilog = """\
module test_dff_en;
    reg clk, en, d;
    reg q;

    always @(posedge clk) begin
        if (en)
            q <= d;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_dff_en);
        clk = 0; en = 0; d = 0; q = 0;
        d = 1;
        #5; clk = 1;
        #5; clk = 0;
        en = 1;
        #5; clk = 1;
        #5; clk = 0;
        d = 0;
        #5; clk = 1;
        #5; clk = 0;
        en = 0; d = 1;
        #5; clk = 1;
        #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "en", "d", "q"])
        assert not diffs, "\n".join(diffs)

    def test_counter_with_load(self):
        """Counter with synchronous load."""
        verilog = """\
module test_ctr_load;
    reg clk, rst, load;
    reg [7:0] load_val;
    reg [7:0] count;

    always @(posedge clk) begin
        if (rst)
            count <= 8'd0;
        else if (load)
            count <= load_val;
        else
            count <= count + 8'd1;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_ctr_load);
        clk = 0; rst = 1; load = 0; load_val = 8'd0; count = 8'd0;
        #5; clk = 1; #5; clk = 0;
        rst = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        load = 1; load_val = 8'd100;
        #5; clk = 1; #5; clk = 0;
        load = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "rst", "load", "load_val", "count"])
        assert not diffs, "\n".join(diffs)

    def test_up_down_counter(self):
        """Up/down counter controlled by dir signal."""
        verilog = """\
module test_updown;
    reg clk, dir;
    reg [7:0] count;

    always @(posedge clk) begin
        if (dir)
            count <= count + 8'd1;
        else
            count <= count - 8'd1;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_updown);
        clk = 0; dir = 1; count = 8'd0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        dir = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "dir", "count"])
        assert not diffs, "\n".join(diffs)

    def test_pipeline_registers(self):
        """Simple 3-stage pipeline (NBA swap semantics)."""
        verilog = """\
module test_pipeline;
    reg clk;
    reg [7:0] d;
    reg [7:0] stage1, stage2, stage3;

    always @(posedge clk) begin
        stage3 <= stage2;
        stage2 <= stage1;
        stage1 <= d;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_pipeline);
        clk = 0; d = 8'd0; stage1 = 8'd0; stage2 = 8'd0; stage3 = 8'd0;
        d = 8'd1;
        #5; clk = 1; #5; clk = 0;
        d = 8'd2;
        #5; clk = 1; #5; clk = 0;
        d = 8'd3;
        #5; clk = 1; #5; clk = 0;
        d = 8'd4;
        #5; clk = 1; #5; clk = 0;
        d = 8'd5;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "d", "stage1", "stage2", "stage3"])
        assert not diffs, "\n".join(diffs)

    def test_async_reset_dff(self):
        """D flip-flop with asynchronous reset."""
        verilog = """\
module test_async_rst;
    reg clk, rst, d;
    reg q;

    always @(posedge clk or posedge rst) begin
        if (rst)
            q <= 1'b0;
        else
            q <= d;
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_async_rst);
        clk = 0; rst = 0; d = 0; q = 0;
        d = 1;
        #5; clk = 1; #5; clk = 0;
        d = 0;
        #5; clk = 1; #5; clk = 0;
        rst = 1;
        #5;
        rst = 0; d = 1;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "rst", "d", "q"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — combinational patterns
# ══════════════════════════════════════════════════════════════════════


class TestExtendedCombinational:
    """Validate more complex combinational patterns."""

    def test_priority_encoder(self):
        """Priority encoder using if/else chain in always @(*)."""
        verilog = """\
module test_prienc;
    reg [3:0] inp;
    reg [1:0] out;
    reg valid;

    always @(*) begin
        if (inp[3]) begin
            out = 2'd3; valid = 1'b1;
        end else if (inp[2]) begin
            out = 2'd2; valid = 1'b1;
        end else if (inp[1]) begin
            out = 2'd1; valid = 1'b1;
        end else if (inp[0]) begin
            out = 2'd0; valid = 1'b1;
        end else begin
            out = 2'd0; valid = 1'b0;
        end
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_prienc);
        inp = 4'b0000;
        #10; inp = 4'b0001;
        #10; inp = 4'b0010;
        #10; inp = 4'b0100;
        #10; inp = 4'b1000;
        #10; inp = 4'b1010;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["inp", "out", "valid"])
        assert not diffs, "\n".join(diffs)

    def test_decoder_3to8(self):
        """3-to-8 decoder using case."""
        verilog = """\
module test_dec;
    reg [2:0] sel;
    reg [7:0] y;

    always @(*) begin
        case (sel)
            3'd0: y = 8'b00000001;
            3'd1: y = 8'b00000010;
            3'd2: y = 8'b00000100;
            3'd3: y = 8'b00001000;
            3'd4: y = 8'b00010000;
            3'd5: y = 8'b00100000;
            3'd6: y = 8'b01000000;
            3'd7: y = 8'b10000000;
        endcase
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_dec);
        sel = 3'd0;
        #10; sel = 3'd1;
        #10; sel = 3'd4;
        #10; sel = 3'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "y"])
        assert not diffs, "\n".join(diffs)

    def test_alu(self):
        """Simple ALU with multiple operations."""
        verilog = """\
module test_alu;
    reg [7:0] a, b;
    reg [2:0] op;
    reg [7:0] result;

    always @(*) begin
        case (op)
            3'd0: result = a + b;
            3'd1: result = a - b;
            3'd2: result = a & b;
            3'd3: result = a | b;
            3'd4: result = a ^ b;
            3'd5: result = ~a;
            3'd6: result = a << 1;
            3'd7: result = a >> 1;
        endcase
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_alu);
        a = 8'd100; b = 8'd25; op = 3'd0;
        #10; op = 3'd1;
        #10; op = 3'd2;
        #10; op = 3'd3;
        #10; op = 3'd4;
        #10; op = 3'd5;
        #10; op = 3'd6;
        #10; op = 3'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "op", "result"])
        assert not diffs, "\n".join(diffs)

    def test_cascaded_assigns(self):
        """Delta cycle chain: a → b → c → d."""
        verilog = """\
module test_cascade;
    reg [7:0] a;
    wire [7:0] b, c, d;
    assign b = a + 8'd1;
    assign c = b + 8'd1;
    assign d = c + 8'd1;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_cascade);
        a = 8'd0;
        #10; a = 8'd10;
        #10; a = 8'd100;
        #10; a = 8'd252;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "c", "d"])
        assert not diffs, "\n".join(diffs)

    def test_multibit_mux(self):
        """4:1 mux using always block and case."""
        verilog = """\
module test_mux4;
    reg [1:0] sel;
    reg [7:0] a, b, c, d;
    reg [7:0] y;

    always @(*) begin
        case (sel)
            2'd0: y = a;
            2'd1: y = b;
            2'd2: y = c;
            2'd3: y = d;
        endcase
    end

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_mux4);
        a = 8'd10; b = 8'd20; c = 8'd30; d = 8'd40;
        sel = 2'd0;
        #10; sel = 2'd1;
        #10; sel = 2'd2;
        #10; sel = 2'd3;
        #10; a = 8'd99; sel = 2'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "a", "b", "c", "d", "y"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — timing & multi-always interaction
# ══════════════════════════════════════════════════════════════════════


class TestTimingAndInteraction:
    """Validate timing patterns and multi-block interaction."""

    def test_negedge_trigger(self):
        """Negedge-triggered flip-flop."""
        verilog = """\
module test_negedge;
    reg clk, d;
    reg q;

    always @(negedge clk)
        q <= d;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_negedge);
        clk = 1; d = 0; q = 0;
        d = 1;
        #5; clk = 0;
        #5; clk = 1;
        d = 0;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "d", "q"])
        assert not diffs, "\n".join(diffs)

    def test_clock_divider(self):
        """Clock divider by 2."""
        verilog = """\
module test_clkdiv;
    reg clk_in;
    reg clk_out;

    always @(posedge clk_in)
        clk_out <= ~clk_out;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_clkdiv);
        clk_in = 0; clk_out = 0;
        repeat (10) begin
            #5; clk_in = ~clk_in;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk_in", "clk_out"])
        assert not diffs, "\n".join(diffs)

    def test_combo_and_seq_interaction(self):
        """Continuous assign reads from clocked register."""
        verilog = """\
module test_combo_seq;
    reg clk;
    reg [7:0] count;
    wire [7:0] doubled;

    assign doubled = count << 1;

    always @(posedge clk)
        count <= count + 8'd1;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_combo_seq);
        clk = 0; count = 8'd0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "count", "doubled"])
        assert not diffs, "\n".join(diffs)

    def test_two_always_blocks(self):
        """Two always blocks reading/writing different signals."""
        verilog = """\
module test_two_always;
    reg clk;
    reg [7:0] a, b;

    always @(posedge clk)
        a <= a + 8'd1;

    always @(posedge clk)
        b <= b + 8'd2;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_two_always);
        clk = 0; a = 8'd0; b = 8'd0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "a", "b"])
        assert not diffs, "\n".join(diffs)

    def test_variable_delay(self):
        """Variable-length delays in initial block."""
        verilog = """\
module test_vardelay;
    reg [7:0] data;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_vardelay);
        data = 8'd0;
        #3;  data = 8'd1;
        #7;  data = 8'd2;
        #1;  data = 8'd3;
        #15; data = 8'd4;
        #4;  data = 8'd5;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["data"])
        assert not diffs, "\n".join(diffs)

    def test_lfsr(self):
        """4-bit linear feedback shift register."""
        verilog = """\
module test_lfsr;
    reg clk;
    reg [3:0] lfsr;
    wire feedback;

    assign feedback = lfsr[3] ^ lfsr[2];

    always @(posedge clk)
        lfsr <= {lfsr[2:0], feedback};

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_lfsr);
        clk = 0; lfsr = 4'b0001;
        repeat (16) begin
            #5; clk = 1;
            #5; clk = 0;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "lfsr", "feedback"])
        assert not diffs, "\n".join(diffs)


# ══════════════════════════════════════════════════════════════════════
# Extended Validation — wider data paths & edge cases
# ══════════════════════════════════════════════════════════════════════


class TestWideAndEdge:
    """Validate wider data paths and more edge cases."""

    def test_16bit_arithmetic(self):
        """16-bit arithmetic operations."""
        verilog = """\
module test_16bit;
    reg [15:0] a, b;
    wire [15:0] sum, diff, prod;
    assign sum  = a + b;
    assign diff = a - b;
    assign prod = a * b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_16bit);
        a = 16'd1000; b = 16'd2000;
        #10;
        a = 16'd65535; b = 16'd1;
        #10;
        a = 16'd256; b = 16'd256;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "sum", "diff", "prod"])
        assert not diffs, "\n".join(diffs)

    def test_single_bit_ops(self):
        """Single-bit signals: AND, OR, XOR, NOT."""
        verilog = """\
module test_1bit;
    reg a, b;
    wire y_and, y_or, y_xor, y_not;
    assign y_and = a & b;
    assign y_or  = a | b;
    assign y_xor = a ^ b;
    assign y_not = ~a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_1bit);
        a = 0; b = 0;
        #10; a = 1;
        #10; b = 1;
        #10; a = 0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_and", "y_or", "y_xor", "y_not"])
        assert not diffs, "\n".join(diffs)

    def test_all_zeros_all_ones(self):
        """Boundary values: all zeros and all ones."""
        verilog = """\
module test_boundary;
    reg [7:0] a;
    wire [7:0] y_not;
    wire y_rand, y_ror;
    assign y_not = ~a;
    assign y_rand = &a;
    assign y_ror  = |a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_boundary);
        a = 8'h00;
        #10;
        a = 8'hFF;
        #10;
        a = 8'h80;
        #10;
        a = 8'h01;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_not", "y_rand", "y_ror"])
        assert not diffs, "\n".join(diffs)

    def test_sign_extension_in_assign(self):
        """Narrower value assigned to wider wire (zero extension)."""
        verilog = """\
module test_signext;
    reg [3:0] narrow;
    wire [7:0] wide;
    assign wide = narrow;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signext);
        narrow = 4'hF;
        #10;
        narrow = 4'h5;
        #10;
        narrow = 4'h0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["narrow", "wide"])
        assert not diffs, "\n".join(diffs)

    def test_complex_expression(self):
        """Complex compound expression."""
        verilog = """\
module test_complex;
    reg [7:0] a, b, c;
    wire [7:0] y;
    assign y = ((a + b) ^ c) & 8'hF0 | (a - c);

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_complex);
        a = 8'd10; b = 8'd20; c = 8'd5;
        #10;
        a = 8'd255; b = 8'd1; c = 8'd128;
        #10;
        a = 8'd0; b = 8'd0; c = 8'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "c", "y"])
        assert not diffs, "\n".join(diffs)


class TestSignedDeclarations:
    """Validate that declared-signed signals produce correct signed behaviour.

    Covers IEEE 1364-2005 §5.5: expression signedness propagation from
    declared-signed nets/variables.  Each test compares all available
    simulation engines against Icarus Verilog via VCD comparison.
    """

    def test_signed_comparison(self):
        """Declared-signed comparison: reg signed a,b; wire lt = a < b."""
        verilog = """\
module test_signed_cmp;
    reg signed [7:0] a, b;
    wire lt, gt, le, ge;
    assign lt = a < b;
    assign gt = a > b;
    assign le = a <= b;
    assign ge = a >= b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_cmp);
        a = -1;    b = 1;       // -1 < 1 → lt=1, gt=0, le=1, ge=0
        #10;
        a = 1;     b = -1;      // 1 > -1 → lt=0, gt=1, le=0, ge=1
        #10;
        a = -128;  b = 127;     // -128 < 127 → lt=1
        #10;
        a = 127;   b = -128;    // 127 > -128 → gt=1
        #10;
        a = -5;    b = -5;      // equal → le=1, ge=1
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "lt", "gt", "le", "ge"])
        assert not diffs, "\n".join(diffs)

    def test_signed_widening(self):
        """Declared-signed widening: assigning narrower signed to wider wire."""
        verilog = """\
module test_signed_widen;
    reg signed [7:0] a;
    wire [15:0] w;
    assign w = a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_widen);
        a = -1;        // 0xFF → w=0xFFFF (sign-extend)
        #10;
        a = -128;      // 0x80 → w=0xFF80
        #10;
        a = 127;       // 0x7F → w=0x007F (no sign-ext, MSB=0)
        #10;
        a = 8'sb0000_0101;  // 5 → w=0x0005
        #10;
        a = 8'sb1111_1011;  // -5 → w=0xFFFB
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "w"])
        assert not diffs, "\n".join(diffs)

    def test_mixed_signed_unsigned_comparison(self):
        """Mixed signed+unsigned: only one operand signed → unsigned compare."""
        verilog = """\
module test_mixed_cmp;
    reg signed [7:0] a;
    reg        [7:0] b;
    wire lt, gt;
    assign lt = a < b;
    assign gt = a > b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_mixed_cmp);
        a = 8'sb1111_1111;  b = 8'd1;   // 255 < 1?  unsigned: false (255 > 1)
        #10;
        a = 8'sb1000_0000;  b = 8'd127; // 128 < 127?  unsigned: false (128 > 127)
        #10;
        a = 8'd5;           b = 8'd10;  // 5 < 10 → true both ways
        #10;
        a = 8'sb1111_1111;  b = 8'sb1111_1111; // equal
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "lt", "gt"])
        assert not diffs, "\n".join(diffs)

    def test_signed_arithmetic_widening(self):
        """Declared-signed arithmetic with wider destination (sign-extension)."""
        verilog = """\
module test_signed_arith;
    reg signed [7:0] a, b;
    wire [15:0] sum;
    wire [15:0] diff;
    assign sum = a + b;
    assign diff = a - b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_arith);
        a = -1;    b = -1;    // -1 + -1 = -2 → sign-extend to 16-bit
        #10;
        a = -128;  b = 1;     // -128 + 1 = -127
        #10;
        a = 127;   b = 127;   // 127 + 127 = 254 (positive)
        #10;
        a = 0;     b = 0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "sum", "diff"])
        assert not diffs, "\n".join(diffs)

    def test_signed_arithmetic_shift_right(self):
        """Declared-signed >>> preserves sign bit during shift."""
        verilog = """\
module test_signed_ashr;
    reg signed [7:0] a;
    wire signed [7:0] y1, y2;
    assign y1 = a >>> 1;
    assign y2 = a >>> 3;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_ashr);
        a = 8'sb1111_0000;   // -16
        #10;
        a = 8'sb0000_1111;   // 15
        #10;
        a = 8'sb1000_0001;   // -127
        #10;
        a = 8'sb0100_0000;   // 64
        #10;
        a = 8'sb1111_1111;   // -1 → right shift by any amount still -1
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y1", "y2"])
        assert not diffs, "\n".join(diffs)

    def test_signed_multiplication(self):
        """Declared-signed multiplication with wider result."""
        verilog = """\
module test_signed_mul;
    reg signed [7:0] a, b;
    wire signed [15:0] prod;
    assign prod = a * b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_mul);
        a = -1;    b = -1;    // (-1) * (-1) = 1
        #10;
        a = -1;    b = 1;     // (-1) * 1 = -1 → 0xFFFF
        #10;
        a = -128;  b = 2;     // (-128) * 2 = -256 → 0xFF00
        #10;
        a = 10;    b = 10;    // 10 * 10 = 100
        #10;
        a = -4;    b = 3;     // (-4) * 3 = -12 → 0xFFF4
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "prod"])
        assert not diffs, "\n".join(diffs)

    def test_signed_division(self):
        """Declared-signed division with correct sign propagation."""
        verilog = """\
module test_signed_div;
    reg signed [7:0] a, b;
    wire signed [7:0] quot, rem;
    assign quot = a / b;
    assign rem = a % b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_div);
        a = -10;  b = 3;    // -10 / 3 = -3, -10 % 3 = -1
        #10;
        a = 10;   b = -3;   // 10 / -3 = -3, 10 % -3 = 1
        #10;
        a = -10;  b = -3;   // -10 / -3 = 3, -10 % -3 = -1
        #10;
        a = 100;  b = 7;    // 100 / 7 = 14, 100 % 7 = 2
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "quot", "rem"])
        assert not diffs, "\n".join(diffs)

    def test_signed_ternary(self):
        """Declared-signed operands in ternary expression."""
        verilog = """\
module test_signed_ternary;
    reg signed [7:0] a, b;
    reg sel;
    wire signed [15:0] y;
    assign y = sel ? a : b;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_ternary);
        a = -1;     b = -1;     sel = 1;  // y = -1 (sign-ext: 0xFFFF)
        #10;
        a = -1;     b = -1;     sel = 0;  // y = -1 (sign-ext: 0xFFFF)
        #10;
        a = 127;    b = -128;   sel = 1;  // y = 127 (0x007F)
        #10;
        a = 127;    b = -128;   sel = 0;  // y = -128 (0xFF80)
        #10;
        a = -5;     b = 3;      sel = 0;  // y = 3
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "sel", "y"])
        assert not diffs, "\n".join(diffs)

    def test_signed_unary_negation(self):
        """Declared-signed unary negation with sign extension."""
        verilog = """\
module test_signed_uneg;
    reg signed [7:0] a;
    wire signed [15:0] y;
    assign y = -a;

    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test_signed_uneg);
        a = 8'sb0000_0101;   // 5 -> -5 = 0xFFFB (sign-ext to 16-bit)
        #10;
        a = 0;                // 0 -> 0
        #10;
        a = -1;               // -1 -> 1 = 0x0001
        #10;
        a = -128;             // -128 -> 128 = 0x0080
        #10;
        a = 127;              // 127 -> -127 = 0xFF81
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y"])
        assert not diffs, "\n".join(diffs)

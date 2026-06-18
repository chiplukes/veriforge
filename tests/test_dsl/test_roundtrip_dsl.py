"""Round-trip equivalence tests: Verilog → DSL → Verilog.

Tests the full pipeline:
    1. Parse Verilog source → model (Module)
    2. Emit model → Verilog "A" (normalized baseline)
    3. Translate model → DSL Python code
    4. Execute DSL code → new model
    5. Emit new model → Verilog "B"
    6. Assert A == B

Literal formatting (e.g. ``8'hFF`` vs ``255``) is normalized away before
comparison so we test *semantic* equivalence, not cosmetic fidelity.
"""

from __future__ import annotations

import textwrap

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.convert.to_dsl import module_to_dsl
from veriforge.model.expressions import Literal
from veriforge.model.statements import SeqBlock
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def parser():
    return verilog_parser(start="module_declaration")


@pytest.fixture
def full_parser():
    return verilog_parser(start="verilog")


def _strip_literal_metadata(module):
    """Strip ``original_text`` / ``base`` from every Literal in *module*.

    This makes emitter output independent of whether a Literal was created
    by the parser (``8'hFF``) or by the DSL builder (``255``).
    """
    for node in module.walk():
        if isinstance(node, Literal):
            node.original_text = ""
            node.base = None


def _unwrap_single_seqblocks(module):
    """Unwrap SeqBlocks that contain exactly one statement.

    The parser preserves ``begin ... end`` as a SeqBlock even for single
    statements, but the DSL builder never wraps single statements.
    Unwrapping here makes both sides comparable.
    """
    for blk in module.always_blocks:
        if isinstance(blk.body, SeqBlock) and len(blk.body.statements) == 1:
            blk.body = blk.body.statements[0]
    for blk in module.initial_blocks:
        if isinstance(blk.body, SeqBlock) and len(blk.body.statements) == 1:
            blk.body = blk.body.statements[0]


def _normalize(module):
    """Apply all normalization passes to make round-trip comparison fair."""
    _strip_literal_metadata(module)
    _unwrap_single_seqblocks(module)


def _parse(parser, verilog: str):
    tree = parser.build_tree(text=verilog)
    design = tree_to_design(tree)
    return design.modules[0]


def _exec_dsl(code: str):
    ns = {}
    exec(code, ns)  # noqa: S102
    return ns["module"]


def _round_trip(parser, verilog: str) -> str:
    """Full round-trip: Verilog → model → DSL → exec → model → Verilog.

    Returns the DSL code for debugging.  Raises ``AssertionError`` if
    the normalized Verilog outputs don't match.
    """
    # Step 1: Parse original
    mod_a = _parse(parser, verilog)
    _normalize(mod_a)
    verilog_a = emit_module(mod_a)

    # Step 2: Translate to DSL and execute
    dsl_code = module_to_dsl(mod_a)
    mod_b = _exec_dsl(dsl_code)
    _normalize(mod_b)
    verilog_b = emit_module(mod_b)

    assert verilog_a == verilog_b, (
        f"Round-trip mismatch:\n"
        f"--- Original (normalized) ---\n{verilog_a}\n"
        f"--- Round-trip ---\n{verilog_b}\n"
        f"--- DSL code ---\n{dsl_code}"
    )
    return dsl_code


# ===================================================================
# Basic declarations
# ===================================================================


class TestRoundTripBasic:
    """Simplest modules: ports, wires, assignments."""

    def test_empty_module(self, parser):
        _round_trip(parser, "module empty; endmodule")

    def test_single_input(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module one_in (input a);
            endmodule
        """),
        )

    def test_input_output(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module buf_gate (
                input a,
                output b
            );
                assign b = a;
            endmodule
        """),
        )

    def test_multi_bit_ports(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module wide (
                input [7:0] a,
                input [7:0] b,
                output [8:0] sum
            );
                assign sum = a + b;
            endmodule
        """),
        )

    def test_wire_internal(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module with_wire (
                input a,
                input b,
                output c
            );
                wire t;
                assign t = a & b;
                assign c = t;
            endmodule
        """),
        )

    def test_multiple_assigns(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module multi (
                input a,
                input b,
                output x,
                output y
            );
                assign x = a & b;
                assign y = a | b;
            endmodule
        """),
        )


# ===================================================================
# Operators
# ===================================================================


class TestRoundTripOperators:
    """Test that all operator types survive the round-trip."""

    def test_arithmetic(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module arith (
                input [7:0] a,
                input [7:0] b,
                output [7:0] sum,
                output [7:0] diff,
                output [15:0] prod
            );
                assign sum = a + b;
                assign diff = a - b;
                assign prod = a * b;
            endmodule
        """),
        )

    def test_bitwise(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module bitwise (
                input [7:0] a,
                input [7:0] b,
                output [7:0] o_and,
                output [7:0] o_or,
                output [7:0] o_xor,
                output [7:0] o_not
            );
                assign o_and = a & b;
                assign o_or = a | b;
                assign o_xor = a ^ b;
                assign o_not = ~a;
            endmodule
        """),
        )

    def test_shift(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module shift (
                input [7:0] a,
                output [7:0] sl,
                output [7:0] sr
            );
                assign sl = a << 2;
                assign sr = a >> 3;
            endmodule
        """),
        )

    def test_arithmetic_shift(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module shift_arith (
                input signed [7:0] a,
                output signed [7:0] sl,
                output signed [7:0] sr
            );
                assign sl = a <<< 2;
                assign sr = a >>> 3;
            endmodule
        """),
        )

    def test_comparison(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module cmp (
                input [7:0] a,
                input [7:0] b,
                output eq,
                output ne,
                output lt,
                output gt
            );
                assign eq = a == b;
                assign ne = a != b;
                assign lt = a < b;
                assign gt = a > b;
            endmodule
        """),
        )

    def test_case_comparison(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module cmp_case (
                input [3:0] a,
                input [3:0] b,
                output eq4,
                output ne4
            );
                assign eq4 = a === b;
                assign ne4 = a !== b;
            endmodule
        """),
        )

    def test_system_function_exprs(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module sysfuncs (
                input signed [7:0] a,
                output signed [7:0] y_signed,
                output [7:0] y_unsigned,
                output [31:0] y_clog2,
                output [31:0] y_time
            );
                assign y_signed = $signed(a);
                assign y_unsigned = $unsigned(a);
                assign y_clog2 = $clog2(a);
                assign y_time = $time;
            endmodule
        """),
        )

    def test_ternary(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module mux2 (
                input sel,
                input [7:0] a,
                input [7:0] b,
                output [7:0] y
            );
                assign y = sel ? a : b;
            endmodule
        """),
        )

    def test_concat(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module concat_mod (
                input [3:0] a,
                input [3:0] b,
                output [7:0] c
            );
                assign c = {a, b};
            endmodule
        """),
        )

    def test_logical_ops(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module logical (
                input a,
                input b,
                output o_and,
                output o_or,
                output o_not
            );
                assign o_and = a && b;
                assign o_or = a || b;
                assign o_not = !a;
            endmodule
        """),
        )

    def test_reduction(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module reduce (
                input [7:0] a,
                output r_and,
                output r_or,
                output r_xor
            );
                assign r_and = &a;
                assign r_or = |a;
                assign r_xor = ^a;
            endmodule
        """),
        )

    def test_bit_select(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module bitsel (
                input [7:0] data,
                output msb,
                output lsb
            );
                assign msb = data[7];
                assign lsb = data[0];
            endmodule
        """),
        )

    def test_range_select(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module rangesel (
                input [15:0] data,
                output [7:0] hi,
                output [7:0] lo
            );
                assign hi = data[15:8];
                assign lo = data[7:0];
            endmodule
        """),
        )


# ===================================================================
# Sequential logic
# ===================================================================


class TestRoundTripSequential:
    """Always blocks, flip-flops, resets."""

    def test_simple_ff(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module dff (
                input clk,
                input d,
                output reg q
            );
                always @(posedge clk)
                    q <= d;
            endmodule
        """),
        )

    def test_ff_sync_reset(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module dff_rst (
                input clk,
                input rst,
                input d,
                output reg q
            );
                always @(posedge clk)
                    if (rst)
                        q <= 0;
                    else
                        q <= d;
            endmodule
        """),
        )

    def test_ff_async_reset(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module dff_arst (
                input clk,
                input rst,
                input d,
                output reg q
            );
                always @(posedge clk or posedge rst)
                    if (rst)
                        q <= 0;
                    else
                        q <= d;
            endmodule
        """),
        )

    def test_counter(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module counter (
                input clk,
                input rst,
                output reg [7:0] count
            );
                always @(posedge clk)
                    if (rst)
                        count <= 0;
                    else
                        count <= count + 1;
            endmodule
        """),
        )

    def test_shift_register(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module shift_reg (
                input clk,
                input din,
                output reg [7:0] dout
            );
                always @(posedge clk)
                    dout <= {dout[6:0], din};
            endmodule
        """),
        )

    def test_begin_end_block(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module multi_reg (
                input clk,
                input rst,
                output reg [7:0] a,
                output reg [7:0] b
            );
                always @(posedge clk)
                    if (rst)
                        begin
                            a <= 0;
                            b <= 0;
                        end
                    else
                        begin
                            a <= a + 1;
                            b <= b + 2;
                        end
            endmodule
        """),
        )

    def test_negedge_clock(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module neg_clk (
                input clk,
                input d,
                output reg q
            );
                always @(negedge clk)
                    q <= d;
            endmodule
        """),
        )


# ===================================================================
# Combinational logic
# ===================================================================


class TestRoundTripCombinational:
    """Combinational always blocks and case statements."""

    def test_always_star(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module comb (
                input a,
                input b,
                output reg y
            );
                always @(*)
                    y = a & b;
            endmodule
        """),
        )

    def test_if_elif_else(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module priority (
                input [3:0] inp,
                output reg [1:0] out,
                output reg valid
            );
                always @(*) begin
                    out = 0;
                    valid = 0;
                    if (inp[3]) begin
                        out = 3;
                        valid = 1;
                    end else if (inp[2]) begin
                        out = 2;
                        valid = 1;
                    end else if (inp[1]) begin
                        out = 1;
                        valid = 1;
                    end else if (inp[0]) begin
                        out = 0;
                        valid = 1;
                    end
                end
            endmodule
        """),
        )

    def test_case_statement(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module mux4 (
                input [1:0] sel,
                input a,
                input b,
                input c,
                input d,
                output reg y
            );
                always @(*)
                    case (sel)
                        0: y = a;
                        1: y = b;
                        2: y = c;
                        default: y = d;
                    endcase
            endmodule
        """),
        )

    def test_case_multi_stmt(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module alu (
                input [1:0] op,
                input [7:0] a,
                input [7:0] b,
                output reg [7:0] result,
                output reg zero
            );
                always @(*) begin
                    result = 0;
                    zero = 0;
                    case (op)
                        0: result = a + b;
                        1: result = a - b;
                        2: result = a & b;
                        default: result = a | b;
                    endcase
                    zero = (result == 0);
                end
            endmodule
        """),
        )


# ===================================================================
# Parameters
# ===================================================================


class TestRoundTripParameters:
    """Parameterized modules."""

    def test_simple_parameter(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module param_mod #(
                parameter WIDTH = 8
            ) (
                input [WIDTH-1:0] data,
                output [WIDTH-1:0] out
            );
                assign out = data;
            endmodule
        """),
        )

    def test_multiple_params(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module multi_param #(
                parameter WIDTH = 8,
                parameter DEPTH = 4
            ) (
                input [WIDTH-1:0] data,
                output [WIDTH-1:0] out
            );
                assign out = data;
            endmodule
        """),
        )

    def test_localparam(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module with_local #(
                parameter WIDTH = 8
            ) (
                input [WIDTH-1:0] data,
                output [WIDTH-1:0] out
            );
                localparam MASK = 0;
                assign out = data & MASK;
            endmodule
        """),
        )


# ===================================================================
# Variables and internal signals
# ===================================================================


class TestRoundTripInternals:
    """Internal regs, wires, integers."""

    def test_internal_reg(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module with_reg (
                input clk,
                input din,
                output reg [7:0] dout
            );
                reg [7:0] temp;
                always @(posedge clk) begin
                    temp <= {temp[6:0], din};
                    dout <= temp;
                end
            endmodule
        """),
        )

    def test_internal_wire(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module with_wire2 (
                input [7:0] a,
                input [7:0] b,
                output [7:0] sum
            );
                wire [7:0] masked_a;
                wire [7:0] masked_b;
                assign masked_a = a & 255;
                assign masked_b = b & 255;
                assign sum = masked_a + masked_b;
            endmodule
        """),
        )

    def test_integer_var(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module with_int;
                integer i;
                initial begin
                    i = 0;
                end
            endmodule
        """),
        )


# ===================================================================
# Instances
# ===================================================================


class TestRoundTripInstances:
    """Module instantiation."""

    def test_named_ports(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module top (
                input clk,
                input d,
                output q
            );
                dff u0 (.clk(clk), .d(d), .q(q));
            endmodule
        """),
        )

    def test_instance_with_params(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module top (
                input [15:0] data,
                output [15:0] out
            );
                buf16 #(.WIDTH(16)) u0 (.data(data), .out(out));
            endmodule
        """),
        )

    def test_multiple_instances(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module chain (
                input clk,
                input din,
                output dout
            );
                wire mid;
                dff u0 (.clk(clk), .d(din), .q(mid));
                dff u1 (.clk(clk), .d(mid), .q(dout));
            endmodule
        """),
        )


# ===================================================================
# Initial blocks and system tasks
# ===================================================================


class TestRoundTripInitial:
    """Initial blocks and system task calls."""

    def test_initial_assign(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module tb;
                reg clk;
                initial begin
                    clk = 0;
                end
            endmodule
        """),
        )

    def test_display_finish(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module tb_sys;
                reg [7:0] val;
                initial begin
                    val = 42;
                    $display("val = %d", val);
                    $finish;
                end
            endmodule
        """),
        )

    def test_delay_control(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module tb_delay;
                reg clk;
                initial begin
                    clk = 0;
                    #5 clk = 1;
                    #5 clk = 0;
                end
            endmodule
        """),
        )


# ===================================================================
# Complex / integration
# ===================================================================


class TestRoundTripComplex:
    """More complex designs combining multiple features."""

    def test_registered_adder(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module reg_adder (
                input clk,
                input rst,
                input [7:0] a,
                input [7:0] b,
                output reg [8:0] sum
            );
                always @(posedge clk)
                    if (rst)
                        sum <= 0;
                    else
                        sum <= a + b;
            endmodule
        """),
        )

    def test_fsm_simple(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module fsm (
                input clk,
                input rst,
                input go,
                output reg [1:0] state
            );
                always @(posedge clk)
                    if (rst)
                        state <= 0;
                    else
                        case (state)
                            0: if (go) state <= 1;
                            1: state <= 2;
                            2: state <= 0;
                            default: state <= 0;
                        endcase
            endmodule
        """),
        )

    def test_mux_tree(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module mux8 (
                input [2:0] sel,
                input [7:0] d0,
                input [7:0] d1,
                input [7:0] d2,
                input [7:0] d3,
                input [7:0] d4,
                input [7:0] d5,
                input [7:0] d6,
                input [7:0] d7,
                output reg [7:0] y
            );
                always @(*)
                    case (sel)
                        0: y = d0;
                        1: y = d1;
                        2: y = d2;
                        3: y = d3;
                        4: y = d4;
                        5: y = d5;
                        6: y = d6;
                        default: y = d7;
                    endcase
            endmodule
        """),
        )

    def test_bidirectional(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module bidir (
                inout [7:0] data,
                input oe,
                input [7:0] data_out,
                output [7:0] data_in
            );
                assign data_in = data;
            endmodule
        """),
        )

    def test_pipeline(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module pipe (
                input clk,
                input [7:0] din,
                output reg [7:0] dout
            );
                reg [7:0] stage1;
                reg [7:0] stage2;
                always @(posedge clk) begin
                    stage1 <= din;
                    stage2 <= stage1;
                    dout <= stage2;
                end
            endmodule
        """),
        )

    def test_mixed_always_assign(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module mixed (
                input clk,
                input [7:0] a,
                input [7:0] b,
                output [7:0] combo,
                output reg [7:0] registered
            );
                assign combo = a ^ b;
                always @(posedge clk)
                    registered <= a + b;
            endmodule
        """),
        )

    def test_multi_always(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module multi_always (
                input clk,
                input rst,
                output reg [7:0] cnt_a,
                output reg [7:0] cnt_b
            );
                always @(posedge clk)
                    if (rst) cnt_a <= 0;
                    else cnt_a <= cnt_a + 1;
                always @(posedge clk)
                    if (rst) cnt_b <= 0;
                    else cnt_b <= cnt_b + 2;
            endmodule
        """),
        )

    def test_decoder(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module decoder (
                input [2:0] sel,
                output reg [7:0] out
            );
                always @(*)
                    case (sel)
                        0: out = 1;
                        1: out = 2;
                        2: out = 4;
                        3: out = 8;
                        4: out = 16;
                        5: out = 32;
                        6: out = 64;
                        default: out = 128;
                    endcase
            endmodule
        """),
        )


# ===================================================================
# Edge cases
# ===================================================================


class TestRoundTripEdgeCases:
    """Edge cases and tricky constructs."""

    def test_no_ports(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module no_ports;
                reg [7:0] x;
                initial x = 0;
            endmodule
        """),
        )

    def test_single_assign_no_always(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module pass_thru (
                input [31:0] x,
                output [31:0] y
            );
                assign y = x;
            endmodule
        """),
        )

    def test_nested_if(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module nested (
                input a,
                input b,
                input c,
                output reg y
            );
                always @(*) begin
                    y = 0;
                    if (a)
                        if (b)
                            if (c)
                                y = 1;
                end
            endmodule
        """),
        )

    def test_wide_bus(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module wide_bus (
                input [63:0] data,
                output [63:0] result
            );
                assign result = ~data;
            endmodule
        """),
        )

    def test_division(self, parser):
        """Verilog ``/`` → DSL ``//`` → model ``/`` → emitted ``/``."""
        _round_trip(
            parser,
            textwrap.dedent("""\
            module divmod (
                input [7:0] a,
                input [7:0] b,
                output [7:0] q,
                output [7:0] r
            );
                assign q = a / b;
                assign r = a % b;
            endmodule
        """),
        )

    def test_readmem(self, parser):
        _round_trip(
            parser,
            textwrap.dedent("""\
            module rom;
                reg [7:0] mem [0:255];
                initial begin
                    $readmemh("data.hex", mem);
                end
            endmodule
        """),
        )


# ===================================================================
# Verilog file round-trip
# ===================================================================


class TestRoundTripFromFile:
    """Round-trip using the actual test Verilog file."""

    def test_verilog_all(self, parser):
        import os

        vfile = os.path.join(
            os.path.dirname(__file__),
            "..",
            "test_verilog_parser",
            "verilog",
            "verilog_all.v",
        )
        with open(vfile) as f:
            verilog = f.read()
        _round_trip(parser, verilog)

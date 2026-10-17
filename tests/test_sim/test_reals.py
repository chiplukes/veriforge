"""`real` / `realtime` (2026-10-16; expected values from Icarus 12, with
Verilator agreeing on every value).

Reals used to be stored as integers (`real r = 3.25;` held 325: the parser
also dropped the `.` of a real literal). They are lowered onto 64-bit IEEE
754 bit patterns now (`sim/reals.py`), every operation an internal
`$__vf_real` call each engine implements natively. Also here: `%-<w>`
left-justified fields and `%s` fields (every byte, zero bytes as spaces;
compiled printed a non-literal `%s` argument as a decimal), which no engine
supported before.

One intentional difference from Icarus: `$display(rt, " ", $realtime)`
prints `$realtime` as a real (`3.00000`), as the LRM says; Icarus prints
`3`.
"""

from __future__ import annotations

import io

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim.testbench import Simulator
from veriforge.sim.trace import attach_vcd
from veriforge.sim.vcd_compare import parse_vcd
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _design(source: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(source))


def _display(source: str, engine: str, max_time: int = 100) -> list[str]:
    design = _design(source)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    sim.run(max_time=max_time)
    return sim.display_output


_CONVERSIONS_AND_FORMATS = """module t;
  real r = 3.25, z, n; realtime rt; integer i; reg [7:0] b; reg signed [7:0] sb; reg [3:0] xx;
  initial begin
    $display(r); $display("%f %e %g %d %0d %h %b", r, r, r, r, r, r, r);
    $display("%d", 2.5); $display("%d", -2.5); $display("%d", 3.5); $display("%0d", 2.4999);
    i = 2.5; $display(i); i = -2.5; $display(i); b = 255.7; $display(b); b = -1.0; $display(b);
    sb = -3; r = sb; $display(r); b = 200; r = b; $display(r); r = b + 0.5; $display(r);
    r = 7 / 2; $display(r); r = 7 / 2.0; $display(r); r = 2 ** 0.5; $display(r);
    $display(z); $display(n == 0.0); xx = 4'b1x01; r = xx; $display(r);
    rt = $realtime; $display(rt); #3 rt = $realtime; $display(rt, " ", $realtime);
    $display($rtoi(3.9), " ", $rtoi(-3.9), " ", $itor(5), " ", $realtobits(1.0), " ", $bitstoreal(64'h4000000000000000));
    r = 1.0 / 0.0; $display(r); r = -r; $display(r); r = 0.0/0.0; $display(r);
    r = 1e300 * 1e300; $display("%e", r); r = 123456789.0; $display(r); r = 0.000012345; $display(r);
    r = 1.5; $display("%10.3f|%-10.2e|%0f|%.1g|%010.2f", r, r, r, r, r);
    r = 2.0; if (r) $display("true"); r = 0.0; if (!r) $display("false"); $display(r ? 1 : 2, " ", (1.5 && 1), " ", 1.0 > 0.5, " ", 2 < 1.5);
    r = 1 ? 1.5 : 2; $display(r); r = 0 ? 1.5 : 2; $display(r);
    r = -1.25; $display(-r, " ", r * r, " ", r - 1, " ", 1 - r);
    $display("%t", 1.5);
    i = 1.5e1; $display(i); b = 3.999; $display(b);
  end
endmodule
"""

_CONVERSIONS_AND_FORMATS_EXPECTED = [
    "3.25000",
    "3.250000 3.250000e+00 3.25 3 3 3 11",
    "3",
    "-3",
    "4",
    "2",
    "          3",
    "         -3",
    "  0",
    "255",
    "-3.00000",
    "200.000",
    "200.500",
    "3.00000",
    "3.50000",
    "1.41421",
    "0.00000",
    "1",
    "9.00000",
    "0.00000",
    "3.00000 3.00000",
    "          3          -3 5.00000  4607182418800017408 2.00000",
    "inf",
    "-inf",
    "nan",
    "inf",
    "1.23457e+08",
    "1.23450e-05",
    "     1.500|1.50e+00  |1.500000|2|0000001.50",
    "true",
    "false",
    "          2 1 1 0",
    "1.50000",
    "2.00000",
    "1.25000 1.56250 -2.25000 2.25000",
    "                   2",
    "         15",
    "  4",
]


_SELF_DETERMINED_INTEGER_OPERANDS = """module t;
  real r; reg [7:0] a, b; reg signed [3:0] s; integer i; reg [7:0] q;
  initial begin
    a = 200; b = 100; s = -2;
    r = (a + b) * 1.0; $display(r);
    r = a + b; $display(r);
    r = a + b + 0.0; $display(r);
    r = s; $display(r);  r = s + 1.5; $display(r); r = {s} + 1.5; $display(r);
    r = -a; $display(r); r = ~a; $display(r);
    i = a + 0.5; $display(i);
    q = (a + b) / 2.0; $display(q);
    r = a > 150 ? a : 0.5; $display(r);
    r = 3; r = r / 2; $display(r);
    r = 1e10; i = r; $display(i);
  end
endmodule
"""
_SELF_DETERMINED_INTEGER_OPERANDS_EXPECTED = [
    "44.0000",
    "44.0000",
    "44.0000",
    "-2.00000",
    "-0.500000",
    "15.5000",
    "56.0000",
    "55.0000",
    "        201",
    " 22",
    "200.000",
    "1.50000",
    " 1410065408",
]


_PROCESSES_FUNCTIONS_TASKS_MEMORIES_PARAMETERS = """module t;
  parameter real GAIN = 1.5;
  parameter real HALF = GAIN / 3;
  localparam integer W = GAIN * 4;
  reg clk = 0;
  reg [7:0] cnt = 0;
  reg signed [7:0] s;
  real acc = 0.25, m [0:3], y, z;
  integer q;
  reg [W-1:0] wv;
  function real scale(input real x, input integer k);
    real t;
    begin t = x * k; scale = t + HALF; end
  endfunction
  task twice(input real a, output real b);
    b = a * 2.0;
  endtask
  always #5 clk = ~clk;
  always @(posedge clk) begin
    cnt <= cnt + 1;
    acc <= acc * GAIN + cnt;
    m[cnt[1:0]] <= acc - 1.0;
  end
  always @* begin
    y = acc > 4.0 ? acc / 2 : -acc;
    s = cnt;
    z = s - 0.5;
  end
  always @(negedge clk) begin
    q = acc;
    twice(acc, y);
    $display("%0d acc=%f y=%g z=%e q=%0d m0=%f sc=%f w=%0d %0d", cnt, acc, y, z, q, m[0], scale(acc, cnt), W, $bits(wv));
    if (acc >= 20.0) $display("big %b", acc > 30.0);
  end
  initial #62 $finish;
endmodule
"""

_PROCESSES_FUNCTIONS_TASKS_MEMORIES_PARAMETERS_EXPECTED = [
    "1 acc=0.375000 y=0.75 z=5.000000e-01 q=0 m0=-0.750000 sc=0.875000 w=6 6",
    "2 acc=1.562500 y=3.125 z=1.500000e+00 q=2 m0=-0.750000 sc=3.625000 w=6 6",
    "3 acc=4.343750 y=8.6875 z=2.500000e+00 q=4 m0=-0.750000 sc=13.531250 w=6 6",
    "4 acc=9.515625 y=19.0312 z=3.500000e+00 q=10 m0=-0.750000 sc=38.562500 w=6 6",
    "5 acc=18.273438 y=36.5469 z=4.500000e+00 q=18 m0=8.515625 sc=91.867188 w=6 6",
    "6 acc=32.410156 y=64.8203 z=5.500000e+00 q=32 m0=8.515625 sc=194.960938 w=6 6",
    "big 1",
]


_REAL_PORTS_THROUGH_HIERARCHY = """module scale #(parameter real K = 2.0) (input real x, input [3:0] n, output real y, output [7:0] r);
  assign y = x * K + n;
  assign r = y;
endmodule
module t;
  real a, b, c; reg [3:0] n; wire [7:0] r1, r2;
  scale #(.K(0.5)) u1(.x(a), .n(n), .y(b), .r(r1));
  scale u2(.x(b), .n(4'd1), .y(c), .r(r2));
  initial begin
    a = 3.0; n = 2; #1 $display("%f %f %0d %0d", b, c, r1, r2);
    a = -7.25; #1 $display("%f %f %0d %0d", b, c, r1, r2);
  end
endmodule
"""

_REAL_PORTS_THROUGH_HIERARCHY_EXPECTED = [
    "3.500000 8.000000 4 8",
    "-1.625000 -2.250000 254 254",
]


_LEFT_JUSTIFIED_FIELDS = """module t; real r = 1.5; reg [7:0] b = 5; reg signed [7:0] sb = -3; reg [3:0] x = 4'b1x0x; reg clk = 0;
always @(posedge clk) begin
  $display("[%-5d][%-5h][%-8.2f][%-d][%-3b][%-6d][%-4d][%-10e][%-2d][%-5s]", 3, b, r, 7, b[2:0], sb, x, r, 12345, "ab");
  $display("[%-0d][%-05d][%-12g][%-9f]%-4d|", 3, 3, r, r * 2, b);
  $display(r, " ", -r, " ", r / 3);
end
initial #1 clk = 1;
endmodule
"""

_LEFT_JUSTIFIED_FIELDS_EXPECTED = [
    "[3    ][05   ][1.50    ][7          ][101][-3    ][X   ][1.500000e+00][12345][ab   ]",
    "[3][3    ][1.5         ][3.000000 ]5   |",
    "1.50000 -1.50000 0.500000",
]


_STRING_FIELDS = """module t; reg [8*5:1] s = "ab"; reg [15:0] h = 16'h0062; reg [7:0] z = 0; reg [7:0] x = 8'bx; reg [8*12:1] w = "hello world"; reg clk = 0;
always @(posedge clk) begin
  $display("[%s][%7s][%-7s][%0s][%2s][%s][%s][%s][%3s]", s, s, s, s, s, h, z, x, "abcd");
  $display("[%s][%14s][%-14s|][%0s][%5s][%-5s]", w, w, w, w, "xy", "xy");
end
initial #1 clk = 1;
endmodule
"""

_STRING_FIELDS_EXPECTED = [
    "[   ab][     ab][ab     ][ab][ab][ b][ ][ ][abcd]",
    "[ hello world][   hello world][hello world   |][hello world][   xy][xy   ]",
]


_CASES = {
    "conversions_and_formats": (_CONVERSIONS_AND_FORMATS, _CONVERSIONS_AND_FORMATS_EXPECTED),
    "self_determined_integer_operands": (_SELF_DETERMINED_INTEGER_OPERANDS, _SELF_DETERMINED_INTEGER_OPERANDS_EXPECTED),
    "processes_functions_tasks_memories_parameters": (
        _PROCESSES_FUNCTIONS_TASKS_MEMORIES_PARAMETERS,
        _PROCESSES_FUNCTIONS_TASKS_MEMORIES_PARAMETERS_EXPECTED,
    ),
    "real_ports_through_hierarchy": (_REAL_PORTS_THROUGH_HIERARCHY, _REAL_PORTS_THROUGH_HIERARCHY_EXPECTED),
    "left_justified_fields": (_LEFT_JUSTIFIED_FIELDS, _LEFT_JUSTIFIED_FIELDS_EXPECTED),
    "string_fields": (_STRING_FIELDS, _STRING_FIELDS_EXPECTED),
}


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", sorted(_CASES))
def test_against_icarus(case, engine):
    source, expected = _CASES[case]
    assert _display(source, engine) == expected


@pytest.mark.parametrize("engine", ENGINES)
def test_python_api(engine):
    # `drive(name, float)` and `read_real`; an integral output rounds half
    # away from zero (-5.5 -> -6).
    source = """module t(input real x, input [3:0] k, output real y, output [7:0] q);
  real m [0:1];
  assign y = x * k + 0.5;
  assign q = y;
  always @* begin m[0] = y; m[1] = -y; end
endmodule
"""
    design = _design(source)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    sim.drive("x", 1.25)
    sim.drive("k", 3)
    sim.settle()
    assert (sim.read_real("y"), sim.read("q").val, sim.read_real("m[1]")) == (4.25, 4, -4.25)
    sim.drive("x", -2.0)
    sim.settle()
    assert (sim.read_real("y"), sim.read("q").val) == (-5.5, 0xFA)
    with pytest.raises(TypeError, match="only a real signal"):
        sim.drive("k", 1.5)
    with pytest.raises(TypeError, match="not a real signal"):
        sim.read_real("q")


@pytest.mark.parametrize("engine", ENGINES)
def test_vcd_real_variables(engine):
    source = """module t; real r = 0.5; reg clk = 0;
  always #1 clk = ~clk;
  always @(posedge clk) r <= r * 1.5;
endmodule
"""
    design = _design(source)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    buf = io.StringIO()
    session = attach_vcd(sim, buf)
    sim.run(max_time=4)
    session.close()
    text = buf.getvalue()
    assert "$var real 64" in text, text
    assert parse_vcd(text).changes["r"] == [(0, "r0.5"), (1, "r0.75"), (3, "r1.125")]


@pytest.mark.parametrize("engine", ENGINES)
def test_parameter_types(engine):
    # An untyped parameter takes its value's type; an integer-typed one
    # rounds (W used to be the real 6.0 in expressions).
    source = """module t;
  parameter P = 2.5;
  parameter real R = 3;
  localparam integer W = P * 2.6;
  localparam [3:0] N = -1.5;
  reg [7:0] a = 8'd250;
  initial $display("%f %f %0d %h %0d %0d", P, R / 2, W, a + W, N, $bits(a[W-1:0]));
endmodule
"""
    assert _display(source, engine) == ["2.500000 1.500000 7 00000101 14 7"]  # (Icarus)


def test_unsupported_and_illegal_uses():
    for body, error in (
        ("real r; reg [3:0] b; initial b = r[1];", TypeError),
        ("real r; reg [3:0] b; initial b = r & 1;", TypeError),
        ("real r; reg [99:0] w; initial r = w;", NotImplementedError),
        ("real r; reg b; initial case (r) 1.0: b = 1; endcase", NotImplementedError),
    ):
        design = _design(f"module t;\n  {body}\nendmodule\n")
        with pytest.raises(error):
            Simulator(design.modules[0], engine="reference", design=design)


def test_parse_and_emit():
    # `3.25` parsed as 325 (the "." was dropped), `1.5e2` was fine;
    # `parameter real` lost its type.
    module = _design("module t; parameter real P = 1.5; real r = 3.25, s = 1.5e2; endmodule").modules[0]
    assert module.parameters[0].param_type == "real"
    assert module.variables[0].initial_value.value == 3.25
    assert module.variables[1].initial_value.value == 150.0
    out = emit_module(module)
    assert "parameter real P = 1.5" in out and "real r = 3.25;" in out, out
    assert emit_module(_design(out).modules[0]) == out

"""SystemVerilog constructs the emitter wrote back as text that doesn't parse.

Found re-emitting ibex for the design reducer (which re-parses every candidate):
`pkg::name` as `pkg.name`, assignment patterns as `/* unknown expression */`, a
select target `m[i][j] = x` of a generate-block array as a size-cast shift, a
port-less function as old-style `name;`, `$time` as `$time()`, functions/typedefs
in a generate block as `/* unknown generate item */`, and typedef ranges as
`[W:-:1:0]` / `$clog2W`. Each must round-trip: emit, re-parse, emit again.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text

_CASES = {
    "package_scope": (
        "package p; localparam int W = 4; endpackage\n"
        "module t (input [p::W-1:0] a, output [3:0] y); assign y = a + p::W; endmodule\n",
        ["p::W"],
    ),
    "assignment_pattern": (
        "module t (output logic [3:0] y [2], output logic [7:0] z);\n"
        "  typedef struct packed { logic [3:0] a; logic [3:0] b; } s_t;\n"
        "  s_t q;\n"
        "  always_comb begin y = '{default: '0}; q = '{a: 4'd1, b: 4'd2}; z = q; end\n"
        "endmodule\n",
        ["'{default: '0}", "'{a: 4'd1, b: 4'd2}"],
    ),
    "lhs_select_of_generate_array": (
        "module t (input [1:0] d, output logic [1:0] y);\n"
        "  if (1) begin : g\n"
        "    logic [1:0] m [4];\n"
        "    always_comb begin m[1] = '0; m[1][1] = d[0]; y = m[1]; end\n"
        "  end\n"
        "endmodule\n",
        ["m[1][1] = d[0]"],
    ),
    "portless_function_and_time": (
        "module t (output logic [31:0] y);\n"
        "  function automatic logic [31:0] f(); return 32'd7; endfunction\n"
        "  always_comb y = f();\n"
        '  initial $display("%t", $time);\n'
        "endmodule\n",
        ["f();", "$time)"],
    ),
    "generate_function_and_typedef": (
        "module t #(parameter int W = 8) (input [W-1:0] a, output [W-1:0] y);\n"
        "  if (W > 4) begin : g\n"
        "    typedef logic [W-1:0] w_t;\n"
        "    function automatic w_t inv(input w_t v); return ~v; endfunction\n"
        "    assign y = inv(a);\n"
        "  end else begin : g2\n"
        "    assign y = a;\n"
        "  end\n"
        "endmodule\n",
        ["typedef logic [W - 1:0] w_t;", "function automatic"],
    ),
    "typedef_ranges": (
        "module t #(parameter int W = 8) (output y);\n"
        "  typedef logic [W-1:0] [$clog2(W)-1:0] c_t;\n"
        "  assign y = 1'b1;\n"
        "endmodule\n",
        ["[W - 1:0] [$clog2(W) - 1:0]"],
    ),
}


@pytest.mark.parametrize("name", sorted(_CASES))
def test_sv_construct_round_trips(name):
    src, expected = _CASES[name]
    first = emit_design(parse_text(src))
    assert "unknown" not in first
    for text in expected:
        assert text in first, (text, first)
    assert emit_design(parse_text(first)) == first

"""Package-qualified values (`p::W`, a package enum member `p::B`) with no `import`.

Only `import p::*;` brought package values into a module; qualified types
(`p::e_t`) were resolved, qualified values were not. So `input [p::W-1:0] a` was
1 bit wide and `M == p::C` read x, on every engine. `resolve_sv_imports` now
resolves them, on a copy of the caller's design. Found while fixing the
emitter's `pkg::name` output (ibex uses `ibex_pkg::...` throughout).
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
package p;
  typedef enum logic [1:0] {A, B, C} e_t;
  localparam int W = 4;
  localparam logic [69:0] BIG = 70'h3F_0000_0000_0000_0001;
endpackage
module sub(input [p::W-1:0] d, output [p::W-1:0] q);
  assign q = d + p::W;
endmodule
module t #(parameter p::e_t M = p::B) (input [p::W-1:0] a, output [3:0] y, output [3:0] y2, output [3:0] y3,
    output [69:0] y4, output [1:0] y5);
  assign y = (M == p::C) ? a : p::W;
  assign y2 = (M == p::B) ? a : 4'd0;
  sub u (.d(a), .q(y3));
  assign y4 = p::BIG + 70'd1;
  assign y5 = p::C;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_package_qualified_values(engine):
    design = parse_text(_SRC)
    before = emit_design(design)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("a", Value(9, width=4))
    sim.settle()
    assert sim.read("a").width == 4
    assert sim.read("y") == Value(4, width=4)
    assert sim.read("y2") == Value(9, width=4)
    assert sim.read("y3") == Value(13, width=4)
    assert sim.read("y4") == Value(0x3F_0000_0000_0000_0002, width=70)
    assert sim.read("y5") == Value(2, width=2)
    assert emit_design(design) == before  # (the caller's model keeps its `p::` names)
    assert "p::W" in before and "p.W" not in before

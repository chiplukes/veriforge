"""A Simulator leaves the caller's design as it was (fixed 2026-11-22).

Its passes rewrite modules in place, and only a design with package-qualified
values was copied first: a second Simulator on the same design (another
engine, as `diverge` runs them) saw the first one's lowering -- `real`
variables were already vectors, so `read_real` raised, and parameter arrays
were already packed.
"""

from __future__ import annotations

from veriforge.model.variables import VariableKind
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
module top(input [1:0] i, output [7:0] y, output z);
  localparam logic [7:0] A [3] = '{8'd1, 8'd2, 8'd3};
  real r;
  initial r = 2.5;
  assign y = A[i];
  assign n = i[0];
  assign z = n;
endmodule
"""


def test_one_design_many_simulators():
    design = parse_text(_SRC)
    module = design.get_module("top")
    kinds = [(v.name, v.kind) for v in module.variables]
    for engine in ENGINES:
        sim = Simulator(module, engine=engine, design=design)
        sim.drive("i", 1)
        sim.run(max_time=0)
        sim.settle()
        assert (sim.read_real("r"), int(sim.read("y")), int(sim.read("z"))) == (2.5, 2, 1), engine
    assert [(v.name, v.kind) for v in module.variables] == kinds
    assert ("r", VariableKind.REAL) in kinds
    assert [p.name for p in module.parameters if p.dimensions] == ["A"]  # (not packed)
    assert [n.name for n in module.nets] == []  # (the implicit net `n` is declared on the copy)

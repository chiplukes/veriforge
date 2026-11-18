"""A field read of a struct-valued parameter (`Nm.lower_cause` of
`localparam c_t Nm = '{irq_int: 1'b1, lower_cause: 5'd31}`) is that field's value.

The parameter itself evaluated to its packed value, but a field select read x on
reference/vm/vm-fast and 0 on compiled. Found by tools/corpus_check.py in ibex's
if_stage (`irq_vec = ExcCauseIrqNm.lower_cause;`). `lower_struct_param_fields`
folds the reads to constants for every engine.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
package p;
  typedef struct packed { logic irq_ext; logic irq_int; logic [4:0] lower_cause; } c_t;
  localparam c_t Nm = '{irq_ext: 1'b0, irq_int: 1'b1, lower_cause: 5'd31};
  localparam c_t Tm = '{irq_ext: 1'b1, irq_int: 1'b0, lower_cause: 5'd07};
endpackage
module sub import p::*; (output logic [4:0] q);
  localparam c_t Sub = '{irq_ext: 1'b0, irq_int: 1'b0, lower_cause: 5'd19};
  assign q = Sub.lower_cause;
endmodule
module t import p::*; (input sel, output logic [4:0] y1, output logic [4:0] y2, output [6:0] y3,
    output y4, output [4:0] y5, output [4:0] y6, output [69:0] y7);
  typedef struct packed { logic [5:0] tag; logic [69:0] body; } w_t;
  localparam c_t Loc = '{irq_ext: 1'b1, irq_int: 1'b1, lower_cause: 5'd5};
  localparam w_t Wide = '{tag: 6'd9, body: 70'h3F_0000_0000_0000_00AB};
  always_comb begin
    y1 = Nm.lower_cause;
    if (sel) y1 = Tm.lower_cause;
  end
  always_comb y2 = Loc.lower_cause;
  assign y3 = Nm;
  assign y4 = Tm.irq_ext;
  assign y5 = Loc.lower_cause + 5'd1;
  sub u (.q(y6));
  assign y7 = Wide.body;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_struct_parameter_field_reads(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("sel", Value(0))
    sim.settle()
    assert sim.read("y1") == Value(31, width=5)
    assert sim.read("y2") == Value(5, width=5)
    assert sim.read("y3") == Value(0b0111111, width=7)
    assert sim.read("y4") == Value(1)
    assert sim.read("y5") == Value(6, width=5)
    assert sim.read("y6") == Value(19, width=5)
    assert sim.read("y7") == Value(0x3F_0000_0000_0000_00AB, width=70)
    sim.drive("sel", Value(1))
    sim.settle()
    assert sim.read("y1") == Value(7, width=5)

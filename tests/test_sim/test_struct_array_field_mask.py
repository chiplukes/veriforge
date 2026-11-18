"""A continuous assign reading a field of an unpacked array of packed structs
(`assign y = arr[0].a;`) keeps the field's x bits.

Compiled's mask emitter had no case for a struct stored in a memory element and
returned 0, so an x element read as a defined 0 (the value path was right).
Found by tools/corpus_check.py in pulp's axi_lite_mailbox test wrapper
(`assign slv0_ar_ready = slv_resps[0].ar_ready;`).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t (input clk, input [7:0] d, input i, output y1, output [7:0] y2, output y3, output [7:0] y4,
          output w1, output [69:0] w2, output reg r1);
  typedef struct packed { logic a; logic [7:0] dat; } s_t;
  typedef struct packed { logic a; logic [69:0] dat; } w_t;
  s_t arr [2];
  w_t warr [2];
  assign y1 = arr[0].a;
  assign y2 = arr[0].dat;
  always @(posedge clk) begin arr[1].a <= i; arr[1].dat <= d; warr[1].a <= i; end
  assign y3 = arr[1].a;
  assign y4 = arr[1].dat;
  assign w1 = warr[1].a;
  assign w2 = warr[0].dat;
  always @* r1 = arr[0].a;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_struct_array_field_reads_keep_x(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk", Value(0))
    sim.drive("d", Value(0x5A, width=8, mask=0x0F))  # low nibble x
    sim.drive("i", Value(1))
    sim.run(max_time=0)
    sim.settle()
    for name, width in (("y1", 1), ("y2", 8), ("y3", 1), ("y4", 8), ("w1", 1), ("w2", 70), ("r1", 1)):
        assert sim.read(name).mask == (1 << width) - 1, f"{engine} {name} = {sim.read(name)}"
    sim.drive("clk", Value(1))
    sim.settle()
    assert sim.read("y3") == Value(1)
    assert sim.read("y4") == Value(0x50, width=8, mask=0x0F)
    assert sim.read("w1") == Value(1)
    assert sim.read("y1").mask == 1  # element 0 still never written

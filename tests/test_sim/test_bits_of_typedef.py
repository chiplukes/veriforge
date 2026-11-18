"""`$bits(type_t)` of a typedef is the type's width on every engine.

The reference engine evaluated the type name as an (unknown) signal and returned 1,
so ibex's `cpu_ctrl_sts_part_t'(csr_wdata_int[$bits(cpu_ctrl_sts_part_t)-1:0])` sliced
one bit (vm and compiled read the width from the elaboration environment). Found by
tools/corpus_check.py in `ibex_cs_registers`. `lower_sv_functions` now folds it to a
constant for all engines.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
package p;
  typedef struct packed { logic [3:0] hi; logic [5:0] lo; } pkg_t;
endpackage
module t import p::*; (input [127:0] w, output [7:0] y1, output [7:0] y3, output [9:0] y4,
    output [31:0] n1, output [31:0] n2, output [31:0] n3, output [31:0] n4, output [31:0] n5, output [31:0] n6,
    output [69:0] y5);
  typedef struct packed { logic a; logic [2:0] b; logic c; logic [2:0] d; } s_t;
  typedef logic [11:0] alias_t;
  typedef enum logic [2:0] { A, B, C } e_t;
  typedef struct packed { logic [5:0] top; logic [63:0] body; } wide_t;
  s_t r1;
  pkg_t r4;
  wide_t r5;
  assign r1 = s_t'(w[$bits(s_t)-1:0]);
  assign y1 = r1;
  assign y3 = w[$bits(s_t)-1:0];
  assign r4 = w[$bits(pkg_t)-1:0];
  assign y4 = r4;
  assign r5 = w[$bits(wide_t)-1:0];
  assign y5 = r5;
  assign n1 = $bits(s_t);
  assign n2 = $bits(r1);
  assign n3 = $bits(pkg_t);
  assign n4 = $bits(alias_t);
  assign n5 = $bits(e_t);
  assign n6 = $bits(wide_t);
endmodule
"""

_W = 0x0123_4567_89AB_CDEF_8909_8DE0_FFEE_DDCC


@pytest.mark.parametrize("engine", ENGINES)
def test_bits_of_typedef(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("w", Value(_W, width=128))
    sim.settle()
    got = {n: sim.read(n).val for n in ("y1", "y3", "y4", "y5", "n1", "n2", "n3", "n4", "n5", "n6")}
    assert got == {
        "y1": _W & 0xFF,
        "y3": _W & 0xFF,
        "y4": _W & 0x3FF,
        "y5": _W & ((1 << 70) - 1),
        "n1": 8,
        "n2": 8,
        "n3": 10,
        "n4": 12,
        "n5": 3,
        "n6": 70,
    }

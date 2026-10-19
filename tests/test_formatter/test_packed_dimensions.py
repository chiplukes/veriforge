"""Packed and unpacked dimensions in emitted declarations (2026-10-18).

The model keeps a multi-dimensional packed array's extra packed ranges in
`dimensions`, as the simulator's memory form (`p[i]` indexes the packed
lanes). The emitter printed every entry of `dimensions` after the name, so
`logic [3:0][7:0] p;` came back as the unpacked `logic [7:0] p [3:0];` --
and `logic [3:0][7:0] q [0:1];` as `logic [7:0] q [3:0][0:1];`. Port
dimensions were dropped altogether, and `reg [2:0][1:0] m [0:3];` parsed as
a variable of a user type named `reg`, losing its width.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.codegen.verilog_formatter import format_module
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

_DECLS = [
    "input [3:0][7:0] ip",
    "output logic [1:0][3:0] op",
    "input [7:0] ia [0:1]",
]
_BODY = [
    "logic [3:0][7:0] p;",
    "wire [1:0][2:0] w;",
    "logic [3:0][7:0] q [0:1];",
    "reg [2:0][1:0] m [0:3];",
    "reg [7:0] r [0:3];",
    "bit [1:0][3:0] b;",
]


def _parse(text: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(text)).modules[0]


@pytest.mark.parametrize("emit", [emit_module, format_module])
def test_declarations_round_trip(emit):
    source = f"module k({', '.join(_DECLS)});\n" + "\n".join(_BODY) + "\nendmodule\n"
    out = emit(_parse(source))
    for decl in [*_DECLS, *_BODY]:
        assert decl in out, (decl, out)
    assert emit(_parse(out)) == out


def test_reg_with_two_packed_ranges_keeps_its_width():
    m = next(v for v in _parse("module k; reg [2:0][1:0] m [0:3]; endmodule").variables if v.name == "m")
    assert m.width is not None and m.kind.value == "reg" and m.packed_dim_count == 1


@pytest.mark.parametrize("engine", ["reference", "vm"])
def test_packed_array_simulates_the_same_after_emit(engine):
    source = """module k(input [7:0] d, input [1:0] i, output [31:0] whole, output [7:0] lane);
  logic [3:0][7:0] p;
  assign p = {d, ~d, d ^ 8'h0f, 8'h5a};
  assign whole = p;
  assign lane = p[i];
endmodule
"""

    def run(text):
        sim = Simulator(_parse(text), engine=engine)
        sim.drive("d", 0x3C)
        sim.drive("i", 2)
        sim.settle()
        return sim.read("whole").val, sim.read("lane").val

    expected = (0x3CC3335A, 0xC3)
    assert run(source) == expected
    assert run(emit_module(_parse(source))) == expected

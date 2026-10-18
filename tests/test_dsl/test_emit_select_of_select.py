"""Selects of selects and of expressions in emitted Verilog (2026-10-18).

The DSL lets any expression be indexed, and every engine reads `a[3:2][1]`
as bit 1 of the slice (`a[3]`) and `(a + b)[7]` as bit 7 of the sum. The
emitter printed both verbatim: `a[3:2][1]` is illegal Verilog (Icarus
rejects it, so only the simulations of emitted RTL failed), and `(a + b)[7]`
came out as `a + b[7]` -- legal, and a select of `b`. A constant select of a
slice now folds (`a[3]`, `a[6:5]`); anything else selects the value's bits
with a shift (`$unsigned(1'(a + b >> 7))`). A memory word's bit
(`mem[i][3]`) is legal and is kept.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.codegen.verilog_formatter import format_module
from veriforge.dsl import Module
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

_DRIVES = {"a": 0b10101100, "b": 0b10000000}
_OUTPUTS = ("y", "z", "w", "v", "u")


def _module():
    m = Module("t")
    a = m.input("a", width=8)
    b = m.input("b", width=8)
    y = m.output("y", width=1)
    z = m.output("z", width=2)
    w = m.output("w", width=1)
    v = m.output("v", width=3)
    u = m.output("u", width=1)
    m.assign(y, a[3:2][1])
    m.assign(z, a[7:4][2:1])
    m.assign(w, (a + b)[7])
    m.assign(v, a.part_select(2, 4)[3:1])
    m.assign(u, a[3:2][b[0]])  # (a non-constant index: a shift)
    return m.build()


def _values(module, engine="reference"):
    sim = Simulator(module, engine=engine)
    for name, value in _DRIVES.items():
        sim.drive(name, value)
    sim.settle()
    return [str(sim.read(n)) for n in _OUTPUTS]


@pytest.mark.parametrize("emit", [emit_module, format_module])
def test_emitted_selects_are_legal_and_mean_the_same(emit):
    module = _module()
    text = emit(module)
    for legal in ("a[3];", "a[6:5];", "1'(a + b >> 7)", "3'(a[2 +: 4] >> 1)"):
        assert legal in text, (legal, text)
    for illegal in ("][", "b[7]"):
        assert illegal not in text, (illegal, text)
    reparsed = tree_to_design(verilog_parser(start="source_text").build_tree(text)).modules[0]
    assert _values(reparsed) == _values(module) == ["1'b1", "2'b01", "1'b0", "3'b101", "1'b1"]


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")
def test_icarus_accepts_emitted_selects(tmp_path):
    src = tmp_path / "t.v"
    src.write_text(
        emit_module(_module())
        + """
module tb;
  reg [7:0] a = 8'b10101100, b = 8'b10000000;
  wire y, w, u; wire [1:0] z; wire [2:0] v;
  t d(.a(a), .b(b), .y(y), .z(z), .w(w), .v(v), .u(u));
  initial #1 $display("%b %b %b %b %b", y, z, w, v, u);
endmodule
"""
    )
    out = tmp_path / "t.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(out), str(src)], check=True, capture_output=True)
    result = subprocess.run(["vvp", str(out)], check=True, capture_output=True, text=True)
    assert result.stdout.split() == ["1", "01", "0", "101", "1"]


def test_memory_word_bit_is_kept():
    text = emit_module(
        tree_to_design(
            verilog_parser(start="source_text").build_tree(
                "module k(input [1:0] i, output q); reg [7:0] mem [0:3]; assign q = mem[i][3]; endmodule"
            )
        ).modules[0]
    )
    assert "mem[i][3]" in text, text

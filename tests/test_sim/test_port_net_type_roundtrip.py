"""Regression tests: an explicit `net_type` keyword on a port (`input logic
clk`, `input wire foo`, `inout tri bar`) was silently dropped during
parsing.

Root cause: `_extract_port_declaration` (`transforms/_declarations.py`)
walked every child of an `input_declaration`/`output_declaration`/
`inout_declaration` subtree looking for `range`/`dimension`/
`scoped_identifier`/`list_of_port_identifiers` trees and `KW_REG`/
`KW_SIGNED`/`IDENTIFIER` tokens, but the grammar
(`input_declaration: KW_INPUT net_type? KW_SIGNED? range* ...`) also puts an
optional `net_type` subtree there, and no branch ever inspected it --
confirmed directly by parsing `input logic clk` and observing the resulting
`Port.net_type` was `None` despite `Port.net_type: str | None` already
existing as a field and the emitter (`codegen/verilog_emitter.py`) already
knowing how to render it. Every one of our own 4 engines shares this same
extraction path before elaboration, so a `logic`/`wire`-declared port was
silently treated as an implicit-type port everywhere internally (though an
external tool like Icarus/Verilog, parsing the emitted text itself, would
still see and honor the keyword) -- a prerequisite fix for fuzzing `logic`-
typed ports at all.
"""

from __future__ import annotations

import pytest

from veriforge.codegen import emit_design
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


class TestPortNetTypeRoundtrip:
    def test_input_logic_port_net_type_extracted(self):
        mod = _parse("module dut(input logic clk); endmodule")
        port = next(p for p in mod.ports if p.name == "clk")
        assert port.net_type == "logic"

    def test_input_wire_port_net_type_extracted(self):
        mod = _parse("module dut(input wire [3:0] a); endmodule")
        port = next(p for p in mod.ports if p.name == "a")
        assert port.net_type == "wire"

    def test_output_logic_port_net_type_extracted(self):
        mod = _parse("module dut(output logic [3:0] o); endmodule")
        port = next(p for p in mod.ports if p.name == "o")
        assert port.net_type == "logic"

    def test_inout_tri_port_net_type_extracted(self):
        mod = _parse("module dut(inout tri [3:0] b); endmodule")
        port = next(p for p in mod.ports if p.name == "b")
        assert port.net_type == "tri"

    def test_plain_port_still_has_no_net_type(self):
        """No regression: an implicit-type port stays untyped."""
        mod = _parse("module dut(input [3:0] a); endmodule")
        port = next(p for p in mod.ports if p.name == "a")
        assert port.net_type is None

    def test_output_reg_port_unaffected(self):
        """`output reg` is a separate grammar alternative (KW_REG, not
        net_type) and must keep going through `data_type`, not `net_type`."""
        mod = _parse("module dut(output reg [3:0] o); endmodule")
        port = next(p for p in mod.ports if p.name == "o")
        assert port.net_type is None
        assert port.data_type == "reg"

    def test_logic_port_survives_reemit_roundtrip(self):
        mod = _parse("module dut(input logic clk, output logic [3:0] o); assign o = {4{clk}}; endmodule")
        from veriforge.model.design import Design  # noqa: PLC0415

        design = Design()
        design.modules.append(mod)
        text = emit_design(design)
        assert "input logic clk" in text
        assert "output logic [3:0] o" in text

        reparsed = _parse(text.split("endmodule")[0] + "endmodule")
        clk_port = next(p for p in reparsed.ports if p.name == "clk")
        o_port = next(p for p in reparsed.ports if p.name == "o")
        assert clk_port.net_type == "logic"
        assert o_port.net_type == "logic"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_logic_typed_ports_simulate_correctly(self, engine):
        """A `logic`-typed port must behave identically to an untyped one --
        this is the same signal, just with an explicit keyword surviving now
        instead of being silently discarded."""
        mod = _parse("""
            module dut(input logic clk, input wire [3:0] a, output logic [3:0] o, output reg [3:0] r);
                logic [3:0] w1;
                assign w1 = a;
                assign o = w1;
                always @(posedge clk) r <= a;
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("clk", Value(0, width=1))
        sim.drive("a", Value(5, width=4))
        sim.settle()
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        assert int(sim.read("o")) == 5
        assert int(sim.read("r")) == 5

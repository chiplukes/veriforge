"""Regression tests for 2-D packed-array ANSI port declarations
(``input logic signed [3:0][17:0] foo`` -- "N parallel W-bit lanes", a
common AXI-Stream ``tdata`` bus style).

Two related bugs in the same area, both in the grammar/extraction layer
that only ever allowed a *single* packed ``range`` before the identifier
list in a port declaration:

1. ``input_declaration``/``output_declaration``/``inout_declaration`` used
   ``range?`` (zero or one), unlike ``net_declaration`` which already had a
   dedicated ``range+`` alternative for exactly this shape -- a second
   packed dimension right after the first was a hard parse error.
2. Even with the grammar accepting multiple ``range`` children,
   ``_extract_port_declaration`` only kept the *last* one seen (each new
   ``range`` silently overwrote ``width``), discarding every dimension but
   the innermost -- ``net_declaration``'s extraction already split
   multiple ranges into ``width`` (last) + extra packed ``dimensions``
   (everything before it); ports needed the identical treatment.
3. Once parsing was fixed, the reference engine's ``Scheduler`` turned out
   to have its own latent gap (previously unreachable, since no such port
   could ever parse before): unlike nets/vars, and unlike the vm/compiled
   engines' own port registration, it never registered a ``dimensions``-
   bearing port as a memory -- it silently fell through to registering it
   as a single ``width``-bit (innermost-dimension-only) scalar signal, so
   ``foo[i]`` became a *bit* select into that truncated scalar instead of
   an element select.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


class TestTwoDimensionalPackedArrayPortParsing:
    def test_input_port_2d_packed_array_signed(self):
        mod = _parse("module dut(input logic signed [3:0][17:0] foo); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert port.signed is True
        assert int(port.width.msb.value) == 17
        assert int(port.width.lsb.value) == 0
        assert len(port.dimensions) == 1
        assert int(port.dimensions[0].msb.value) == 3
        assert int(port.dimensions[0].lsb.value) == 0

    def test_output_port_2d_packed_array(self):
        mod = _parse("module dut(output logic [3:0][17:0] foo); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert int(port.width.msb.value) == 17
        assert len(port.dimensions) == 1
        assert int(port.dimensions[0].msb.value) == 3

    def test_output_reg_port_2d_packed_array(self):
        """The `output reg [N][W]` grammar alternative (list_of_variable_port_identifiers)."""
        mod = _parse("module dut(output reg [3:0][17:0] foo); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert port.data_type == "reg"
        assert int(port.width.msb.value) == 17
        assert len(port.dimensions) == 1
        assert int(port.dimensions[0].msb.value) == 3

    def test_inout_port_2d_packed_array(self):
        mod = _parse("module dut(inout wire [3:0][17:0] foo); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert int(port.width.msb.value) == 17
        assert len(port.dimensions) == 1
        assert int(port.dimensions[0].msb.value) == 3

    def test_three_dimensional_packed_array_port(self):
        """More than two packed dimensions: only the innermost becomes width,
        everything else becomes extra packed dims, in outer-to-inner order."""
        mod = _parse("module dut(input logic [1:0][2:0][17:0] foo); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert int(port.width.msb.value) == 17
        assert len(port.dimensions) == 2
        assert int(port.dimensions[0].msb.value) == 1
        assert int(port.dimensions[1].msb.value) == 2

    def test_plain_single_range_port_unaffected(self):
        """No regression: a plain single-dimension port must be unaffected."""
        mod = _parse("module dut(input logic [17:0] foo); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert int(port.width.msb.value) == 17
        assert port.dimensions in (None, [])

    def test_scalar_port_unaffected(self):
        mod = _parse("module dut(input clk); endmodule")
        port = next(p for p in mod.ports if p.name == "clk")
        assert port.width is None
        assert port.dimensions in (None, [])


class TestTwoDimensionalPackedArrayPortSimulation:
    """Each lane must be independently readable/writable as a full
    element (`foo[i]`), not a single bit of a truncated scalar."""

    _SRC = """
    module dut (
        input clk,
        input logic signed [3:0][17:0] foo,
        output logic [17:0] lane2_out
    );
    assign lane2_out = foo[2];
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_each_lane_independently_addressable(self, engine):
        mod = _parse(self._SRC)
        sim = Simulator(mod, engine=engine)
        sim.drive("foo[0]", 0x11111)
        sim.drive("foo[1]", 0x22222)
        sim.drive("foo[2]", 0x3AAAA)
        sim.drive("foo[3]", 0x4CCCC)
        sim.run(max_time=0)
        assert int(sim.signal("lane2_out").value) == 0x3AAAA

    @pytest.mark.parametrize("engine", ENGINES)
    def test_lane_width_is_the_innermost_range_not_one_bit(self, engine):
        mod = _parse(self._SRC)
        sim = Simulator(mod, engine=engine)
        sim.drive("foo[0]", (1 << 18) - 1)  # all-ones fits only if the lane is really 18 bits wide
        sim.run(max_time=0)
        v = sim.signal("foo[0]").value
        assert v.width == 18, f"expected an 18-bit lane, got width={v.width}"
        assert int(v) == (1 << 18) - 1

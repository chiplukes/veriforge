"""Regression tests for ANSI port-list inline initializers
(``output reg foo = 1;``, IEEE 1364-2005 style, common Verilog-2001+).

Two related bugs, both in the same area:

1. The parser's ``list_of_variable_port_identifiers`` grammar production
   (used for ``output reg foo = 1;`` -- any ``reg``-typed ANSI port) has a
   *flat* shape: ``PORT_IDENTIFIER ("=" constant_expression)?``, unlike
   ``list_of_port_identifiers`` (used for plain ``wire``-style ports),
   where each identifier is wrapped in its own ``port_id_with_dimensions``
   subtree carrying its own default-value child.
   ``_extract_port_identifiers_with_dimensions`` only handled the second
   shape; for the first, it matched the bare identifier token and
   unconditionally recorded ``None`` for its default value, silently
   discarding the following ``constant_expression`` sibling regardless of
   whether one was present.
2. Even once the default value is correctly parsed onto ``Port.default_value``,
   none of the three simulation engines' port-registration code applied it
   as the port's initial value at t=0 -- only nets/vars ever consulted
   their own ``initial_value``. Every reg-typed output/inout port with an
   inline initializer started as X regardless.

Input ports carrying a ``default_value`` are a different, unrelated SV
feature (the value an unconnected *instance* port falls back to) that real
hardware can't apply to an externally-driven signal; `check_input_port_init`
already warns about that case and it's deliberately left unapplied.
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


class TestPortDefaultValueParsing:
    """`Port.default_value` must be captured for both ANSI port grammar shapes."""

    def test_reg_output_port_inline_initializer_parsed(self):
        """`output reg foo = 1;` -- the flat list_of_variable_port_identifiers shape."""
        mod = _parse("module dut(input clk, output reg ready = 1); endmodule")
        port = next(p for p in mod.ports if p.name == "ready")
        assert port.default_value is not None
        assert int(port.default_value.value) == 1

    def test_reg_output_port_no_initializer_stays_none(self):
        """A reg output port *without* an initializer must not pick up a stray default."""
        mod = _parse("module dut(input clk, output reg ready); endmodule")
        port = next(p for p in mod.ports if p.name == "ready")
        assert port.default_value is None

    def test_multiple_reg_ports_only_the_initialized_one_gets_a_default(self):
        """A mix of initialized/uninitialized reg ports in one declaration."""
        mod = _parse("module dut(output reg a = 1, output reg b, output reg c = 0); endmodule")
        by_name = {p.name: p for p in mod.ports}
        assert int(by_name["a"].default_value.value) == 1
        assert by_name["b"].default_value is None
        assert int(by_name["c"].default_value.value) == 0

    def test_plain_wire_port_initializer_still_parsed(self):
        """Regression guard: the list_of_port_identifiers shape (plain wire-style
        ports) must keep working after touching the shared extraction helper."""
        mod = _parse("module dut(output [7:0] foo = 8'd5); endmodule")
        port = next(p for p in mod.ports if p.name == "foo")
        assert port.default_value is not None
        assert int(port.default_value.value) == 5


class TestPortDefaultValueSimulation:
    """The parsed default value must actually seed the signal at t=0."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_output_port_initializer_applied(self, engine):
        mod = _parse("module dut(input clk, output reg ready = 1); endmodule")
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert int(sim.signal("ready").value) == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_output_port_initializer_zero_applied(self, engine):
        """A `= 0` initializer must be distinguishable from "uninitialized" (X)."""
        mod = _parse("module dut(input clk, output reg flag = 0); endmodule")
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        v = sim.signal("flag").value
        assert v.mask == 0, f"expected a known value, got X (mask={v.mask:#x})"
        assert int(v) == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_output_port_without_initializer_stays_x(self, engine):
        """No regression: a reg output port with no initializer must still start X."""
        mod = _parse("module dut(input clk, output reg ready); endmodule")
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert sim.signal("ready").value.mask != 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wide_reg_output_port_initializer_applied(self, engine):
        mod = _parse("module dut(input clk, output reg [15:0] count = 16'hBEEF); endmodule")
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert int(sim.signal("count").value) == 0xBEEF

    @pytest.mark.parametrize("engine", ENGINES)
    def test_downstream_always_block_sees_initialized_value_immediately(self, engine):
        """The scenario from the original bug report: downstream combinational/
        sequential logic conditioning on the initialized port must see the
        real value at t=0, not X (which would otherwise persist through any
        `always` block lacking an unconditional final else)."""
        src = """
        module dut(
            input        clk,
            output reg   ready = 1,
            output reg   gated_count
        );
            always @(posedge clk) begin
                if (ready) gated_count <= gated_count + 1;
            end
        endmodule
        """
        mod = _parse(src)
        sim = Simulator(mod, engine=engine)
        sim.drive("clk", 0)
        sim.run(max_time=0)
        assert int(sim.signal("ready").value) == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_input_port_initializer_not_applied(self, engine):
        """Input ports keep the pre-existing, intentional behavior: the
        initializer is not synthesizable and must be ignored, not applied.
        (`check_input_port_init` warns about this at WARNING level.)"""
        mod = _parse("module dut(input clk, input reg en = 1, output reg ready = 1); endmodule")
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert int(sim.signal("ready").value) == 1
        assert sim.signal("en").value.mask != 0, "input port default_value must not be applied"

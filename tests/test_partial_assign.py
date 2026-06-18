"""Tests for partial net continuous assignments.

Verifies that multiple `assign` statements targeting different bit-ranges of the
same net produce the correct result across all simulator engines. This was broken
because _build_net_lvalue() did not extract msb_constant_expression /
lsb_constant_expression from the parse tree, collapsing range-select LHS to
plain identifiers.
"""

import shutil

import pytest

from veriforge.model.design import Design
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines():
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        try:
            import Cython  # noqa: F401

            engines.append("compiled")
        except ImportError:
            pass
    return engines


ENGINES = _engines()


def _parse_module(source: str):
    vp = verilog_parser(start="module_declaration")
    tree = vp.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    assert isinstance(design, Design)
    return design.modules[0]


class TestPartialNetAssign:
    """Verify multiple assign statements to different bit-ranges of a net."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_four_byte_lanes(self, engine):
        """assign out[7:0]=a; assign out[15:8]=b; etc. builds correct 32-bit value."""
        mod = _parse_module(r"""
        module test;
          reg [7:0] a, b, c, d;
          wire [31:0] out;

          assign out[7:0]   = a;
          assign out[15:8]  = b;
          assign out[23:16] = c;
          assign out[31:24] = d;

          initial begin
            a = 8'hAA;
            b = 8'hBB;
            c = 8'hCC;
            d = 8'hDD;
            #1;
            $finish;
          end
        endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=10)
        assert sim.read("out").val == 0xDDCCBBAA

    @pytest.mark.parametrize("engine", ENGINES)
    def test_partial_assign_reacts_to_input_change(self, engine):
        """Partial assigns update when their RHS inputs change."""
        mod = _parse_module(r"""
        module test;
          reg [7:0] lo, hi;
          wire [15:0] out;

          assign out[7:0]  = lo;
          assign out[15:8] = hi;

          initial begin
            lo = 8'h11;
            hi = 8'h22;
            #1;
            lo = 8'hFF;
            #1;
            $finish;
          end
        endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=10)
        # After the second assignment: lo=0xFF, hi=0x22
        assert sim.read("out").val == 0x22FF

    @pytest.mark.parametrize("engine", ENGINES)
    def test_partial_assign_with_expressions(self, engine):
        """Partial assigns with non-trivial RHS expressions (ternary, etc.)."""
        mod = _parse_module(r"""
        module test;
          reg sel;
          reg [7:0] a, b;
          wire [15:0] out;

          assign out[7:0]  = sel ? a : b;
          assign out[15:8] = sel ? b : a;

          initial begin
            sel = 0;
            a = 8'hAA;
            b = 8'hBB;
            #1;
            $finish;
          end
        endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=10)
        # sel=0: out[7:0]=b=0xBB, out[15:8]=a=0xAA
        assert sim.read("out").val == 0xAABB

    @pytest.mark.parametrize("engine", ENGINES)
    def test_single_bit_assign(self, engine):
        """assign out[0] = a; assign out[1] = b; (BitSelect LHS)."""
        mod = _parse_module(r"""
        module test;
          reg a, b;
          wire [1:0] out;

          assign out[0] = a;
          assign out[1] = b;

          initial begin
            a = 1;
            b = 1;
            #1;
            $finish;
          end
        endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=10)
        assert sim.read("out").val == 3

    @pytest.mark.parametrize("engine", ENGINES)
    def test_lhs_range_parsed_correctly(self, engine):
        """The model builder creates RangeSelect (not Identifier) for assign out[7:0]."""
        from veriforge.model.expressions import RangeSelect

        mod = _parse_module(r"""
        module test;
          wire [31:0] w;
          reg [7:0] r;
          assign w[15:8] = r;
          initial begin r = 0; #1; $finish; end
        endmodule
        """)
        assert len(mod.continuous_assigns) >= 1
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.lhs, RangeSelect), f"Expected RangeSelect, got {type(ca.lhs).__name__}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

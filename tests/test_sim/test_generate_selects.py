"""Regression tests for selects in constant generate conditions."""

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.model.expressions import BitSelect, Identifier, Literal, PartSelect, RangeSelect
from veriforge.sim.elaborate import _eval_const_expr
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


@pytest.mark.parametrize(
    ("select", "node_type"),
    [("[0]", BitSelect), ("[2:1]", RangeSelect), ("[1 +: 2]", PartSelect), ("[2 -: 2]", PartSelect)],
)
def test_constant_primary_preserves_select(parser, select, node_type):
    source = f"""module top;
  localparam P = 6;
  if (P{select}) begin : yes wire x; end
endmodule"""
    module = tree_to_design(parser.build_tree(source)).modules[0]
    condition = module.generate_blocks[0].condition
    assert isinstance(condition, node_type)
    assert isinstance(condition.target, Identifier)
    assert condition.target.name == "P"
    assert _eval_const_expr(condition, {"P": 6}) == (0 if select == "[0]" else 3)
    emitted = emit_module(module)
    reparsed = tree_to_design(parser.build_tree(emitted)).modules[0]
    assert isinstance(reparsed.generate_blocks[0].condition, node_type)
    assert _eval_const_expr(reparsed.generate_blocks[0].condition, {"P": 6}) == (0 if select == "[0]" else 3)


@pytest.mark.parametrize("target", [Literal(2), Identifier("i")])
def test_constant_bit_select_evaluation(target):
    env = {"i": 2, "BIT": 0}
    assert _eval_const_expr(BitSelect(target, Identifier("BIT")), env) == 0
    assert _eval_const_expr(BitSelect(target, Literal(1)), env) == 1


@pytest.mark.parametrize("engine", ENGINES)
def test_generate_if_selects_genvar_bits(parser, engine):
    source = """module top(output [7:0] low_bit, high_bit, inverted, ranged, indexed);
  localparam BIT = 0;
  for (genvar i = 0; i < 8; i = i + 1) begin : g
    if (i[0]) begin : low_true
      assign low_bit[i] = 1'b1;
    end else begin : low_false
      assign low_bit[i] = 1'b0;
    end
    if (i[1]) begin : high_true
      assign high_bit[i] = 1'b1;
    end else begin : high_false
      assign high_bit[i] = 1'b0;
    end
    if (i[BIT + 1] == 1'b0) begin : inv_true
      assign inverted[i] = 1'b1;
    end else begin : inv_false
      assign inverted[i] = 1'b0;
    end
    if (i[1:0] == 2) begin : range_true
      assign ranged[i] = 1'b1;
    end else begin : range_false
      assign ranged[i] = 1'b0;
    end
    if (i[BIT +: 2] == 2) begin : index_true
      assign indexed[i] = 1'b1;
    end else begin : index_false
      assign indexed[i] = 1'b0;
    end
  end
endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.settle()
    expected = {"low_bit": 0xAA, "high_bit": 0xCC, "inverted": 0x33, "ranged": 0x44, "indexed": 0x44}
    for name, value in expected.items():
        result = sim.read(name)
        assert result.is_defined, (name, result)
        assert int(result) == value, (name, result)


@pytest.mark.parametrize("width", [65, 128, 257])
@pytest.mark.parametrize("engine", ENGINES)
def test_generate_if_selects_wide_values(parser, engine, width):
    mask = sum(1 << bit for bit in range(0, width, 3))
    mask = (mask | (1 << (width - 1))) & ~(1 << (width - 2))
    source = f"""module top(input [{width - 1}:0] data,
  output [{width - 1}:0] by_genvar, by_mask);
  localparam [{width - 1}:0] MASK = {width}'h{mask:x};
  for (genvar i = 0; i < {width}; i = i + 1) begin : g
    if (i[0]) begin : odd
      assign by_genvar[i] = ~data[i];
    end else begin : even
      assign by_genvar[i] = data[i];
    end
    if (MASK[i]) begin : set_bit
      assign by_mask[i] = ~data[i];
    end else begin : clear_bit
      assign by_mask[i] = data[i];
    end
  end
endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    odd_mask = sum(1 << bit for bit in range(1, width, 2))
    for data in (0, (1 << width) - 1, (1 << (width - 1)) | (1 << 64) | 0xFEDCBA9876543210):
        sim.drive("data", Value(data, width=width))
        sim.settle()
        for name, expected in (("by_genvar", data ^ odd_mask), ("by_mask", data ^ mask)):
            result = sim.read(name)
            assert result.is_defined, (name, result)
            assert result.width == width
            assert int(result) == expected, (name, result)

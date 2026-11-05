"""Packed dimensions keep their shape through nested procedural selects."""

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("assignment", ["=", "<="])
def test_nested_loop_packed_3d_assignment(parser, engine, width, assignment):
    source = f"""module top(input clk, input [{width - 1}:0] d,
                           output [{8 * width - 1}:0] y);
      logic [1:0][3:0][{width - 1}:0] s7_corr;
      integer g, k;
      always @(posedge clk) begin
        for (g=0; g<2; g=g+1)
          for (k=0; k<4; k=k+1)
            s7_corr[g][k] {assignment} d ^ (g*4+k);
      end
      assign y = s7_corr;
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    sim.drive("clk", 0)
    sim.drive("d", 0)
    sim.settle()
    for data, mask in ((5, 0), (1 << (width - 1), 0), (0, 1 << (width - 1)), (9, 0)):
        sim.drive("d", Value(data, mask=mask, width=width))
        sim.drive("clk", 1)
        sim.settle()
        expected = sum((data ^ lane) << (lane * width) for lane in range(8))
        expected_mask = sum(mask << (lane * width) for lane in range(8))
        assert sim.read("y") == Value(expected, mask=expected_mask, width=8 * width)
        sim.drive("clk", 0)
        sim.settle()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
def test_packed_dynamic_selects_and_invalid_indices(parser, engine, width):
    source = f"""module top(input clk, load,
      input signed [31:0] g, k, b,
      input [{width - 1}:0] d, input [{8 * width - 1}:0] seed,
      output [{8 * width - 1}:0] y, output [{4 * width - 1}:0] row,
      output [{8 * width - 1}:0] all_rows,
      output [{width - 1}:0] lane, output bit_out,
      output [3:0] slice_out, output [{2 * width - 1}:0] pair_out);
      logic [1:0][3:0][{width - 1}:0] a;
      always @(posedge clk)
        if (load) a <= seed; else a[g][k] <= d;
      assign y = a;
      assign row = a[g];
      assign all_rows = a[1:0];
      assign lane = a[g][k];
      assign bit_out = a[g][k][b];
      assign slice_out = a[g][k][{width - 2} -: 4];
      assign pair_out = a[g][k +: 2];
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    expected = sum(((1 << (width - 1)) | i) << (i * width) for i in range(8))
    for name, value in (("clk", 0), ("load", 1), ("g", 0), ("k", 0), ("b", width - 1), ("seed", expected)):
        sim.drive(name, value)
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    sim.drive("load", 0)
    for g, k in ((0, 2), (1, 3), (0, 4), (1, -1), (2, 1), (0, Value.x(32)), (Value.x(32), 0)):
        sim.drive("clk", 0)
        sim.drive("g", g)
        sim.drive("k", k)
        sim.drive("d", Value(0xA5, width=width))
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        valid = isinstance(g, int) and isinstance(k, int) and 0 <= g < 2 and 0 <= k < 4
        if valid:
            shift = (g * 4 + k) * width
            expected = (expected & ~(((1 << width) - 1) << shift)) | (0xA5 << shift)
        assert sim.read("y") == Value(expected, width=8 * width)
        assert sim.read("all_rows") == Value(expected, width=8 * width)
        if isinstance(g, int) and 0 <= g < 2:
            assert sim.read("row") == Value(expected >> (g * 4 * width), width=4 * width)
        else:
            assert sim.read("row") == Value.x(4 * width)
        if valid:
            lane = (expected >> ((g * 4 + k) * width)) & ((1 << width) - 1)
            assert sim.read("lane") == Value(lane, width=width)
            assert sim.read("bit_out") == Value(lane >> (width - 1), width=1)
            assert sim.read("slice_out") == Value(lane >> (width - 5), width=4)
        else:
            assert sim.read("lane") == Value.x(width)
            assert sim.read("bit_out") == Value.x(1)
            assert sim.read("slice_out") == Value.x(4)
        parts = []
        for offset in (1, 0):
            if isinstance(g, int) and isinstance(k, int) and 0 <= g < 2 and 0 <= k + offset < 4:
                parts.append(Value(expected >> ((g * 4 + k + offset) * width), width=width))
            else:
                parts.append(Value.x(width))
        assert sim.read("pair_out") == parts[0].concat(parts[1]), (g, k, sim.read("pair_out"), parts)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("selector", ["[g][k][b]", "[g][k][5:2]", "[g][k][b +: 4]", "[g][k +: 2]"])
def test_packed_partial_writes(parser, engine, width, selector):
    source = f"""module top(input clk, load, input signed [31:0] g, k, b,
      input [{8 * width - 1}:0] seed, input [{2 * width - 1}:0] d, output [{8 * width - 1}:0] y);
      logic [1:0][3:0][{width - 1}:0] a;
      always @(posedge clk)
        if (load) a <= seed; else a{selector} <= d;
      assign y = a;
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    expected = (1 << (8 * width)) - 1
    expected_mask = 0
    data = (1 << (2 * width - 1)) | 0xA5
    mask = (1 << (width - 1)) | 2
    for name, value in (("clk", 0), ("load", 1), ("g", 0), ("k", 0), ("b", 0), ("seed", expected)):
        sim.drive(name, value)
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    sim.drive("load", 0)
    sim.drive("d", Value(data, mask=mask, width=2 * width))
    for g, k, b in ((0, 1, 3), (1, 3, width - 2), (0, 4, 0), (1, -1, 0), (2, 0, 0), (0, 2, -2)):
        sim.drive("clk", 0)
        sim.drive("g", g)
        sim.drive("k", k)
        sim.drive("b", b)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        if selector == "[g][k +: 2]":
            positions = [(k + offset, bit) for offset in range(2) for bit in range(width)]
        elif selector == "[g][k][b]":
            positions = [(k, b)]
        elif selector == "[g][k][5:2]":
            positions = [(k, bit) for bit in range(2, 6)]
        else:
            positions = [(k, b + offset) for offset in range(4)]
        for source_bit, (col, bit) in enumerate(positions):
            if 0 <= g < 2 and 0 <= col < 4 and 0 <= bit < width:
                target = (g * 4 + col) * width + bit
                expected = (expected & ~(1 << target)) | (((data >> source_bit) & 1) << target)
                expected_mask = (expected_mask & ~(1 << target)) | (((mask >> source_bit) & 1) << target)
        assert sim.read("y") == Value(expected, mask=expected_mask, width=8 * width), (g, k, b)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("packed", ["[1:0][3:0][7:0]", "[1:2][5:8][7:0]", "[3:2][7:4][-1:-8]"])
def test_packed_range_direction_and_queries(parser, engine, packed):
    source = f"""module top(input [63:0] d, input integer g, k,
      output [31:0] row, output [7:0] lane, output bit_out,
      output [31:0] size0, size1, size2, left0, right0, bits);
      wire {packed} a;
      assign a = d;
      assign row = a[g];
      assign lane = a[g][k];
      assign bit_out = a[g][k][$left(a, 3)];
      assign size0 = $size(a, 1);
      assign size1 = $size(a, 2);
      assign size2 = $size(a, 3);
      assign left0 = $left(a, 1);
      assign right0 = $right(a, 1);
      assign bits = $bits(a);
    endmodule"""
    bounds = {
        "[1:0][3:0][7:0]": ((1, 0), (3, 0)),
        "[1:2][5:8][7:0]": ((1, 2), (5, 8)),
        "[3:2][7:4][-1:-8]": ((3, 2), (7, 4)),
    }[packed]
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    data = 0x9182736455463728
    sim.drive("d", data)
    for g in range(min(bounds[0]), max(bounds[0]) + 1):
        for k in range(min(bounds[1]), max(bounds[1]) + 1):
            sim.drive("g", g)
            sim.drive("k", k)
            sim.settle()
            row = bounds[0][1] - g if bounds[0][0] < bounds[0][1] else g - bounds[0][1]
            col = bounds[1][1] - k if bounds[1][0] < bounds[1][1] else k - bounds[1][1]
            assert sim.read("row") == Value(data >> (row * 32), width=32)
            assert sim.read("a[" + str(g) + "]") == Value(data >> (row * 32), width=32)
            assert sim.read("lane") == Value(data >> (row * 32 + col * 8), width=8)
            assert sim.read("bit_out") == Value(data >> (row * 32 + col * 8 + 7), width=1)
    for name, value in (
        ("size0", 2),
        ("size1", 4),
        ("size2", 8),
        ("left0", bounds[0][0]),
        ("right0", bounds[0][1]),
        ("bits", 64),
    ):
        assert int(sim.read(name)) == value


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("unpacked", ["[0:1]", "[1:2][3:4]"])
def test_packed_dimensions_inside_unpacked_memory(parser, engine, width, unpacked):
    address = "[1]" if unpacked == "[0:1]" else "[2][3]"
    source = f"""module top(input clk, input [{width - 1}:0] d, output [{8 * width - 1}:0] y,
                           output [{width - 1}:0] lane);
      logic [1:0][3:0][{width - 1}:0] mem {unpacked};
      integer g, k;
      always @(posedge clk)
        for (g=0; g<2; g=g+1)
          for (k=0; k<4; k=k+1)
            mem{address}[g][k] <= d ^ (g*4+k);
      assign y = mem{address};
      assign lane = mem{address}[1][2];
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    sim.drive("clk", 0)
    sim.drive("d", Value(1 << (width - 1), mask=2, width=width))
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    expected = sum(((1 << (width - 1)) ^ i) << (i * width) for i in range(8))
    mask = sum(2 << (i * width) for i in range(8))
    assert sim.read("y") == Value(expected, mask=mask, width=8 * width)
    assert sim.read("lane") == Value((1 << (width - 1)) ^ 6, mask=2, width=width)


@pytest.mark.parametrize("engine", ENGINES)
def test_four_packed_dimensions_through_generated_instances(parser, engine):
    source = """module channel(input clk, input [95:0] d, output logic [1:0][2:0][1:0][95:0] y);
      integer g, k, b;
      always @(posedge clk)
        for (g=0; g<2; g=g+1)
          for (k=0; k<3; k=k+1)
            for (b=0; b<2; b=b+1) y[g][k][b] <= d ^ (g*6+k*2+b);
    endmodule
    module top(input clk, input [95:0] d, output [2303:0] y);
      for (genvar i=0; i<2; i=i+1) begin : channels
        channel u(.clk(clk), .d(d ^ i), .y(y[i*1152 +: 1152]));
      end
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    data = 1 << 95
    sim.drive("clk", 0)
    sim.drive("d", Value(data, width=96))
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    expected = sum(
        (data ^ channel ^ lane) << ((channel * 12 + lane) * 96) for channel in range(2) for lane in range(12)
    )
    assert sim.read("y") == Value(expected, width=2304)


@pytest.mark.parametrize("engine", ENGINES)
def test_packed_input_and_internal_declarations_through_instance(parser, engine):
    source = """module channel(input clk, input [2:1][0:3][95:0] d, output [767:0] y);
      logic [2:1][0:3][95:0] stored;
      wire [2:1][0:3][95:0] copied;
      integer g,k;
      always @(posedge clk)
        for (g=1; g<=2; g=g+1)
          for (k=0; k<4; k=k+1) stored[g][k] <= d[g][k];
      assign copied = stored;
      assign y = copied;
    endmodule
    module top(input clk, input [767:0] d, output [767:0] y);
      channel u(.clk(clk), .d(d), .y(y));
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    for data in (0xA5, 1 << 767):
        value = Value(data, mask=1 << 600, width=768)
        sim.drive("clk", 0)
        sim.drive("d", value)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        assert sim.read("y") == value


@pytest.mark.parametrize("engine", ENGINES)
def test_ascending_2d_packed_array_python_access(parser, engine):
    source = """module top(input [1:2][7:0] d, input integer i,
                           output [15:0] y, output [7:0] row);
      assign y = d;
      assign row = d[i];
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    sim.drive("d", 0xA512)
    for index, expected in ((1, 0xA5), (2, 0x12)):
        sim.drive("i", index)
        sim.settle()
        assert sim.read("row") == Value(expected, width=8)
        assert sim.read(f"d[{index}]") == Value(expected, width=8)
    sim.drive("d[1]", 0x7F)
    sim.settle()
    assert sim.read("y") == Value(0x7F12, width=16)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("inner_dimensions", [1, 4])
def test_signed_packed_selects_are_unsigned(parser, engine, width, inner_dimensions):
    row_width = width * inner_dimensions
    packed = f"[1:0][{width - 1}:0]" if inner_dimensions == 1 else f"[1:0][3:0][{width - 1}:0]"
    lane = "a[0]" if inner_dimensions == 1 else "a[0][0]"
    source = f"""module top(input [{2 * row_width - 1}:0] d,
      output [{2 * row_width - 1}:0] row_unsigned, row_signed,
      output [{2 * width - 1}:0] lane_unsigned, lane_signed);
      wire signed {packed} a;
      assign a = d;
      assign row_unsigned = a[0];
      assign row_signed = $signed(a[0]);
      assign lane_unsigned = {lane};
      assign lane_signed = $signed({lane});
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    sim.drive("d", (1 << (2 * row_width)) - 1)
    sim.settle()
    assert sim.read("row_unsigned") == Value((1 << row_width) - 1, width=2 * row_width)
    assert sim.read("row_signed") == Value((1 << (2 * row_width)) - 1, width=2 * row_width)
    assert sim.read("lane_unsigned") == Value((1 << width) - 1, width=2 * width)
    assert sim.read("lane_signed") == Value((1 << (2 * width)) - 1, width=2 * width)

"""Declared loop and block locals retain type and lexical scope in subprograms."""

from pathlib import Path

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


def _sim(parser, source, engine):
    design = tree_to_design(parser.build_tree(source))
    return Simulator(design.modules[0], engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_reported_function_loop_bit_select(parser, engine):
    source = (Path(__file__).parent / "verilog" / "func_loop_bitsel.sv").read_text()
    sim = _sim(parser, source, engine)
    sim.drive("clk", 0)
    sim.drive("idx", 0)
    sim.run(max_time=0)
    results = []
    for idx in (0, 1, 2, 3, 5, 8):
        sim.drive("idx", idx)
        sim.settle()
        results.append(int(sim.read("r1")))
    assert results == [0, 1, 2, 2, 3, 4]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [16, 96])
@pytest.mark.parametrize("declaration", ["int", "int unsigned", "integer", "predeclared"])
def test_function_loop_indices_keep_integer_width(parser, engine, width, declaration):
    local = "integer i;" if declaration == "predeclared" else ""
    init = "i=0" if declaration == "predeclared" else f"{declaration} i=0"
    source = f"""module top(input [{width - 1}:0] d, output [31:0] y);
      function automatic [31:0] bitlen(input [{width - 1}:0] n);
        {local}
        begin
          bitlen=0;
          for ({init}; i<{width}; i=i+1)
            if (n[i]) bitlen=i+1;
        end
      endfunction
      assign y=bitlen(d);
    endmodule"""
    sim = _sim(parser, source, engine)
    for data in (0, 1, 2, 3, 5, 8, 1 << (width - 1), (1 << (width - 1)) | 5, 0):
        sim.drive("d", Value(data, width=width))
        sim.settle()
        assert sim.read("y") == Value(data.bit_length(), width=32)
    # An X test does not take the if arm; a higher known set bit still wins.
    for data, mask in ((1, 1 << (width - 1)), (1 << (width - 1), 2)):
        sim.drive("d", Value(data, mask=mask, width=width))
        sim.settle()
        assert sim.read("y") == Value(data.bit_length(), width=32)


@pytest.mark.parametrize("engine", ENGINES)
def test_function_loop_redeclarations_shadow_ports_and_iterators(parser, engine):
    source = """module top(input [7:0] d, output [15:0] y);
      function automatic [15:0] f(input [7:0] i);
        begin
          f=i;
          for (int i=0; i<3; i=i+1) begin
            f=f+i;
            for (int i=0; i<2; i=i+1) f=f+i+1;
            f=f+i;
          end
          f=f+i;
        end
      endfunction
      assign y=f(d);
    endmodule"""
    sim = _sim(parser, source, engine)
    for data in (0, 3, 128, 9):
        sim.drive("d", data)
        sim.settle()
        assert sim.read("y") == Value(2 * data + 15, width=16)


@pytest.mark.parametrize("engine", ENGINES)
def test_function_loop_wide_part_selects_and_block_local(parser, engine):
    source = """module top(input [95:0] d, output [95:0] y);
      function automatic [95:0] f(input [95:0] n);
        begin
          f=0;
          for (int i=0; i<4; i=i+1) begin
            logic [23:0] part;
            part=n[i*24 +: 24];
            f[(3-i)*24 +: 24]=part;
          end
        end
      endfunction
      assign y=f(d);
    endmodule"""
    sim = _sim(parser, source, engine)
    for data, mask in ((0x123456_789ABC_DEF012_345678, 0), (1 << 95, 1 << 70), (0, 1 << 90)):
        sim.drive("d", Value(data, mask=mask, width=96))
        sim.settle()
        expected = sum(((data >> (i * 24)) & 0xFFFFFF) << ((3 - i) * 24) for i in range(4))
        expected_mask = sum(((mask >> (i * 24)) & 0xFFFFFF) << ((3 - i) * 24) for i in range(4))
        assert sim.read("y") == Value(expected, mask=expected_mask, width=96)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kind", ["task", "process"])
def test_loop_local_indices_in_other_procedural_contexts(parser, engine, kind):
    loop = "for (int i=0; i<16; i=i+1) if (n[i]) r=i+1;"
    if kind == "task":
        body = f"""task automatic bitlen(input [15:0] n, output [4:0] r);
          begin r=0; {loop} end
        endtask
        always_comb begin bitlen(d,y); end"""
    else:
        body = f"""wire [15:0] n=d; reg [4:0] r;
        always_comb begin r=0; {loop} end
        assign y=r;"""
    source = f"module top(input [15:0] d, output logic [4:0] y); {body} endmodule"
    sim = _sim(parser, source, engine)
    for data in (0, 1, 2, 8, 1 << 15, 5):
        sim.drive("d", data)
        sim.settle()
        assert sim.read("y") == Value(data.bit_length(), width=5)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
def test_function_loop_bit_writes_keep_full_index(parser, engine, width):
    source = f"""module top(input [{width - 1}:0] d, output [{width - 1}:0] y);
      function automatic [{width - 1}:0] reverse_bits(input [{width - 1}:0] n);
        for (int i=0; i<{width}; i=i+1) reverse_bits[i]=n[{width - 1}-i];
      endfunction
      assign y=reverse_bits(d);
    endmodule"""
    sim = _sim(parser, source, engine)
    for data, mask in ((1, 0), ((1 << (width - 1)) | 5, 0), (0, 1 << (width - 2))):
        sim.drive("d", Value(data, mask=mask, width=width))
        sim.settle()
        expected = int(f"{data:0{width}b}"[::-1], 2)
        expected_mask = int(f"{mask:0{width}b}"[::-1], 2)
        assert sim.read("y") == Value(expected, mask=expected_mask, width=width)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("unsigned", [False, True])
def test_function_loop_iterator_signedness(parser, engine, unsigned):
    declaration = "int unsigned" if unsigned else "int"
    source = f"""module top(input [7:0] d, output [7:0] y);
      function automatic [7:0] f(input [7:0] n);
        begin
          f=n;
          for ({declaration} i=0; i<4; i=i+1)
            if (-i < 0) f=f+1;
        end
      endfunction
      assign y=f(d);
    endmodule"""
    sim = _sim(parser, source, engine)
    sim.drive("d", 5)
    sim.settle()
    assert sim.read("y") == Value(5 if unsigned else 8, width=8)

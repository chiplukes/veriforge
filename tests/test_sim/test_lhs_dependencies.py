"""Icarus-checked target changes and index-only combinational dependencies."""

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


def _sim(parser, source, engine):
    design = tree_to_design(parser.build_tree(source))
    return Simulator(design.modules[0], design=design, engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("select", ["range", "+:", "-:"])
def test_continuous_memory_slice_wakes_fanout(parser, engine, width, select):
    target = (
        f"mem[0][{width + 7}:8]"
        if select == "range"
        else f"mem[0][{8 if select == '+:' else width + 7} {select} {width}]"
    )
    source = f"""module top(input [{width - 1}:0] d, output [{width - 1}:0] y);
      wire [{width + 7}:8] mem [0:0];
      assign y = mem[0][{width + 7}:8];
      assign {target} = d;
    endmodule"""
    sim = _sim(parser, source, engine)
    for data in (
        Value(0, width=width),
        Value(1 << (width - 1), width=width),
        Value(0, mask=1 << (width - 1), width=width),
        Value(0, width=width),
    ):
        sim.drive("d", data)
        sim.settle()
        assert sim.read("y") == data


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("direction", ["+:", "-:"])
def test_indexed_lhs_only_changes_wake_always_star(parser, engine, width, direction):
    source = f"""module top(input [7:0] idx, output reg [{width - 1}:0] q);
      initial q = 0;
      always @* q[idx {direction} 3] = 3'b101;
    endmodule"""
    sim = _sim(parser, source, engine)
    # Finish initial writes before the external stimulus, avoiding a t=0
    # race between initialization and the combinational partial write.
    sim.run(max_time=0)
    sim.drive("idx", 2)
    expected = 0
    for step, index in enumerate((2, width - 2, 0, width + 2)):
        sim.drive("idx", index)
        sim.run(max_time=step + 1)
        low = index if direction == "+:" else index - 2
        for bit in range(3):
            if 0 <= low + bit < width:
                expected = (expected & ~(1 << (low + bit))) | (((5 >> bit) & 1) << (low + bit))
        assert sim.read("q") == Value(expected, width=width)
    sim.drive("idx", Value(0, mask=1, width=8))
    sim.settle()
    assert sim.read("q") == Value(expected, width=width)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("form", ["bit", "range", "concat"])
def test_nested_lhs_indices_wake_always_star(parser, engine, width, form):
    lhs = {
        "bit": "mem[addr][bitidx]",
        "range": f"mem[addr][{width - 1}:0]",
        "concat": "{mem[addr][bitidx], flag[bitidx +: 1]}",
    }[form]
    rhs = f"{width}'d5" if form == "range" else "2'b11" if form == "concat" else "1'b1"
    source = f"""module top(input addr, input [7:0] bitidx,
      output [{2 * width - 1}:0] y, output reg [{width - 1}:0] flag);
      reg [{width - 1}:0] mem [0:1];
      initial begin mem[0] = 0; mem[1] = 0; flag = 0; end
      always @* {lhs} = {rhs};
      assign y = {{mem[1], mem[0]}};
    endmodule"""
    sim = _sim(parser, source, engine)
    sim.run(max_time=0)
    sim.drive("addr", 0)
    sim.drive("bitidx", 0)
    words, flag = [0, 0], 0
    for step, (addr, bitidx) in enumerate(((0, 0), (1, 0), (1, width - 1), (0, width - 1))):
        sim.drive("addr", addr)
        sim.drive("bitidx", bitidx)
        if step % 2:
            sim.settle()
        else:
            sim.run(max_time=step + 1)
        words[addr] = 5 if form == "range" else words[addr] | (1 << bitidx)
        if form == "concat":
            flag |= 1 << bitidx
            assert sim.read("flag") == Value(flag, width=width)
        assert sim.read("y") == Value(words[0] | (words[1] << width), width=2 * width)

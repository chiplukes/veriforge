"""Memories not starting at address 0, multi-dimensional unpacked arrays, and
signed values in timing-control blocks (expected output from Icarus).

* Every engine used a word index as the slot number: in `reg m [1:4]` the
  word `m[4]` read x (reference/vm/vm-fast), and compiled's native code and
  its reference-executor fallback disagreed, reading every word one off.
* `reg m [0:1][0:2]`: `m[i][j]` read bit j of word i, on every engine.
* vm/vm-fast/compiled run blocks with delays through the reference executor,
  whose context had no signedness: `integer k = -2; if (k < 0)` was false
  there, so `for (k = -2; ...)` never ran.
"""

from __future__ import annotations

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_MEMORIES = """module t;
  reg [7:0] a [1:4];
  reg [7:0] d [7:4];
  reg [7:0] m2 [1:2][3:5];
  reg [0:7] e [2:3];
  reg [15:8] f [-2:1];
  reg [7:0] z [0:1][0:2];
  reg [2:0] i;
  integer k, j;
  initial begin
    for (k = 1; k <= 4; k = k + 1) a[k] = k * 10;
    for (k = 4; k <= 7; k = k + 1) d[k] = k * 11;
    for (k = 1; k <= 2; k = k + 1) for (j = 3; j <= 5; j = j + 1) m2[k][j] = k * 16 + j;
    for (k = 0; k <= 1; k = k + 1) for (j = 0; j <= 2; j = j + 1) z[k][j] = k * 16 + j;
    e[2] = 8'b1000_0001; e[3] = 8'b0100_0010;
    for (k = -2; k <= 1; k = k + 1) f[k] = k + 100;
    #1;
    for (k = 0; k < 8; k = k + 1) begin
      i = k;
      #1 $display("%0d: a=%h d=%h m=%h e=%b%b f=%h z=%h", k, a[i], d[i], m2[i[0] + 1][i[1:0] + 3], e[i][0], e[i][1],
                  f[$signed(i[1:0])], z[i[0]][i[1:0]]);
    end
    a[i - 3] = 8'hee; d[i] = 8'hdd; f[-2] = 8'hff; z[1][3] = 8'h99;
    #1 $display("w: %h %h %h %h %h %h %h", a[4], d[7], f[-2], m2[2][5], e[3][1:2], z[1][2], z[1][3]);
  end
endmodule
"""

_MEMORIES_EXPECTED = [
    "0: a=xx d=xx m=13 e=xx f=64 z=00",
    "1: a=0a d=xx m=24 e=xx f=65 z=11",
    "2: a=14 d=xx m=15 e=10 f=62 z=02",
    "3: a=1e d=xx m=xx e=01 f=63 z=xx",
    "4: a=28 d=2c m=13 e=xx f=64 z=00",
    "5: a=xx d=37 m=24 e=xx f=65 z=11",
    "6: a=xx d=42 m=15 e=xx f=62 z=02",
    "7: a=xx d=4d m=xx e=xx f=63 z=xx",
    # (`z[1][3]` is out of range: the write is dropped, the read is x -- it
    # must not alias the next row.)
    "w: ee dd ff 25 2 12 xx",
]

_SIGNED = """module t;
  integer k, n, m;
  initial begin
    n = 0; m = 0;
    for (k = -2; k <= 1; k = k + 1) n = n + 1;
    k = -2;
    if (k < 0) m = 1;
    #1 $display("%0d %0d %0d", n, m, k);
  end
endmodule
"""


def _display(source: str, engine: str) -> list[str]:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=100)
    return sim.display_output


@pytest.mark.parametrize("engine", ENGINES)
def test_memory_bases_and_multidim(engine):
    assert _display(_MEMORIES, engine) == _MEMORIES_EXPECTED


@pytest.mark.parametrize("engine", ENGINES)
def test_signed_values_in_timing_blocks(engine):
    assert _display(_SIGNED, engine) == ["4 1 -2"]


@pytest.mark.parametrize("engine", ENGINES)
def test_dynamic_index_into_based_memory(engine):
    # Combinational read/write, no timing controls: the natively compiled paths.
    source = """module t(input [2:0] i, output [7:0] o, output [7:0] w);
  reg [7:0] m [1:4];
  reg [7:0] n [1:4];
  always @* begin m[1] = 11; m[2] = 22; m[3] = 33; m[4] = 44; n[i] = 77; end
  assign o = m[i];
  assign w = n[4];
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    got = []
    for value in (1, 2, 3, 4, 5):
        sim.drive("i", value)
        sim.settle()
        got.append((str(sim.read("o")), str(sim.read("w"))))
    assert got == [
        ("8'b00001011", "8'bxxxxxxxx"),
        ("8'b00010110", "8'bxxxxxxxx"),
        ("8'b00100001", "8'bxxxxxxxx"),
        ("8'b00101100", "8'b01001101"),
        ("8'bxxxxxxxx", "8'b01001101"),
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_python_api_uses_declared_addresses(engine):
    # sim.read/drive("m[4]") of `reg m [1:4]` means address 4, also through
    # hierarchy and for `m[i][j]` of a flattened array.
    source = """module sub(input [1:0] i, output [7:0] o);
  reg [7:0] m [1:4];
  reg [7:0] z [1:2][0:2];
  initial begin m[1] = 8'h11; m[2] = 8'h22; m[3] = 8'h33; m[4] = 8'h44; z[2][1] = 8'h21; end
  assign o = m[i + 1];
endmodule
module top(input [1:0] i, output [7:0] o, output [7:0] h);
  sub u(.i(i), .o(o));
  assign h = u.m[4];
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    link_instances(design)
    resolve_port_connections(design)
    top = next(m for m in design.modules if m.name == "top")
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=2)
    sim.drive("i", 3)
    sim.settle()
    assert int(sim.read("o")) == int(sim.read("h")) == 0x44
    assert int(sim.read("u.m[4]")) == 0x44
    assert int(sim.read("u.m[1]")) == 0x11
    assert int(sim.read("u.z[2][1]")) == 0x21
    assert sim.read("u.z[0][1]").is_x  # (address 0 is outside [1:2])


@pytest.mark.parametrize("engine", ENGINES)
def test_vcd_labels_memory_elements_by_declared_address(engine):
    import io  # noqa: PLC0415

    from veriforge.sim.trace import attach_vcd  # noqa: PLC0415

    source = """module t(input clk);
  reg [7:0] m [1:3];
  reg [7:0] z [0:1][1:2];
  initial begin m[3] = 8'h33; z[1][2] = 8'h12; end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    buf = io.StringIO()
    attach_vcd(sim, buf, memories=True)
    sim.run(max_time=2)
    text = buf.getvalue()
    ids = {line.split()[4]: line.split()[3] for line in text.splitlines() if line.startswith("$var")}
    assert sorted(ids) == ["clk", "m[1]", "m[2]", "m[3]", "z[0][1]", "z[0][2]", "z[1][1]", "z[1][2]"]
    assert f"b00110011 {ids['m[3]']}" in text
    assert f"b00010010 {ids['z[1][2]']}" in text


def test_sub_array_access_raises():
    # A sub-array (`m[1]` of `m [0:1][0:2]`) used to run, reading bit j of
    # word i everywhere. Assigning one is supported now
    # (test_sub_array_assign.py); comparing them isn't.
    source = """module t;
  reg [7:0] m [0:1][0:2]; reg e;
  initial begin m[1][2] = 8'h12; e = m[0] == m[1]; end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    with pytest.raises(NotImplementedError, match="accessed as a sub-array"):
        Simulator(design.modules[0], engine="reference", design=design)


@pytest.mark.parametrize("engine", ENGINES)
def test_bit_write_into_multidim_element(engine):
    # `z[1][2][3] <= 1` was flattened twice -- the inner `z[1][2]` to `z[5]`,
    # then `z[5][3]` again as a 2-D access, out of range -- so it wrote nothing.
    source = """module t;
  reg [7:0] mem0 [1:-3] [3:5];
  reg [7:0] z [0:1][0:2];
  reg clk;
  wire [7:0] o6 = mem0[1][5];
  wire [7:0] o7 = z[1][2];
  always @(posedge clk) begin
    mem0[1][5][3] <= 4294967295;
    z[1][2][3] <= 1'b1;
  end
  initial begin clk = 0; #1 clk = 1; #1 $display("%b %b", o6, o7); end
endmodule
"""
    assert _display(source, engine) == ["xxxx1xxx xxxx1xxx"]

"""Cross-engine regressions: wide concat-LHS / range-select writes, X/Z
preservation in narrow partial writes, and x/z literal extension.

Every case was wrong in at least one engine before (all found 2026-10-06
while adding per-element memory snapshot journals):

- compiled evaluated a concat-LHS RHS wider than 64 bits as one 64-bit value
  (``{hi, lo} = x + 1``, ``w[i] <= {w[i][119:0], d}``), dropping everything
  above bit 63; a range write into a wide signal with a computed RHS
  (``rz[127:12] <= x[115:0] + 1``) went to narrow storage a wide signal never
  reads; wide slices ignored a nonzero declared LSB (``reg [135:8]``);
- compiled seq bodies read a wide signal's pre-edge snapshot after a
  blocking partial write to it in the same body (``rz[127:12] = ...; wq <=
  rz;``, ``w[a] = {...}`` on a wide memory);
- compiled narrow bit/range writes cleared the destination's whole X/Z mask
  (``r[0] <= 1`` on an all-x ``r`` gave ``00000001``);
- every engine dropped the x/z-extension of a literal's leftmost x/z digit
  (``8'bx`` read as ``0000000x``), and compiled made any x literal x across
  the whole target (``8'b1x`` into 12 bits gave 12 x bits).
"""

from __future__ import annotations

import random

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_NON_REFERENCE = [e for e in ENGINES if e != "reference"]


def _design(source: str):
    tree = verilog_parser(start="source_text").build_tree(source)
    # A source_file that doesn't exist on disk: the parser must not need it
    # (the part-select direction used to be recovered by re-reading it).
    return tree_to_design(tree, source_file="no_such_file.v")


_SEQ_TEMPLATE = """
module t(input clk, input rst, input [1:0] a, input [7:0] d, input [127:0] x,
         output reg [127:0] wq, output reg [63:0] hi, output reg [63:0] lo,
         output reg [55:0] h2, output reg [71:0] l2);
  reg [127:0] w [0:3];
  reg [7:0] m8 [0:3];
  reg [127:0] rz;
  reg [135:8] rb;
  wire [135:8] xb = x;
  integer i;
  always @(posedge clk) begin
    if (rst) begin
      for (i = 0; i < 4; i = i + 1) begin
        w[i] <= (i + 1) * 128'h0123456789ABCDEF;
        m8[i] <= i * 8'h35;
      end
    end else begin
      wq <= w[a];
      STMT
    end
  end
endmodule
"""

_SEQ_CASES = {
    # wide-memory whole-element concat writes (self-referencing RHS)
    "mem_nba_self_concat": "w[a] <= {w[a][119:0], d};",
    "mem_blk_self_concat": "w[a] = {w[a][119:0], d};",
    "mem_nba_concat_or": "w[a] <= {w[a][119:0], 8'd0} | d;",
    "mem_nba_other_concat": "w[a] <= {w[a ^ 2'd1][119:0], d};",
    # concat LHS with a >64-bit computed RHS
    "sig_concat_lhs_mem": "{hi, lo} <= {w[a][119:0], d};",
    "sig_concat_lhs_add": "{hi, lo} <= x + {w[a][63:0], 64'd0};",
    "sig_concat_lhs_uneven": "{h2, l2} <= x ^ {w[a][119:0], d};",
    "sig_concat_lhs_blk": "{h2, l2} = x + 128'd1;",
    # nonzero declared LSB
    "base_wide_slice": "wq <= xb[135:16] + 128'd1;",
    "base_wide_slice2": "wq <= {xb[100:30], d} ^ x;",
    "base_concat_lhs": "{hi, lo} <= {xb[135:16], d};",
    "base_lhs_range": "begin rb[135:20] <= x[115:0] + 1; wq <= rb; end",
    "base_lhs_range_lo": "begin rb[71:8] <= x[63:0]; rb[135:72] <= ~x[63:0]; wq <= rb; end",
    # range writes into a wide signal
    "zero_lhs_range": "begin rz[127:12] <= x[115:0] + 1; wq <= rz; end",
    "zero_lhs_range_blk": "begin rz[127:12] = x[115:0] + 1; wq <= rz; end",
    "dyn_lhs_range": "begin rz[d[3:0] + 100 -: 90] <= x[89:0] ^ {x[20:0], x[127:59]}; wq <= rz; end",
    "dyn_lhs_range_blk": "begin rz[d[2:0] * 4 +: 70] = ~x[69:0]; wq <= rz; end",
    # reads after a blocking partial write in the same body
    "blk_whole_then_slice": "begin rz = x + 1; lo <= rz[100:40]; end",
    "blk_range_then_slice": "begin rz[127:12] = x[115:0]; lo <= rz[100:40]; end",
    "blk_bit_then_whole": "begin rz = x; rz[100] = ~rz[100]; wq <= rz; end",
    "blk_concat_then_whole": "begin {rz, lo} = {x, d, 56'd3}; wq <= rz; hi <= lo; end",
    # memory NBAs to one element apply in program order (last wins), and a
    # partial memory NBA isn't visible until the NBA phase
    "nba_range_then_whole": "begin m8[a][3:0] <= d[3:0]; m8[a] <= x[7:0]; lo <= m8[a]; end",
    "nba_whole_then_range": "begin m8[a] <= x[7:0]; m8[a][3:0] <= d[3:0]; lo <= m8[a]; end",
    "nba_range_reads_old": "begin m8[a][3:0] <= d[3:0]; lo <= m8[a]; end",
    "nba_wide_range_then_whole": "begin w[a][70:60] <= d; w[a] <= x; hi <= w[a][127:64]; end",
    "nba_wide_whole_then_range": "begin w[a] <= x; w[a][70:60] <= d; hi <= w[a][127:64]; end",
    # narrow partial writes keep / write X
    "narrow_bit_keeps_x": "begin h2[3] <= d[0]; l2 <= h2; end",
    "narrow_range_keeps_x": "begin h2[11:4] <= d; l2 <= h2; end",
    "narrow_range_x": "begin hi[11:4] <= d[0] ? 8'bx : d; lo <= hi; end",
    "narrow_range_x_blk": "begin h2[11:4] = d[0] ? 8'bx : d; lo <= h2; end",
    "narrow_bit_x": "begin h2[3] <= d[0] ? 1'bx : d[1]; l2 <= h2; end",
    "narrow_dyn_range_x": "begin h2[a*8 +: 8] <= d[0] ? 8'bx : d; l2 <= h2; end",
}

_OUTPUTS = ("wq", "hi", "lo", "h2", "l2")


def _cycle(sim: Simulator) -> None:
    sim.drive("clk", 1)
    sim.settle()
    sim.drive("clk", 0)
    sim.settle()


def _seq_trace(engine: str, stmt: str) -> list[tuple]:
    design = _design(_SEQ_TEMPLATE.replace("STMT", stmt))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    for name, val in (("clk", 0), ("rst", 1), ("a", 0), ("d", 0), ("x", 0)):
        sim.drive(name, val)
    sim.settle()
    _cycle(sim)
    sim.drive("rst", 0)
    rng = random.Random(7)
    out = []
    for _ in range(12):
        sim.drive("a", rng.randrange(4))
        sim.drive("d", rng.randrange(256))
        sim.drive("x", rng.getrandbits(128))
        _cycle(sim)
        out.append(tuple((sim.read(n).val, sim.read(n).mask) for n in _OUTPUTS))
    return out


@pytest.mark.parametrize("engine", _NON_REFERENCE)
@pytest.mark.parametrize("case", list(_SEQ_CASES))
def test_seq_partial_writes_match_reference(case, engine):
    assert _seq_trace(engine, _SEQ_CASES[case]) == _seq_trace("reference", _SEQ_CASES[case])


# Expected values verified against Icarus Verilog (z prints as x here: the
# simulator's 4-state encoding doesn't distinguish them).
_LITERAL_DESIGN = """
module t(input [1:0] s, output [11:0] a, output [11:0] b, output [11:0] c, output [11:0] e,
         output [11:0] f, output [11:0] g, output [11:0] k, output [39:0] m, output [11:0] n,
         output [11:0] p, output [99:0] w, output [11:0] sm, output [39:0] nx,
         output reg [63:0] r, output reg [7:0] r8, output reg [63:0] rq, output reg [99:0] rq2);
  assign a = 8'bx;
  assign b = 8'hx;
  assign c = 8'bz;
  assign e = 8'b1x;
  assign f = 8'bx1;
  assign g = 8'hx5;
  assign k = 8'h5x;
  assign m = 'bx;
  assign n = 6'ox;
  assign p = 8'dx;
  assign w = 'hx;
  assign sm = 'bx;
  assign nx = 'b1x;
  always @* case (s) 2'd0: begin r = 64'd5; r8 = 8'd1; end default: begin r = 'bx; r8 = 'bx; end endcase
  always @* begin rq = (s == 2'd3) ? 64'd1 : 'bx; rq2 = (s == 2'd3) ? 100'd1 : 'bx; end
endmodule
"""

_LITERAL_EXPECTED = {
    "a": "0000xxxxxxxx",
    "b": "0000xxxxxxxx",
    "c": "0000xxxxxxxx",
    "e": "00000000001x",
    "f": "0000xxxxxxx1",
    "g": "0000xxxx0101",
    "k": "00000101xxxx",
    "m": "x" * 40,
    "n": "000000xxxxxx",
    "p": "0000xxxxxxxx",
    "w": "x" * 100,
    "sm": "x" * 12,
    "nx": "0" * 38 + "1x",
    "r": "x" * 64,
    "r8": "x" * 8,
    "rq": "x" * 64,
    "rq2": "x" * 100,
}


def _bits(value) -> str:
    return "".join("x" if (value.mask >> i) & 1 else str((value.val >> i) & 1) for i in reversed(range(value.width)))


@pytest.mark.parametrize("engine", ENGINES)
def test_xz_literal_extension(engine):
    design = _design(_LITERAL_DESIGN)
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("s", 1)
    sim.settle()
    got = {name: _bits(sim.read(name)) for name in _LITERAL_EXPECTED}
    assert got == _LITERAL_EXPECTED


def test_part_select_direction_needs_no_source_file():
    """`-:` vs `+:` comes from the parse tree, not from re-reading the file."""
    from veriforge.model.expressions import PartSelect

    design = _design(
        "module t(input [15:0] x, output [3:0] y, output [3:0] z);"
        " assign y = x[x[1:0] + 7 -: 4]; assign z = x[x[1:0] +: 4]; endmodule"
    )
    selects = [a.rhs for a in design.modules[0].continuous_assigns]
    assert [s.direction for s in selects if isinstance(s, PartSelect)] == ["-:", "+:"]

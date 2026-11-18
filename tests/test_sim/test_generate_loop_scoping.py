"""Generate-loop scopes: each iteration's localparams, and writes to its signals.

- A generate block's localparam was flattened as one plain `Cnt` shared by every
  iteration, so an instance override using it (`.ProvideValUpd(Cnt == 10)`, ibex's
  mhpmcounters) saw the last iteration's value everywhere: wrong on every engine.
- The reference engine dropped range/part-select writes to a signal whose
  flattened name has a `[` (`gen[0].c.q[63:40] = '0`), a guard meant for
  unresolved struct-memory accesses: the port stayed x.

Found by tools/corpus_check.py in ibex_cs_registers (`ibex_counter`'s
`assign counter_val_upd_o[63:CounterWidth] = '0;`).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module ctr #(parameter int CounterWidth = 32, parameter bit ProvideValUpd = 0)
    (input clk, input [99:0] d, output logic [99:0] q, output logic [99:0] p);
  if (CounterWidth < 100) begin : g_part
    if (ProvideValUpd) begin : g_upd
      assign q[CounterWidth-1:0] = d[CounterWidth-1:0];
    end else begin : g_no
      assign q[CounterWidth-1:0] = '0;
    end
    assign q[99:CounterWidth] = '0;
  end else begin : g_full
    assign q = d;
  end
  always @(posedge clk) begin
    p[CounterWidth +: 8] <= d[7:0];
    p[CounterWidth-1:0] <= '1;
  end
  initial p = '0;
endmodule
module t #(parameter int unsigned W = 70) (input clk, input [99:0] d, output [99:0] y0, output [99:0] y1,
    output [99:0] y2, output [99:0] p1, output [99:0] p2, output logic [99:0] z);
  ctr #(.CounterWidth(W)) u0 (.clk(clk), .d(d), .q(y0), .p());
  for (genvar i = 0; i < 2; i++) begin : gen
    localparam int Cnt = i + 9;
    logic [99:0] nxt, pp, loc;
    ctr #(.CounterWidth(W), .ProvideValUpd(Cnt == 10)) c (.clk(clk), .d(d), .q(nxt), .p(pp));
    always_comb begin loc = '0; loc[Cnt +: 4] = 4'hF; end
    if (i == 0) begin : g0 assign y1 = nxt; assign p1 = pp; end
    else begin : g1 assign y2 = nxt; assign p2 = pp; assign z = loc; end
  end
endmodule
"""

_D = 0x1234_5678_9ABC_DEF0_1357_9BDF_0
_W = 70


@pytest.mark.parametrize("engine", ENGINES)
def test_generate_loop_localparams_and_bracketed_writes(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk", Value(0))
    sim.drive("d", Value(_D, width=100))
    sim.run(max_time=0)
    sim.settle()
    low = _D & ((1 << _W) - 1)
    assert sim.read("y0") == Value(0, width=100)  # ProvideValUpd = 0
    assert sim.read("y1") == Value(0, width=100)  # Cnt = 9: ProvideValUpd = 0
    assert sim.read("y2") == Value(low, width=100)  # Cnt = 10: ProvideValUpd = 1
    assert sim.read("z") == Value(0xF << 10, width=100)  # gen[1]'s own Cnt = 10
    sim.drive("clk", Value(1))
    sim.settle()
    expect_p = ((_D & 0xFF) << _W) | ((1 << _W) - 1)
    assert sim.read("p1") == Value(expect_p, width=100)  # NBA range and part-select writes
    assert sim.read("p2") == Value(expect_p, width=100)


@pytest.mark.parametrize("engine", ENGINES)
def test_generate_block_typedef_width_from_localparam(engine):
    # `typedef logic [SelWidth-1:0] sel_t;` in a generate block, sized by the
    # block's own localparam (pulp's axi_lite_dw_converter): the typedef's width
    # must see the localparam's value once the localparam is scoped per block.
    src = """\
module t #(parameter int W = 128) (input [31:0] addr, output logic [7:0] sel, output logic [15:0] strb);
  if (W > 32) begin : gen_up
    localparam int unsigned SelWidth = $clog2(W / 32);
    localparam int unsigned SelOffset = 2;
    typedef logic [SelWidth-1:0] sel_t;
    sel_t s;
    always_comb begin
      s = sel_t'(addr >> SelOffset);
      sel = s;
      strb = 16'h000F << (s * 4);
    end
  end else begin : gen_pass
    assign sel = '0;
    assign strb = 16'h000F;
  end
endmodule
"""
    design = parse_text(src)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("addr", Value(0x0000_0008 | (0xFF << 4), width=32))  # addr >> 2 = 0x3F2 -> low 2 bits = 2
    sim.settle()
    assert sim.read("sel") == Value(2, width=8)
    assert sim.read("strb") == Value(0x0F00, width=16)

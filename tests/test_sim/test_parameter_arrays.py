"""Parameters with an unpacked dimension (``parameter t_t A [N] = '{...}``)
on every engine (``lower_parameter_arrays``, fixed 2026-11-20).

The parser dropped the dimension, so every engine took the pattern as one
value and read every element as 0, and the emitter wrote the parameter
without it. ibex passes its package's ``PmpCfgRst``/``PmpAddrRst`` down as
``PMPRstCfg[PMP_MAX_REGIONS]`` and reads ``PMPRstCfg[i]`` per generated
region. Expected values are computed here, with an element wider than 64
bits.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
package p;
  parameter int N = 3;
  typedef struct packed { logic lock; logic [1:0] mode; logic r; } cfg_t;
  parameter cfg_t CfgRst [N] = '{
    '{lock: 1'b1, mode: 2'd2, r: 1'b0},
    '{lock: 1'b0, mode: 2'd1, r: 1'b1},
    '{default: 1'b1}
  };
  parameter logic [69:0] AddrRst [N] = '{70'h3f_0000_0000_0000_0001, 70'h5, 70'h2a_0000_0000_0000_0000};
endpackage

module reg_m #(parameter logic [3:0] ResetValue = 4'd0) (input clk, output [3:0] q);
  assign q = ResetValue;
endmodule

module regs #(
  parameter p::cfg_t RstCfg [p::N] = p::CfgRst,
  parameter logic [69:0] RstAddr [p::N] = p::AddrRst
) (input clk, output [11:0] q, output [2:0] locks, output [69:0] a1, output [7:0] top);
  for (genvar i = 0; i < p::N; i++) begin : g
    reg_m #(.ResetValue(RstCfg[i])) u (.clk(clk), .q(q[i*4 +: 4]));
    assign locks[i] = RstCfg[i].lock;
  end
  assign a1 = RstAddr[0];
  assign top = RstAddr[2][69:62];
endmodule

module t(input clk, input [1:0] k, output [11:0] q, output [2:0] locks, output [69:0] a1, output [7:0] top,
    output [1:0] mode1, output [3:0] dyn, output [7:0] desc, output [7:0] keyed, output [11:0] q2);
  import p::*;
  localparam logic [3:0] D [2:0] = '{4'd1, 4'd2, 4'd3};
  localparam logic [7:0] K [0:3] = '{8'd9, 8'd7, 8'd9, 8'd9};
  localparam cfg_t Alt [3] = '{'{lock: 1'b0, mode: 2'd3, r: 1'b1}, '{default: 1'b0}, '{default: 1'b1}};
  regs u (.clk(clk), .q(q), .locks(locks), .a1(a1), .top(top));
  regs #(.RstCfg(Alt)) v (.clk(clk), .q(q2), .locks(), .a1(), .top());
  assign mode1 = CfgRst[1].mode;
  assign dyn = CfgRst[k];
  assign desc = {D[2], D[0]};
  assign keyed = K[k] ^ {4'(K[1][3:0]), 4'd0};
endmodule
"""


def _cfg(lock, mode, r):
    return (lock << 3) | (mode << 1) | r


_CFG = [_cfg(1, 2, 0), _cfg(0, 1, 1), _cfg(1, 1, 1)]  # (`default: 1'b1` gives the 2-bit mode 2'b01)
_ALT = [_cfg(0, 3, 1), 0, _cfg(1, 1, 1)]
_ADDR = [(0x3F << 64) | 1, 5, 0x2A << 64]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("k", [0, 1, 2])
def test_parameter_arrays(engine, k):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk", 0)
    sim.drive("k", k)
    sim.settle()
    assert sim.read("q") == sum(c << (4 * i) for i, c in enumerate(_CFG))
    assert sim.read("q2") == sum(c << (4 * i) for i, c in enumerate(_ALT))
    assert sim.read("locks") == sum((c >> 3) << i for i, c in enumerate(_CFG))
    assert sim.read("a1") == _ADDR[0]
    assert sim.read("top") == _ADDR[2] >> 62
    assert sim.read("mode1") == 1
    assert sim.read("dyn") == _CFG[k]
    assert sim.read("desc") == (1 << 4) | 3  # (`[2:0]`: the first item is D[2])
    assert sim.read("keyed") == (7 if k == 1 else 9) ^ (7 << 4)


def test_parser_and_emitter_keep_the_dimension():
    design = parse_text(_SRC)
    package = next(pk for pk in design.packages if pk.name == "p")
    params = {prm.name: prm for prm in package.parameters}
    assert len(params["CfgRst"].dimensions) == 1
    text = emit_design(design)
    assert "CfgRst [0:N - 1] =" in text
    assert text.index("package p;") < text.index("module reg_m")
    reparsed = parse_text(text)
    assert len(next(pk for pk in reparsed.packages if pk.name == "p").parameters[1].dimensions) == 1

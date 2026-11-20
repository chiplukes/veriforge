"""A field of an indexed packed-struct array element (``m[k ^ 1].f``,
``m[j].f.g[3:0]``) on every engine (``lower_struct_array_fields``, fixed
2026-11-19).

The parser keeps such an access with its index as text, and each engine read
that text as a signal name: an expression index (``k ^ 1``, ``k[0]``) was
silently not read or written on reference, raised on vm/vm-fast, and raised
or used the wrong element on compiled. In a submodule even a plain name
failed (flattening prefixed the array, not the index), and compiled could not
resolve a numeric one (the corpus's pulp xbar test benches,
``i_tb.addr_map[0].idx = 32'd0``). Expected values are computed here, with
elements wider than 64 bits.
"""

from __future__ import annotations

import random

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_PKG = """\
package p;
  typedef struct packed { logic [3:0] a; logic [2:0] b; } small_t;
  typedef struct packed { logic [31:0] idx; small_t s; logic [63:0] start_addr; logic [63:0] end_addr; } rule_t;
endpackage
"""
_RULE_W = 167  # (32 + 7 + 64 + 64; field offsets: idx 135, s 128, start_addr 64, end_addr 0)


def _sim(src: str, engine: str) -> Simulator:
    design = parse_text(_PKG + src)
    return Simulator(design.get_module("t"), engine=engine, design=design)


_TOP = """\
module t(input clk, input [63:0] d, input [1:0] k, output [27:0] q, output [6:0] r, output [6:0] u,
    output [333:0] mq, output [70:0] mr);
  p::small_t [3:0] s, w;
  p::rule_t [1:0] m, n;
  always_comb begin s = '0; s[k ^ 2'd1].a = d[3:0]; s[k[1]].b = d[6:4]; s[k + 2'd1].b[0] = 1'b1; end
  assign q = s;
  assign w = d[27:0];
  assign r = {w[k ^ 2'd1].a, w[k[1]].b};
  assign u = {w[k + 1].a, w[(k * 3) % 4].b};
  always_ff @(posedge clk) begin
    m[k[0]].start_addr <= d;
    m[~k[0]].idx[7:4] <= d[3:0];
    m[k[1]].s.b <= d[9:7];
    m[k[0] ^ k[1]].end_addr[40] <= d[0];
  end
  assign mq = m;
  assign n = {d, d[31:0], 7'd3, ~d, d};
  assign mr = {n[k[0]].start_addr, n[k[1]].s};
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_expression_index_reads_and_writes(engine):
    sim = _sim(_TOP, engine)
    rng = random.Random(1)
    m, known = [0, 0], [0, 0]

    def put(i, lo, width, value):
        field = ((1 << width) - 1) << lo
        m[i] = (m[i] & ~field) | ((value << lo) & field)
        known[i] |= field

    for _ in range(16):
        d, k = rng.getrandbits(64), rng.getrandbits(2)
        k0, k1 = k & 1, k >> 1
        sim.drive("clk", 0)
        sim.drive("d", d)
        sim.drive("k", k)
        sim.settle()
        s = [0] * 4
        s[k ^ 1] |= (d & 15) << 3
        s[k1] = (s[k1] & ~7) | ((d >> 4) & 7)
        s[(k + 1) % 4] |= 1
        assert sim.read("q") == sum(v << (7 * i) for i, v in enumerate(s))
        w = [(d >> (7 * i)) & 127 for i in range(4)]
        assert sim.read("r") == (w[k ^ 1] & ~7) | (w[k1] & 7)
        if k == 3:  # (`k + 1` is 32 bits: element 4 is out of range, x)
            assert sim.read("u").mask == 0b1111000
        else:
            assert sim.read("u") == (w[k + 1] & ~7) | (w[k * 3 % 4] & 7)
        n = (d << 167) | ((d & 0xFFFF_FFFF) << 135) | (3 << 128) | ((~d & (2**64 - 1)) << 64) | d
        elements = [n & ((1 << _RULE_W) - 1), n >> _RULE_W]
        assert sim.read("mr") == (((elements[k0] >> 64) & (2**64 - 1)) << 7) | ((elements[k1] >> 128) & 127)
        sim.drive("clk", 1)
        sim.settle()
        put(k0, 64, 64, d)
        put(1 - k0, 139, 4, d & 15)
        put(k1, 128, 3, (d >> 7) & 7)
        put(k0 ^ k1, 40, 1, d & 1)
        got = sim.read("mq")
        full, defined = m[0] | (m[1] << _RULE_W), known[0] | (known[1] << _RULE_W)
        assert got.val & defined == full & defined
        assert got.mask & defined == 0


_SUB = """\
module c(input [63:0] d, input [1:0] j, output [27:0] q, output [3:0] r, output [333:0] mq, output [31:0] mr);
  p::small_t [3:0] s;
  p::rule_t [1:0] m;
  always_comb begin s = '0; s[j].a = d[3:0]; end
  always_comb begin m = '0; m[j[0]].start_addr = d; m[j[1] ^ 1'b1].idx = d[31:0]; m[0].end_addr = ~d; end
  assign q = s;
  assign r = s[j].a;
  assign mq = m;
  assign mr = m[~j[0]].idx;
endmodule
module t(input [63:0] d, input [1:0] k, output [27:0] q, output [3:0] r, output [333:0] mq, output [31:0] mr);
  wire [1:0] j = ~k;  // (a different name and value than the child's port)
  c u(.d(d), .j(j), .q(q), .r(r), .mq(mq), .mr(mr));
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_index_inside_a_submodule(engine):
    sim = _sim(_SUB, engine)
    rng = random.Random(4)
    for _ in range(10):
        d, k = rng.getrandbits(64), rng.getrandbits(2)
        j = 3 - k
        sim.drive("d", d)
        sim.drive("k", k)
        sim.settle()
        assert sim.read("q") == (d & 15) << (7 * j + 3)
        assert sim.read("r") == d & 15
        m = [0, 0]
        m[j & 1] |= d << 64
        m[(j >> 1) ^ 1] |= (d & 0xFFFF_FFFF) << 135
        m[0] |= ~d & (2**64 - 1)
        assert sim.read("mq") == m[0] | (m[1] << _RULE_W)
        assert sim.read("mr") == m[1 - (j & 1)] >> 135


_LOOPS = """\
module t(input [63:0] d, input [1:0] k, output reg [27:0] q, output [27:0] g);
  p::small_t [3:0] s, h;
  always_comb begin
    for (int i = 0; i < 4; i++) begin s[i].a = d[i*4 +: 4]; s[i].b = 3'(i); end
    s[k].b = 3'd7;
    q = s;
  end
  for (genvar e = 0; e < 2; e++) begin : G
    always_comb begin h[e * 2 + k[0]] = '0; h[e * 2 + k[0]].a = d[e * 4 +: 4]; h[e * 2 + 1 - k[0]] = '1; end
  end
  assign g = h;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_loop_and_genvar_indices(engine):
    sim = _sim(_LOOPS, engine)
    rng = random.Random(9)
    for _ in range(8):
        d, k = rng.getrandbits(64), rng.getrandbits(2)
        sim.drive("d", d)
        sim.drive("k", k)
        sim.settle()
        nib = [(d >> (4 * i)) & 15 for i in range(4)]
        assert sim.read("q") == sum((nib[i] << 3 | (7 if i == k else i)) << (7 * i) for i in range(4))
        h = [0] * 4
        for e in range(2):
            h[e * 2 + (k & 1)] = nib[e] << 3
            h[e * 2 + 1 - (k & 1)] = 127
        assert sim.read("g") == sum(v << (7 * i) for i, v in enumerate(h))

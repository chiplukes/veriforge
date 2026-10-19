"""Designs that exceed a fixed internal capacity of an engine (2026-10-10
audit). Each one used to silently produce wrong output, or fail to build:
vm-fast's 64-entry wide-NBA queues, compiled's 16-word (1024-bit)
multiply/divide/modulo temporaries, its 4096-bit display scratch and 64 KB
output buffer, and the Cython parser's recursion limit on deep
expressions. Expected values are computed here with Python integers
(checked against Icarus)."""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_N_REGS = 100
_D0 = 0x0123_4567_89AB_CDEF


def _sim(src: str, engine: str, name: str) -> Simulator:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=name)
    return Simulator(design.modules[0], engine=engine, design=design)


def _many_wide_nbas_src() -> str:
    decls = "\n".join(f"  reg [79:0] r{i}; reg [79:0] p{i};" for i in range(_N_REGS))
    nbas = "\n".join(f"    r{i} <= {{d, 16'd{i}}}; p{i}[70:3] <= {{d, 4'd{i % 16}}};" for i in range(_N_REGS))
    # A 200-term expression: deep enough to overflow the Cython parser's
    # default recursion limit when building the compiled engine.
    xor = " ^ ".join(f"r{i}[{i % 80}] ^ p{i}[{3 + i * 7 % 68}]" for i in range(_N_REGS))
    return f"""module t(input clk);
  reg [63:0] d = 64'h{_D0:x};
{decls}
  always @(posedge clk) begin
    d <= d + 64'h1111;
{nbas}
  end
  always @(negedge clk) $display("%h %h %h %h %b", r0, r{_N_REGS - 1}, p0[70:3], p{_N_REGS - 1}[70:3], {xor});
endmodule
"""


def _many_wide_nbas_expected(cycles: int) -> list[str]:
    out = []
    d = _D0
    for _ in range(cycles):
        r = [(d << 16) | i for i in range(_N_REGS)]
        p = [((d << 4) | (i % 16)) << 3 for i in range(_N_REGS)]
        bit = 0
        for i in range(_N_REGS):
            bit ^= (r[i] >> (i % 80)) & 1
            bit ^= (p[i] >> (3 + i * 7 % 68)) & 1
        out.append(f"{r[0]:020x} {r[-1]:020x} {p[0] >> 3:017x} {p[-1] >> 3:017x} {bit}")
        d = (d + 0x1111) & ((1 << 64) - 1)
    return out


@pytest.mark.parametrize("engine", ENGINES)
def test_many_wide_nbas_and_deep_expression(engine):
    """100 whole and 100 part-select NBAs to wide regs in one process
    (vm-fast dropped the ones past 64), read by a 200-term expression."""
    sim = _sim(_many_wide_nbas_src(), engine, "cap_nba.v")
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=32)
    assert list(sim.display_output) == _many_wide_nbas_expected(3)


_BIG_ARITH = """module t(input clk);
  reg [1099:0] a = {100{11'h5a3}}; reg [1099:0] b = {50{22'h1abc5}};
  wire [1099:0] m = a * b;
  wire [1099:0] q = a / (b >> 600);
  wire [1099:0] r = a % (b >> 900);
  reg [99:0] o1, o2, o3;
  always @(posedge clk) begin
    o1 = ({a,a} * {b,b}) >> 2100;
    o2 = ({a,a} / {b[700:0],b[700:0]}) >> 100;
    o3 = ({a,a} ** 3) >> 2000;
    $display("%h %h %h", m[1099:1000], q[499:400], r[199:100]);
    $display("%h %h %h", o1, o2, o3);
  end
endmodule
"""


def _repl(value: int, width: int, times: int) -> int:
    out = 0
    for _ in range(times):
        out = (out << width) | value
    return out


def _big_arith_expected() -> list[str]:
    a = _repl(0x5A3, 11, 100)
    b = _repl(0x1ABC5, 22, 50)
    m1100 = (1 << 1100) - 1
    m2200 = (1 << 2200) - 1
    m100 = (1 << 100) - 1
    m = (a * b) & m1100
    q = a // (b >> 600)
    r = a % (b >> 900)
    aa = (a << 1100) | a
    bb = (b << 1100) | b
    b701 = b & ((1 << 701) - 1)
    bb701 = (b701 << 701) | b701
    o1 = (((aa * bb) & m2200) >> 2100) & m100
    o2 = ((aa // bb701) >> 100) & m100
    o3 = ((pow(aa, 3) & m2200) >> 2000) & m100
    return [
        f"{m >> 1000 & m100:025x} {q >> 400 & m100:025x} {r >> 100 & m100:025x}",
        f"{o1:025x} {o2:025x} {o3:025x}",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_arithmetic_wider_than_1024_bits(engine):
    """`*`, `/`, `%` and `**` above 1024 bits (compiled returned all-x)."""
    sim = _sim(_BIG_ARITH, engine, "cap_arith.v")
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=8)
    assert list(sim.display_output) == _big_arith_expected()


_BIG_DISPLAY = """module t(input clk);
  reg [4999:0] a = {500{10'h2b7}};
  reg [4999:0] h = {1'b1, 4999'd77};
  reg [4199:0] m [0:1];
  initial m[1] = {420{10'h1c9}};
  always @(posedge clk) begin
    $display("%h", a);
    $display("%d", a);
    $display("%o", a[4500:0]);
    $display("%h", m[1]);
    $display("%e %f", h, a[99:0]);
  end
endmodule
"""


def _big_display_expected() -> list[str]:
    a = _repl(0x2B7, 10, 500)
    m1 = _repl(0x1C9, 10, 420)
    h = (1 << 4999) | 77
    a100 = a & ((1 << 100) - 1)
    return [
        f"{a:01250x}",
        f"{a:>{int(5000 * 0.30102999566398120) + 1}d}",
        f"{a & ((1 << 4501) - 1):01501o}",
        f"{m1:01050x}",
        f"{float(h) if h < 2**1024 else float('inf'):e} {float(a100):f}",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_display_wider_than_4096_bits(engine):
    """Values above 4096 bits (compiled printed only the low 4096 bits;
    `%e` of a value beyond double range crashed reference/vm)."""
    sim = _sim(_BIG_DISPLAY, engine, "cap_display.v")
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=8)
    assert list(sim.display_output) == _big_display_expected()


_CHATTY = """module t(input clk);
  reg [31:0] n = 0;
  always @(posedge clk) begin
    n <= n + 1;
    $display("%0d %h", n, {12{n}});
  end
endmodule
"""


@pytest.mark.parametrize("engine", [e for e in ENGINES if e in {"vm-fast", "compiled"}])
def test_batch_run_output_beyond_64kb(engine):
    """~100 KB of `$display` output in one batch_run (compiled kept only the
    first 64 KB: its output buffer is drained when the batch returns)."""
    from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON  # noqa: PLC0415

    if engine == "vm-fast" and not _HAS_CYTHON:
        pytest.skip("vm-fast batch_run requires the native VM extension")
    sim = _sim(_CHATTY, engine, "cap_chatty.v")
    cycles = 1000
    assert sim.batch_run(cycles, "clk", 10) == cycles
    expected = [f"{n} {f'{n:08x}' * 12}" for n in range(cycles)]
    assert list(sim.display_output) == expected


_DEPTH = 300


def _deep_src() -> str:
    xor = "d[0]"
    total = "d"
    for i in range(1, _DEPTH):
        xor = f"(d[{i % 64}] ^ {xor})"
        total = f"({{d[{i % 32}:0], d}} + {total})"
    parts = ", ".join(f"d[{i * 7 % 64}]" for i in range(_DEPTH))
    return f"""module t(input clk);
  reg [63:0] d = 64'h{_D0:x}; reg [99:0] s; reg [{_DEPTH - 1}:0] c;
  integer k;
  always @(posedge clk) begin
    s = {total};
    c = {{{parts}}};
    $display("%b %h %h", {xor}, s, c);
    for (k = 0; k < 3000; k = k + 1) $display("%0d %h", k, s);
  end
endmodule
"""


def _deep_expected() -> list[str]:
    def bit(i: int) -> int:
        return (_D0 >> i) & 1

    xor = 0
    total = 0
    c = 0
    for i in range(_DEPTH):
        xor ^= bit(i % 64)
        total += _D0 if i == 0 else ((_D0 & ((1 << (i % 32 + 1)) - 1)) << 64) | _D0
        c = (c << 1) | bit(i * 7 % 64)
    s = f"{total & ((1 << 100) - 1):025x}"
    return [f"{xor} {s} {c:0{(_DEPTH + 3) // 4}x}", *(f"{k} {s}" for k in range(3000))]


@pytest.mark.parametrize("engine", ENGINES)
def test_deep_expressions_and_many_displays_per_delta(engine):
    """Expressions nested 300 deep (the front end hit Python's recursion
    limit), a 300-part concatenation (vm-fast's 256-slot evaluation stack
    overflowed: wrong values or a crash), and 3000 displays from one process
    in one delta (vm-fast raised a display-buffer overflow)."""
    sim = _sim(_deep_src(), engine, "cap_deep.v")
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=8)
    assert list(sim.display_output) == _deep_expected()

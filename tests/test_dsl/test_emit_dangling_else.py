"""The emitter must not let an `else` bind to an `if` nested inside a bare then-body.

DSL:
    if a:        x = 1
    elif b:
        if c:    x = 2      # nested if, no else
    elif d:      x = 3

was emitted as `... else if (b) if (c) x <= 2; else if (d) x <= 3;`, where
Verilog binds `else if (d)` to `if (c)`: with a=0, b=0, d=1 nothing happened,
and with b=1, c=0, d=1 x became 3. The fix wraps such then-bodies in begin/end.
"""

import itertools
import shutil
import subprocess

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.dsl import Module, posedge
from veriforge.project import Design


def _build(loop_body: bool = False):
    m = Module("dangle")
    clk = m.input("clk")
    a = m.input("a")
    b = m.input("b")
    c = m.input("c")
    d = m.input("d")
    x = m.output_reg("x", width=2, init=0)
    with m.always(posedge(clk)):
        x <<= 0
        with m.if_(a):
            x <<= 1
        with m.elif_(b):
            with m.if_(c):
                x <<= 2
        with m.elif_(d):
            x <<= 3
    return m.build()


def _expected(a: int, b: int, c: int, d: int) -> int:
    if a:
        return 1
    if b:
        return 2 if c else 0
    if d:
        return 3
    return 0


def test_nested_if_body_is_wrapped():
    text = emit_design(Design(modules=[_build()]))
    lines = [line.strip() for line in text.splitlines()]
    i = lines.index("else if (b)")
    assert lines[i + 1] == "begin", text
    assert lines.index("else if (d)") > lines.index("end", i), text


def test_if_without_else_is_unchanged():
    m = Module("plain")
    clk = m.input("clk")
    b = m.input("b")
    c = m.input("c")
    x = m.output_reg("x", init=0)
    with m.always(posedge(clk)):
        with m.if_(b):
            with m.if_(c):
                x <<= 1
    text = emit_design(Design(modules=[m.build()]))
    assert "begin" not in text.split("always", 1)[1], text


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="Icarus Verilog not installed")
def test_emitted_logic_matches_dsl_semantics(tmp_path):
    cases = list(itertools.product((0, 1), repeat=4))
    checks = "\n".join(
        f"    a={a}; b={b}; c={c}; d={d}; @(posedge clk); #1;\n"
        f'    if (x !== {_expected(a, b, c, d)}) begin $display("FAIL a={a} b={b} c={c} d={d} x=%0d", x); $finish; end'
        for a, b, c, d in cases
    )
    tb = f"""
module tb;
reg clk = 0, a = 0, b = 0, c = 0, d = 0;
wire [1:0] x;
always #5 clk = ~clk;
dangle dut(.clk(clk), .a(a), .b(b), .c(c), .d(d), .x(x));
initial begin
{checks}
    $display("PASS");
    $finish;
end
endmodule
"""
    (tmp_path / "dut.v").write_text(emit_design(Design(modules=[_build()])))
    (tmp_path / "tb.v").write_text(tb)
    subprocess.run(
        ["iverilog", "-o", str(tmp_path / "sim"), str(tmp_path / "dut.v"), str(tmp_path / "tb.v")], check=True
    )
    result = subprocess.run(["vvp", str(tmp_path / "sim")], capture_output=True, text=True, check=True)
    assert "PASS" in result.stdout, result.stdout

"""Python ints beyond 32 bits must emit as sized literals.

An unsized decimal >= 2**31 is tool-dependent: Icarus and Verilator keep the
full value, Vivado truncates it to 32 bits. A 128-bit lane mask
`(1 << 96) - 1` therefore simulated correctly and synthesized wrong. The DSL
now emits such values as `N'sd<value>` (signed, one bit wider than the
magnitude), which keeps the value and signedness of the unsized form.
"""

import re
import shutil
import subprocess

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.dsl import Module
from veriforge.project import Design

WIDE = (1 << 96) - 1


def _build_mux():
    from veriforge.dsl import mux

    m = Module("wide_lit")
    sel = m.input("sel")
    s = m.input("s", width=40, signed=True)
    mask = m.output("mask", width=128)
    narrow = m.output("narrow", width=32)
    total = m.output("total", width=48, signed=True)
    neg = m.output("neg", width=48, signed=True)
    mask.assign = mux(sel, 1 << 127, WIDE)
    narrow.assign = 5
    total.assign = s - (1 << 40)
    neg.assign = -(1 << 40) + 0 * s
    return m.build()


def test_wide_ints_emit_sized():
    text = emit_design(Design(modules=[_build_mux()]))
    assert f"97'sd{WIDE}" in text
    assert "129'sd" in text  # 1 << 127
    assert "assign narrow = 5;" in text
    bare = [int(n) for n in re.findall(r"(?<![\w'])\d+(?!\w)", text)]
    assert all(n < 1 << 31 for n in bare), "a bare decimal >= 2**31 was emitted"


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")
def test_wide_literal_values_simulate(tmp_path):
    (tmp_path / "dut.v").write_text(emit_design(Design(modules=[_build_mux()])))
    (tmp_path / "tb.v").write_text(
        """
module tb;
reg sel; reg signed [39:0] s;
wire [127:0] mask; wire [31:0] narrow; wire signed [47:0] total, neg;
wide_lit dut(.sel(sel), .s(s), .mask(mask), .narrow(narrow), .total(total), .neg(neg));
initial begin
    sel = 0; s = -40'sd3; #1;
    if (mask !== {32'd0, {96{1'b1}}}) $fatal(1, "mask %h", mask);
    if (total !== -48'sd1099511627779) $fatal(1, "total %0d", total);
    if (neg !== -48'sd1099511627776) $fatal(1, "neg %0d", neg);
    sel = 1; #1;
    if (mask !== {1'b1, 127'd0}) $fatal(1, "mask %h", mask);
    $display("OK");
end
endmodule
"""
    )
    build = subprocess.run(
        ["iverilog", "-g2012", "-o", str(tmp_path / "a.out"), str(tmp_path / "dut.v"), str(tmp_path / "tb.v")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(["vvp", str(tmp_path / "a.out")], capture_output=True, text=True, check=False)
    assert "OK" in run.stdout, run.stdout + run.stderr

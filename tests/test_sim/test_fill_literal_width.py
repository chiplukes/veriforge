"""Regression test for issue #8: a bare `'1` fill literal assigned to a
>32-bit packed vector in `always_comb` was evaluated in a 32-bit context
and zero-extended, instead of being sized to the LHS.

Root cause: `_build_sv_fill_literal` (transforms/_expressions.py) builds
`'1` as `Literal(value=-1, original_text="'1")`, width `None`. Every
engine's width-extension logic for a `Literal` decides `sign_extend(width)`
vs `resize(width)` (zero-extend) based on `Literal.signed` -- `'1` was
never marked `signed`, so a >32-bit context zero-extended its 32-bit
`0xFFFFFFFF` value instead of correctly filling every additional bit with
1 (IEEE 1800-2017 SS5.7.1: unsized fill literals extend by REPLICATING
their own fill digit, not by zero-extension).

Fixed by marking `'1`'s `Literal` node `signed=True` -- a deliberate,
narrow reuse of the existing sign-extend/zero-extend width-extension
decision every engine already makes: for `'1` specifically (whose own
32-bit value is all-1s, i.e. its own top bit is 1), sign-extension and
correct fill-extension produce IDENTICAL bits, so no new width-extension
code path is needed. `'0` doesn't need the same treatment (zero-extension
already IS correct fill-extension for an all-0s value).

The explicit replication idiom (`{WIDTH{1'b1}}`) was never affected --
included here as a same-file cross-check, matching the original report.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """
module top (
    input logic sel,
    output logic [127:0] out_bare,
    output logic [127:0] out_repl
);
localparam WIDTH = 128;
always_comb begin
    if (sel) out_bare = '1;
    else     out_bare = '0;
    if (sel) out_repl = {WIDTH{1'b1}};
    else     out_repl = {WIDTH{1'b0}};
end
endmodule
"""


class TestFillLiteralWidth:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_bare_ones_fill_sized_to_wide_lhs(self, engine, tmp_path):
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("sel", 1)
        sim.run(max_time=0)
        assert int(sim.signal("out_bare").value) == (1 << 128) - 1
        assert int(sim.signal("out_repl").value) == (1 << 128) - 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_bare_zeros_fill_unaffected(self, engine, tmp_path):
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("sel", 0)
        sim.run(max_time=0)
        assert int(sim.signal("out_bare").value) == 0
        assert int(sim.signal("out_repl").value) == 0

"""Regression tests for issue #7d: a REGISTERED whole-array ternary
(`arr_out <= sel ? arr_a : arr_b;`) corrupting to a broadcast of index 0's
value across every lane, when one operand is a 2-D packed array that
crossed a module boundary via a plain port.

Two distinct root causes, both fixed together (the second discovered while
verifying the compiled engine):

1. `_inline_logic` (sim/elaborate.py) had a PROCEDURAL sibling of the #7b
   continuous-assign bug: every submodule always/initial-block body, when
   inlined as a child instance's own logic, was run through
   `_expand_unpacked_array_stmt` -- unconditionally expanding any
   blocking/non-blocking assign whose LHS was a bare dimensioned
   identifier into N per-element assigns, keeping the FULL (unindexed)
   RHS unchanged in every copy whenever that RHS wasn't itself a
   same-shaped array Identifier or assignment pattern (exactly the #7b
   bug's fallback: `return [copy.deepcopy(rhs) for _ in lhs_parts]`).
   For `m_axis_tdata <= bypass ? s_axis_tdata : merged_tdata_wide;`, this
   produced N per-element assigns each keeping the whole (unindexed)
   ternary as their RHS -- each 8-bit element assign then evaluated the
   *whole* 32-bit ternary result at its own narrow width, keeping only
   its low byte, so every lane ended up holding the same value. Fixed by
   removing this expansion entirely (mirroring #7b's fix) -- the
   already-fixed whole-array read/write machinery handles the
   un-expanded assign correctly.

2. Once (1) was fixed, the reference/vm/vm-fast engines were already
   correct, but the COMPILED engine's `_emit_lhs_write`
   (sim/compiled/_stmt_emitters.py) had never had a case for "whole-array
   (memory-backed) LHS with an RHS more complex than a bare matching-
   shape memory-to-memory copy" at all -- it fell through to a
   `NotImplementedError`. Fixed by splitting into one per-element write
   per memory index (all compile-time-constant), each element's RHS a
   `RangeSelect` over the whole RHS expression -- mirrors
   `sim/executor.py`'s equivalent fix for the reference engine's NBA
   whole-memory-write path (see test_flat_to_array_assign.py's docstring
   for that half).
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """
module producer (
    output logic [3:0][7:0] arr
);
integer i;
always_comb begin
    for (i = 0; i < 4; i = i + 1) begin
        arr[i] = (i + 1) * 8'h11;
    end
end
endmodule

module dg_merge (
    input  logic clk,
    input  logic bypass,
    input  logic [3:0][7:0] s_axis_tdata,
    input  logic [3:0][7:0] merged_tdata_wide,
    output logic [3:0][7:0] m_axis_tdata
);
always_ff @(posedge clk) begin
    m_axis_tdata <= bypass ? s_axis_tdata : merged_tdata_wide;
end
endmodule

module top (
    input logic clk,
    input logic bypass,
    input logic [3:0][7:0] merged_tdata_wide,
    output logic [3:0][7:0] m_axis_tdata
);
logic [3:0][7:0] s_axis_tdata;
producer u_prod (.arr(s_axis_tdata));
dg_merge u_dg (.clk(clk), .bypass(bypass), .s_axis_tdata(s_axis_tdata),
               .merged_tdata_wide(merged_tdata_wide), .m_axis_tdata(m_axis_tdata));
endmodule
"""


class TestWholeArrayRegisteredTernary:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_selected_branch_from_boundary_crossing_producer(self, engine, tmp_path):
        """bypass=1 selects `s_axis_tdata` (the boundary-crossing,
        generate/always_comb-for-loop-driven operand) -- must register
        its real per-lane values, not broadcast lane 0."""
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("bypass", 1)
        sim.drive("merged_tdata_wide", 0)
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        assert int(sim.signal("m_axis_tdata").value) == 0x44332211

    @pytest.mark.parametrize("engine", ENGINES)
    def test_unselected_branch_still_correct(self, engine, tmp_path):
        """bypass=0 selects `merged_tdata_wide` instead -- the
        boundary-crossing operand is present but unused; must not
        corrupt the OTHER (selected) branch's value either."""
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("bypass", 0)
        sim.drive("merged_tdata_wide", 0xDEADBEEF)
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        assert int(sim.signal("m_axis_tdata").value) == 0xDEADBEEF

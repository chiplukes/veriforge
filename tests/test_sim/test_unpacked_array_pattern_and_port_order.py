"""Regression tests for a second family of bugs found during the same
follow-up audit as test_whole_array_audit_followups.py, discovered via the
real `ibex_alu`/`ibex_ex_block` RTL (`test_ibex_alu_assignment_patterns_cross_engine`
and `test_ibex_ex_block_intermediate_bridge_cross_engine` in
test_ibex_examples.py):

1. `arr = '{a, b, ...};` (an unkeyed positional assignment pattern) assigned
   to a whole, plain UNPACKED array -- whether as a procedural blocking/
   non-blocking assign or as a continuous assign -- must assign pattern item
   k to array index k (IEEE 1800-2017 SS10.9.1's declaration-order rule,
   matching how `arr[k]` indexing already works). All 4 engines previously
   routed this through the SAME "flatten the whole pattern into one bit
   vector, then unflatten it MSB-first" machinery used for a genuine 2-D
   PACKED array's own bit-vector reinterpretation -- correct for that case,
   but it silently REVERSES element order for a plain unpacked array, whose
   "index" has no bit-position meaning at all.

2. A bare-Identifier-to-bare-Identifier whole-memory CONTINUOUS assign
   (typically a hierarchy-flattened whole-array PORT CONNECTION, e.g.
   `.imd_val_q_i(imd_val_q_full)`) must be a direct per-index copy, not the
   same flatten/unflatten path -- for the identical reason. Procedural
   blocking/non-blocking whole-memory-identifier copies already had a
   direct-copy fast path (`_copy_whole_memory`/`_compile_whole_memory_copy`/
   `_emit_whole_mem_copy_lines`); continuous assigns never used it.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES


def _parse(src: str, tmp_path):
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


class TestAssignmentPatternToUnpackedArray:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_procedural_positional_pattern_preserves_index_order(self, engine, tmp_path):
        """`arr = '{a, b};` (blocking, inside always_comb) -- item 0 -> arr[0]."""
        design = _parse(
            """
            module top (
                input logic [31:0] a,
                input logic [31:0] b,
                output logic [31:0] arr0,
                output logic [31:0] arr1
            );
            logic [31:0] arr [2];
            always_comb begin
                arr = '{a, b};
            end
            assign arr0 = arr[0];
            assign arr1 = arr[1];
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("a", 0x1234_5678)
        sim.drive("b", 0x0000_0000)
        sim.run(max_time=0)
        assert int(sim.signal("arr0").value) == 0x1234_5678
        assert int(sim.signal("arr1").value) == 0x0000_0000

    @pytest.mark.parametrize("engine", ENGINES)
    def test_continuous_positional_pattern_preserves_index_order(self, engine, tmp_path):
        """`assign arr = '{a, b};` (continuous) -- item 0 -> arr[0]."""
        design = _parse(
            """
            module top (
                input logic [31:0] a,
                input logic [31:0] b,
                output logic [31:0] arr0,
                output logic [31:0] arr1
            );
            logic [31:0] arr [2];
            assign arr = '{a, b};
            assign arr0 = arr[0];
            assign arr1 = arr[1];
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("a", 0x1234_5678)
        sim.drive("b", 0x0000_0000)
        sim.run(max_time=0)
        assert int(sim.signal("arr0").value) == 0x1234_5678
        assert int(sim.signal("arr1").value) == 0x0000_0000


class TestWholeMemoryPortConnectionOrder:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_unpacked_array_port_connection_preserves_index_order(self, engine, tmp_path):
        """A whole-array port connection (bare Identifier on both sides,
        matching shape) must not swap lanes across a hierarchy boundary."""
        design = _parse(
            """
            module leaf (
                input logic [31:0] in_arr [2],
                output logic [31:0] out_arr [2]
            );
            assign out_arr[0] = in_arr[0];
            assign out_arr[1] = in_arr[1];
            endmodule

            module top (
                input logic [31:0] a,
                input logic [31:0] b,
                output logic [31:0] result0,
                output logic [31:0] result1
            );
            logic [31:0] in_arr [2];
            logic [31:0] out_arr [2];
            assign in_arr[0] = a;
            assign in_arr[1] = b;
            leaf u_leaf (.in_arr(in_arr), .out_arr(out_arr));
            assign result0 = out_arr[0];
            assign result1 = out_arr[1];
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("a", 0x1234_5678)
        sim.drive("b", 0x0000_0000)
        sim.run(max_time=0)
        assert int(sim.signal("result0").value) == 0x1234_5678
        assert int(sim.signal("result1").value) == 0x0000_0000

"""Regression test for a whole-memory-shaped ternary RHS producing garbage
in the compiled engine.

`assign lhs = cond ? mem_a : mem_b;` (or the NBA form, `lhs <= cond ? mem_a
: mem_b;`) with `lhs`/`mem_a`/`mem_b` all matching-shape 2-D packed arrays
("memories" in the compiled engine's own terminology) -- e.g. a classic
bypass mux, `m_axis_tdata <= bypass ? s_axis_tdata : merged_tdata_wide;`.

Both the continuous-assign compiler (`_compile_continuous_assigns` /
`_process_compiler.py`) and the procedural statement compiler
(`_emit_lhs_write` / `_stmt_emitters.py`) have their own "whole-array LHS
with a non-trivial RHS" fallback, and both had the same bug: neither
recognized a `TernaryOp` RHS as a special case, so both wrapped the ENTIRE
ternary in a per-element `RangeSelect`/offset-extraction and ran it through
the scalar-only `_emit_expr`/`_emit_concat_rhs_extract` path -- which has no
support for a memory-identifier operand at all (`_emit_expr(Identifier)` for
a memory silently doesn't know what to do with it). The result was garbage
for nearly every element, not merely X: found via real-world feedback on
`axis_dg_merge.sv`'s `m_axis_tdata <= bypass ? s_axis_tdata :
merged_tdata_wide;` (both operands 128-element, 18-bit 2-D packed arrays),
where ~97% of output lanes read back nonsense while a handful of early
lanes happened to read correctly (by coincidence of what the broken scalar
expression evaluated to).

Fixed by adding a dedicated case to both compilers for "TernaryOp whose
`true_expr`/`false_expr` are both bare Identifiers resolving to memories of
matching shape to the LHS", reusing a new shared helper,
`_emit_whole_mem_ternary_lines` (`_wide_emitter.py`), that does a genuine
per-element conditional copy instead.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

NUM_ELEMS = 128
ELEM_W = 18


def _parse(src: str, tmp_path):
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


class TestWholeMemoryTernaryRhs:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_continuous_assign_bypass_mux(self, engine, tmp_path):
        """`assign m_data = en ? merged : s_data;` -- combinational bypass mux
        between two matching-shape 2-D packed arrays."""
        design = _parse(
            f"""
            module top (
                input logic en,
                input logic [{NUM_ELEMS - 1}:0][{ELEM_W - 1}:0] s_data,
                output logic [{NUM_ELEMS - 1}:0][{ELEM_W - 1}:0] m_data
            );
            logic [{NUM_ELEMS - 1}:0][{ELEM_W - 1}:0] merged;
            always_comb begin
                for (int i = 0; i < {NUM_ELEMS}; i++) begin
                    merged[i] = s_data[i] + 1000;
                end
            end
            assign m_data = en ? merged : s_data;
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("en", 0)
        expected = [(100 + i) for i in range(NUM_ELEMS)]
        for i in range(NUM_ELEMS):
            sim.signal(f"s_data[{i}]").value = expected[i]
        sim.run(max_time=0)
        for i in range(NUM_ELEMS):
            got = int(sim.signal(f"m_data[{i}]").value)
            assert got == expected[i], f"lane {i}: want {expected[i]}, got {got}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_registered_bypass_mux(self, engine, tmp_path):
        """`m_data <= bypass ? s_data : merged;` (NBA, inside always_ff) --
        the exact shape that exposed this in the real `axis_dg_merge.sv`
        RTL."""
        design = _parse(
            f"""
            module top (
                input logic clk,
                input logic en,
                input logic [{NUM_ELEMS - 1}:0][{ELEM_W - 1}:0] s_data,
                output logic [{NUM_ELEMS - 1}:0][{ELEM_W - 1}:0] m_data
            );
            logic [{NUM_ELEMS - 1}:0][{ELEM_W - 1}:0] merged;
            always_comb begin
                for (int i = 0; i < {NUM_ELEMS}; i++) begin
                    merged[i] = s_data[i] + 1000;
                end
            end
            logic bypass = 1;
            always_ff @(posedge clk) begin
                bypass <= !en;
                m_data <= bypass ? s_data : merged;
            end
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("en", 0)
        expected = [(100 + i) for i in range(NUM_ELEMS)]
        for i in range(NUM_ELEMS):
            sim.signal(f"s_data[{i}]").value = expected[i]
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        for i in range(NUM_ELEMS):
            got = int(sim.signal(f"m_data[{i}]").value)
            assert got == expected[i], f"lane {i}: want {expected[i]}, got {got}"

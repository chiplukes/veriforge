"""Regression tests for a family of "missing whole-memory-Identifier
fallback" bugs found during a follow-up audit prompted by issues #6-#8 (see
test_2d_array_concat_operand.py, test_flat_to_array_assign.py,
test_whole_array_registered_ternary.py). Each of these is the same root
cause pattern recurring in a function that hadn't been touched yet:

1. `_emit_expr_mask` (sim/compiled/_expr_emitter.py) -- used for `casex`/
   `casez` selector and item-value masks -- had no whole-memory Identifier
   case at all (unlike its siblings `_emit_expr`/`_emit_py_expr`/
   `_emit_py_mask_expr`/`_emit_mask_expr`, all fixed earlier). A bare 2-D
   packed array used directly as a `casex` selector or item value always
   read as fully-known (mask "0"), so any real x/z bits in it were never
   wildcard-matched.

2. `_expr_self_width` (sim/evaluator.py, reference engine) had no
   whole-memory case either -- it hardcoded `32` for any Identifier not
   found in `ctx._signals`. Since a Concatenation's own eval() sizes each
   member via `_expr_self_width(part, ctx)` before evaluating it, a
   member array whose true width ISN'T coincidentally 32 bits got
   zero-extended to (or truncated from) 32 bits before being concatenated
   -- corrupting the whole aggregate. (The earlier issue-#7a regression
   tests all happened to use exactly 32-bit arrays, which is why this
   didn't surface then.)

3. `_concat_nba_accumulate` (sim/executor.py) and `_emit_concat_lhs`
   (sim/compiled/_stmt_emitters.py) both had no case for a whole-array
   (memory-backed) bare-Identifier CONCATENATION-LHS member --
   `{tuser, tlast, arr} <= wide_in;` (reference engine: silently created a
   scalar `ctx._signals["arr"]` shadow entry that happened to read back
   correctly for a WHOLE read but left `ctx._memories["arr"]` -- what
   `arr[i]` actually reads -- untouched forever; compiled engine: silently
   emitted no write at all, leaving `arr` permanently X).
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


class TestCasexSelectorMemberMask:
    def test_bare_2d_array_casex_selector_wildcards_x_bits(self, tmp_path):
        """`casex (sel)` with `sel` a bare 2-D packed array containing a
        genuinely-X element must still wildcard-match against it -- only
        the compiled engine was affected."""
        design = _parse(
            """
            module top (
                input logic [1:0] sel_lo,
                output logic matched
            );
            logic [1:0][1:0] sel;
            assign sel[0] = sel_lo;
            // sel[1] left undriven -> X
            always_comb begin
                matched = 1'b0;
                casex (sel)
                    4'b01xx: matched = 1'b1;
                    default: matched = 1'b0;
                endcase
            end
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        for engine in ENGINES:
            sim = Simulator(mod, engine=engine, design=design)
            sim.drive("sel_lo", 0)
            sim.run(max_time=0)
            assert int(sim.signal("matched").value) == 1, engine


class TestConcatenationMemberSelfWidth:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_non_32_bit_array_concat_member_not_corrupted(self, engine, tmp_path):
        """A 24-bit (not coincidentally 32-bit) 2-D packed array used as a
        bare `Concatenation` member must be sized to its OWN true width,
        not a hardcoded 32."""
        design = _parse(
            """
            module top (
                output logic [25:0] probe
            );
            logic [2:0][7:0] arr;
            logic tuser, tlast;
            assign arr[0] = 8'h11;
            assign arr[1] = 8'h22;
            assign arr[2] = 8'h33;
            assign tuser = 1'b1;
            assign tlast = 1'b0;
            assign probe = {tuser, tlast, arr};
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.run(max_time=0)
        expected = (1 << 25) | (0 << 24) | 0x332211
        assert int(sim.signal("probe").value) == expected


class TestConcatenationLHSMemoryMember:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_nba_concat_lhs_memory_member(self, engine, tmp_path):
        """`{tuser, tlast, arr} <= wide_in;` (NBA) with `arr` a 2-D packed
        array -- must correctly update both the whole-array read AND every
        individual `arr[i]` element read."""
        design = _parse(
            """
            module top (
                input logic clk,
                input logic [33:0] wide_in,
                output logic tuser,
                output logic tlast,
                output logic [3:0][7:0] arr
            );
            always_ff @(posedge clk) begin
                {tuser, tlast, arr} <= wide_in;
            end
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        wide = (1 << 33) | (0 << 32) | 0x44332211
        sim.drive("wide_in", wide)
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        assert int(sim.signal("tuser").value) == 1
        assert int(sim.signal("tlast").value) == 0
        assert int(sim.signal("arr").value) == 0x44332211
        assert int(sim.signal("arr[0]").value) == 0x11
        assert int(sim.signal("arr[1]").value) == 0x22
        assert int(sim.signal("arr[2]").value) == 0x33
        assert int(sim.signal("arr[3]").value) == 0x44

    @pytest.mark.parametrize("engine", ENGINES)
    def test_blocking_concat_lhs_memory_member(self, engine, tmp_path):
        """Same shape, blocking (`=`) instead of non-blocking -- already
        worked before this audit, kept as a same-file cross-check."""
        design = _parse(
            """
            module top (
                input logic [33:0] wide_in,
                output logic tuser,
                output logic tlast,
                output logic [3:0][7:0] arr
            );
            always_comb begin
                {tuser, tlast, arr} = wide_in;
            end
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        wide = (1 << 33) | (0 << 32) | 0x44332211
        sim.drive("wide_in", wide)
        sim.run(max_time=0)
        assert int(sim.signal("arr").value) == 0x44332211
        assert int(sim.signal("arr[0]").value) == 0x11

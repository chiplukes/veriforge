"""Regression tests for a 2-D packed array used as a bare `Concatenation`
operand (`{tuser, tlast, arr}`) getting corrupted -- lane order reversed, or
(in the compiled engine specifically) read back as all-zero.

Two distinct root causes, both fixed together:

1. `expand_array_concat_operands` (sim/elaborate.py), added to support the
   streaming-concatenation operator's unpacked-array operands
   (`{>>{a, b}}` -- see test_streaming_concatenation.py), ran on EVERY
   `Concatenation` node unconditionally -- including an ordinary, directly-
   written `{tuser, tlast, arr}` that never went through streaming
   desugaring at all. For a genuinely unpacked SV array operand (the only
   place this expansion is legal/required, per IEEE 1800-2017 SS11.4.14.1),
   ascending-index-first is correct. But a 2-D PACKED array is legal
   directly in plain `{}` concatenation too, where it must be read as its
   own ordinary whole-array bit-vector value (index N-1 is the MSB-most
   slice, index 0 the LSB-most, matching every other packed-array
   convention already used throughout this codebase) -- expanding it
   ascending-index-first there put index 0 at the MSB and index N-1 at the
   LSB, i.e. exactly reversed. Fixed by only expanding a `Concatenation`
   when it's actually streaming-derived (`Concatenation.from_streaming`,
   set only by `_build_streaming_concatenation` for the no-slice-size
   `{>>{...}}` case) or a `StreamingConcatenation` node (`<<`, which only
   ever represents genuine streaming) -- an ordinary `{a, b, arr}` is left
   completely untouched, so `arr` is read via the SAME already-correct
   whole-array-read machinery any other bare-array-Identifier context
   uses.

2. Once (1) was fixed, the COMPILED engine's scalar/narrow expression
   emitter (`_emit_expr`/`_emit_py_expr`/`_emit_py_mask_expr`/
   `_emit_mask_expr`, sim/compiled/_expr_emitter.py) turned out to have
   never had a whole-memory-read fallback added to their `Identifier`
   case at all (unlike `_expr_width`, and unlike `_wide_emitter.py`'s
   equivalent wide-path emitter) -- a bare memory-backed Identifier
   reaching any of these four functions silently fell through to
   `return "0"`/`return None`. This was previously masked entirely by (1)
   always pre-expanding array Concatenation members into per-element
   `BitSelect`s before either of these emitters ever saw a bare array
   Identifier; it's a strictly more general gap (reproduces even for a
   plain `assign flat = arr;`, no concatenation at all) that (1) simply
   never exercised until now. Fixed by adding the same
   synthesize-a-Concatenation-of-BitSelects-and-recurse fallback already
   used by `_expr_width`/`_wide_emitter.py`.
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


_ONE_HOP_SRC = """
module leaf_producer (
    output logic [3:0][7:0] arr
);
assign arr[0] = 8'h11;
assign arr[1] = 8'h22;
assign arr[2] = 8'h33;
assign arr[3] = 8'h44;
endmodule

module top (
    output logic [33:0] probe
);
logic [3:0][7:0] arr;
logic tuser, tlast;
assign tuser = 1'b1;
assign tlast = 1'b0;

leaf_producer u_prod (.arr(arr));

wire [33:0] packed_bus = {tuser, tlast, arr};
assign probe = packed_bus;
endmodule
"""

_TWO_HOP_COMB_SRC = """
module grandparent (
    output logic [3:0][7:0] arr
);
integer i;
always_comb begin
    for (i = 0; i < 4; i = i + 1) begin
        arr[i] = (i + 1) * 8'h11;
    end
end
endmodule

module parent (
    output logic [3:0][7:0] arr
);
grandparent u_gp (.arr(arr));
endmodule

module top (
    output logic [33:0] probe
);
logic [3:0][7:0] arr;
logic tuser, tlast;
assign tuser = 1'b1;
assign tlast = 1'b0;

parent u_parent (.arr(arr));

wire [33:0] packed_bus = {tuser, tlast, arr};
assign probe = packed_bus;
endmodule
"""

_BARE_ARRAY_NO_CONCAT_SRC = """
module top (
    output logic [31:0] flat
);
logic [3:0][7:0] arr;
assign arr[0] = 8'h11;
assign arr[1] = 8'h22;
assign arr[2] = 8'h33;
assign arr[3] = 8'h44;
assign flat = arr;
endmodule
"""

_EXPECTED_ARR = 0x44332211
_EXPECTED_PROBE = (1 << 33) | _EXPECTED_ARR  # tuser=1, tlast=0, arr


class TestTwoDArrayConcatOperand:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_array_plus_scalars_one_hop_crossing(self, engine, tmp_path):
        """`arr` is a plain port of one child instance before being
        concatenated with scalar control bits -- must not reverse lane
        order."""
        design = _parse(_ONE_HOP_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.run(max_time=0)
        assert int(sim.signal("probe").value) == _EXPECTED_PROBE

    @pytest.mark.parametrize("engine", ENGINES)
    def test_array_plus_scalars_two_hop_crossing_from_comb_loop(self, engine, tmp_path):
        """`arr` originates from an `always_comb` for-loop two module
        boundaries up before being concatenated -- must not broadcast the
        last lane's value to every lane."""
        design = _parse(_TWO_HOP_COMB_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.run(max_time=0)
        assert int(sim.signal("probe").value) == _EXPECTED_PROBE

    @pytest.mark.parametrize("engine", ENGINES)
    def test_bare_array_identifier_no_concatenation(self, engine, tmp_path):
        """The narrower, concatenation-independent gap: a bare 2-D packed
        array read directly (`assign flat = arr;`, no concat at all) --
        this alone used to read back all-zero on the compiled engine."""
        design = _parse(_BARE_ARRAY_NO_CONCAT_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.run(max_time=0)
        assert int(sim.signal("flat").value) == _EXPECTED_ARR

    def test_streaming_unpacked_array_expansion_still_ascending(self, tmp_path):
        """Regression guard: the `from_streaming` gating must not break
        the ALREADY-correct ascending-index expansion for a genuine
        streaming context (`{>>{...}}`, unpacked array operand) -- see
        test_streaming_concatenation.py for the full suite; this is a
        narrow duplicate check tied directly to this fix."""
        design = _parse(
            """
            module dut(input logic [7:0] a [2:0], output logic [23:0] flat);
            assign flat = {>>{a}};
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("dut")
        sim = Simulator(mod, engine="reference", design=design)
        sim.drive("a[0]", 0x11)
        sim.drive("a[1]", 0x22)
        sim.drive("a[2]", 0x33)
        sim.run(max_time=0)
        assert int(sim.signal("flat").value) == 0x112233

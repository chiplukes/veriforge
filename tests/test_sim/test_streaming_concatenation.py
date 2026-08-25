"""Regression tests for the ``>>`` (right-stream) side of the SystemVerilog
streaming concatenation operator (IEEE 1800-2017 SS11.4.14.1):
``{>>[slice_size]{a, b, ...}}``.

``>>`` (with or without an explicit slice_size) is defined by the standard
to never reorder anything -- it's always identical to plain concatenation
``{...}``. The only reason SV requires the streaming syntax at all is that
plain ``{}`` concatenation syntactically forbids unpacked-array operands,
while the streaming form accepts them (each unpacked-array operand streams
out element-by-element, ascending index order, before being concatenated
with the rest). This is implemented in two pieces:

1. ``_build_streaming_concatenation`` (transforms/_expressions.py) parses
   ``{>>[slice_size]{...}}`` straight into an ordinary ``Concatenation``
   node, discarding the slice_size.
2. ``expand_array_concat_operands`` (sim/elaborate.py), run unconditionally
   for every simulation right alongside ``materialize_process_locals``,
   expands any ``Concatenation`` part that's a bare unpacked-array
   identifier into that array's individual elements -- so by the time any
   engine registers signals, every ``Concatenation.parts`` list is
   already fully scalar and no engine needs its own array-operand
   handling.

``{<<[slice_size]{...}}`` (the "left" stream -- genuine bit/chunk-level
reversal) is covered separately in
``test_streaming_concatenation_reversal.py``.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


class TestStreamingConcatenationParsing:
    def test_right_stream_desugars_to_concatenation(self):
        from veriforge.model.expressions import Concatenation

        mod = _parse("""
            module dut(input logic [17:0] a [3:0], input logic [17:0] b [3:0],
                       output logic [8*18-1:0] wide);
            assign wide = {>>{a, b}};
            endmodule
        """)
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.rhs, Concatenation)

    def test_left_stream_builds_streaming_concatenation_node(self):
        """`{<<{...}}` now builds a dedicated node instead of raising --
        see test_streaming_concatenation_reversal.py for its semantics."""
        from veriforge.model.expressions import StreamingConcatenation

        mod = _parse("""
            module dut(input logic [17:0] a [3:0], output logic [4*18-1:0] wide);
            assign wide = {<<{a}};
            endmodule
        """)
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.rhs, StreamingConcatenation)

    def test_right_stream_with_explicit_slice_size_desugars_to_concatenation(self):
        """`{>>N{...}}` (explicit slice_size) is still a no-op -- slice
        size never reorders anything for `>>`."""
        from veriforge.model.expressions import Concatenation

        mod = _parse("""
            module dut(input logic [7:0] x, input logic [7:0] y, output logic [15:0] z);
            assign z = {>>8{x, y}};
            endmodule
        """)
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.rhs, Concatenation)

    def test_scalar_operands_parse_and_desugar(self):
        from veriforge.model.expressions import Concatenation

        mod = _parse("""
            module dut(input logic [7:0] x, input logic [7:0] y, output logic [15:0] z);
            assign z = {>>{x, y}};
            endmodule
        """)
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.rhs, Concatenation)


class TestStreamingConcatenationSimulation:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_scalar_operands_matches_plain_concatenation(self, engine):
        """{>>{x, y}} with scalar operands must behave exactly like {x, y}."""
        mod = _parse("""
            module dut(input logic [7:0] x, input logic [7:0] y, output logic [15:0] z);
            assign z = {>>{x, y}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("x", 0xAB)
        sim.drive("y", 0xCD)
        sim.run(max_time=0)
        assert int(sim.signal("z").value) == 0xABCD

    @pytest.mark.parametrize("engine", ENGINES)
    def test_scalar_operands_with_explicit_slice_size_matches_plain_concatenation(self, engine):
        """`{>>N{x, y}}` with an explicit slice_size must still behave
        exactly like `{x, y}` -- slice size is a no-op for `>>`."""
        mod = _parse("""
            module dut(input logic [7:0] x, input logic [7:0] y, output logic [15:0] z);
            assign z = {>>4{x, y}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("x", 0xAB)
        sim.drive("y", 0xCD)
        sim.run(max_time=0)
        assert int(sim.signal("z").value) == 0xABCD

    @pytest.mark.parametrize("engine", ENGINES)
    def test_unpacked_array_operands_stream_ascending_index_order(self, engine):
        """The bug report's exact shape: two 4-element unpacked arrays of
        18-bit lanes, streamed (no slice size) into a flat packed target.
        Verified against the report's own independently-derived expected
        value: element 0 of the first-listed operand lands closest to the
        target's MSB, ascending index order, operand order preserved."""
        mod = _parse("""
            module dut(
                input logic [17:0] a [3:0],
                input logic [17:0] b [3:0],
                output logic [8*18-1:0] wide
            );
            assign wide = {>>{a, b}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        a_vals = [0x11111, 0x22222, 0x33333, 0x44444]
        b_vals = [0x55555, 0x66666, 0x77777, 0x88888]
        for i, v in enumerate(a_vals):
            sim.drive(f"a[{i}]", v)
        for i, v in enumerate(b_vals):
            sim.drive(f"b[{i}]", v)
        sim.run(max_time=0)

        expected = 0
        for v in [*a_vals, *b_vals]:
            expected = (expected << 18) | (v & ((1 << 18) - 1))
        assert int(sim.signal("wide").value) == expected

    @pytest.mark.parametrize("engine", ENGINES)
    def test_single_array_operand_matches_manual_concatenation(self, engine):
        """A single streamed array must equal manually concatenating its
        own elements in ascending index order (independent cross-check of
        the same claim as the two-operand test above, via a completely
        different code path -- ordinary indexed concatenation)."""
        streamed_mod = _parse("""
            module dut(input logic [7:0] a [2:0], output logic [23:0] flat);
            assign flat = {>>{a}};
            endmodule
        """)
        manual_mod = _parse("""
            module dut(input logic [7:0] a [2:0], output logic [23:0] flat);
            assign flat = {a[0], a[1], a[2]};
            endmodule
        """)
        vals = [0x11, 0x22, 0x33]
        results = {}
        for tag, mod in (("streamed", streamed_mod), ("manual", manual_mod)):
            sim = Simulator(mod, engine=engine)
            for i, v in enumerate(vals):
                sim.drive(f"a[{i}]", v)
            sim.run(max_time=0)
            results[tag] = int(sim.signal("flat").value)
        assert results["streamed"] == results["manual"]

    @pytest.mark.parametrize("engine", ENGINES)
    def test_truncation_to_narrower_lhs_matches_plain_concat_truncation(self, engine):
        """A too-wide streamed result assigned to a narrower LHS truncates
        the same way plain concatenation would (keeps the low bits)."""
        mod = _parse("""
            module dut(
                input logic [17:0] a [3:0],
                input logic [17:0] b [3:0],
                output logic [4*18-1:0] narrow
            );
            assign narrow = {>>{a, b}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        a_vals = [0x11111, 0x22222, 0x33333, 0x44444]
        b_vals = [0x55555, 0x66666, 0x77777, 0x88888]
        for i, v in enumerate(a_vals):
            sim.drive(f"a[{i}]", v)
        for i, v in enumerate(b_vals):
            sim.drive(f"b[{i}]", v)
        sim.run(max_time=0)

        full = 0
        for v in [*a_vals, *b_vals]:
            full = (full << 18) | (v & ((1 << 18) - 1))
        assert int(sim.signal("narrow").value) == full & ((1 << 72) - 1)

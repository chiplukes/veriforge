"""Regression tests for the `<<` (left-stream) side of the SystemVerilog
streaming concatenation operator (IEEE 1800-2017 SS11.4.14.1):
``{<<[slice_size]{a, b, ...}}`` -- genuine bit/chunk-level reversal, not
just element reordering (see test_streaming_concatenation.py for the `>>`
side, which is always a no-op).

Semantics: build the ordinary concatenation of parts (after unpacked-array
expansion), call its bit vector ``full``, width ``W``. Split ``full``'s
bits into consecutive ``slice_size``-bit chunks starting from the MSB end
(the last, LSB-most chunk may be narrower if ``W % slice_size != 0``), then
reverse chunk order (each chunk's own bit order is preserved). No slice
size defaults to 1, i.e. full bit reversal.

Implemented via a dedicated ``StreamingConcatenation`` AST node (built in
``_build_streaming_concatenation``, transforms/_expressions.py) that
survives to each engine:

- reference/vm: ``Value.stream_reverse()`` (sim/value.py), a pure
  bit-vector chunk-reversal op reused by both (vm's bytecode interpreter
  and vm-fast's Cython interpreter both operate on ``Value``/word-array
  representations at runtime).
- compiled (scalar, <=64-bit total width): desugared at codegen time into
  a plain ``Concatenation`` of ``RangeSelect``s over a synthetic inner
  ``Concatenation(parts=expr.parts)`` (`_stream_reverse_synthetic` in
  sim/compiled/_expr_emitter.py).
- compiled (wide, >64-bit total width): re-chunked via the same
  ``wide_slice_extract``/``wide_shl``/``wide_or`` primitives ordinary wide
  ``Concatenation`` assembly uses (sim/compiled/_wide_emitter.py).

``slice_size`` must be a compile-time constant (parameter or numeric
literal) on every engine; `vm`/`vm-fast`/`compiled` additionally restrict
it to <= 64 (a chunk itself never spans more than one 64-bit word in
their fixed-width scratch representations) -- `reference` has no such
limit, since `Value` is arbitrary-precision.
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


def _bit_reverse(val: int, nbits: int) -> int:
    return int(bin(val & ((1 << nbits) - 1))[2:].zfill(nbits)[::-1], 2)


def _stream_reverse(val: int, total_w: int, slice_size: int) -> int:
    """Independent reference implementation (mirrors Value.stream_reverse)."""
    val &= (1 << total_w) - 1
    chunks = []
    pos = total_w
    while pos > 0:
        lo = max(0, pos - slice_size)
        w = pos - lo
        chunks.append(((val >> lo) & ((1 << w) - 1), w))
        pos = lo
    result = 0
    for chunk, w in reversed(chunks):
        result = (result << w) | chunk
    return result


class TestStreamingConcatenationReversalParsing:
    def test_left_stream_no_slice_size_builds_streaming_concatenation(self):
        from veriforge.model.expressions import StreamingConcatenation

        mod = _parse("""
            module dut(input logic [15:0] a, output logic [15:0] y);
            assign y = {<<{a}};
            endmodule
        """)
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.rhs, StreamingConcatenation)
        assert ca.rhs.slice_size is None

    def test_left_stream_with_slice_size_captures_it(self):
        from veriforge.model.expressions import Literal, StreamingConcatenation

        mod = _parse("""
            module dut(input logic [31:0] a, output logic [31:0] y);
            assign y = {<<8{a}};
            endmodule
        """)
        ca = mod.continuous_assigns[0]
        assert isinstance(ca.rhs, StreamingConcatenation)
        assert isinstance(ca.rhs.slice_size, Literal)
        assert int(ca.rhs.slice_size.value) == 8


class TestStreamingConcatenationReversalSimulation:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_full_bit_reversal_single_operand(self, engine):
        """`{<<{a}}` (no slice size) is full bit reversal."""
        mod = _parse("""
            module dut(input logic [15:0] a, output logic [15:0] y);
            assign y = {<<{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("a", 0b1100_0000_0000_0011)
        sim.run(max_time=0)
        assert int(sim.signal("y").value) == _bit_reverse(0b1100_0000_0000_0011, 16)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_byte_swap_idiom(self, engine):
        """`{<<8{a}}` is the classic byte-swap idiom."""
        mod = _parse("""
            module dut(input logic [31:0] a, output logic [31:0] y);
            assign y = {<<8{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("a", 0x12345678)
        sim.run(max_time=0)
        assert int(sim.signal("y").value) == 0x78563412

    @pytest.mark.parametrize("engine", ENGINES)
    def test_two_differently_sized_operands(self, engine):
        """Chunks must straddle operand boundaries correctly, not just
        reorder whole operands."""
        mod = _parse("""
            module dut(input logic [11:0] a, input logic [19:0] b, output logic [31:0] y);
            assign y = {<<8{a, b}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        a_val = 0xABC
        b_val = 0x12345
        sim.drive("a", a_val)
        sim.drive("b", b_val)
        sim.run(max_time=0)
        full = (a_val << 20) | b_val
        assert int(sim.signal("y").value) == _stream_reverse(full, 32, 8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_slice_size_not_evenly_dividing_width(self, engine):
        """20-bit total, slice_size 8: two full 8-bit chunks plus one
        partial 4-bit chunk -- exercises the partial-chunk boundary."""
        mod = _parse("""
            module dut(input logic [19:0] a, output logic [19:0] y);
            assign y = {<<8{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        val = 0xABCDE
        sim.drive("a", val)
        sim.run(max_time=0)
        assert int(sim.signal("y").value) == _stream_reverse(val, 20, 8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wide_total_width_byte_swap(self, engine):
        """>64-bit total width, exercising the compiled engine's wide
        scratch-buffer path (and vm-fast's word-array path)."""
        mod = _parse("""
            module dut(input logic [95:0] a, output logic [95:0] y);
            assign y = {<<8{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        val = 0x0102030405060708090A0B0C
        sim.drive("a", val)
        sim.run(max_time=0)
        assert int(sim.signal("y").value) == _stream_reverse(val, 96, 8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wide_total_width_full_bit_reversal(self, engine):
        """>64-bit total width, no slice size (full bit reversal)."""
        mod = _parse("""
            module dut(input logic [95:0] a, output logic [95:0] y);
            assign y = {<<{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        val = 0x0102030405060708090A0B0C
        sim.drive("a", val)
        sim.run(max_time=0)
        assert int(sim.signal("y").value) == _bit_reverse(val, 96)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_unpacked_array_operand_composes_with_reversal(self, engine):
        """An unpacked-array operand inside `{<<{...}}` must first expand
        to its elements (expand_array_concat_operands), THEN reverse --
        composes the two already-tested pieces."""
        mod = _parse("""
            module dut(input logic [7:0] a [3:0], output logic [31:0] y);
            assign y = {<<8{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        vals = [0x11, 0x22, 0x33, 0x44]
        for i, v in enumerate(vals):
            sim.drive(f"a[{i}]", v)
        sim.run(max_time=0)
        full = 0
        for v in vals:
            full = (full << 8) | v
        assert int(sim.signal("y").value) == _stream_reverse(full, 32, 8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_slice_size_from_parameter(self, engine):
        """slice_size may be a parameter reference, not just a literal."""
        mod = _parse("""
            module dut #(parameter SZ = 8) (input logic [31:0] a, output logic [31:0] y);
            assign y = {<<SZ{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("a", 0x12345678)
        sim.run(max_time=0)
        assert int(sim.signal("y").value) == 0x78563412


class TestStreamingConcatenationReversalSensitivity:
    """Regression for a real bug found via the grammar-driven fuzzer's own
    streaming-concat generation (`notes/roadmap.md`, "the fuzzing round's
    headline finding"): a signal referenced ONLY inside a
    `StreamingConcatenation` was invisible to sensitivity/dependency
    analysis on `reference`/`vm`/`vm-fast` (`sim/scheduler.py`'s
    `_walk_expr_reads` and `sim/vm/compiler.py`'s `_walk_expr_signals` both
    had no case for it -- the exact same gap class already fixed once for
    `AssignmentPattern`, just never extended to this later-added node).
    `compiled` was unaffected (it collects signal references via a generic
    reflective walk, not a hand-maintained per-node-type dispatch).

    This is invisible to every OTHER test in this file: they all drive
    once and settle via `sim.run(max_time=0)`, which -- unlike calling
    `sim.settle()` directly -- always performs at least one full,
    sensitivity-independent pass (`settle()`'s own docstring: "`run()`
    already does the equivalent unconditionally on every call"). A
    continuous assign has no such protection even on its FIRST `settle()`
    call; a combinational `always @(*)` block gets a one-time bootstrap
    pass on its first `settle()` only, so reproducing the bug there needs
    TWO drive-then-settle cycles, not one. Both shapes are covered below.
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_continuous_assign_rereads_after_settle(self, engine):
        """`assign y = {<<{a}};` must reflect a *newly driven* `a` -- not
        just correctly evaluate once at elaboration/first bootstrap time.
        """
        mod = _parse("""
            module dut(input logic [15:0] a, output logic [15:0] y);
            assign y = {<<{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("a", 0x1234)
        sim.settle()
        assert int(sim.signal("y").value) == _bit_reverse(0x1234, 16)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_combinational_always_rereads_after_second_settle(self, engine):
        """`always @(*) y = {<<{a}};` must re-fire on a SECOND drive+settle
        cycle, not just the first (which a one-time bootstrap pass makes
        correct regardless of the sensitivity-tracking bug this guards
        against -- confirmed directly: this test's first assertion alone
        passes even without the fix; only the second one catches it).
        """
        mod = _parse("""
            module dut(input logic [15:0] a, output logic [15:0] y);
            always @(*) y = {<<{a}};
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        sim.drive("a", 0x1234)
        sim.settle()
        assert int(sim.signal("y").value) == _bit_reverse(0x1234, 16)
        sim.drive("a", 0x5678)
        sim.settle()
        assert int(sim.signal("y").value) == _bit_reverse(0x5678, 16)


class TestStreamingConcatenationReversalErrors:
    def test_simple_type_slice_size_is_not_supported(self):
        """`{<<byte{...}}` (simple_type slice_size) is out of scope --
        only constant_expression slice sizes are supported. The type name
        misparses as an ordinary (unresolvable) identifier expression."""
        mod = _parse("""
            module dut(input logic [31:0] a, output logic [31:0] y);
            assign y = {<<byte{a}};
            endmodule
        """)
        with pytest.raises(Exception):  # noqa: B017, PT011
            sim = Simulator(mod, engine="reference")
            sim.drive("a", 0x12345678)
            sim.run(max_time=0)


class TestStreamingConcatenationVmFastWideCapacity:
    """`vm`/`vm-fast` share one compiled bytecode whose wide (>64-bit)
    values -- signals, constants, AND intermediate stack values alike --
    are stored in a fixed `_VM_FAST_WIDE_WORDS`-word slot (`sim/vm/
    compiler.py`; kept in sync with `DEF WIDE_WORDS` in `_interp_fast.pyx`
    and `_WIDE_WORDS` in `vm_scheduler.py`).

    A streaming concatenation's PRE-reversal combined width is the sum of
    all its parts -- unlike a plain `Concatenation`, which the compiler can
    narrow to a smaller destination width up front, a chunk-reversal needs
    the full stream materialized before any truncation, so that combined
    width can exceed the fixed capacity even when every individual operand
    and the final destination are both comfortably narrow. Found via the
    grammar-driven fuzzer (`notes/roadmap.md`): two real fuzzer-generated
    modules landed at 397 and 385 combined bits, just over the
    then-current 384-bit (6-word) cap, and silently returned wrong
    (frequently all-zero) results on `vm-fast` only -- `reference`/`vm`
    have no such limit and computed them correctly. Fixed two ways: the
    cap was raised a modest amount (6 -> 8 words, 384 -> 512 bits) to
    correctly compute both of the fuzzer's own found cases outright, AND
    exceeding even the new cap now raises a clear `NotImplementedError` at
    compile time (matching the existing `slice_size > 64` guard) instead
    of silently corrupting -- raising the cap by a large amount instead
    was deliberately rejected: it's a fixed per-value allocation applied
    to every wide signal/constant/stack-slot in every vm-fast design, not
    just ones using streaming concatenation.
    """

    @pytest.mark.parametrize("engine", ("vm", "vm-fast"))
    def test_combined_width_just_under_cap_computes_correctly(self, engine):
        """397 bits (three ~128-bit-class operands, one nested) -- over the
        OLD 384-bit cap, under the current 512-bit one. Exact shape from a
        real fuzzer-found mismatch, reduced.
        """
        mod = _parse("""
            module dut(input logic [7:0] i3, input logic [127:0] i1,
                       input logic [63:0] i2, input logic [127:0] i4,
                       output logic [33:0] o);
            assign o = {<<{i3, i1, {i4, i4, i2}}};
            endmodule
        """)
        i3, i1, i2, i4 = (
            0xAB,
            0x0123456789ABCDEF0123456789ABCDEF,
            0xFEDCBA9876543210,
            0xAAAABBBBCCCCDDDDEEEEFFFF11112222,
        )
        full = (i3 << (128 + 128 + 128 + 64)) | (i1 << (128 + 128 + 64)) | (i4 << (128 + 64)) | (i4 << 64) | i2
        expected = _stream_reverse(full, 8 + 128 + 128 + 128 + 64, 1) & ((1 << 34) - 1)

        sim = Simulator(mod, engine=engine)
        sim.drive("i3", i3)
        sim.drive("i1", i1)
        sim.drive("i2", i2)
        sim.drive("i4", i4)
        sim.run(max_time=0)
        assert int(sim.signal("o").value) == expected

    @pytest.mark.parametrize("engine", ("vm", "vm-fast"))
    def test_combined_width_over_cap_raises_clearly(self, engine):
        """Comfortably over even the current (raised) cap -- must raise a
        clear NotImplementedError at compile time, never silently corrupt.
        """
        mod = _parse("""
            module dut(input logic [127:0] a, input logic [127:0] b,
                       input logic [127:0] c, input logic [127:0] d,
                       input logic [127:0] e, output logic [33:0] o);
            assign o = {<<{a, b, c, d, e}};
            endmodule
        """)
        with pytest.raises(NotImplementedError, match="exceeds vm-fast's fixed wide-value capacity"):
            Simulator(mod, engine=engine)

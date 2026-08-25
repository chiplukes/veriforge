"""Regression tests for whole-vector (unindexed) read/write of a 2-D
packed-array signal/port (`logic [3:0][17:0] foo`).

Internally, a 2-D packed array (or any net/var/port declared with a
`dimensions` array) is represented as a memory (per-element storage,
supporting `foo[i]` indexing). But from the outside it's also a single,
ordinary `depth*elem_width`-bit vector -- `assign wide = foo;`, a
testbench reading/writing `foo` as a whole, or `sim.signal("foo").width`
all need to see it that way. Previously, every "whole array, no index"
code path silently treated it as a phantom/fallback 1-bit value instead:

- `sim.signal("foo").width` reported 1, not `depth*elem_width`.
- Reading/writing `foo` as a whole scalar produced/discarded almost all
  of the real data.
- Compiled RTL (`assign wide = foo;`, or `assign foo = bar;` where both
  sides are whole 2-D arrays) either silently used a fabricated 1-bit
  signal, evaluated to a constant 0, or — for the compiled engine's LHS
  case — was dropped from codegen entirely, leaving the destination
  stuck at X forever.

Fixed across every engine at the two levels each needed it: the external
read_signal/drive_signal API (`sim/evaluator.py`'s `EvalContext`,
`sim/vm/vm_scheduler.py`'s `VMScheduler`, `sim/compiled/
compiled_scheduler.py`'s `CompiledScheduler`), and RTL codegen/
compilation for an actual `assign` (`sim/evaluator.py`'s hot-path
Identifier case for the reference engine; `sim/vm/compiler.py`'s
`_expr_width`/`_compile_expr`/`_compile_store_lhs` for the VM engine;
`sim/compiled/_expr_emitter.py`'s `_expr_width`, `sim/compiled/
_wide_emitter.py`'s `_emit_wide_expr_to_scratch`, and `sim/compiled/
_process_compiler.py`'s continuous-assign LHS dispatch for the compiled
engine). Per standard packed-array bit layout, the highest Verilog index
(`depth-1`) is the flat vector's MSB-most `elem_width`-bit slice and
index 0 is its LSB-most slice.
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


class TestWholeArraySignalAccess:
    _COPY_SRC = """
    module dut (
        input  logic [3:0][17:0] din,
        output logic [3:0][17:0] dout
    );
    assign dout = din;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_width_is_flat_not_one(self, engine):
        mod = _parse(self._COPY_SRC)
        sim = Simulator(mod, engine=engine)
        assert sim.signal("din").width == 4 * 18

    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_write_then_read_round_trips(self, engine):
        mod = _parse(self._COPY_SRC)
        sim = Simulator(mod, engine=engine)
        pattern = 0x1234_5678_9ABC_DEF1_1111 & ((1 << 72) - 1)
        sim.drive("din", pattern)
        sim.run(max_time=0)
        assert int(sim.signal("din").value) == pattern

    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_copied_through_continuous_assign(self, engine):
        """`assign dout = din;` (both sides whole 2-D packed arrays) must
        propagate the real data end to end, not a fabricated/dropped value."""
        mod = _parse(self._COPY_SRC)
        sim = Simulator(mod, engine=engine)
        pattern = 0x1234_5678_9ABC_DEF1_1111 & ((1 << 72) - 1)
        sim.drive("din", pattern)
        sim.run(max_time=0)
        assert int(sim.signal("dout").value) == pattern

    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_matches_per_lane_indexed_writes(self, engine):
        """Driving each lane individually and reading the whole array back
        must agree with the standard packed-array layout: the highest
        Verilog index is the MSB-most slice, index 0 is the LSB-most."""
        mod = _parse(self._COPY_SRC)
        sim = Simulator(mod, engine=engine)
        lanes = [0x11111, 0x22222, 0x33333, 0x04444]
        for i, v in enumerate(lanes):
            sim.drive(f"din[{i}]", v)
        sim.run(max_time=0)
        expected = 0
        for v in reversed(lanes):
            expected = (expected << 18) | v
        assert int(sim.signal("din").value) == expected

    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_write_then_per_lane_reads_agree(self, engine):
        """The inverse of the above: driving the whole array and reading
        individual lanes back must agree with the same layout."""
        mod = _parse(self._COPY_SRC)
        sim = Simulator(mod, engine=engine)
        lanes = [0x11111, 0x22222, 0x33333, 0x04444]
        pattern = 0
        for v in reversed(lanes):
            pattern = (pattern << 18) | v
        sim.drive("din", pattern)
        sim.run(max_time=0)
        for i, expected in enumerate(lanes):
            assert int(sim.signal(f"din[{i}]").value) == expected, f"lane {i}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_narrow_whole_array_fits_in_one_word(self, engine):
        """Regression guard: a 2-D packed array whose *total* flat width
        is <= 64 bits (unlike the 72-bit case above) must also work --
        this exercises the narrow/fast-path codegen instead of the wide
        (>64-bit) path."""
        mod = _parse("""
            module dut (
                input  logic [1:0][7:0] din,
                output logic [1:0][7:0] dout
            );
            assign dout = din;
            endmodule
        """)
        sim = Simulator(mod, engine=engine)
        assert sim.signal("din").width == 16
        sim.drive("din", 0xABCD)
        sim.run(max_time=0)
        assert int(sim.signal("din").value) == 0xABCD
        assert int(sim.signal("dout").value) == 0xABCD

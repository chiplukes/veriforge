"""Regression test: a dynamically-indexed, >64-bit-wide memory element
whole-write (`mem[idx] = <arbitrary computed expression>;`) crashed the
`compiled` engine at Cython-compile time, and would have silently produced
wrong data for any word beyond the first had it compiled at all.

Found while investigating an unrelated report (a "dynamically-indexed
memory array wider than 32 bits returns corrupted data" claim against
`reference`/`vm`, which did not reproduce in extensive testing -- see
`notes/roadmap.md` for that side of the investigation). Building a >64-bit
memory with a genuinely computed (not just copied/sliced/concatenated)
initializer expression, on the `compiled` engine specifically, surfaced two
real, distinct bugs:

1. `_emit_mem_write`'s whole-element write dispatch
   (`compiled/_stmt_emitters.py`) fell through to
   `_emit_scalar_mem_write_lines` for ANY RHS shape it didn't specifically
   recognize (bare memory-to-memory copy, signal-slice source, flat
   identifier concat, literal zero) -- regardless of the memory's own
   element width. That scalar emitter stores into `c.mem_{mid}_val[idx]`,
   a plain scalar `long long[]` that cannot represent an element wider
   than 64 bits at all, and its own `wmask(elem_w)` call is *also* only
   valid up to 64 bits (`narrow_accessors.pxi`'s `wmask()` caps at
   returning -1 for any width >= 64) -- confirmed to produce a hard Cython
   compile error ("Cannot convert 'SimCtx *' to Python object") for a
   65-bit memory, since the generated masking expression's width no
   longer fit any fixed-size C integer type at all.

2. Fixing (1) by routing through the recursive scratch-space wide
   expression emitter (`_emit_wide_expr_to_scratch`, the same one a wide
   PLAIN SIGNAL assignment already uses) exposed a SECOND, pre-existing
   gap: the "Initial block values" section of `_gen_sections.py` declared
   its temporaries via a hand-maintained whitelist of known scratch-var
   markers, rather than the general `_hoist_inline_cdefs` pass the other
   two process-function generators in the same file already use. Any
   emitter using inline `cdef`s or fixed-size scratch arrays (`_sc{n}_v`/
   `_sc{n}_m`) that this whitelist didn't happen to cover -- exactly the
   case for `_emit_wide_expr_to_scratch`'s own locals when reached from
   inside an `initial`-block `for` loop -- produced "cdef statement not
   allowed here" / "Cannot convert Python object to 'unsigned long long
   *'" compile errors. Fixed by applying the same general hoisting pass
   used elsewhere.

Both fixed in the same commit; verified against `reference` (which was
already correct) across element widths 16 through 128, both a
combinational and a clocked (NBA) whole-element write, and both a
constant-folding-friendly and a genuinely non-trivial (shift/xor/add)
initializer expression.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES


def _parse(src: str, tmp_path):
    path = tmp_path / "dut.v"
    path.write_text(src)
    return parse_file(path)


def _make_src(elem_w: int, depth: int) -> str:
    idx_w = depth.bit_length() - 1
    return f"""
module dut (
    input clk,
    input [{idx_w}:0] idx,
    output reg [{elem_w - 1}:0] data
);
    reg [{elem_w - 1}:0] mem [0:{depth - 1}];
    integer i;
    initial begin
        for (i = 0; i < {depth}; i = i + 1)
            mem[i] = ({elem_w}'h1 << (i % {elem_w})) ^ ({elem_w}'hDEADBEEF_CAFEBABE_1234 + i * {elem_w}'h9E3779B9);
    end
    always @(posedge clk)
        data <= mem[idx];
endmodule
"""


class TestWideMemDynamicWholeElementWrite:
    @pytest.mark.parametrize("elem_w", [65, 96, 128])
    @pytest.mark.parametrize("engine", ENGINES)
    def test_computed_expression_initializer(self, engine, elem_w, tmp_path):
        """A >64-bit memory whose elements are set via a genuinely computed
        (shift/xor/add) expression -- not a bare copy/slice/concat/zero --
        inside an `initial`-block `for` loop, then read back through a
        clocked, dynamically-indexed whole-element read."""
        depth = 16
        idx_w = depth.bit_length() - 1
        design = _parse(_make_src(elem_w, depth), tmp_path)
        top = design.get_module("dut")

        sim = Simulator(top, engine=engine, design=design)
        sim.drive("idx", Value(0, width=idx_w))
        sim.drive("clk", Value(0, width=1))
        sim.run(max_time=0)  # let the initial for-loop populate mem

        golden = {}
        for i in range(depth):
            sim.drive("idx", Value(i, width=idx_w))
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            golden[i] = int(sim.read("data"))

        # Recompute the expected values in Python (mirrors the RTL formula
        # exactly) and compare -- avoids needing a second oracle engine per
        # parametrized case.
        mask = (1 << elem_w) - 1
        for i in range(depth):
            expected = ((1 << (i % elem_w)) ^ (0xDEADBEEFCAFEBABE1234 + i * 0x9E3779B9)) & mask
            assert golden[i] == expected, (
                f"engine={engine} elem_w={elem_w} idx={i}: {hex(golden[i])} != {hex(expected)}"
            )

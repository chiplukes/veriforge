"""Regression test for a fixed-size C-array buffer overflow in the compiled
engine's non-blocking memory-write queues.

`sim/compiled/_gen_sections.py`'s `_gen_constants` used to hardcode
`DEF NBA_MEM_MAX = 64` / `DEF NBA_MEM_RANGE_MAX = 64` -- the queue capacity
for whole-element and partial-bit-range non-blocking memory writes queued
during ONE delta-loop iteration (`c.nba_mem_val[]`/`c.nba_mem_addr[]`/etc.,
fixed-size `cdef` C arrays inside `SimCtx`). A single non-blocking whole-array
copy (`mem <= other;`) pushes one queue entry per element of the target
memory, so any memory with MORE than 64 elements silently overflowed this
fixed-size array -- with no bounds check, corrupting whatever simulator
state happens to follow it in memory (empirically: the `CompiledSim`
extension type's `_snap_v[]`/`_snap_m[]` pre-edge signal snapshot arrays,
`cdef` fields declared right after `ctx` in the class -- confirmed via
real-world feedback on the `axis_pix_correction2` design, where a 128-lane
AXI-Stream FIFO's whole-array NBA copy corrupted an unrelated combinational
FIFO-read-enable signal's snapshotted value mid-iteration, stalling the
entire simulation).

Fixed by sizing both queues from the design's actual memory shapes
(`sum(depth for _, depth in self._mem_info) * 4`, floored at the previous
`64` default) instead of a hardcoded constant.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

DEPTH = 100  # > 64: the old hardcoded queue bound this must exceed.


def _parse(src: str, tmp_path):
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


class TestNbaMemQueueOverflow:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_nba_copy_over_64_elements(self, engine, tmp_path):
        """`mem <= mem_in;` (NBA) with a 100-element memory -- every element,
        not just the first 64, must land correctly, and an unrelated
        combinational signal elaborated alongside the memory must not be
        corrupted by the overflow."""
        design = _parse(
            f"""
            module top (
                input logic clk,
                input logic [7:0] mem_in [{DEPTH}],
                output logic [7:0] mem_out [{DEPTH}],
                input logic other_in,
                output logic other_out
            );
            logic [7:0] mem [{DEPTH}];
            always_ff @(posedge clk) begin
                mem <= mem_in;
            end
            assign mem_out = mem;
            assign other_out = other_in;
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        expected = [(i * 37 + 5) & 0xFF for i in range(DEPTH)]
        for i in range(DEPTH):
            sim.signal(f"mem_in[{i}]").value = expected[i]
        sim.drive("other_in", 1)
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        for i in range(DEPTH):
            got = int(sim.signal(f"mem_out[{i}]").value)
            assert got == expected[i], f"mem_out[{i}]: want {expected[i]}, got {got}"
        assert int(sim.signal("other_out").value) == 1

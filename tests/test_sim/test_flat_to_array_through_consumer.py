"""Regression test for issue #7c: a whole-array flat-to-array conversion
(`assign arr = flat_in;`, the exact shape already fixed for issue #7b) that
appeared to STILL corrupt in a real multi-module pipeline even after
applying the #7b fix, when the converted array was then fed into a further
downstream consumer INSTANCE (not just read back through a plain port).

The original bug report could not isolate a minimal repro for this beyond
"it still breaks in the real pipeline" and worked around it with a
per-element `generate` for-loop instead of a single whole-vector `assign`.
That workaround shape is consistent with (and now explained by) the #7b
root cause: a `generate` loop's per-element assigns each have a `BitSelect`
LHS (`arr[gi] = ...`), never a bare dimensioned `Identifier` LHS -- so they
were never routed through `_inline_logic`'s (now-removed) buggy per-element
expansion in the first place, while a single whole-vector `assign arr =
flat_in;`, once *inlined as a child instance's own logic* (not simulated
standalone), was. This test exercises exactly that shape -- a flat-to-array
conversion, immediately followed by feeding the converted array into a
further child instance -- confirming the #7b fix (see
test_flat_to_array_assign.py) already covers it; no separate fix needed.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """
module fifo_like (
    input logic [3:0][7:0] din,
    output logic [3:0][7:0] dout
);
assign dout = din;
endmodule

module row_correct (
    input  logic [31:0] flat_in,
    output logic [3:0][7:0] arr_out
);
logic [3:0][7:0] arr;
assign arr = flat_in;
fifo_like u_fifo (.din(arr), .dout(arr_out));
endmodule

module top (
    input  logic [31:0] flat_in,
    output logic [3:0][7:0] arr_out
);
row_correct u_rc (.flat_in(flat_in), .arr_out(arr_out));
endmodule
"""


class TestFlatToArrayThroughConsumer:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_flat_to_array_conversion_survives_further_consumer_instance(self, engine, tmp_path):
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("flat_in", 0x44332211)
        sim.run(max_time=0)
        assert int(sim.signal("arr_out").value) == 0x44332211

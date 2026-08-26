"""Regression tests for `assign 2d_array = flat_vector;` corrupting once the
array result is passed straight through a child instance's plain port to a
same-shaped signal one level up (issue #7b), plus its related sub-bug: a
memory array whose ELEMENT type is itself a 2-D packed array.

Two distinct root causes, both fixed together:

1. `_inline_logic` (sim/elaborate.py), used when inlining a submodule's own
   logic into the flat top module during hierarchy flattening, used to
   unconditionally expand any continuous assign whose LHS was a bare
   dimensioned identifier into N per-element assigns
   (`_expand_unpacked_array_elements`/`_expand_unpacked_array_rhs`) --
   correct only for a genuinely unpacked array's ELEMENT-shaped RHS
   (another same-shaped array, or an assignment pattern); its fallback for
   any OTHER RHS shape (`return [copy.deepcopy(rhs) for _ in lhs_parts]`)
   broadcast the RHS UNCHANGED (unsliced) to every single LHS element
   instead of bit-slicing it -- exactly wrong for `assign arr = flat;`
   (`arr` a 2-D packed array, `flat` an ordinary scalar). This expansion
   was entirely redundant even for the cases it got right: the "memory"
   whole-array read/write machinery (`_read_whole_memory`/
   `_write_whole_memory` in evaluator.py, and each engine's own
   equivalent) already reads/writes ANY dimensioned identifier as its own
   ordinary flat bit-vector value correctly with no per-element
   decomposition needed at all. Fixed by simply deep-copying and renaming
   a submodule's continuous assigns unchanged during inlining, same as
   any other assign -- confirmed via a broad regression sweep that this
   expansion pass was never actually load-bearing.

2. A separate, deeper modeling gap, exposed by testing this fix: a
   dimensioned declaration's `dimensions` list can hold BOTH extra PACKED
   dims (from the element's own multi-dim packed declaration, e.g. the
   outer `[3:0]` in `logic [3:0][7:0] mem [3:0]`) and the genuine
   UNPACKED array-depth dim (the trailing `[3:0]` there) -- every engine's
   memory-registration code used `dimensions[0]` (or, in the compiled
   engine, ALL of `dimensions`) as if every entry were a genuine,
   separately-addressable unpacked dimension, silently mistaking the
   packed extra dim for (part of) the depth and dropping the true depth
   dimension (and the packed dims' own width contribution) entirely.
   Fixed by using only the LAST `dimensions` entry as the genuine address
   dimension everywhere (a no-op for the overwhelmingly common single-
   dimension case), with any earlier entries multiplying into the
   element's own width instead -- see `sim/scheduler.py`'s
   `_memory_shape`, `sim/vm/compiler.py`'s `_memory_shape`, and
   `sim/compiled/codegen.py`'s `_memory_shape_layout`.
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


_FLAT_TO_ARRAY_THROUGH_PORT_SRC = """
module child (
    input  logic [31:0] flat_in,
    output logic [3:0][7:0] arr_out
);
assign arr_out = flat_in;
endmodule

module parent (
    input  logic [31:0] flat_in,
    output logic [3:0][7:0] arr_out
);
child u (.flat_in(flat_in), .arr_out(arr_out));
endmodule
"""

_MEM_OF_PACKED_ARRAYS_SRC = """
module top (
    input logic clk,
    input logic we,
    input logic [1:0] addr,
    input logic [31:0] wdata,
    output logic [31:0] rdata
);
logic [3:0][7:0] mem [3:0];
always_ff @(posedge clk) begin
    if (we) mem[addr] <= wdata;
end
assign rdata = mem[addr];
endmodule
"""


class TestFlatToArrayAssignThroughPort:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_flat_to_array_conversion_survives_plain_port_passthrough(self, engine, tmp_path):
        """`assign arr_out = flat_in;` inside a child module, then passed
        straight through a plain port to the parent's own same-shaped
        output -- must round-trip losslessly, not broadcast one byte."""
        design = _parse(_FLAT_TO_ARRAY_THROUGH_PORT_SRC, tmp_path)
        mod = design.get_module("parent")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("flat_in", 0x44332211)
        sim.run(max_time=0)
        assert int(sim.signal("arr_out").value) == 0x44332211

    @pytest.mark.parametrize("engine", ENGINES)
    def test_child_alone_still_correct(self, engine, tmp_path):
        """Regression guard: `child` simulated standalone (never
        instantiated, so `_inline_logic` never runs on it) already worked
        correctly before this fix -- must stay that way."""
        design = _parse(_FLAT_TO_ARRAY_THROUGH_PORT_SRC, tmp_path)
        mod = design.get_module("child")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("flat_in", 0x44332211)
        sim.run(max_time=0)
        assert int(sim.signal("arr_out").value) == 0x44332211


class TestMemoryOfPackedArrayElements:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_array_with_2d_packed_element_round_trips(self, engine, tmp_path):
        """`logic [3:0][7:0] mem [3:0];` -- a memory whose ELEMENT is
        itself a 2-D packed array -- must read back the full 32-bit
        element it was written, not just its lowest byte."""
        design = _parse(_MEM_OF_PACKED_ARRAYS_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("addr", 1)
        sim.drive("wdata", 0x44332211)
        sim.drive("we", 1)
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        sim.drive("we", 0)
        sim.settle()
        assert int(sim.signal("rdata").value) == 0x44332211

    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_array_with_2d_packed_element_multiple_addresses(self, engine, tmp_path):
        """Each address must independently store its own full-width
        element, not alias or collapse into a shared/undersized slot."""
        design = _parse(_MEM_OF_PACKED_ARRAYS_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        writes = {0: 0x11223344, 2: 0xAABBCCDD, 3: 0x01020304}
        for addr, val in writes.items():
            sim.drive("addr", addr)
            sim.drive("wdata", val)
            sim.drive("we", 1)
            sim.drive("clk", 0)
            sim.settle()
            sim.drive("clk", 1)
            sim.settle()
            sim.drive("we", 0)
            sim.settle()
        for addr, expected in writes.items():
            sim.drive("addr", addr)
            sim.settle()
            assert int(sim.signal("rdata").value) == expected, f"addr {addr}"

"""Regression tests for a 2-D packed-array signal driven through a
child-instance port connection (a Design with an inner + outer module,
where the outer wires the inner's 2-D packed-array ports to its own
internal wires).

This was the deepest issue in the original bug report: a wire being (a)
driven by a child instance's matching 2-D-packed-array output port, and
(b) also read elsewhere via constant per-lane indexing. In practice this
turned out to be fully covered by the same fixes as the standalone
whole-array case (`test_whole_array_signal_access.py`) -- once a bare
memory-backed identifier is correctly read/written as a flat vector
end-to-end, an instance port connection (itself lowered to an ordinary
continuous assign between two such identifiers during hierarchy
flattening) needs nothing hierarchy-specific on top.

The compiled engine needed one further fix, exposed by this scenario's
real-world "unpack flat port into a 2-D wire, run it through a child
instance, repack a 2-D wire back into a flat port" wrapper shape (the
exact technique the bug report itself used as its issue-#4 workaround):
`assign flat[(i+1)*W-1 -: W] = mem2d[i];` -- a range-select LHS on a
*wide* (>64-bit) destination fed from a *narrow*-element memory read --
used to silently fall through to code that treated the wide destination
signal as if it were narrow (`c.val[sid]`/`c.mask[sid]`, the wrong
storage for a >64-bit signal), rather than the correct wide-insert
helper.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES


def _parse_design(src: str, tmp_path):
    """Parse *src* via `project.parse_file` (not the bare
    verilog_parser+tree_to_design path) -- this design uses `generate`/
    `genvar` constructs, which need the elaboration `parse_file` applies
    on top of the raw parse tree."""
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


_CHILD_INSTANCE_SRC = """
module dut_inner #(parameter N=4, parameter W=18) (
    input  logic             clk,
    input  logic [N-1:0][W-1:0] din,
    output logic [N-1:0][W-1:0] dout
);
always_ff @(posedge clk) dout <= din;
endmodule

module dut_wrap #(parameter N=4, parameter W=18) (
    input  logic             clk,
    input  logic [N*W-1:0]   din_flat,
    output logic [N*W-1:0]   dout_flat
);
logic [N-1:0][W-1:0] din_2d, dout_2d;
genvar i;
generate
    for (i = 0; i < N; i = i + 1) begin : unpack
        assign din_2d[i] = din_flat[(i+1)*W-1 -: W];
    end
endgenerate
generate
    for (i = 0; i < N; i = i + 1) begin : repack
        assign dout_flat[(i+1)*W-1 -: W] = dout_2d[i];
    end
endgenerate
dut_inner #(.N(N), .W(W)) u_inner (.clk(clk), .din(din_2d), .dout(dout_2d));
endmodule
"""


class TestTwoDArrayThroughChildInstance:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_data_propagates_through_child_instance_registered_port(self, engine, tmp_path):
        """The exact shape from the bug report: a 2-D packed-array wire
        driven by a child instance's matching output port, then read back
        both as a whole (`dout_2d`, internally) and repacked lane-by-lane
        (`dout_flat`) -- must round-trip the real data, not corrupt it."""
        design = _parse_design(_CHILD_INSTANCE_SRC, tmp_path)
        wrap = design.get_module("dut_wrap")
        sim = Simulator(wrap, engine=engine, design=design)
        pattern = 0x1234_5678_9ABC_DEF1_1111 & ((1 << 72) - 1)
        sim.drive("clk", 0)
        sim.drive("din_flat", pattern)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        assert int(sim.signal("dout_flat").value) == pattern

    @pytest.mark.parametrize("engine", ENGINES)
    def test_intermediate_2d_wire_matches_per_lane_reads(self, engine, tmp_path):
        """The child instance's OWN registered copy (`dout_2d`, not yet
        repacked) must also be internally consistent lane-by-lane -- this
        isolates the port-connection/register-copy step from the
        surrounding unpack/repack wrapper logic."""
        design = _parse_design(_CHILD_INSTANCE_SRC, tmp_path)
        wrap = design.get_module("dut_wrap")
        sim = Simulator(wrap, engine=engine, design=design)
        lanes = [0x11111, 0x22222, 0x33333, 0x04444]
        pattern = 0
        for v in reversed(lanes):
            pattern = (pattern << 18) | v
        sim.drive("clk", 0)
        sim.drive("din_flat", pattern)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        for i, expected in enumerate(lanes):
            assert int(sim.signal(f"dout_2d[{i}]").value) == expected, f"lane {i}"


class TestRangeSelectWideDestinationFromNarrowMemoryElement:
    """The compiled-engine-specific fix: `flat[(i+1)*W-1 -: W] = mem[i];`
    where `flat` is a >64-bit destination and `mem`'s element is <=64
    bits."""

    _SRC = """
    module dut (
        input  logic [17:0] mem2 [3:0],
        output logic [71:0] dout_flat
    );
    assign dout_flat[17:0]  = mem2[0];
    assign dout_flat[35:18] = mem2[1];
    assign dout_flat[53:36] = mem2[2];
    assign dout_flat[71:54] = mem2[3];
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_narrow_memory_elements_packed_into_wide_destination(self, engine, tmp_path):
        design = _parse_design(self._SRC, tmp_path)
        mod = design.get_module("dut")
        sim = Simulator(mod, engine=engine, design=design)
        lanes = [0x11111, 0x22222, 0x33333, 0x04444]
        for i, v in enumerate(lanes):
            sim.drive(f"mem2[{i}]", v)
        sim.run(max_time=0)
        expected = 0
        for i, v in enumerate(lanes):
            expected |= (v & 0x3FFFF) << (18 * i)
        assert int(sim.signal("dout_flat").value) == expected

    @pytest.mark.parametrize("engine", ENGINES)
    def test_top_lane_bits_not_truncated(self, engine, tmp_path):
        """Regression guard for the specific symptom found: the topmost
        range-select write (into the highest bits of the wide destination)
        must not silently drop bits -- previously it fell through to code
        that treated the wide destination as a narrow (<=64-bit) signal."""
        design = _parse_design(self._SRC, tmp_path)
        mod = design.get_module("dut")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("mem2[3]", 0x3FFFF)  # all-ones 18-bit value
        for i in range(3):
            sim.drive(f"mem2[{i}]", 0)
        sim.run(max_time=0)
        assert int(sim.signal("dout_flat").value) == (0x3FFFF << 54)


class TestWholeArrayReadOfGenerateLoopWrittenMemory:
    """Regression: `vm`/`vm-fast` — a whole-array continuous-assign READ of
    a memory whose elements are each written by a SEPARATE generate-loop
    child instance (not one instance driving the whole array port at
    once, the shape `TestTwoDArrayThroughChildInstance` above already
    covers) used to read permanently X, on every cycle including the
    first. `reference`/`compiled` were unaffected. Two distinct bugs, both
    in `sim/vm/vm_scheduler.py::VMScheduler.drive_signal`:

    1. The memory-array write branches (both the indexed-element and
       whole-array forms) never added the memory's dirty "marker" signal
       to `_pending_drives` -- `settle()` seeds its delta loop from
       `_pending_drives`, not `interpreter.dirty` (that set only matters
       mid-delta-loop), so `settle()` silently returned immediately
       without propagating the drive through anything reading the memory
       at all.
    2. Once (1) was fixed, driving ANY memory-backed signal on the Cython
       (`vm-fast`) path called `_cy_ctx.sync_mem_from_lists(self.compiler.
       mem_val, self.compiler.mem_mask)` -- copying ALL memories' cells
       from the compiler's OWN Python-side lists, which are only ever
       populated once at elaboration time and never kept in sync with
       whatever `_cy_ctx` has since computed at runtime for OTHER
       memories via its own delta-cycle execution. This clobbered every
       other, already-correctly-computed memory-backed signal back to its
       stale elaboration-time value on every single drive. Fixed by using
       `_cy_ctx.write_mem(mid, idx, val, mask)` for just the elements
       actually being driven, mirroring the existing single-signal
       `write_signal` pattern.

    See `notes/known_issues.md` and `notes/roadmap.md` for the full
    investigation trail.
    """

    _SRC = """
    module leaf (input clk, input [17:0] d, input dv, output [17:0] q);
        logic s1=0; logic [17:0] d1=0;
        logic s2=0; logic [17:0] d2=0;
        always_ff @(posedge clk) begin s1 <= dv; d1 <= d; end
        always_ff @(posedge clk) begin s2 <= s1; d2 <= d1; end
        assign q = d2;
    endmodule

    module top #(parameter N = 4) (input clk, input [N-1:0][17:0] d, input dv, output [N-1:0][17:0] q);
        logic [N-1:0][17:0] gen_q;
        genvar i;
        generate
            for (i = 0; i < N; i++) begin : gen_ch
                leaf u_leaf (.clk(clk), .d(d[i]), .dv(dv), .q(gen_q[i]));
            end
        endgenerate
        assign q = gen_q;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_whole_array_read_tracks_generate_loop_writes(self, engine, tmp_path):
        design = _parse_design(self._SRC, tmp_path)
        top = design.get_module("top")
        sim = Simulator(top, engine=engine, design=design)
        lane_pattern = sum((0x1234 & 0x3FFFF) << (18 * lane) for lane in range(4))
        sim.drive("clk", 0)
        sim.drive("dv", 0)
        sim.drive("d", lane_pattern)  # 0x1234 replicated across all 4 lanes
        sim.settle()

        results = []
        for cyc in range(6):
            sim.drive("dv", 1 if cyc < 2 else 0)
            sim.drive("clk", 1)
            sim.settle()
            sim.drive("clk", 0)
            sim.settle()
            q = sim.signal("q").value
            results.append(None if q.mask else int(q))

        # 2-cycle pipeline delay: the leaf's `d1`/`d2` explicit initial
        # value (0) for cyc 0, then the replicated pattern for every
        # remaining cycle (`dv` deasserting doesn't change `d`'s value,
        # only whether it's a "real" beat -- this test only checks the
        # data path, not validity).
        assert results[0] == 0
        assert results[1:] == [lane_pattern] * 5

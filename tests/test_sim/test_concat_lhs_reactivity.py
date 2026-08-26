"""Regression tests for a concat-target LHS (an assignment or child-instance
port connection whose LHS/output is a `Concatenation`, e.g. `{a, b} = ...` or
`.dout({tuser, tlast})`) never re-triggering downstream readers.

Root cause (reference engine only -- vm/vm-fast/compiled were never
affected): `Scheduler._read_lhs` (sim/scheduler.py), used by both
`_run_continuous_assigns` and `_run_dirty_continuous_assigns` to detect
whether a continuous assign's write actually changed anything, had no
`Concatenation` case and fell through to its final `return None`. With both
the pre-write ``old`` and post-write ``new`` snapshots always `None`, every
caller's `if old is not None and new is not None:` guard silently skipped
change detection for ANY concat-target LHS -- so `_run_dirty_continuous_assigns`
never added the concat's member signals to the dirty set (even though
`_lhs_base_names`, which builds that same member-name set for a different
purpose, already handled `Concatenation` correctly), and no other
continuous assign/always_comb/instance-port-connection reading one of those
members downstream was ever re-triggered once initial elaboration settled.
Confirmed to affect only the driving-a-*new*-value-during-an-already-running
simulation path (`drive()`+`settle()`) -- a one-shot value present from
construction can appear to "work" purely because the *unconditional*
initial-elaboration convergence loop (`_run_continuous_assigns`) still
*writes* the concat members correctly regardless of its own (broken) change
-detection; only downstream *re-triggering* on a later change was broken.

Fixed by giving `_read_lhs` a `Concatenation` case that recurses per-part
(reusing every other already-handled LHS shape, including nested
BitSelect/RangeSelect concat members) and concatenates the results,
MSB-first, mirroring `Concatenation`'s own value semantics.
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


_CONCAT_OUTPUT_PORT_SRC = """
module dut_inner (
    input  logic clk,
    input  logic din,
    output logic [1:0] dout   // {tuser, tlast}
);
always_ff @(posedge clk) dout <= {din, 1'b0};
endmodule

module dut_wrap (
    input  logic clk,
    input  logic din,
    output logic derived
);
logic tuser, tlast;
dut_inner u (.clk(clk), .din(din), .dout({tuser, tlast}));

logic tlast_prev;
initial tlast_prev = 0;

assign derived = tlast_prev || tuser;
endmodule
"""

_SINGLE_ELEMENT_CONCAT_PORT_SRC = """
module cdc_stub (
    input  logic src_in,
    output logic dest_out
);
assign dest_out = src_in;
endmodule

module consumer (
    input  logic en,
    output logic bypass
);
assign bypass = !en;
endmodule

module top (
    input  logic ctrl_dual_gain_mode,
    output logic bypass_out
);
wire dual_gain_mode;
cdc_stub u_cdc (
    .src_in   ({ctrl_dual_gain_mode}),
    .dest_out ({dual_gain_mode})
);
consumer u_cons (.en(dual_gain_mode), .bypass(bypass_out));
endmodule
"""

_CONCAT_LHS_ASSIGN_SRC = """
module leaf (
    input  logic [2:0] bank,
    input  logic [7:0] addr,
    output logic [10:0] sum
);
assign sum = {bank, addr};
endmodule

module top (
    input  logic [10:0] LUT_addr,
    output logic [10:0] result
);
logic [2:0] bank;
logic [7:0] addr;
assign {bank, addr} = LUT_addr;

leaf u (.bank(bank), .addr(addr), .sum(result));
endmodule
"""

_CONCAT_MEMBER_INTO_FURTHER_CONCAT_PORT_SRC = """
module inner (
    input  logic clk,
    input  logic din,
    output logic [2:0] dout // {tuser, tlast, tdata}
);
always_ff @(posedge clk) dout <= {din, ~din, din};
endmodule

module downstream (
    input logic [2:0] s_data,
    output logic [2:0] q
);
assign q = s_data;
endmodule

module top (
    input logic clk,
    input logic din,
    output logic [2:0] q
);
logic tuser, tlast, tdata;
inner u_inner (.clk(clk), .din(din), .dout({tuser, tlast, tdata}));

downstream u_down (.s_data({tuser, tlast, tdata}), .q(q));
endmodule
"""


class TestConcatLHSReactivity:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_target_output_port_read_by_further_assign(self, engine, tmp_path):
        """A concat-target output port connection (`.dout({tuser, tlast})`)
        feeding a downstream `assign` must re-trigger it on every clock
        edge, not just leave it stuck at its initial X forever."""
        design = _parse(_CONCAT_OUTPUT_PORT_SRC, tmp_path)
        mod = design.get_module("dut_wrap")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("din", 1)
        sim.drive("clk", 0)
        for _ in range(3):
            sim.drive("clk", 1)
            sim.settle()
            sim.drive("clk", 0)
            sim.settle()
        assert int(sim.signal("tuser").value) == 1
        assert int(sim.signal("derived").value) == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_single_element_concat_port_read_by_plain_reference(self, engine, tmp_path):
        """Even a trivial single-element (`{sig}`, no packing at all)
        concat-target port connection must not break re-triggering for a
        PLAIN downstream reference (not just a further assign/concat)."""
        design = _parse(_SINGLE_ELEMENT_CONCAT_PORT_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("ctrl_dual_gain_mode", 0)
        sim.settle()
        sim.drive("ctrl_dual_gain_mode", 1)
        sim.settle()
        assert int(sim.signal("bypass_out").value) == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_target_lhs_of_plain_assign(self, engine, tmp_path):
        """`assign {bank, addr} = wide;` (concat-target LHS of an ordinary
        continuous assign, not a port connection) must re-trigger plain
        downstream port-connected readers on every change."""
        design = _parse(_CONCAT_LHS_ASSIGN_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("LUT_addr", 0)
        sim.settle()
        sim.drive("LUT_addr", 0b101_00100000)
        sim.settle()
        assert int(sim.signal("result").value) == 0b101_00100000

    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_member_repacked_into_further_concat_port(self, engine, tmp_path):
        """A concat-target output port's member, re-packed into a FURTHER
        concatenation used as a child instance's own input port connection,
        must still propagate."""
        design = _parse(_CONCAT_MEMBER_INTO_FURTHER_CONCAT_PORT_SRC, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.drive("din", 1)
        sim.drive("clk", 0)
        for _ in range(3):
            sim.drive("clk", 1)
            sim.settle()
            sim.drive("clk", 0)
            sim.settle()
        assert int(sim.signal("tuser").value) == 1
        assert int(sim.signal("q").value) == 0b101

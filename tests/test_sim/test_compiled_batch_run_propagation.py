"""Regression tests for the compiled-engine ``batch_run`` event-propagation fix.

The bug: ``batch_run`` applied scheduled events by writing directly to
``ctx.val[ev_sid]`` and immediately snapshotted ``ctx.val -> sv`` for the
pre-posedge view that always blocks read from. Port-wired signals (driven by
continuous assigns generated for instance port connections) had not yet been
re-evaluated, so the snapshot saw a *stale* value. A bench-driven reset
deassertion was therefore invisible to the DUT's always block on that cycle,
causing the DUT's reset branch to fire spuriously and clobber non-reset
registers.

The fix runs a settle pass (delta_loop with ``sv == ctx.val`` so no posedges
fire) immediately after events are applied. This propagates the events through
continuous assigns before the real pre-posedge snapshot is taken.

These tests exercise a parent module that wires ``rst`` through to a child via
port connection (i.e. an indirect ``inner.rst`` signal driven by a continuous
assign), then schedules a reset-deassert event through ``batch_run`` and
verifies the child's counter increments on every cycle after deassert with no
off-by-one due to a spurious last-cycle reset.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_files
from veriforge.sim import Simulator
from veriforge.sim.value import Value


_NESTED_COUNTER_SRC = """
module inner(
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
    always @(posedge clk) begin
        if (rst)
            count <= 8'd0;
        else
            count <= count + 8'd1;
    end
endmodule

module top(
    input  wire       clk,
    input  wire       rst,
    output wire [7:0] count
);
    inner u_inner(
        .clk   (clk),
        .rst   (rst),
        .count (count)
    );
endmodule
"""


@pytest.fixture()
def nested_design(tmp_path):
    src = tmp_path / "nested_counter.sv"
    src.write_text(_NESTED_COUNTER_SRC)
    return parse_files([str(src)], preprocess=True)


def _setup_sim(design):
    top = design.get_module("top")
    sim = Simulator(top, engine="compiled", design=design)
    sim.drive("rst", Value(1, width=1))
    sim.drive("clk", Value(0, width=1))
    sim._sched._sim.step()
    return sim


def test_batch_run_event_propagates_through_port_wiring(nested_design):
    """Reset deassert event must propagate to inner.rst before the pre-posedge snapshot."""
    sim = _setup_sim(nested_design)

    # Reset asserted for cycles 0..4, deasserted at cycle 5, then 20 increment cycles.
    sim.batch_run(
        25,
        "clk",
        clock_period=10,
        events=[(5, "rst", 0)],
    )

    # 25 cycles - 5 reset cycles = 20 increment cycles -> count == 20.
    # Pre-fix: spurious reset fired at cycle 5 (stale port wire), giving count == 19.
    assert sim.read("u_inner.count").val == 20


def test_batch_run_multiple_resets_alternate(nested_design):
    """Multiple reset assert/deassert events must each propagate through port wiring."""
    sim = _setup_sim(nested_design)

    sim.batch_run(
        30,
        "clk",
        clock_period=10,
        events=[
            (2, "rst", 0),  # deassert after 2 reset cycles
            (12, "rst", 1),  # re-assert (10 increment cycles -> count = 10)
            (15, "rst", 0),  # deassert again (count cleared to 0 at cycle 12, then 0 for 13..14)
        ],
    )

    # Cycles 0..1 reset, 2..11 increment (10 cycles -> count goes 1..10),
    # cycle 12 reset asserted -> count <= 0, 13..14 stay 0,
    # cycle 15 deassert -> 16..29 increment (15 cycles -> count = 15).
    assert sim.read("u_inner.count").val == 15


def test_batch_run_matches_run_with_port_wiring(nested_design):
    """batch_run with events must match the equivalent run() trace cycle-for-cycle."""
    # Reference: drive reset manually, advance with run().
    top = nested_design.get_module("top")
    sim_ref = Simulator(top, engine="compiled", design=nested_design)
    sim_ref.drive("rst", Value(1, width=1))
    sim_ref.drive("clk", Value(0, width=1))
    sim_ref.run(max_time=0)
    # 5 reset cycles via run().
    for _ in range(5):
        sim_ref.drive("clk", Value(1, width=1))
        sim_ref.run(max_time=sim_ref.time + 1)
        sim_ref.drive("clk", Value(0, width=1))
        sim_ref.run(max_time=sim_ref.time + 1)
    sim_ref.drive("rst", Value(0, width=1))
    sim_ref.run(max_time=sim_ref.time + 1)
    for _ in range(20):
        sim_ref.drive("clk", Value(1, width=1))
        sim_ref.run(max_time=sim_ref.time + 1)
        sim_ref.drive("clk", Value(0, width=1))
        sim_ref.run(max_time=sim_ref.time + 1)
    ref_count = sim_ref.read("u_inner.count").val

    # batch_run path.
    sim_b = _setup_sim(nested_design)
    sim_b.batch_run(25, "clk", clock_period=10, events=[(5, "rst", 0)])
    batch_count = sim_b.read("u_inner.count").val

    assert batch_count == ref_count

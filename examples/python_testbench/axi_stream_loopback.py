"""Example: Phase 7 transaction-level Python testbench DSL.

This example shows the end-to-end use of the
:class:`veriforge.sim.bench.Testbench` runtime against a parsed
Verilog DUT. The DUT is a two-domain AXI-Stream loopback with one
combinational pass-through per domain (each domain has its own clock and
reset). The testbench drives each domain independently via the
high-level transaction API::

    bench = Testbench(dut)
    with bench.run():
        bench.reset_all()
        bench.iface("a_axis_in").put([0x11, 0x22, 0x33])
        frame = bench.iface("a_axis_out").get(timeout=200)

Key features demonstrated:

* automatic discovery of clocks, resets, and AXI-Stream interfaces from
  the parsed module,
* role inversion (the proxy for a DUT *slave* AXIS is a *source*; the
  proxy for a DUT *master* AXIS is a *sink*),
* per-domain stepping (``bench.iface("a_axis_out").get`` only counts
  rising edges of *that* domain's clock),
* both an integer-list and a :class:`bytes` payload form,
* ``expect(...)`` for assertion-style frame matching,
* ``BenchTimeoutError`` raised when no frame arrives within the
  configured cycle budget.

Run with ``uv run python examples/python_testbench/axi_stream_loopback.py``.
"""

from __future__ import annotations

from veriforge.sim.bench import BenchTimeoutError, PlannerOverrides, Testbench
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

# ---------------------------------------------------------------------------
# DUT: two independent AXI-Stream pass-throughs, one per clock domain.
# The tiny clocked counters give the planner anchors for both clocks.
# ---------------------------------------------------------------------------

DUT_SOURCE = """
module dual_axis_loopback (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire        bclk,
    input  wire        bresetn,

    // Domain A: slave (DUT consumes) -> master (DUT produces)
    input  wire        a_axis_in_tvalid,
    output wire        a_axis_in_tready,
    input  wire [7:0]  a_axis_in_tdata,
    input  wire        a_axis_in_tlast,
    output wire        a_axis_out_tvalid,
    input  wire        a_axis_out_tready,
    output wire [7:0]  a_axis_out_tdata,
    output wire        a_axis_out_tlast,

    // Domain B: slave (DUT consumes) -> master (DUT produces)
    input  wire        b_axis_in_tvalid,
    output wire        b_axis_in_tready,
    input  wire [7:0]  b_axis_in_tdata,
    input  wire        b_axis_in_tlast,
    output wire        b_axis_out_tvalid,
    input  wire        b_axis_out_tready,
    output wire [7:0]  b_axis_out_tdata,
    output wire        b_axis_out_tlast
);
    // Combinational pass-through on each domain.
    assign a_axis_out_tvalid = a_axis_in_tvalid;
    assign a_axis_out_tdata  = a_axis_in_tdata;
    assign a_axis_out_tlast  = a_axis_in_tlast;
    assign a_axis_in_tready  = a_axis_out_tready;

    assign b_axis_out_tvalid = b_axis_in_tvalid;
    assign b_axis_out_tdata  = b_axis_in_tdata;
    assign b_axis_out_tlast  = b_axis_in_tlast;
    assign b_axis_in_tready  = b_axis_out_tready;

    // Anchor clocks/resets for the planner.
    reg [7:0] a_tick, b_tick;
    always @(posedge aclk or negedge aresetn)
        if (!aresetn) a_tick <= 8'h00; else a_tick <= a_tick + 8'd1;
    always @(posedge bclk or negedge bresetn)
        if (!bresetn) b_tick <= 8'h00; else b_tick <= b_tick + 8'd1;
endmodule
"""


def parse_dut():
    """Parse the inline DUT source into a model module."""
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=DUT_SOURCE)
    design = tree_to_design(tree)
    return design.modules[0]


def main() -> None:
    dut = parse_dut()

    # The auto-inferred plan would put all `b_axis_*` interfaces on aclk
    # because the naming heuristic prefers the first registered clock for
    # un-prefixed AXIS bundles. We use the override surface to bind them
    # explicitly to `bclk`, illustrating how a user takes control when
    # automatic inference picks the wrong domain.
    overrides = PlannerOverrides(
        iface_domains={
            "b_axis_in": "bclk",
            "b_axis_out": "bclk",
        }
    )
    bench = Testbench(dut, overrides=overrides)

    # The plan is human-readable; print it so users can see what was
    # discovered and how to override it if needed.
    print("Discovered testbench plan:\n")
    print(bench.plan.summary())
    print()

    with bench.run():
        bench.reset_all()

        # Drive domain A with a list of integers.
        a_in = bench.iface("a_axis_in")
        a_out = bench.iface("a_axis_out")
        a_in.put([0x11, 0x22, 0x33])

        # Drive domain B with a bytes object — same API, different
        # underlying payload type.
        b_in = bench.iface("b_axis_in")
        b_out = bench.iface("b_axis_out")
        b_in.put(bytes([0xB1, 0xB2, 0xB3, 0xB4]))

        # `expect(...)` raises AssertionError on mismatch.
        a_out.expect([0x11, 0x22, 0x33], timeout=200)
        print("Domain A: round-trip matched [0x11, 0x22, 0x33]")

        # `get(...)` returns an AXIStreamFrame.
        b_frame = b_out.get(timeout=200)
        print(f"Domain B: received {list(b_frame.data)}")

        # Demonstrate the cycle-budget timeout. The DUT is idle now; no
        # frame should arrive on the A output within 20 cycles.
        try:
            a_out.get(timeout=20)
        except BenchTimeoutError as exc:
            print(f"As expected, a short timeout raised: {exc}")


if __name__ == "__main__":
    main()

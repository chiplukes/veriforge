from __future__ import annotations

import pytest

from veriforge.dsl import Module
from veriforge.dsl.lib import axi4_lite, axi_stream
from veriforge.model.ports import Port, PortDirection
from veriforge.model.design import Module as ModelModule
from veriforge.sim.endpoints import (
    detect_axi_lite_interfaces,
    detect_axi_stream_interfaces,
    detect_interfaces,
    detect_near_misses,
    detect_relaxed_interfaces,
    NearMissInterface,
)
from veriforge.sim.endpoints.detect import detect_membus_interfaces
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse_module(src: str):
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(src)
    design = tree_to_design(tree)
    assert design.modules
    return design.modules[0]


def test_detect_axi_stream_and_axi_lite_on_dsl_module() -> None:
    module = Module("dut")
    module.input("clk")
    module.input("rst")
    module.interface("s_axis", axi_stream(data_width=8), role="slave")
    module.interface("m_axis", axi_stream(data_width=8, tuser_width=4), role="master")
    module.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")
    built = module.build()

    detected = detect_interfaces(built)

    assert [(bundle.protocol, bundle.prefix, bundle.role) for bundle in detected] == [
        ("axi_lite", "s_axi", "slave"),
        ("axi_stream", "m_axis", "master"),
        ("axi_stream", "s_axis", "slave"),
    ]


def test_detect_axi_stream_optional_signals() -> None:
    module = Module("dut")
    module.interface("m_axis", axi_stream(data_width=16, tid_width=2, tdest_width=3, tuser_width=4), role="master")
    bundle = detect_axi_stream_interfaces(module.build())[0]

    assert bundle.signal_names() == {
        "tvalid": "m_axis_tvalid",
        "tready": "m_axis_tready",
        "tdata": "m_axis_tdata",
        "tlast": "m_axis_tlast",
        "tdest": "m_axis_tdest",
        "tid": "m_axis_tid",
        "tuser": "m_axis_tuser",
    }


def test_detect_parsed_axi_lite_module() -> None:
    module = _parse_module(
        """
module regs(
    input clk,
    input rst,
    input [31:0] s_axi_awaddr,
    input [2:0] s_axi_awprot,
    input s_axi_awvalid,
    output s_axi_awready,
    input [31:0] s_axi_wdata,
    input [3:0] s_axi_wstrb,
    input s_axi_wvalid,
    output s_axi_wready,
    output [1:0] s_axi_bresp,
    output s_axi_bvalid,
    input s_axi_bready,
    input [31:0] s_axi_araddr,
    input [2:0] s_axi_arprot,
    input s_axi_arvalid,
    output s_axi_arready,
    output [31:0] s_axi_rdata,
    output [1:0] s_axi_rresp,
    output s_axi_rvalid,
    input s_axi_rready
);
endmodule
"""
    )

    bundles = detect_axi_lite_interfaces(module)

    assert len(bundles) == 1
    assert bundles[0].prefix == "s_axi"
    assert bundles[0].role == "slave"


def test_detect_ignores_incomplete_bundle() -> None:
    module = Module("dut")
    module.input("s_axis_tvalid")
    module.output("s_axis_tready")
    module.input("s_axis_tdata", width=8)

    assert detect_interfaces(module.build()) == []


def test_detect_interface_creators_match_role() -> None:
    module = Module("dut")
    module.interface("s_axis", axi_stream(data_width=8), role="slave")
    module.interface("m_axis", axi_stream(data_width=8), role="master")
    module.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")
    built = module.build()

    axis_bundles = {bundle.prefix: bundle for bundle in detect_axi_stream_interfaces(built)}
    axi_bundle = detect_axi_lite_interfaces(built)[0]

    assert axis_bundles["s_axis"].role == "slave"
    assert axis_bundles["m_axis"].role == "master"
    assert axi_bundle.role == "slave"


# ---------------------------------------------------------------------------
# MemBus detection
# ---------------------------------------------------------------------------


def test_detect_membus_slave_dsl() -> None:
    """detect_membus_interfaces finds a DUT-slave MemBus bundle on a DSL module."""
    module = Module("sram")
    module.input("clk")
    module.input("rst")
    module.input("mem_addr", width=4)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)
    module.input("mem_wen")
    module.input("mem_ren")
    module.output("mem_rvalid")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    assert bundles[0].prefix == "mem"
    assert bundles[0].role == "slave"
    assert bundles[0].protocol == "membus"


def test_detect_membus_master_dsl() -> None:
    """detect_membus_interfaces finds a DUT-master MemBus bundle (outputs are addr/wdata/wen)."""
    module = Module("bus_master")
    module.input("clk")
    module.input("rst")
    module.output("bus_addr", width=4)
    module.output("bus_wdata", width=32)
    module.input("bus_rdata", width=32)
    module.output("bus_wen")
    module.output("bus_ren")
    module.input("bus_rvalid")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    assert bundles[0].prefix == "bus"
    assert bundles[0].role == "master"


def test_detect_membus_we_suffix_normalised() -> None:
    """detect_membus_interfaces normalises 'we' → 'wen' so the bundle is found."""
    module = Module("sram_we")
    module.input("m_addr", width=8)
    module.input("m_wdata", width=32)
    module.output("m_rdata", width=32)
    module.input("m_we")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    assert bundles[0].prefix == "m"
    assert "wen" in bundles[0].signal_names()


def test_detect_membus_in_detect_interfaces_ordering() -> None:
    """detect_interfaces returns MemBus bundles after AXI bundles (last priority)."""
    module = Module("mixed")
    module.input("clk")
    module.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")
    module.input("mem_addr", width=4)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)
    module.input("mem_wen")
    built = module.build()

    all_bundles = detect_interfaces(built)
    protocols = [b.protocol for b in all_bundles]

    assert "axi_lite" in protocols
    assert "membus" in protocols
    assert protocols.index("axi_lite") < protocols.index("membus")


def test_detect_membus_incomplete_bundle_ignored() -> None:
    """A prefix with only addr+wdata but no wen is not returned."""
    module = Module("incomplete")
    module.input("mem_addr", width=4)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)

    bundles = detect_membus_interfaces(module.build())

    assert bundles == []


# ---------------------------------------------------------------------------
# Near-miss detection tests
# ---------------------------------------------------------------------------


def _make_model_module(ports: list[tuple[str, PortDirection]]) -> ModelModule:
    """Build a minimal model Module from (name, direction) tuples."""
    return ModelModule(
        "test_dut",
        ports=[Port(name, direction) for name, direction in ports],
    )


def test_near_miss_axis_missing_tlast() -> None:
    """Prefix with tvalid/tready/tdata but no tlast is a near-miss for AXI-Stream."""
    mod = _make_model_module(
        [
            ("s_tvalid", PortDirection.INPUT),
            ("s_tready", PortDirection.OUTPUT),
            ("s_tdata", PortDirection.INPUT),
        ]
    )
    nms = detect_near_misses(mod)
    assert len(nms) == 1
    assert nms[0].protocol == "axi_stream"
    assert nms[0].prefix == "s"
    assert "tlast" in nms[0].missing


def test_near_miss_axi_lite_missing_prot_strb() -> None:
    """Prefix with most AXI-Lite signals but missing awprot + wstrb is a near-miss."""
    required = (
        "awaddr",
        "awvalid",
        "awready",
        "wdata",
        "wvalid",
        "wready",
        "bresp",
        "bvalid",
        "bready",
        "araddr",
        "arvalid",
        "arready",
        "rdata",
        "rresp",
        "rvalid",
        "rready",
    )  # awprot and wstrb intentionally omitted
    ports = [(f"slv_{s}", PortDirection.INPUT) for s in required]
    mod = _make_model_module(ports)
    nms = detect_near_misses(mod)
    assert len(nms) == 1
    assert nms[0].protocol == "axi_lite"
    assert nms[0].prefix == "slv"
    assert "awprot" in nms[0].missing
    assert "wstrb" in nms[0].missing


def test_near_miss_explain_format() -> None:
    """NearMissInterface.explain() returns the expected human-readable string."""
    nm = NearMissInterface(
        prefix="slv",
        protocol="axi_lite",
        matched=("awaddr", "awvalid"),
        missing=("awprot", "wstrb"),
    )
    text = nm.explain()
    assert "slv" in text
    assert "AXI-Lite" in text
    assert "awprot" in text
    assert "wstrb" in text
    assert "missing" in text.lower()


def test_near_miss_not_reported_for_full_match() -> None:
    """A prefix that fully matches a protocol is not a near-miss."""
    module = Module("dut")
    module.interface("s_axis", axi_stream(data_width=8), role="slave")
    nms = detect_near_misses(module.build())
    # The full AXIS bundle should not also appear as a near-miss
    axis_nms = [nm for nm in nms if nm.prefix == "s_axis"]
    assert axis_nms == []


def test_near_miss_empty_for_unrelated_ports() -> None:
    """Ports with no protocol signal names produce no near-misses."""
    mod = _make_model_module(
        [
            ("clk", PortDirection.INPUT),
            ("rst_n", PortDirection.INPUT),
            ("data_in", PortDirection.INPUT),
            ("data_out", PortDirection.OUTPUT),
        ]
    )
    nms = detect_near_misses(mod)
    assert nms == []


def test_near_miss_too_few_signals_not_reported() -> None:
    """A prefix with only one AXI-Lite signal is not a near-miss (below threshold)."""
    mod = _make_model_module([("slv_awaddr", PortDirection.INPUT)])
    nms = detect_near_misses(mod)
    assert nms == []


# ── Relaxed detection tests ───────────────────────────────────────


def test_relaxed_axis_without_tlast() -> None:
    """With tlast relaxed, a tlast-less AXIS bundle is detected as a full interface."""
    mod = _make_model_module(
        [
            ("s_tvalid", PortDirection.INPUT),
            ("s_tready", PortDirection.OUTPUT),
            ("s_tdata", PortDirection.INPUT),
        ]
    )
    relaxed = detect_relaxed_interfaces(mod, relaxed_signals={"axi_stream": ["tlast"]})
    assert len(relaxed) == 1
    assert relaxed[0].protocol == "axi_stream"
    assert relaxed[0].prefix == "s"
    assert "tlast" not in relaxed[0].signals


def test_relaxed_axis_without_tready() -> None:
    """With tready relaxed, a tready-less (fixed-latency, no-flow-control) AXIS bundle is detected."""
    mod = _make_model_module(
        [
            ("s_tvalid", PortDirection.INPUT),
            ("s_tdata", PortDirection.INPUT),
            ("s_tlast", PortDirection.INPUT),
        ]
    )
    relaxed = detect_relaxed_interfaces(mod, relaxed_signals={"axi_stream": ["tready"]})
    assert len(relaxed) == 1
    assert relaxed[0].protocol == "axi_stream"
    assert relaxed[0].prefix == "s"
    assert relaxed[0].role == "slave"
    assert "tready" not in relaxed[0].signals


def test_relaxed_axis_needs_core_signals() -> None:
    """Relaxing tlast doesn't help when core signals (tvalid/tready/tdata) are missing."""
    mod = _make_model_module(
        [
            ("s_tvalid", PortDirection.INPUT),
            ("s_tlast", PortDirection.INPUT),
        ]
    )
    relaxed = detect_relaxed_interfaces(mod, relaxed_signals={"axi_stream": ["tlast"]})
    assert relaxed == []


def test_relaxed_axi_lite_without_awprot() -> None:
    """With awprot relaxed, an awprot-less AXI-Lite bundle is detected."""
    required = (
        "awaddr",
        "awvalid",
        "awready",
        "wdata",
        "wstrb",
        "wvalid",
        "wready",
        "bresp",
        "bvalid",
        "bready",
        "araddr",
        "arvalid",
        "arready",
        "rdata",
        "rresp",
        "rvalid",
        "rready",
    )
    ports = [
        (
            f"slv_{sig}",
            PortDirection.OUTPUT
            if sig in {"awvalid", "wvalid", "wdata", "wstrb", "arvalid", "rready"}
            else PortDirection.INPUT,
        )
        for sig in required
    ]
    # Also need bresp/rdata/rvalid as inputs → let me fix directions
    ports = [
        ("slv_awaddr", PortDirection.OUTPUT),
        ("slv_awvalid", PortDirection.OUTPUT),
        ("slv_awready", PortDirection.INPUT),
        ("slv_wdata", PortDirection.OUTPUT),
        ("slv_wstrb", PortDirection.OUTPUT),
        ("slv_wvalid", PortDirection.OUTPUT),
        ("slv_wready", PortDirection.INPUT),
        ("slv_bresp", PortDirection.INPUT),
        ("slv_bvalid", PortDirection.INPUT),
        ("slv_bready", PortDirection.OUTPUT),
        ("slv_araddr", PortDirection.OUTPUT),
        ("slv_arvalid", PortDirection.OUTPUT),
        ("slv_arready", PortDirection.INPUT),
        ("slv_rdata", PortDirection.INPUT),
        ("slv_rresp", PortDirection.INPUT),
        ("slv_rvalid", PortDirection.INPUT),
        ("slv_rready", PortDirection.OUTPUT),
    ]
    mod = _make_model_module(ports)
    relaxed = detect_relaxed_interfaces(mod, relaxed_signals={"axi_lite": ["awprot", "arprot"]})
    assert len(relaxed) == 1
    assert relaxed[0].protocol == "axi_lite"
    assert relaxed[0].prefix == "slv"
    assert "awprot" not in relaxed[0].signals
    assert "arprot" not in relaxed[0].signals


def test_relaxed_does_not_duplicate_existing() -> None:
    """A fully-matched AXI-Stream bundle is not re-detected by relaxed detection."""
    mod = _make_model_module(
        [
            ("s_tvalid", PortDirection.INPUT),
            ("s_tready", PortDirection.OUTPUT),
            ("s_tdata", PortDirection.INPUT),
            ("s_tlast", PortDirection.INPUT),
        ]
    )
    relaxed = detect_relaxed_interfaces(mod, relaxed_signals={"axi_stream": ["tlast"]})
    assert relaxed == []  # already fully detected


def test_relaxed_empty_when_no_relaxation() -> None:
    """Without relaxation, a near-miss bundle stays a near-miss."""
    mod = _make_model_module(
        [
            ("s_tvalid", PortDirection.INPUT),
            ("s_tready", PortDirection.OUTPUT),
            ("s_tdata", PortDirection.INPUT),
        ]
    )
    relaxed = detect_relaxed_interfaces(mod, relaxed_signals={})
    assert relaxed == []


# ---------------------------------------------------------------------------
# AXI4 (full) detection
# ---------------------------------------------------------------------------


def _add_axi4_write_channel(module: Module, prefix: str) -> None:
    module.output(f"{prefix}_awaddr", width=8)
    module.output(f"{prefix}_awlen", width=8)
    module.output(f"{prefix}_awsize", width=3)
    module.output(f"{prefix}_awburst", width=2)
    module.output(f"{prefix}_awvalid")
    module.input(f"{prefix}_awready")
    module.output(f"{prefix}_wdata", width=32)
    module.output(f"{prefix}_wstrb", width=4)
    module.output(f"{prefix}_wlast")
    module.output(f"{prefix}_wvalid")
    module.input(f"{prefix}_wready")
    module.input(f"{prefix}_bresp", width=2)
    module.input(f"{prefix}_bvalid")
    module.output(f"{prefix}_bready")


def _add_axi4_read_channel(module: Module, prefix: str) -> None:
    module.output(f"{prefix}_araddr", width=8)
    module.output(f"{prefix}_arlen", width=8)
    module.output(f"{prefix}_arsize", width=3)
    module.output(f"{prefix}_arburst", width=2)
    module.output(f"{prefix}_arvalid")
    module.input(f"{prefix}_arready")
    module.input(f"{prefix}_rdata", width=32)
    module.input(f"{prefix}_rresp", width=2)
    module.input(f"{prefix}_rlast")
    module.input(f"{prefix}_rvalid")
    module.output(f"{prefix}_rready")


def test_detect_full_axi4_via_awlen_arlen() -> None:
    """A full AXI4 bundle (AWLEN/ARLEN present) is detected as 'axi4', not 'axi_lite'."""
    module = Module("dut")
    _add_axi4_write_channel(module, "m_axi")
    _add_axi4_read_channel(module, "m_axi")
    bundle = detect_interfaces(module.build())[0]

    assert bundle.protocol == "axi4"
    assert bundle.role == "master"  # DUT drives AWVALID/ARVALID etc -> DUT is the master
    assert "awlen" in bundle.signals and "arlen" in bundle.signals


def test_detect_full_axi4_slave_role() -> None:
    """A DUT that *observes* AW/AR (module inputs) is detected as the AXI4 slave."""
    module = Module("dut")
    module.input("s_axi_awaddr", width=8)
    module.input("s_axi_awlen", width=8)
    module.input("s_axi_awsize", width=3)
    module.input("s_axi_awburst", width=2)
    module.input("s_axi_awvalid")
    module.output("s_axi_awready")
    module.input("s_axi_wdata", width=32)
    module.input("s_axi_wstrb", width=4)
    module.input("s_axi_wlast")
    module.input("s_axi_wvalid")
    module.output("s_axi_wready")
    module.output("s_axi_bresp", width=2)
    module.output("s_axi_bvalid")
    module.input("s_axi_bready")
    module.input("s_axi_araddr", width=8)
    module.input("s_axi_arlen", width=8)
    module.input("s_axi_arsize", width=3)
    module.input("s_axi_arburst", width=2)
    module.input("s_axi_arvalid")
    module.output("s_axi_arready")
    module.output("s_axi_rdata", width=32)
    module.output("s_axi_rresp", width=2)
    module.output("s_axi_rlast")
    module.output("s_axi_rvalid")
    module.input("s_axi_rready")
    bundle = detect_interfaces(module.build())[0]

    assert bundle.protocol == "axi4"
    assert bundle.role == "slave"


def test_near_miss_full_axi4_missing_signals() -> None:
    """A near-complete AXI4 bundle missing a handful of required signals is
    reported as a near-miss, not silently dropped."""
    mod = _make_model_module(
        [
            ("m_axi_awaddr", PortDirection.OUTPUT),
            ("m_axi_awlen", PortDirection.OUTPUT),
            ("m_axi_awsize", PortDirection.OUTPUT),
            ("m_axi_awburst", PortDirection.OUTPUT),
            ("m_axi_awvalid", PortDirection.OUTPUT),
            ("m_axi_awready", PortDirection.INPUT),
            ("m_axi_wdata", PortDirection.OUTPUT),
            ("m_axi_wstrb", PortDirection.OUTPUT),
            ("m_axi_wvalid", PortDirection.OUTPUT),
            ("m_axi_wready", PortDirection.INPUT),
            ("m_axi_bresp", PortDirection.INPUT),
            ("m_axi_bvalid", PortDirection.INPUT),
            ("m_axi_bready", PortDirection.OUTPUT),
            ("m_axi_araddr", PortDirection.OUTPUT),
            ("m_axi_arlen", PortDirection.OUTPUT),
            ("m_axi_arsize", PortDirection.OUTPUT),
            ("m_axi_arburst", PortDirection.OUTPUT),
            ("m_axi_arvalid", PortDirection.OUTPUT),
            ("m_axi_arready", PortDirection.INPUT),
            ("m_axi_rdata", PortDirection.INPUT),
            ("m_axi_rresp", PortDirection.INPUT),
            ("m_axi_rlast", PortDirection.INPUT),
            ("m_axi_rvalid", PortDirection.INPUT),
            ("m_axi_rready", PortDirection.OUTPUT),
            # wlast deliberately omitted — the only missing required signal,
            # so this clearly reads as "almost AXI4" (axi_lite would still be
            # missing awprot/arprot, a worse match).
        ]
    )

    near_misses = detect_near_misses(mod)
    assert len(near_misses) == 1
    nm = near_misses[0]
    assert nm.protocol == "axi4"
    assert nm.prefix == "m_axi"
    assert set(nm.missing) == {"wlast"}
    assert detect_interfaces(mod) == []  # not a full match


def test_detect_read_only_axi4() -> None:
    """A read-only AXI4 interface (no AW/W/B at all, e.g. a DMA read engine)
    is detected as 'axi4' via the AR/R-only fallback."""
    module = Module("dut")
    _add_axi4_read_channel(module, "m_axi")
    detected = detect_interfaces(module.build())

    assert len(detected) == 1
    bundle = detected[0]
    assert bundle.protocol == "axi4"
    assert bundle.role == "master"
    assert set(bundle.signal_names()) == {
        "araddr",
        "arlen",
        "arsize",
        "arburst",
        "arvalid",
        "arready",
        "rdata",
        "rresp",
        "rlast",
        "rvalid",
        "rready",
    }


def test_detect_write_only_axi4() -> None:
    """A write-only AXI4 interface (no AR/R at all) is detected as 'axi4' via
    the AW/W/B-only fallback."""
    module = Module("dut")
    _add_axi4_write_channel(module, "m_axi")
    detected = detect_interfaces(module.build())

    assert len(detected) == 1
    bundle = detected[0]
    assert bundle.protocol == "axi4"
    assert bundle.role == "master"
    assert set(bundle.signal_names()) == {
        "awaddr",
        "awlen",
        "awsize",
        "awburst",
        "awvalid",
        "awready",
        "wdata",
        "wstrb",
        "wlast",
        "wvalid",
        "wready",
        "bresp",
        "bvalid",
        "bready",
    }


def test_read_only_axi4_make_axi4_responder() -> None:
    """The read-only bundle's role='master' factory is make_axi4_responder,
    matching a bidirectional bundle's asymmetry (make_axi4_master is only
    valid for role='slave')."""
    module = Module("dut")
    _add_axi4_read_channel(module, "m_axi")
    bundle = detect_interfaces(module.build())[0]

    with pytest.raises(ValueError, match="slave-side"):
        bundle.make_axi4_master(sim=None)


AXI4_COMBINATIONAL_PASSTHRU_SRC = """
module axi4_combinational_passthru (
    input wire clk,
    input wire rst_n,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [7:0]  s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [2:0]  s_axi_awsize,
    input  wire [1:0]  s_axi_awburst,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    output wire [1:0]  s_axi_bresp,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [7:0]  s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,
    input  wire [2:0]  s_axi_arsize,
    input  wire [1:0]  s_axi_arburst,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,
    output wire        m_axi_awvalid,
    input  wire        m_axi_awready,
    output wire [7:0]  m_axi_awaddr,
    output wire [7:0]  m_axi_awlen,
    output wire [2:0]  m_axi_awsize,
    output wire [1:0]  m_axi_awburst,
    output wire        m_axi_wvalid,
    input  wire        m_axi_wready,
    output wire [31:0] m_axi_wdata,
    output wire [3:0]  m_axi_wstrb,
    output wire        m_axi_wlast,
    input  wire        m_axi_bvalid,
    output wire        m_axi_bready,
    input  wire [1:0]  m_axi_bresp,
    output wire        m_axi_arvalid,
    input  wire        m_axi_arready,
    output wire [7:0]  m_axi_araddr,
    output wire [7:0]  m_axi_arlen,
    output wire [2:0]  m_axi_arsize,
    output wire [1:0]  m_axi_arburst,
    input  wire        m_axi_rvalid,
    output wire        m_axi_rready,
    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rlast
);
    assign m_axi_awvalid = s_axi_awvalid;
    assign s_axi_awready = m_axi_awready;
    assign m_axi_awaddr  = s_axi_awaddr;
    assign m_axi_awlen   = s_axi_awlen;
    assign m_axi_awsize  = s_axi_awsize;
    assign m_axi_awburst = s_axi_awburst;
    assign m_axi_wvalid = s_axi_wvalid;
    assign s_axi_wready = m_axi_wready;
    assign m_axi_wdata  = s_axi_wdata;
    assign m_axi_wstrb  = s_axi_wstrb;
    assign m_axi_wlast  = s_axi_wlast;
    assign s_axi_bvalid = m_axi_bvalid;
    assign m_axi_bready = s_axi_bready;
    assign s_axi_bresp  = m_axi_bresp;
    assign m_axi_arvalid = s_axi_arvalid;
    assign s_axi_arready = m_axi_arready;
    assign m_axi_araddr  = s_axi_araddr;
    assign m_axi_arlen   = s_axi_arlen;
    assign m_axi_arsize  = s_axi_arsize;
    assign m_axi_arburst = s_axi_arburst;
    assign s_axi_rvalid = m_axi_rvalid;
    assign m_axi_rready = s_axi_rready;
    assign s_axi_rdata  = m_axi_rdata;
    assign s_axi_rresp  = m_axi_rresp;
    assign s_axi_rlast  = m_axi_rlast;
endmodule
"""


def test_detect_two_axi4_bundles_in_combinational_passthru() -> None:
    """A pure `assign`-wired slave-to-master passthrough (no registers) is
    detected as two independent AXI4 bundles with the correct, opposite
    roles — this is also the fixture used by
    `test_bench_native.py::test_axi4_master_to_slave_combinational_passthru`
    (native/batch_run pairing) and `test_axi4_responder.py` (register-slice
    variant, for the pure-Python pairing)."""
    mod = _parse_module(AXI4_COMBINATIONAL_PASSTHRU_SRC)
    bundles = {b.prefix: b for b in detect_interfaces(mod)}

    assert set(bundles) == {"s_axi", "m_axi"}
    assert bundles["s_axi"].protocol == "axi4"
    assert bundles["s_axi"].role == "slave"  # DUT observes AW/AR here -> DUT is the slave
    assert bundles["m_axi"].protocol == "axi4"
    assert bundles["m_axi"].role == "master"  # DUT drives AW/AR here -> DUT is the master


def test_make_axi4_responder_requires_master_role() -> None:
    module = Module("dut")
    module.input("s_axi_awaddr", width=8)
    module.input("s_axi_awlen", width=8)
    module.input("s_axi_awsize", width=3)
    module.input("s_axi_awburst", width=2)
    module.input("s_axi_awvalid")
    module.output("s_axi_awready")
    module.input("s_axi_wdata", width=32)
    module.input("s_axi_wstrb", width=4)
    module.input("s_axi_wlast")
    module.input("s_axi_wvalid")
    module.output("s_axi_wready")
    module.output("s_axi_bresp", width=2)
    module.output("s_axi_bvalid")
    module.input("s_axi_bready")
    module.input("s_axi_araddr", width=8)
    module.input("s_axi_arlen", width=8)
    module.input("s_axi_arsize", width=3)
    module.input("s_axi_arburst", width=2)
    module.input("s_axi_arvalid")
    module.output("s_axi_arready")
    module.output("s_axi_rdata", width=32)
    module.output("s_axi_rresp", width=2)
    module.output("s_axi_rlast")
    module.output("s_axi_rvalid")
    module.input("s_axi_rready")
    bundle = detect_interfaces(module.build())[0]  # role='slave'

    with pytest.raises(ValueError, match="master-side"):
        bundle.make_axi4_responder(sim=None)

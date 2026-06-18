from __future__ import annotations

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

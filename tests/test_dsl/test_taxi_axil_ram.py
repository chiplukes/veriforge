"""Pytest tests for the taxi/axil_ram example (Wave F).

Covers three scenarios for the ``axil_ram`` DUT (verilog-axi fallback):

1. Interface auto-detection — verifies the parser finds exactly one AXI-Lite
   bundle (``s_axil`` slave) on the flat-port module.
2. Python-stepped simulation — writes 4 words, reads them back, and performs a
   WSTRB partial-byte write.
3. compile_native — drives 4 writes + 4 reads via ``AXILiteMasterLowering``
   and validates the batch_run results.

Note on reset detection
-----------------------
``axil_ram.v`` uses a Xilinx-style "reset at end" always-block pattern where
``if (rst)`` appears at the bottom of the always block rather than the top.
``planner.build_plan`` now falls back to canonical port-name detection for
resets when structural analysis finds clocks but no resets, correctly
associating ``rst`` with the ``clk`` domain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veriforge.project import build_testbench, parse_file
from veriforge.sim.bench import AXILiteMasterLowering, AXILiteOp, compile_native
from veriforge.sim.endpoints import detect_axi_lite_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
DUT_PATH = REPO_ROOT / "examples" / "taxi" / "vendor" / "verilog-axi" / "axil_ram.v"

ADDRS = [0x00, 0x04, 0x08, 0x0C]
VALS = [0xDEAD_BEEF, 0xCAFE_F00D, 0x1234_5678, 0xA5A5_A5A5]


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axil_ram.v vendor file not present")
def test_axil_ram_interface_detected():
    """Auto-detection finds s_axil (slave) with all required AXI-Lite signals."""
    design = parse_file(DUT_PATH)
    module = design.get_module("axil_ram")
    assert module is not None

    bundles = detect_axi_lite_interfaces(module)
    assert len(bundles) == 1, f"expected 1 bundle, got {[b.prefix for b in bundles]}"
    bundle = bundles[0]
    assert bundle.prefix == "s_axil"
    assert bundle.role == "slave"


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axil_ram.v vendor file not present")
def test_axil_ram_python_stepped():
    """4-word write/read sweep + WSTRB partial-byte write."""
    bench = build_testbench(DUT_PATH)
    with bench.run():
        bench.reset_all()
        axil = bench.iface("s_axil")

        for addr, val in zip(ADDRS, VALS, strict=True):
            axil.write(addr, val)

        for addr, expected in zip(ADDRS, VALS, strict=True):
            got = axil.read(addr)
            assert got == expected, f"addr 0x{addr:02x}: got 0x{got:08x}, want 0x{expected:08x}"

        # WSTRB partial write: update only byte 0 of address 0x00
        axil.write(0x00, 0x0000_00AB, strb=0b0001)
        got_partial = axil.read(0x00)
        expected_partial = (VALS[0] & 0xFFFF_FF00) | 0xAB
        assert got_partial == expected_partial, f"WSTRB: got 0x{got_partial:08x}, want 0x{expected_partial:08x}"


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axil_ram.v vendor file not present")
def test_axil_ram_compile_native():
    """4 writes + 4 reads via compile_native batch_run.

    Operations 0..3 are writes; operations 4..7 are reads.
    Read data is keyed as ``s_axil_op_<i>_rdata`` (i=4..7).
    addr_width=5 covers byte addresses 0x00..0x0C.
    """
    ops = [AXILiteOp.write(addr, val) for addr, val in zip(ADDRS, VALS, strict=True)] + [
        AXILiteOp.read(addr) for addr in ADDRS
    ]

    bench = build_testbench(DUT_PATH)
    lowered = compile_native(
        bench,
        lowerings={
            "s_axil": AXILiteMasterLowering(operations=ops, addr_width=5),
        },
    )
    results = lowered.batch_run(cycles=1024, reset_cycles=4)

    assert results["s_axil_master_done"] == 1, "AXI-Lite master FSM never completed"
    for i, (addr, expected) in enumerate(zip(ADDRS, VALS, strict=True)):
        rdata = results[f"s_axil_op_{4 + i}_rdata"]
        assert rdata == expected, f"op {4 + i} addr 0x{addr:02x}: got 0x{rdata:08x}, want 0x{expected:08x}"

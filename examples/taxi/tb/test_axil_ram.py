"""AXI-Lite RAM testbench — verilog-axi fallback (Verilog 2001).

DUT: axil_ram from verilog-axi (flat Verilog 2001 module).

Note: The taxi project provides a SystemVerilog version (taxi_axil_ram.sv)
that uses SV interface syntax (``taxi_axil_if``). veriforge parses Verilog
2005 only, so the taxi SV module cannot be simulated here.  This testbench
uses the equivalent flat-port Verilog 2001 module from the verilog-axi library.

Note on reset detection
-----------------------
axil_ram uses a Xilinx-style "reset at end" always-block pattern::

    always @(posedge clk) begin
        // normal register updates ...
        if (rst) begin  // reset at the END, not the top
            // clear registers
        end
    end

The veriforge planner's structural reset extractor only inspects the
*first* if-statement in each always block, so this pattern was previously
undetected. A fix was applied to ``planner.py`` (``build_plan``) to fall back
to canonical port-name detection for resets when structural analysis finds
clocks but no resets.  With the fix, ``rst`` is correctly identified as an
active-high synchronous reset and ``compile_native`` works correctly.

Demo 1 — Python-stepped
-----------------------
Performs an 8-address write/read sweep plus a WSTRB partial-byte write to
verify byte-enable masking works correctly.

Demo 2 — compile_native (fast path)
------------------------------------
Drives 8 writes + 8 reads via AXILiteMasterLowering and validates all read
data using batch_run().

Run::

    uv run python examples/taxi/tb/test_axil_ram.py
"""

from __future__ import annotations

import time
from pathlib import Path

from veriforge.project import build_testbench
from veriforge.sim.bench import AXILiteMasterLowering, AXILiteOp, compile_native

VENDOR_DIR = Path(__file__).parent.parent / "vendor"
DUT_PATH = VENDOR_DIR / "verilog-axi" / "axil_ram.v"

# 8 word-aligned addresses; each word is 4 bytes (DATA_WIDTH=32)
ADDRS = [0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C]
# Distinct 32-bit values for each address
VALS = [0x1111_0001, 0x2222_0002, 0x3333_0003, 0x4444_0004, 0x5555_0005, 0x6666_0006, 0x7777_0007, 0x8888_0008]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _banner(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# Demo 1 — Python-stepped: 8-word sweep + WSTRB
# ---------------------------------------------------------------------------


def demo_axil_ram_python(vcd_dir: Path | None = None) -> None:
    """Write 8 words, read them back, then do a WSTRB partial-byte write.

    Verifies:
    * Full 32-bit write followed by read returns the expected value.
    * WSTRB=0b0001 (byte 0 only) updates only the least-significant byte.
    """
    _banner("Demo 1 — axil_ram Python-stepped (8-word sweep + WSTRB)")

    vcd = vcd_dir / "axil_ram_py.vcd" if vcd_dir else None
    bench = build_testbench(DUT_PATH)

    with bench.run(vcd=vcd):
        bench.reset_all()
        axil = bench.iface("s_axil")

        t0 = time.perf_counter()
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
        elapsed_ms = (time.perf_counter() - t0) * 1000

    vcd_note = f"  → VCD: {vcd}" if vcd else ""
    print(f"  8-word write/read sweep + WSTRB: {elapsed_ms:>6.1f} ms  PASSED{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 2 — compile_native: 8 writes + 8 reads
# ---------------------------------------------------------------------------


def demo_axil_ram_native() -> None:
    """Validate all 8 read-back values from a scripted write/read sequence.

    The AXILiteMasterLowering walks: 8 writes (ops 0..7) then 8 reads
    (ops 8..15).  Results are keyed as ``s_axil_op_<i>_rdata``.

    addr_width=5 covers byte addresses 0x00..0x1F (8 words × 4 bytes).
    """
    _banner("Demo 2 — axil_ram compile_native (8 writes + 8 reads, batch_run)")

    ops = [AXILiteOp.write(addr, val) for addr, val in zip(ADDRS, VALS, strict=True)] + [
        AXILiteOp.read(addr) for addr in ADDRS
    ]

    t0 = time.perf_counter()
    bench = build_testbench(DUT_PATH)
    lowered = compile_native(
        bench,
        lowerings={
            "s_axil": AXILiteMasterLowering(operations=ops, addr_width=5),
        },
    )
    results = lowered.batch_run(cycles=2048, reset_cycles=4)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert results["s_axil_master_done"] == 1, "AXI-Lite master FSM never completed"

    for i, (addr, expected) in enumerate(zip(ADDRS, VALS, strict=True)):
        rdata = results[f"s_axil_op_{8 + i}_rdata"]
        assert rdata == expected, f"op {8 + i} addr 0x{addr:02x}: got 0x{rdata:08x}, want 0x{expected:08x}"

    print(f"  8-word write/read via compile_native: {elapsed_ms:>6.1f} ms  PASSED")
    print(f"  Read results: {[hex(results[f's_axil_op_{8 + i}_rdata']) for i in range(8)]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="axil_ram testbench demos")
    parser.add_argument("--vcd", metavar="DIR", help="write VCD traces to DIR")
    args = parser.parse_args()

    vcd_dir = Path(args.vcd) if args.vcd else None
    if vcd_dir:
        vcd_dir.mkdir(parents=True, exist_ok=True)

    demo_axil_ram_python(vcd_dir)
    demo_axil_ram_native()
    print("\nAll axil_ram demos passed.")

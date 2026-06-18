"""Multi-interface testbench — build_testbench() API demonstration.

Demonstrates the ``build_testbench()`` API from ``veriforge.project``:
point it at a directory of Verilog files, name the top module, and get a
fully-configured :class:`~veriforge.sim.bench.Testbench` back with
auto-detected interfaces and clock/reset domains.

DUT: ``multi_iface_top`` (examples/multi_iface_project/rtl/)
     Structural wrapper over three sub-modules:

       axis_loopback  — AXI-Stream combinational loopback (m_axis → s_axis)
       axil_regfile   — AXI-Lite 4 × 32-bit register file (slave)
       axi4_ram       — AXI4 8 × 32-bit single-beat RAM    (slave)

Auto-detected interfaces on the top:
   m_axis   (axi_stream, slave)   bench.iface("m_axis") → AXIStreamProxy (source)
   s_axis   (axi_stream, master)  bench.iface("s_axis") → AXIStreamProxy (sink)
   axil     (axi_lite,   slave)   bench.iface("axil")   → AXILiteProxy   (master)
   ram      (axi4,       slave)   bench.iface("ram")    → AXI4Proxy      (master)

How the testbench scaffold was originally generated
----------------------------------------------------
Explain the planner's decisions::

    uv run veriforge generate-python-testbench \\
        --directory examples/multi_iface_project/rtl/ \\
        --module multi_iface_top \\
        --explain-plan

Generate a bench-style scaffold (then hand-edit into this file)::

    uv run veriforge generate-python-testbench \\
        --directory examples/multi_iface_project/rtl/ \\
        --module multi_iface_top \\
        --style bench

Run this demo::

    uv run python examples/multi_iface_project/tb/multi_iface_tb.py
    uv run python examples/multi_iface_project/tb/multi_iface_tb.py --vcd /tmp/waves/

compile_native (fast path) — demo 5 — drives all four interfaces simultaneously
using engine-native FSMs (AXIStreamSourceLowering, AXIStreamSinkLowering,
AXILiteMasterLowering, AXI4MasterLowering) and runs the combined wrapper in the
compiled C engine via batch_run() with zero Python per-cycle overhead.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from veriforge.project import build_testbench
from veriforge.sim.bench import (
    AXI4MasterLowering,
    AXI4MasterOp,
    AXILiteMasterLowering,
    AXILiteOp,
    AXIStreamSinkLowering,
    AXIStreamSourceLowering,
    compile_native,
)
from veriforge.sim.endpoints import PauseGenerator

RTL_DIR = Path(__file__).parent.parent / "rtl"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _banner(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# Demo 1 — AXI-Stream loopback with source and sink pause
# ---------------------------------------------------------------------------


def demo_axis_loopback(vcd_dir: Path | None = None) -> None:
    """AXI-Stream loopback: bench source → DUT → bench sink.

    Runs three variants:
      * No pause            — full 100% bandwidth
      * 1-in-3 source pause — bench holds tvalid low ~33% of cycles (~67% BW)
      * 1-in-3 sink pause   — bench holds tready low ~33% of cycles (~67% BW)

    PauseGenerator(n, m, seed) asserts a pause approximately n-out-of-m
    cycles using a seeded PRNG (reproducible runs).
    """
    _banner("Demo 1 — AXI-Stream loopback (bench source → DUT → bench sink)")

    payload = list(range(0x10, 0x30))  # 32 bytes

    variants = [
        ("no pause       (100% BW)", False, False),
        ("1/3 src pause  ( 67% BW)", PauseGenerator(1, 3, seed=42), False),
        ("1/3 snk pause  ( 67% BW)", False, PauseGenerator(1, 3, seed=99)),
    ]

    for label, src_pause, snk_pause in variants:
        vcd = vcd_dir / f"axis_{label.split()[0]}.vcd" if vcd_dir else None
        bench = build_testbench(RTL_DIR, top="multi_iface_top")
        with bench.run(vcd=vcd):
            bench.reset_all()
            src = bench.iface("m_axis")
            snk = bench.iface("s_axis")
            src.pause = src_pause
            snk.pause = snk_pause
            t0 = time.perf_counter()
            src.put(payload)
            src.wait_drain()
            received = snk.get()
            elapsed_ms = (time.perf_counter() - t0) * 1000
        assert list(received.data) == payload, f"payload mismatch under '{label}'"
        vcd_note = f"  → VCD: {vcd}" if vcd else ""
        print(f"  {label}: {elapsed_ms:>6.1f} ms for {len(payload)} beats{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 2 — AXI-Lite register file write/read sweep
# ---------------------------------------------------------------------------


def demo_axil_registers(vcd_dir: Path | None = None) -> None:
    """AXI-Lite register file: bench master drives 4-register sweep.

    Writes four distinct 32-bit patterns, reads them back, then performs a
    WSTRB partial-byte write to verify byte-enable masking.
    """
    _banner("Demo 2 — AXI-Lite register file (bench master → DUT slave)")

    patterns = [0xDEAD_BEEF, 0xCAFE_F00D, 0x1234_5678, 0xA5A5_A5A5]
    addrs = [0x0, 0x4, 0x8, 0xC]
    vcd = vcd_dir / "axil_regs.vcd" if vcd_dir else None

    bench = build_testbench(RTL_DIR, top="multi_iface_top")
    with bench.run(vcd=vcd):
        bench.reset_all()
        axil = bench.iface("axil")

        # Write all 4 registers
        for addr, val in zip(addrs, patterns, strict=False):
            axil.write(addr, val)

        # Read back and verify
        for addr, expected in zip(addrs, patterns, strict=False):
            got = axil.read(addr)
            assert got == expected, f"reg[0x{addr:x}] = 0x{got:08X}, expected 0x{expected:08X}"

        # WSTRB partial write: update only the low byte of register 0
        axil.write(0x0, 0x0000_00AB, strb=0b0001)
        got_partial = axil.read(0x0)
        expected_partial = (patterns[0] & 0xFFFF_FF00) | 0xAB
        assert got_partial == expected_partial, f"WSTRB: got 0x{got_partial:08X}, expected 0x{expected_partial:08X}"

    vcd_note = f"  → VCD: {vcd}" if vcd else ""
    print(f"  4-register write/read sweep + WSTRB partial byte: PASSED{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 3 — AXI4 RAM single-beat write/read sweep
# ---------------------------------------------------------------------------


def demo_axi4_ram(vcd_dir: Path | None = None) -> None:
    """AXI4 RAM: bench master performs 8-word single-beat sweep.

    Writes 8 words, reads them back, then verifies WSTRB byte-enable on word 0.
    The AXI4 interface is detected by the presence of awlen in the ram_* port group.
    """
    _banner("Demo 3 — AXI4 RAM (bench master → DUT slave)")

    payload = [0xDEAD_0000 | i for i in range(8)]
    vcd = vcd_dir / "axi4_ram.vcd" if vcd_dir else None

    bench = build_testbench(RTL_DIR, top="multi_iface_top")
    with bench.run(vcd=vcd):
        bench.reset_all()
        ram = bench.iface("ram")

        # Write 8 words
        for i, val in enumerate(payload):
            ram.write(i * 4, val)

        # Read back and verify
        for i, expected in enumerate(payload):
            got = ram.read(i * 4, length=1)
            assert got[0] == expected, f"word[{i}] = 0x{got[0]:08X}, expected 0x{expected:08X}"

        # WSTRB partial-byte write: update only the low byte of word 0
        ram.write(0x00, 0x0000_00AB, strb=0b0001)
        got_partial = ram.read(0x00, length=1)
        expected_partial = (payload[0] & 0xFFFF_FF00) | 0xAB
        assert got_partial[0] == expected_partial, (
            f"WSTRB: got 0x{got_partial[0]:08X}, expected 0x{expected_partial:08X}"
        )

    vcd_note = f"  → VCD: {vcd}" if vcd else ""
    print(f"  8-word write/read sweep + WSTRB partial byte: PASSED{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 4 — All three interfaces in one bench.run() session
# ---------------------------------------------------------------------------


def demo_all_together(vcd_dir: Path | None = None) -> None:
    """Drive AXI-Stream, AXI-Lite, and AXI4 in a single bench.run() context.

    Illustrates that all three proxies coexist on the same Testbench instance
    and share the same simulation time axis.
    """
    _banner("Demo 4 — All three interfaces in one bench.run() session")

    vcd = vcd_dir / "all_together.vcd" if vcd_dir else None
    bench = build_testbench(RTL_DIR, top="multi_iface_top")
    with bench.run(vcd=vcd):
        bench.reset_all()

        src = bench.iface("m_axis")
        snk = bench.iface("s_axis")
        axil = bench.iface("axil")
        ram = bench.iface("ram")

        # AXI-Lite: write two registers
        axil.write(0x0, 0xAAAA_0001)
        axil.write(0x4, 0xBBBB_0002)

        # AXI4: write two words
        ram.write(0x00, 0xCCCC_0003)
        ram.write(0x04, 0xDDDD_0004)

        # AXI-Stream: send a packet and capture the loopback
        axis_data = [0xA0, 0xB1, 0xC2, 0xD3]
        src.put(axis_data)
        src.wait_drain()
        pkt = snk.get()

        # Verify all results
        assert axil.read(0x0) == 0xAAAA_0001
        assert axil.read(0x4) == 0xBBBB_0002
        assert ram.read(0x00, length=1)[0] == 0xCCCC_0003
        assert ram.read(0x04, length=1)[0] == 0xDDDD_0004
        assert list(pkt.data) == axis_data

    vcd_note = f"  → VCD: {vcd}" if vcd else ""
    print(f"  AXI-Stream + AXI-Lite + AXI4 in one session: PASSED{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 5 — compile_native: all four interfaces on multi_iface_top
# ---------------------------------------------------------------------------


def demo_compile_native_all() -> None:  # noqa: PLR0915
    """Compile-native fast path targeting the full multi_iface_top DUT.

    ``compile_native()`` requires a lowering for *every* detected interface.
    With ``AXI4MasterLowering`` now available this demo drives all four
    simultaneously using engine-native FSMs:

      * ``AXIStreamSourceLowering``  — fixed-pattern AXIS source (m_axis)
      * ``AXIStreamSinkLowering``    — capturing AXIS sink        (s_axis)
      * ``AXILiteMasterLowering``    — ROM-scripted AXI-Lite master (axil)
      * ``AXI4MasterLowering``       — ROM-scripted AXI4 master    (ram)

    The combined wrapper is lowered to the compiled C engine and run via
    ``batch_run()`` — zero Python per-cycle overhead after compilation.

    Speed comparison
    ----------------
    The same payload is first run through the Python-stepped Testbench for
    reference.  compile_native includes a one-time C compilation cost
    (~0.5–2 s); speedup is most visible in long regression sweeps.

    Limitations
    -----------
    * ``PauseGenerator`` is NOT supported in ``compile_native`` / ``batch_run``.
      Use the Python-stepped Testbench (Demos 1–4) for dynamic per-cycle pause.
    * ``AXI4MasterLowering`` only supports single-beat transfers (``awlen=0``).
      Multi-beat bursts require the Python-stepped ``AXI4Proxy``.
    """
    _banner("Demo 5 — compile_native: all 4 interfaces on multi_iface_top")

    # ── Payload definitions ─────────────────────────────────────────────────
    axis_beats = list(range(0x10, 0x1A))  # 10 bytes

    axil_ops = [
        AXILiteOp.write(0x0, 0xDEAD_BEEF),
        AXILiteOp.write(0x4, 0x1234_5678),
        AXILiteOp.read(0x0),
        AXILiteOp.read(0x4),
    ]

    # AXI4 RAM: byte address = word_index × 4, addr[4:2] → word index
    ram_ops = [
        AXI4MasterOp.write(0x00, 0xCAFE_0001),
        AXI4MasterOp.write(0x04, 0xCAFE_0002),
        AXI4MasterOp.read(0x00),
        AXI4MasterOp.read(0x04),
    ]

    # ── Python-stepped reference baseline ───────────────────────────────────
    t0 = time.perf_counter()
    bench_py = build_testbench(RTL_DIR, top="multi_iface_top")
    with bench_py.run():
        bench_py.reset_all()
        src = bench_py.iface("m_axis")
        snk = bench_py.iface("s_axis")
        axil = bench_py.iface("axil")
        ram = bench_py.iface("ram")

        # AXI-Lite sweep
        for op in axil_ops:
            if op.kind == "write":
                axil.write(op.addr, op.data)
        axil_reads = [axil.read(op.addr) for op in axil_ops if op.kind == "read"]

        # AXI4 RAM sweep
        for op in ram_ops:
            if op.kind == "write":
                ram.write(op.addr, op.data)
        ram_reads = [ram.read(op.addr, length=1)[0] for op in ram_ops if op.kind == "read"]

        # AXI-Stream loopback
        src.put(axis_beats)
        src.wait_drain()
        received = snk.get()
    t_py = time.perf_counter() - t0

    assert list(received.data) == axis_beats
    assert axil_reads[0] == 0xDEAD_BEEF
    assert axil_reads[1] == 0x1234_5678
    assert ram_reads[0] == 0xCAFE_0001
    assert ram_reads[1] == 0xCAFE_0002

    # ── compile_native + batch_run ───────────────────────────────────────────
    t1 = time.perf_counter()
    bench_n = build_testbench(RTL_DIR, top="multi_iface_top")
    lowered = compile_native(
        bench_n,
        lowerings={
            "m_axis": AXIStreamSourceLowering(beats=axis_beats, data_width=8),
            "s_axis": AXIStreamSinkLowering(n_beats=len(axis_beats), data_width=8),
            "axil": AXILiteMasterLowering(operations=axil_ops, addr_width=4),
            "ram": AXI4MasterLowering(
                operations=ram_ops,
                addr_width=5,
                data_width=32,
                id_width=4,
            ),
        },
    )
    # Allow enough cycles for all FSMs to complete (AXIS + AXI-Lite + AXI4)
    results = lowered.batch_run(cycles=1024, reset_cycles=4)
    t_native = time.perf_counter() - t1

    # Verify AXIS capture
    assert results["s_axis_snk_done"] == 1, "AXIS sink never flagged done"
    captured = [results[f"s_axis_cap_{i}"] for i in range(len(axis_beats))]
    assert captured == axis_beats, f"AXIS captured mismatch: {captured}"

    # Verify AXI-Lite (write ops are 0,1; read ops are 2,3)
    assert results["axil_master_done"] == 1, "AXI-Lite master FSM never completed"
    assert results["axil_op_2_rdata"] == 0xDEAD_BEEF
    assert results["axil_op_3_rdata"] == 0x1234_5678

    # Verify AXI4 RAM (write ops are 0,1; read ops are 2,3)
    assert results["ram_master_done"] == 1, "AXI4 master FSM never completed"
    assert results["ram_op_2_rdata"] == 0xCAFE_0001
    assert results["ram_op_3_rdata"] == 0xCAFE_0002

    speedup = t_py / t_native if t_native > 0 else float("inf")
    print(f"  Python-stepped (reference engine): {t_py * 1000:>7.1f} ms")
    print(f"  compile_native  (compiled engine): {t_native * 1000:>7.1f} ms")
    print(f"  Speedup: {speedup:.1f}x")
    print()
    print("  Notes:")
    print("  - compile_native includes a one-time C compilation cost (~0.5–2 s).")
    print("    For short tests this outweighs the runtime gain; speedup is most")
    print("    visible in long regression sweeps (thousands of cycles).")
    print("  - PauseGenerator is NOT supported in compile_native / batch_run.")
    print("  - AXI4MasterLowering supports single-beat transfers only (awlen=0).")
    print("    Use the Python-stepped AXI4Proxy for multi-beat bursts.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-interface testbench — build_testbench() API demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--vcd",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Optional directory for VCD waveform output.  One .vcd file is written"
            " per demo variant.  The directory is created if it does not exist."
        ),
    )
    parser.add_argument(
        "--demo",
        choices=["1", "2", "3", "4", "5", "all"],
        default="all",
        help="Run a single numbered demo (1–5) or all (default).",
    )
    args = parser.parse_args()

    vcd_dir: Path | None = None
    if args.vcd is not None:
        vcd_dir = args.vcd
        vcd_dir.mkdir(parents=True, exist_ok=True)

    demo_map = {
        "1": demo_axis_loopback,
        "2": demo_axil_registers,
        "3": demo_axi4_ram,
        "4": demo_all_together,
        "5": demo_compile_native_all,
    }

    run = list(demo_map.keys()) if args.demo == "all" else [args.demo]
    for key in run:
        fn = demo_map[key]
        # Demo 5 uses compile_native and doesn't accept a vcd_dir argument
        if key == "5":
            fn()
        else:
            fn(vcd_dir)

    print("\n✓ All requested demos passed!")


if __name__ == "__main__":
    main()

"""Bench-framework version of the pulp axi_lite_mailbox example.

Mirrors the original ``run_sim.py`` but uses the high-level
:class:`~veriforge.sim.bench.Testbench` runtime and AXILiteProxy for
both DUT slave ports (``slv0`` / ``slv1``).

Regen recipe (scaffold; this file is hand-edited)::

    uv run veriforge generate-python-testbench \\
        -f examples/pulp/axi/axi_lite_mailbox/rtl/axi_lite_mailbox.sv \\
        -f examples/pulp/axi/axi_lite_mailbox/tb/axi_lite_mailbox_tb.sv \\
        --module axi_lite_mailbox_exec_tb --enhanced --style bench \\
        --auto-deps --no-strict \\
        -o examples/pulp/axi/axi_lite_mailbox/bench/axi_lite_mailbox_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

SCRIPT_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = SCRIPT_DIR.parent
FILES = [
    str(EXAMPLE_DIR / "rtl" / "axi_lite_mailbox.sv"),
    str(EXAMPLE_DIR / "tb" / "axi_lite_mailbox_tb.sv"),
]
TOP = "axi_lite_mailbox_exec_tb"

# Register map (mirrors run_sim.py)
BASE0 = 0x000
BASE1 = 0x100
REG_MBOXW = 0x00
REG_MBOXR = 0x04
REG_STATUS = 0x08
REG_ERROR = 0x0C
REG_WIRQT = 0x10
REG_RIRQT = 0x14
REG_IRQS = 0x18
REG_IRQEN = 0x1C
REG_IRQP = 0x20
MAIL_P0_TO_P1 = 0xAABBCCDD
MAIL_P1_TO_P0 = 0x11223344
IRQ_ERROR = 0x4


def build_bench() -> Testbench:
    """Construct the Testbench from the parsed pulp design."""
    design = parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_lite_mailbox_pcache",
    )
    dut = design.get_module(TOP)
    if dut is None:
        raise RuntimeError(f"Top module {TOP!r} not found")
    overrides = PlannerOverrides(
        iface_domains={"slv0": "clk", "slv1": "clk"},
    )
    return Testbench(dut, design=design, overrides=overrides, engine="reference")


def _expect(actual: int, expected: int, message: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{message}: expected 0x{expected:x}, got 0x{actual:x}")


def _expect_signal(bench: Testbench, name: str, expected: int, message: str) -> None:
    raw = bench.sim.read(name)
    actual = int(raw)
    if actual != expected:
        raise RuntimeError(f"{message}: expected 0x{expected:x}, got 0x{actual:x}")


def exercise(bench: Testbench) -> None:
    """Drive both AXI-Lite slaves through the mailbox protocol."""
    port0 = bench.iface("slv0")
    port1 = bench.iface("slv1")

    # Reset state: both ports report status = 1 (write side empty).
    _expect(port0.read(BASE0 + REG_STATUS), 0x1, "port0 reset status")
    _expect(port1.read(BASE1 + REG_STATUS), 0x1, "port1 reset status")

    # Configure IRQ thresholds.
    _expect(port0.write(BASE0 + REG_WIRQT, 0x1), 0x0, "port0 WIRQT")
    _expect(port0.write(BASE0 + REG_RIRQT, 0x1), 0x0, "port0 RIRQT")
    _expect(port1.write(BASE1 + REG_WIRQT, 0x1), 0x0, "port1 WIRQT")
    _expect(port1.write(BASE1 + REG_RIRQT, 0x1), 0x0, "port1 RIRQT")

    # Mail port 0 -> port 1.
    _expect(port0.write(BASE0 + REG_MBOXW, MAIL_P0_TO_P1), 0x0, "port0 mbox write")
    _expect(port0.read(BASE0 + REG_STATUS), 0x1, "port0 post-write status")
    _expect(port1.read(BASE1 + REG_STATUS), 0x0, "port1 post-receive status")
    _expect(port1.read(BASE1 + REG_MBOXR), MAIL_P0_TO_P1, "port1 mbox read")
    _expect(port1.read(BASE1 + REG_STATUS), 0x1, "port1 post-read status")

    # Mail port 1 -> port 0.
    _expect(port1.write(BASE1 + REG_MBOXW, MAIL_P1_TO_P0), 0x0, "port1 mbox write")
    _expect(port0.read(BASE0 + REG_MBOXR), MAIL_P1_TO_P0, "port0 mbox read")

    # Trigger and acknowledge an error IRQ on port1.
    _expect(port1.write(BASE1 + REG_IRQEN, IRQ_ERROR), 0x0, "port1 IRQEN")
    # Reading an empty mailbox triggers an error response (SLVERR).
    try:
        port1.read(BASE1 + REG_MBOXR)
    except Exception as exc:
        if "expected 0x0, got 0x2" not in str(exc):
            raise RuntimeError(f"unexpected AXI-Lite error text: {exc}") from exc
    else:
        raise RuntimeError("expected SLVERR on empty mailbox read")

    _expect_signal(bench, "irq1", 1, "port1 irq should assert")
    _expect(port1.read(BASE1 + REG_IRQP), IRQ_ERROR, "port1 IRQP")
    _expect(port1.read(BASE1 + REG_ERROR), 0x1, "port1 ERROR")
    _expect(port1.write(BASE1 + REG_IRQS, IRQ_ERROR), 0x0, "port1 IRQS ack")
    _expect(port1.read(BASE1 + REG_IRQP), 0x0, "port1 IRQP cleared")
    _expect_signal(bench, "irq1", 0, "port1 irq should clear")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    bench = build_bench()
    print("Discovered testbench plan:\n")
    print(bench.plan.summary())
    print()

    with bench.run(vcd=args.vcd):
        if args.vcd is not None:
            print(f"VCD tracing -> {args.vcd}\n")
        bench.reset_all()
        exercise(bench)

    print("axi_lite_mailbox passed: dual-port mailbox + IRQ ack")


if __name__ == "__main__":
    main()

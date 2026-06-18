"""Bench-framework version of the pulp axi_lite_xbar example.

Exercises a 2x2 AXI-Lite crossbar where each master port can route to
either target (target0 lives at addr 0x000, target1 at 0x100).
Mirrors the high-level routing scenario from the original
``run_sim.py`` (cross-port reads/writes plus decode-error handling).

The lower-level same-target arbitration test in the original script
relies on direct signal poking and is intentionally skipped here.

Regen recipe (scaffold; this file is hand-edited)::

    uv run veriforge generate-python-testbench \\
        -f examples/pulp/axi/axi_lite_xbar/rtl/axi_pkg.sv \\
        -f examples/pulp/axi/axi_lite_xbar/rtl/axi_lite_xbar.sv \\
        -f examples/pulp/axi/axi_lite_xbar/tb/axi_lite_xbar_tb.sv \\
        --module axi_lite_xbar_exec_tb --enhanced --style bench \\
        --auto-deps --no-strict \\
        -o examples/pulp/axi/axi_lite_xbar/bench/axi_lite_xbar_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

SCRIPT_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = SCRIPT_DIR.parent
FILES = [
    str(EXAMPLE_DIR / "rtl" / "axi_pkg.sv"),
    str(EXAMPLE_DIR / "rtl" / "axi_lite_xbar.sv"),
    str(EXAMPLE_DIR / "tb" / "axi_lite_xbar_tb.sv"),
]
TOP = "axi_lite_xbar_exec_tb"

TARGET0_INIT = 0x11111111
TARGET1_INIT = 0x22222222
TARGET0_WRITE = 0x11223344
TARGET1_WRITE = 0xAABBCCDD
ADDR_TARGET0 = 0x000
ADDR_TARGET1 = 0x100
ADDR_INVALID = 0x200


def build_bench() -> Testbench:
    design = parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_lite_xbar_pcache",
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


def exercise_routing(bench: Testbench) -> None:
    port0 = bench.iface("slv0")
    port1 = bench.iface("slv1")

    _expect(port0.read(ADDR_TARGET0), TARGET0_INIT, "target0 reset read")
    _expect(port1.read(ADDR_TARGET1), TARGET1_INIT, "target1 reset read")

    # Cross-port writes/reads.
    _expect(port0.write(ADDR_TARGET1, TARGET1_WRITE), 0x0, "target1 write resp")
    _expect(port1.read(ADDR_TARGET1), TARGET1_WRITE, "cross-port target1 read")
    _expect(port1.write(ADDR_TARGET0, TARGET0_WRITE), 0x0, "target0 write resp")
    _expect(port0.read(ADDR_TARGET0), TARGET0_WRITE, "cross-port target0 read")

    # Decode error: invalid address returns DECERR (0x3).
    _expect(
        port0.write(ADDR_INVALID, 0x55AA55AA, expected_resp=0x3),
        0x3,
        "decode error write resp",
    )
    try:
        port1.read(ADDR_INVALID)
    except Exception as exc:
        if "expected 0x0, got 0x3" not in str(exc):
            raise RuntimeError(f"unexpected AXI-Lite error: {exc}") from exc
    else:
        raise RuntimeError("expected DECERR on invalid read")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None)
    args = parser.parse_args()

    bench = build_bench()
    print("Discovered testbench plan:\n")
    print(bench.plan.summary())
    print()

    with bench.run(vcd=args.vcd):
        if args.vcd is not None:
            print(f"VCD tracing -> {args.vcd}\n")
        bench.reset_all()
        exercise_routing(bench)

    print("axi_lite_xbar passed: cross-port routing + decode error")


if __name__ == "__main__":
    main()

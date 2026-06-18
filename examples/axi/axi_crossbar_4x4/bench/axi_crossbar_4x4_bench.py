"""4x4 AXI crossbar testbench — alexforencich axi_crossbar.

Exercises all 16 source→sink routes by having each of the 4 slave-port
masters write a unique data word to each of the 4 master-port memory
regions, then reading back and verifying every word.

Originally scaffolded with:

    uv run veriforge generate-python-testbench \\
      -f examples/axi/axi_crossbar_4x4/rtl/axi_crossbar_4x4.v \\
      --include-dir examples/axi/axi_crossbar_4x4/rtl \\
      --module axi_crossbar_4x4 --enhanced --style bench \\
      --auto-deps --no-strict \\
      -o examples/axi/axi_crossbar_4x4/bench/axi_crossbar_4x4_bench.py

Plan summary:
  TestbenchPlan(top='axi_crossbar_4x4')
    domains:
      - clk: clock=clk (posedge, period=?); reset=rst (active-high, async)
    interfaces:
      - m00_axi (axi4, role=master) -> domain=clk [sole-domain]
      - m01_axi (axi4, role=master) -> domain=clk [sole-domain]
      - m02_axi (axi4, role=master) -> domain=clk [sole-domain]
      - m03_axi (axi4, role=master) -> domain=clk [sole-domain]
      - s00_axi (axi4, role=slave) -> domain=clk [sole-domain]
      - s01_axi (axi4, role=slave) -> domain=clk [sole-domain]
      - s02_axi (axi4, role=slave) -> domain=clk [sole-domain]
      - s03_axi (axi4, role=slave) -> domain=clk [sole-domain]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

SCRIPT_DIR = Path(__file__).resolve().parent
RTL_DIR = SCRIPT_DIR.parent / "rtl"

# All RTL files required to elaborate axi_crossbar_4x4 (wrapper + full dep chain).
_RTL_FILES = [
    "axi_crossbar.v",
    "axi_crossbar_rd.v",
    "axi_crossbar_wr.v",
    "axi_crossbar_addr.v",
    "axi_register.v",
    "axi_register_rd.v",
    "axi_register_wr.v",
    "arbiter.v",
    "priority_encoder.v",
    "axi_crossbar_4x4.v",
]

# Address map: M_BASE_ADDR=0, M_ADDR_WIDTH=24 per port.
# calcBaseAddrs accumulates base[i] = i * 2^24.
# Address match condition: addr[31:24] == master_index.
ADDR_SINK = [i << 24 for i in range(4)]  # [0x00000000, 0x01000000, 0x02000000, 0x03000000]

_MASTER_PORTS = ["m00_axi", "m01_axi", "m02_axi", "m03_axi"]
_SLAVE_PORTS = ["s00_axi", "s01_axi", "s02_axi", "s03_axi"]


def _data_pattern(src: int, sink: int) -> int:
    """Unique 32-bit word for source *src* writing to sink region *sink*."""
    return 0xA000_0000 | (src << 20) | (sink << 16) | 0x5A5A


def parse_dut():
    """Parse the DUT module (with all dependency files) from disk."""
    paths = [str(RTL_DIR / f) for f in _RTL_FILES]
    design = parse_files(paths)
    return design, design.get_module("axi_crossbar_4x4")


def build_bench() -> Testbench:
    """Construct the Testbench from the parsed DUT."""
    design, dut = parse_dut()
    overrides = PlannerOverrides(
        iface_domains={name: "clk" for name in [*_MASTER_PORTS, *_SLAVE_PORTS]},
    )
    return Testbench(dut, design=design, overrides=overrides, engine="vm")


def exercise_all_routes(bench: Testbench) -> None:
    """Drive all 16 source→sink routes: write unique words, read back, verify.

    Each slave port *s_i* writes one word to each of the four master regions.
    The word at ``ADDR_SINK[j] + i*4`` is always ``_data_pattern(i, j)``, so
    every (src, sink) pair exercises a distinct crossbar path with a distinct
    payload.
    """
    # Register all four master-port responders.  The AXI4Responder callback is
    # installed on the first bench.iface() call; subsequent writes/reads from
    # any slave will be auto-serviced by the appropriate responder.
    for mname in _MASTER_PORTS:
        bench.iface(mname)

    # Write phase: each source writes to every sink region.
    for src_idx, sname in enumerate(_SLAVE_PORTS):
        src_iface = bench.iface(sname)
        for sink_idx in range(4):
            addr = ADDR_SINK[sink_idx] + src_idx * 4
            src_iface.write(addr, _data_pattern(src_idx, sink_idx))

    # Read-back phase: verify all 16 routes.
    errors: list[str] = []
    for src_idx, sname in enumerate(_SLAVE_PORTS):
        src_iface = bench.iface(sname)
        for sink_idx in range(4):
            addr = ADDR_SINK[sink_idx] + src_idx * 4
            expected = _data_pattern(src_idx, sink_idx)
            got = src_iface.read(addr, length=1)[0]
            if got != expected:
                errors.append(
                    f"  s{src_idx:02d}→m{sink_idx:02d}: addr=0x{addr:08x}  expected=0x{expected:08x}  got=0x{got:08x}"
                )

    if errors:
        raise AssertionError(f"axi_crossbar_4x4: {len(errors)} route(s) failed:\n" + "\n".join(errors))

    print("axi_crossbar_4x4 passed: all 16 source→sink routes verified")


def run_smoke_test(vcd: Path | None = None) -> None:
    """Build the bench and exercise all routes (programmatic entry point)."""
    bench = build_bench()
    with bench.run(vcd=vcd):
        bench.reset_all()
        exercise_all_routes(bench)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--vcd",
        type=Path,
        default=None,
        help="Optional VCD output path. Captures every top-level DUT signal.",
    )
    args = parser.parse_args()

    bench = build_bench()
    print("Discovered testbench plan:\n")
    print(bench.plan.summary())
    print()

    with bench.run(vcd=args.vcd):
        if args.vcd is not None:
            print(f"VCD tracing -> {args.vcd}\n")
        bench.reset_all()
        exercise_all_routes(bench)

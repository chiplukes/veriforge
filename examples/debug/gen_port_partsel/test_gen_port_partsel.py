"""Isolated test for Bug 2: output port connected to PartSelect slice in generate-for loop.

The pattern from axi_crossbar_wr.v:
    axi_register_wr #(...) axi_register_wr_inst (.s_axi_bid(int_m_axi_bid[n*M_ID_WIDTH +: M_ID_WIDTH]));

Each iteration's child drives a fixed value into a distinct slice of combined_out.
Expected combined_out = 0xA3_A2_A1_A0 (COUNT=4, WIDTH=8).
"""

from pathlib import Path
from veriforge.project import parse_files
from veriforge.sim.testbench import Simulator

RTL = Path(__file__).parent / "gen_port_partsel.v"

COUNT = 4
WIDTH = 8
EXPECTED = 0xA3_A2_A1_A0  # n=3 in high bytes, n=0 in low bytes


def run_case(engine: str) -> None:
    design = parse_files([str(RTL)])
    mod = design.get_module("gen_port_partsel")
    sim = Simulator(mod, design=design, engine=engine)
    sim.run(max_time=0)

    got = int(sim.signal("combined_out").value)
    ok = got == EXPECTED
    print(f"[{engine}] combined_out: got=0x{got:08x} exp=0x{EXPECTED:08x} -> {'PASS' if ok else 'FAIL'}")
    assert ok, f"combined_out mismatch: got 0x{got:08x}, expected 0x{EXPECTED:08x}"


def main() -> None:
    for engine in ("vm", "reference"):
        run_case(engine)
    print("All cases passed.")


if __name__ == "__main__":
    main()

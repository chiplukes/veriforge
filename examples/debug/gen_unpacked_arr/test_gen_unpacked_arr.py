"""Isolated test for Bug 1: unpacked reg array element access in generate-for loop.

The pattern from axi_crossbar_addr.v:
    reg [ID_WIDTH-1:0] thread_id_reg[S_INT_THREADS-1:0];
    generate for (n = ...) begin
        always @(posedge clk)
            if (en[n]) thread_id_reg[n] <= s_axi_aid;
    end endgenerate

After a clock edge, data_in is written element-wise into arr[n].
data_out should reflect the full WIDTH bits stored in each element.

Bug symptom: arr[n] is treated as bit n of a flat scalar (1-bit storage),
so only the LSB of each element is retained.
"""

from pathlib import Path
from veriforge.project import parse_files
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

RTL = Path(__file__).parent / "gen_unpacked_arr.v"

COUNT = 4
WIDTH = 8


def run_case(engine: str) -> None:
    design = parse_files([str(RTL)])
    mod = design.get_module("gen_unpacked_arr")
    sim = Simulator(mod, design=design, engine=engine)

    # Pack data_in: element n = (0xA0 | n)
    # n=0->0xA0, n=1->0xA1, n=2->0xA2, n=3->0xA3
    data_in = 0
    for i in range(COUNT):
        data_in |= (0xA0 | i) << (i * WIDTH)

    expected = data_in  # after one clock, data_out should match data_in

    # Drive data_in before clock. Use schedule_at to properly toggle clk
    # so the VM edge-detection mechanism fires the always @posedge clk blocks.
    sim.drive("data_in", data_in)
    sim._sched.schedule_at(0, ("clock_toggle", "clk", Value(0, width=1)))
    sim._sched.schedule_at(1, ("clock_toggle", "clk", Value(1, width=1)))  # posedge at t=1
    sim._sched.schedule_at(2, ("clock_toggle", "clk", Value(0, width=1)))
    sim._sched.run(max_time=4)

    got = int(sim.signal("data_out").value)
    ok = got == expected
    print(f"[{engine}] data_out: got=0x{got:08x} exp=0x{expected:08x} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        for i in range(COUNT):
            g_elem = (got >> (i * WIDTH)) & 0xFF
            e_elem = (expected >> (i * WIDTH)) & 0xFF
            status = "OK" if g_elem == e_elem else "FAIL"
            print(f"  arr[{i}]: got=0x{g_elem:02x} exp=0x{e_elem:02x} {status}")
    assert ok, f"data_out mismatch: got 0x{got:08x}, expected 0x{expected:08x}"


def main() -> None:
    # Reference engine does not support generate-for + unpacked arrays
    for engine in ("vm",):
        run_case(engine)
    print("All cases passed.")


if __name__ == "__main__":
    main()

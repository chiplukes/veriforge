"""Isolated test for the generate-for PartSelect LHS parser fix.

Tests the pattern:
    assign int_axi_bvalid[n*S_COUNT +: S_COUNT] = int_m_axi_bvalid[n] << bsel;
    assign int_m_axi_bready[n] = int_axi_bready[bsel*M_COUNT + n];

With M_COUNT=4, S_COUNT=4, the 4 generate iterations produce:
  n=0: int_axi_bvalid[0:3]  = int_m_axi_bvalid[0] << bsel[0]
  n=1: int_axi_bvalid[4:7]  = int_m_axi_bvalid[1] << bsel[1]
  n=2: int_axi_bvalid[8:11] = int_m_axi_bvalid[2] << bsel[2]
  n=3: int_axi_bvalid[12:15]= int_m_axi_bvalid[3] << bsel[3]
"""

from pathlib import Path
from veriforge.project import parse_files
from veriforge.sim.testbench import Simulator

RTL = Path(__file__).parent / "gen_partselect.v"

M_COUNT = 4
S_COUNT = 4


def make_sim() -> Simulator:
    design = parse_files([str(RTL)])
    mod = design.get_module("gen_partselect")
    sim = Simulator(mod, design=design, engine="vm")
    return sim


def run_case(engine: str, desc: str, m_bvalid: int, b_select: list[int], bready: int):
    """Drive inputs, evaluate, read back outputs; assert expected values."""
    design = parse_files([str(RTL)])
    mod = design.get_module("gen_partselect")
    sim = Simulator(mod, design=design, engine=engine)

    # Pack b_select: 2 bits per master, n=0 in LSBs
    bsel_packed = 0
    for i, v in enumerate(b_select):
        bsel_packed |= (v & 0x3) << (i * 2)

    sim.drive("int_m_axi_bvalid", m_bvalid)
    sim.drive("b_select", bsel_packed)
    sim.drive("int_axi_bready", bready)
    sim.run(max_time=0)  # evaluate combinational logic

    got_bvalid = int(sim.signal("int_axi_bvalid").value)
    got_bready = int(sim.signal("int_m_axi_bready").value)

    # Compute expected int_axi_bvalid
    exp_bvalid = 0
    for n in range(M_COUNT):
        bit = (m_bvalid >> n) & 1
        sel = b_select[n]
        # bit shifted left by sel within the 4-bit slice at offset n*S_COUNT
        slot = bit << sel
        exp_bvalid |= (slot & 0xF) << (n * S_COUNT)

    # Compute expected int_m_axi_bready
    exp_bready = 0
    for n in range(M_COUNT):
        sel = b_select[n]
        bit_idx = sel * M_COUNT + n
        exp_bready |= ((bready >> bit_idx) & 1) << n

    ok_bv = got_bvalid == exp_bvalid
    ok_br = got_bready == exp_bready
    status = "PASS" if (ok_bv and ok_br) else "FAIL"
    print(
        f"[{engine}] {desc}: "
        f"bvalid got=0x{got_bvalid:04x} exp=0x{exp_bvalid:04x} {'OK' if ok_bv else 'FAIL'} | "
        f"bready got=0b{got_bready:04b} exp=0b{exp_bready:04b} {'OK' if ok_br else 'FAIL'} "
        f"-> {status}"
    )
    assert ok_bv, f"int_axi_bvalid mismatch: got 0x{got_bvalid:04x}, exp 0x{exp_bvalid:04x}"
    assert ok_br, f"int_m_axi_bready mismatch: got 0b{got_bready:04b}, exp 0b{exp_bready:04b}"


def main():
    for engine in ("vm", "reference"):
        # Case 1: master 0 has bvalid, routed to slave slot 0 (b_select[0]=0)
        run_case(engine, "m0->s0", m_bvalid=0b0001, b_select=[0, 0, 0, 0], bready=0xFFFF)

        # Case 2: master 0 has bvalid, routed to slave slot 2 (b_select[0]=2)
        run_case(engine, "m0->s2", m_bvalid=0b0001, b_select=[2, 0, 0, 0], bready=0xFFFF)

        # Case 3: master 1 has bvalid, routed to slave slot 1
        run_case(engine, "m1->s1", m_bvalid=0b0010, b_select=[0, 1, 0, 0], bready=0xFFFF)

        # Case 4: all masters have bvalid, each routed to a different slave slot
        run_case(engine, "all->diag", m_bvalid=0b1111, b_select=[0, 1, 2, 3], bready=0xFFFF)

        # Case 5: check bready back-pressure: only slot 2 has bready for master 0
        run_case(
            engine, "bready sel", m_bvalid=0b0001, b_select=[2, 0, 0, 0], bready=(1 << (2 * M_COUNT + 0))
        )  # bsel=2, n=0 → bit index 8

        print()
    print("All cases passed.")


if __name__ == "__main__":
    main()

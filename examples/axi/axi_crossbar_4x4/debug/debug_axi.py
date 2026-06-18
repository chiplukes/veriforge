"""Targeted debug script for axi_crossbar_4x4 B-channel deadlock.

Usage: uv run python debug_axi.py [engine] [timeout]
  engine: reference | vm (default: vm)
  timeout: max cycles for write (default: 30)

Produces debug_axi.vcd alongside per-posedge signal printout.
"""

import sys
from pathlib import Path
from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench
from veriforge.sim.trace import attach_vcd, register_time_step_callback

RTL_DIR = Path("examples/axi/axi_crossbar_4x4/rtl")
RTL_FILES = [
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
paths = [str(RTL_DIR / f) for f in RTL_FILES]

ENGINE = sys.argv[1] if len(sys.argv) > 1 else "vm"
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 30
VCD_OUT = f"debug_axi_{ENGINE}.vcd"
print(f"=== Testing with engine={ENGINE!r}, timeout={TIMEOUT}, vcd={VCD_OUT} ===")

design = parse_files(paths)
dut = design.get_module("axi_crossbar_4x4")
overrides = PlannerOverrides(
    iface_domains={
        n: "clk" for n in ["m00_axi", "m01_axi", "m02_axi", "m03_axi", "s00_axi", "s01_axi", "s02_axi", "s03_axi"]
    }
)
bench = Testbench(dut, design=design, overrides=overrides, engine=ENGINE)

MASTER_PORTS = ["m00_axi", "m01_axi", "m02_axi", "m03_axi"]

TRACE_SIGS = [
    "m00_axi_bvalid",
    "m00_axi_bready",
    "s00_axi_bvalid",
    "s00_axi_bready",
]

print("--- Single write: s00 -> m00 (addr=0x00000000) ---")
try:
    with bench.run():
        with attach_vcd(bench.sim, VCD_OUT):
            bench.reset_all()
            print("Reset done.")

            for mname in MASTER_PORTS:
                bench.iface(mname)

            src = bench.iface("s00_axi")

            # Install per-posedge trace callback
            _prev_clk = [None]
            _posedge_count = [0]
            _trace_active = [False]

            def _trace_cb(_sched):
                sim = bench.sim
                try:
                    clk_val = int(sim.signal("clk").value)
                except Exception:
                    return
                prev = _prev_clk[0]
                _prev_clk[0] = clk_val
                if prev == 0 and clk_val == 1:  # posedge
                    if not _trace_active[0]:
                        return
                    _posedge_count[0] += 1
                    cyc = _posedge_count[0]
                    vals = {}
                    for s in TRACE_SIGS:
                        try:
                            v = sim.signal(s).value
                            vals[s] = int(v) if v is not None else "X"
                        except Exception:
                            vals[s] = "?"
                    print(
                        f"  [posedge {cyc:3d}] "
                        f"m00:bv={vals.get('m00_axi_bvalid', '?')} br={vals.get('m00_axi_bready', '?')}  "
                        f"s00:bv={vals.get('s00_axi_bvalid', '?')} br={vals.get('s00_axi_bready', '?')}"
                    )

            handle = register_time_step_callback(bench.sim._sched, _trace_cb)

            print("Responders ready. Starting write trace...")
            _trace_active[0] = True
            src.write(0, 0xDEAD_BEEF, timeout_cycles=TIMEOUT)
            _trace_active[0] = False
            print(f"Write succeeded after {_posedge_count[0]} posedges!")
            handle.close()

    print(f"VCD written to: {VCD_OUT}")

except Exception as e:
    import traceback

    traceback.print_exc()
    print(f"FAIL: {type(e).__name__}: {e}")
    print(f"VCD written to: {VCD_OUT} (partial)")

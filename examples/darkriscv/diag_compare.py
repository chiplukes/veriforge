"""Quick diagnostic: compare compiled vs reference PC at key timepoints."""

import os
import time

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim"))

from veriforge.project import parse_files
from veriforge.sim.testbench import Simulator

RTL_DIR = os.path.join("..", "rtl")
DEFINES = {"SIMULATION": "", "__ICARUS__": "", "__RESETPC__": "32'd0"}
FILES = [
    os.path.join(RTL_DIR, f)
    for f in [
        "config.vh",
        "darksocv.v",
        "darkbridge.v",
        "darkriscv.v",
        "darkram.v",
        "darkio.v",
        "darkuart.v",
        "darkpll.v",
    ]
]
FILES.insert(1, "darksimv.v")

d = parse_files(FILES, preprocess=True, defines=DEFINES, include_paths=[RTL_DIR])
top = d.get_top_modules()[0]

SIGNALS = [
    "soc0.bridge0.core0.PC",
    "soc0.bridge0.core0.FLUSH",
    "soc0.bridge0.core0.XRES",
    "soc0.bridge0.core0.HLT",
    "soc0.bridge0.core0.JREQ",
]

CHECKPOINTS = [500, 1000, 1500, 2000, 3000, 5000]

for eng in ["reference", "compiled"]:
    print(f"\n=== {eng.upper()} ENGINE ===")
    t0 = time.time()
    sim = Simulator(top, engine=eng, design=d)
    for cp in CHECKPOINTS:
        sim.run(max_time=cp)
        vals = {s.split(".")[-1]: str(sim.read(s)) for s in SIGNALS}
        print(
            f"  t={cp:5d}: PC={vals['PC']}  FLUSH={vals['FLUSH']}  XRES={vals['XRES']}  HLT={vals['HLT']}  JREQ={vals['JREQ']}"
        )
    elapsed = time.time() - t0
    print(f"  ({elapsed:.1f}s total)")
    print(f"  display_lines = {len(sim.display_output)}")
    for line in sim.display_output[:5]:
        print(f"    > {line}")

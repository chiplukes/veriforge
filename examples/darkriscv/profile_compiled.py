"""Profile the compiled engine on DarkRISCV for a short run."""

import cProfile
import os
import pstats
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

MAX_TIME = 50_000

d = parse_files(FILES, preprocess=True, defines=DEFINES, include_paths=[RTL_DIR])
top = d.get_top_modules()[0]

sim = Simulator(top, engine="compiled", design=d)

prof = cProfile.Profile()
prof.enable()
t0 = time.time()
sim.run(max_time=MAX_TIME)
elapsed = time.time() - t0
prof.disable()

print(f"Completed {MAX_TIME} time units in {elapsed:.2f}s")
print(f"Display lines: {len(sim.display_output)}")

stats = pstats.Stats(prof)
stats.sort_stats("cumulative")
stats.print_stats(40)
print("\n--- By tottime ---")
stats.sort_stats("tottime")
stats.print_stats(40)

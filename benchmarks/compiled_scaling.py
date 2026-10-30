"""Measure compiled build options and idle-state scaling with fresh caches.

Run ``uv run python benchmarks/compiled_scaling.py --case helpers`` or select
``idle``, ``instances``, ``split``, or ``snapshots``. JSON rows include build time, generated
source size, and median runtime excluding initialization/builds.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time

from veriforge.sim.testbench import Simulator
from veriforge.sim.compiled.compiler import _build_configuration
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import VerilogParser


def counter_source(extra=0, *, idle_processes=False, width=16, idle_width=16):
    body = (
        "reg [15:0] idle_q; always @(posedge idle_clk) if (rst) idle_q <= 0; else idle_q <= idle_q + 16'd1;"
        if idle_processes
        else f"wire [{idle_width - 1}:0] idle_q;"
    )
    return f"""module top(input clk, idle_clk, rst, output reg [{width - 1}:0] q);
  always @(posedge clk) if (rst) q <= 0; else q <= q + {width}'d1;
  for (genvar i=0; i<{extra}; i=i+1) begin : g {body} end
endmodule"""


def instances_source(lanes):
    return f"""module leaf(input clk, rst, en, output reg [15:0] q, output [15:0] y);
  always @(posedge clk) if (rst) q <= 0; else if (en) q <= q + 16'd1;
  assign y = (q ^ (q >> 3)) + 16'd1;
endmodule
module top(input clk, rst, en);
  for (genvar i=0; i<{lanes}; i=i+1) begin : g
    wire [15:0] q, y;
    leaf u(.clk(clk), .rst(rst), .en(en), .q(q), .y(y));
  end
endmodule"""


def fragmented_source(lanes):
    return f"""module top(input clk, idle_clk, rst, output reg [15:0] q);
  always @(posedge clk) if (rst) q <= 0; else q <= q + 16'd1;
  for (genvar i=0; i<{lanes}; i=i+1) begin : g
    reg [15:0] lane, padding_a, padding_b, padding_c;
    always @(posedge clk) if (rst) lane <= 0; else lane <= lane + 16'd1;
  end
endmodule"""


@contextmanager
def options(values):
    old = {name: os.environ.get(name) for name in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def measure(source, *, cycles, repeats, label, config, output):
    parser = VerilogParser(start="source_text")
    design = tree_to_design(parser.build_tree(source))
    with tempfile.TemporaryDirectory(prefix="vf_scaling_", ignore_cleanup_errors=True) as cache:
        with options({**config, "VERIFORGE_COMPILE_CACHE": cache}):
            start = time.perf_counter()
            sim = Simulator(design.get_module("top"), design=design, engine="compiled")
            build_seconds = time.perf_counter() - start
            names = list(sim._sched.signal_names())
            sim.drive("clk", 0)
            sim.drive("rst", 1)
            for name in ("idle_clk", "en"):
                if name in names:
                    sim.drive(name, int(name == "en"))
            sim.batch_run(2, "clk")
            sim.drive("rst", 0)
            sim.batch_run(1000, "clk")
            times = []
            for _ in range(repeats):
                start = time.perf_counter()
                sim.batch_run(cycles, "clk")
                times.append((time.perf_counter() - start) / cycles * 1e9)
            target = "q" if "q" in names else next(name for name in names if name.endswith(".u.q"))
            result = sim.read(target)
            assert result.is_defined
            assert int(result) == (1000 + cycles * repeats) & ((1 << result.width) - 1)
            files = list(Path(cache).glob("*/*.pyx"))
            lines = 0
            implementations = 0
            for path in files:
                for line in path.open(encoding="utf-8"):
                    lines += 1
                    implementations += line.startswith("cdef void _vf_shared_impl_")
            row = {
                "case": label,
                "options": config,
                "effective_build": json.loads(_build_configuration()),
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cycles_per_sample": cycles,
                "runtime_repeats": repeats,
                "build_seconds": build_seconds,
                "ns_per_cycle": statistics.median(times),
                "runtime_samples_ns": times,
                "pyx_files": len(files),
                "pyx_lines": lines,
                "shared_implementations": implementations,
                "signals": sim._sched._codegen.n_sigs,
            }
            print(json.dumps(row), flush=True)
            if output:
                with Path(output).open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", choices=("helpers", "idle", "instances", "split", "snapshots", "fragmented"), default="helpers"
    )
    parser.add_argument("--lanes", type=int, default=64)
    parser.add_argument("--cycles", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output")
    args = parser.parse_args()
    if min(args.lanes, args.cycles, args.repeats) < 1:
        parser.error("lanes, cycles, and repeats must be positive")
    base = {
        "VERIFORGE_DELTA_ENGINE": "queue",
        "VERIFORGE_COMPILE_SPLIT": "0",
        "VERIFORGE_COMPILE_HELPERS": "needed",
        "VERIFORGE_COMPILE_SNAPSHOTS": "needed",
        "VERIFORGE_COMPILE_SHARE_PROCESSES": "0",
        "VERIFORGE_NO_COMPILE_CACHE": "0",
    }
    cases = []
    if args.case == "helpers":
        cases = [
            (counter_source(width=width), f"{width}bit_{mode}", {"VERIFORGE_COMPILE_HELPERS": mode})
            for width in (16, 128)
            for mode in ("all", "needed")
        ]
    elif args.case == "idle":
        cases = [(counter_source(), "baseline", {})]
        cases.extend(
            (counter_source(n, idle_processes=processes), f"idle_{n}_{'processes' if processes else 'signals'}", {})
            for n in (128, 512)
            for processes in (False, True)
        )
    elif args.case in {"snapshots", "fragmented"}:
        sources = (
            ("small", counter_source()),
            ("sparse_scalar", counter_source(512)),
            ("sparse_wide", counter_source(512, width=128, idle_width=256)),
            ("dense_scalar", counter_source(512, idle_processes=True)),
            ("instances", instances_source(args.lanes)),
            ("fragmented", fragmented_source(args.lanes)),
        )
        if args.case == "fragmented":
            sources = sources[-1:]
        cases = [
            (source, f"snapshots_{label}_{mode}", {"VERIFORGE_COMPILE_SNAPSHOTS": mode})
            for label, source in sources
            for mode in ("all", "needed")
        ]
    else:
        option = "VERIFORGE_COMPILE_SHARE_PROCESSES" if args.case == "instances" else "VERIFORGE_COMPILE_SPLIT"
        cases = [(instances_source(args.lanes), f"{args.case}_{value}", {option: value}) for value in ("0", "1")]
    for source, label, overrides in cases:
        measure(
            source,
            cycles=args.cycles,
            repeats=args.repeats,
            label=label,
            config={**base, **overrides},
            output=args.output,
        )


if __name__ == "__main__":
    main()

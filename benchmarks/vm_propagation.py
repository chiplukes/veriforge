#!/usr/bin/env python3
"""Exercise VM propagation on sparse, active, memory, and lowered workloads.

Run: uv run python benchmarks/vm_propagation.py --cycles 10000 --repeat 3
Each batch result is checked against the VM event loop, including memory cells.
Parsing and lowering happen once, outside the timed execution section.
"""

from __future__ import annotations

import argparse
import statistics
import time
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import patch

from benchmark import Clock, Simulator, Value
from veriforge.sim.bench import AXIStreamSinkLowering, AXIStreamSourceLowering, Testbench, compile_native
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(src):
    return tree_to_design(verilog_parser(start="source_text").build_tree(src)).modules[0]


def _many_assigns(n: int, *, active: bool, dependent: bool = False):
    assigns = ["wire [7:0] out0; assign out0 = q[7:0];"]
    for i in range(1, n):
        rhs = f"q[7:0] + 8'd{i % 256}" if active else f"src{i} + 8'd1"
        assigns.append(f"reg [7:0] src{i}; wire [7:0] out{i}; assign out{i} = {rhs};")
    if dependent:
        assigns.append("wire [7:0] tail; assign tail = out0;")
    init = " ".join(f"src{i}=8'd{i % 256};" for i in range(1, n))
    return (
        _parse(
            "module many(input clk, output reg [31:0] q); "
            + " ".join(assigns)
            + f" initial begin q=0; {init} end always @(posedge clk) q <= q + 1; endmodule"
        ),
        None,
        None,
    )


def _memory():
    return (
        _parse("""
    module memory(input clk, output wire [7:0] data);
      reg [7:0] mem [0:255];
      reg [7:0] addr;
      initial begin addr = 0; mem[0] = 0; end
      always @(posedge clk) begin
        mem[addr] <= addr;
        addr <= addr + 1;
      end
      assign data = mem[addr];
    endmodule
    """),
        None,
        None,
    )


def _chain(stages: int, *, reconvergent: bool):
    declarations = " ".join(f"wire [7:0] n{i};" for i in range(stages))
    assignments = []
    for i in reversed(range(stages)):
        parent = f"n{i - 1}" if i else "q"
        rhs = "q" if reconvergent else "8'd1"
        assignments.append(f"assign n{i} = {parent} + {rhs};")
    return (
        _parse(
            "module chain(input clk, output reg [7:0] q); "
            + declarations
            + " ".join(assignments)
            + " initial q=0; always @(posedge clk) q<=q+1; endmodule"
        ),
        None,
        None,
    )


def _lowered():
    module = _parse("""
    module axis_loopback(
      input clk, input rst_n,
      input m_axis_tvalid, output m_axis_tready,
      input [7:0] m_axis_tdata, input m_axis_tlast,
      output s_axis_tvalid, input s_axis_tready,
      output [7:0] s_axis_tdata, output s_axis_tlast
    );
      assign s_axis_tvalid = m_axis_tvalid;
      assign s_axis_tdata = m_axis_tdata;
      assign s_axis_tlast = m_axis_tlast;
      assign m_axis_tready = s_axis_tready;
      reg [7:0] tick;
      always @(posedge clk or negedge rst_n)
        if (!rst_n) tick <= 0; else tick <= tick + 1;
    endmodule
    """)
    lowered = compile_native(
        Testbench(module),
        lowerings={
            "m_axis": AXIStreamSourceLowering(list(range(32)), data_width=8),
            "s_axis": AXIStreamSinkLowering(32, data_width=8),
        },
    )
    return lowered.wrapper, lowered.design, [(0, "rst_n", 0), (4, "rst_n", 1)]


def _run(module, design, events, cycles, mode):
    construction = (
        patch("veriforge.sim.vm.vm_scheduler.plan_continuous_order", return_value=None)
        if mode == "legacy"
        else nullcontext()
    )
    with construction:
        sim = Simulator(deepcopy(module), design=design, engine="vm-fast")
    if sim._sched._cy_ctx is None or not hasattr(sim._sched._cy_ctx, "batch_run"):
        raise RuntimeError("Build the native VM: uv run python setup_cython.py build_ext --inplace")
    t0 = time.perf_counter()
    if mode in ("batch", "legacy"):
        completed = sim.batch_run(cycles, "clk", events=events)
        assert completed == cycles
    else:
        for cycle, name, value in events or ():
            if cycle == 0:
                sim.drive(name, value)
            else:
                sim._sched.schedule_at(cycle * 10, ("clock_toggle", name, Value(value)))
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.run(max_time=cycles * 10 - 5)
    elapsed = time.perf_counter() - t0
    names = sorted(sim._sched.signal_names())
    for name, mid in sim._sched.compiler.mem_map.items():
        _, depth, _ = sim._sched.compiler.mem_info[mid]
        names.extend(f"{name}[{i}]" for i in range(depth))
    state = {name: (val.val, val.mask, val.width) for name in names for val in [sim.read(name)]}
    return elapsed, state


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", type=int, default=10000)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--assigns", type=int, default=256)
    ap.add_argument("--stages", type=int, default=64)
    ap.add_argument(
        "--compare-legacy", action="store_true", help="also time batch execution with dependency ordering disabled"
    )
    args = ap.parse_args()
    if min(args.cycles, args.repeat, args.assigns, args.stages) < 1:
        ap.error("cycles, repeat, assigns, and stages must be positive")
    workloads = {
        "sparse": _many_assigns(args.assigns, active=False),
        "active": _many_assigns(args.assigns, active=True),
        "shallow-fanout": _many_assigns(args.assigns, active=True, dependent=True),
        "chain": _chain(args.stages, reconvergent=False),
        "reconvergent": _chain(args.stages, reconvergent=True),
        "memory": _memory(),
        "lowered-axis": _lowered(),
    }
    for name, (module, design, events) in workloads.items():
        events = [(cycle, sig, val) for cycle, sig, val in events or () if cycle < args.cycles]
        _run(module, design, events, min(args.cycles, 100), "batch")
        pairs = []
        legacy_times = []
        for _ in range(args.repeat):
            step = _run(module, design, events, args.cycles, "step")
            batch = _run(module, design, events, args.cycles, "batch")
            if step[1] != batch[1]:
                keys = [k for k in step[1] if step[1][k] != batch[1].get(k)]
                raise AssertionError(f"{name}: final state mismatch at {keys[:8]}")
            pairs.append((step[0], batch[0]))
            if args.compare_legacy:
                legacy = _run(module, design, events, args.cycles, "legacy")
                if legacy[1] != batch[1]:
                    raise AssertionError(f"{name}: final state differs from legacy batch propagation")
                legacy_times.append(legacy[0])
        step_time = statistics.median(x for x, _ in pairs)
        batch_time = statistics.median(x for _, x in pairs)
        comparison = f", {statistics.median(legacy_times) / batch_time:.2f}x vs legacy batch" if legacy_times else ""
        print(
            f"{name}: step {step_time:.4f}s, batch {batch_time:.4f}s, "
            f"{args.cycles / batch_time:,.0f} batch cycles/s, "
            f"{step_time / batch_time:.2f}x batch/step{comparison}",
            flush=True,
        )


if __name__ == "__main__":
    main()

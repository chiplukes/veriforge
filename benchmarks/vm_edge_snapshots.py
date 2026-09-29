#!/usr/bin/env python3
"""Measure native VM batch edge bookkeeping with many inactive signals.

Run: uv run python benchmarks/vm_edge_snapshots.py --cycles 20000 --repeat 5
Construction and parsing are excluded. Every batch result is compared with
the event-driven native VM, including all signal values, masks, and widths.
"""

from __future__ import annotations

import argparse
import statistics

from vm_propagation import _many_assigns, _parse, _run


def _dormant(count):
    names = ",".join(f"d{i}" for i in range(count))
    return _parse(
        "module dormant(input clk, output reg [31:0] q); "
        f"reg [7:0] {names}; "
        "initial q=0; always @(posedge clk) q<=q+1; endmodule"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--dormant-counts", nargs="+", type=int, default=[1024, 4096])
    parser.add_argument("--sparse-assigns", type=int, default=1024)
    args = parser.parse_args()
    if min(args.cycles, args.repeat, args.sparse_assigns, *args.dormant_counts) < 1:
        parser.error("all counts must be positive")

    workloads = [(f"dormant-{count}", _dormant(count)) for count in args.dormant_counts]
    workloads.append((f"sparse-{args.sparse_assigns}", _many_assigns(args.sparse_assigns, active=False)[0]))
    for name, module in workloads:
        _run(module, None, None, min(args.cycles, 100), "batch")
        pairs = []
        for _ in range(args.repeat):
            step = _run(module, None, None, args.cycles, "step")
            batch = _run(module, None, None, args.cycles, "batch")
            if step[1] != batch[1]:
                raise AssertionError(f"{name}: final state differs from event loop")
            pairs.append((step[0], batch[0]))
        step_time = statistics.median(x for x, _ in pairs)
        batch_time = statistics.median(y for _, y in pairs)
        print(
            f"{name}: step {step_time:.4f}s, batch {batch_time:.4f}s, "
            f"{args.cycles / batch_time:,.0f} batch cycles/s, "
            f"{step_time / batch_time:.2f}x batch/step",
            flush=True,
        )


if __name__ == "__main__":
    main()

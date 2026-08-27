"""Regression test for the VM engine's scalar non-blocking-write queue
(`NBA_MAX`, `sim/vm/_interp_fast.pyx`) being hardcoded too small for a
design with many parallel-triggered `always_ff` writes in a single
delta-loop iteration.

`NBA_MAX` defaults to 1024, sized generously for ordinary designs, but a
design with many parallel-instantiated submodules (e.g. a `generate`-loop
of dozens of copies, each with its own internal pipeline registers) can
legitimately queue more non-blocking writes than that when they all fire
on the same clock edge -- found via real-world feedback on
`axis_dg_merge.sv` (64 `axis_dg_one_merge` instances triggering together).
This queue's own overflow check is sound (it raises a clean
`RuntimeError` rather than corrupting anything), but a legitimate design
should not need to hit that error at all.

Fixed by sizing `nba_cap` off `sig_count` (`max(NBA_MAX, sig_count * 8)`,
`CyContext.setup`) instead of only the fixed default.
"""

from __future__ import annotations

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

N = 300  # >> NBA_MAX // 4: enough parallel always_ff writes to exceed the old fixed 1024-entry cap.


def _build_design(tmp_path):
    decls = []
    procs = []
    ports = ["input logic clk", "input logic [15:0] d"]
    for i in range(N):
        ports.append(f"output logic [15:0] q{i}")
        decls.append(f"logic [15:0] r{i}_a, r{i}_b, r{i}_c;")
        procs.append(
            f"""
            always_ff @(posedge clk) begin
                r{i}_a <= d + {i};
                r{i}_b <= r{i}_a;
                r{i}_c <= r{i}_b;
                q{i} <= r{i}_c;
            end
            """
        )
    src = (
        "module top (\n    "
        + ",\n    ".join(ports)
        + "\n);\n"
        + "\n".join(decls)
        + "\n"
        + "\n".join(procs)
        + "\nendmodule\n"
    )
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


class TestVmNbaScalarQueueCapacity:
    def test_many_parallel_always_ff_writes_in_one_edge(self, tmp_path):
        design = _build_design(tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine="vm-fast", design=design)
        sim.drive("d", 5)
        sim.drive("clk", 0)
        # 4 edges: propagate through the r{i}_a -> r{i}_b -> r{i}_c -> q{i} chain.
        for _ in range(4):
            sim.settle()
            sim.drive("clk", 1)
            sim.settle()
            sim.drive("clk", 0)
        for i in range(N):
            got = int(sim.signal(f"q{i}").value)
            assert got == 5 + i, f"q{i}: want {5 + i}, got {got}"

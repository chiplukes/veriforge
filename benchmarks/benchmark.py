#!/usr/bin/env python3
"""Simulation benchmark — all engines and external simulators.

Benchmarks Reference, VM (Python), VM (Cython), Compiled (step), and
Compiled (batch) engines against Icarus Verilog and Verilator (if found).

Usage:
    uv run python benchmarks/benchmark.py                  # 50K cycles, console output
    uv run python benchmarks/benchmark.py --cycles 100000  # more cycles
    uv run python benchmarks/benchmark.py --update         # also write notes/benchmarks.md
    uv run python benchmarks/benchmark.py --profile        # cProfile on reference engine
"""

from __future__ import annotations

import argparse
import cProfile
import io
import os
import platform
import pstats
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser

# ──────────────────────────────────────────────────────────────────────────────
# DUT
# ──────────────────────────────────────────────────────────────────────────────
# Medium-high complexity designed to exercise the major simulation paths:
#   - 8-bit ALU: 12 operations including carry via concatenation LHS
#   - 8-entry register file: memory array with synchronous write
#   - 4-state FSM: IDLE → LOAD → EXEC → STORE cycle
#   - Free-running counters: 16-bit cycle counter + 8-bit phase counter
#   - 16-bit LFSR: XOR-feedback shift register
#   - 16-bit accumulator: conditional on FSM state
#   - Continuous-assign chain: 4 assigns with mux and reduction-OR
#   - Counter-driven stimulus: inputs change every cycle without a testbench driver
#
# Ports: input clk, rst; output [15:0] accum_out.
# Clock and reset are driven by testbench wrappers (not internal initial blocks)
# for the VM / compiled engine runners.

BENCH_DUT = """\
module bench(
    input wire clk,
    input wire rst,
    output wire [15:0] accum_out
);

    // ── Free-running counters ──
    reg [15:0] cycle_count;
    reg [7:0]  phase;

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            cycle_count <= 16'd0;
            phase <= 8'd0;
        end else begin
            cycle_count <= cycle_count + 16'd1;
            phase <= phase + 8'd1;
        end
    end

    // ── 8-entry register file ──
    reg [7:0] regfile [0:7];
    reg [2:0] wr_addr, rd_addr_a, rd_addr_b;
    reg [7:0] wr_data;
    reg       wr_en;
    wire [7:0] rd_data_a, rd_data_b;

    assign rd_data_a = regfile[rd_addr_a];
    assign rd_data_b = regfile[rd_addr_b];

    always @(posedge clk) begin
        if (wr_en)
            regfile[wr_addr] <= wr_data;
    end

    // ── ALU ──
    reg [3:0] alu_op;
    reg [7:0] alu_a, alu_b;
    reg [7:0] alu_result;
    reg       alu_zero, alu_carry;

    always @(*) begin
        alu_carry = 1'b0;
        case (alu_op)
            4'd0: alu_result = alu_a + alu_b;
            4'd1: alu_result = alu_a - alu_b;
            4'd2: alu_result = alu_a & alu_b;
            4'd3: alu_result = alu_a | alu_b;
            4'd4: alu_result = alu_a ^ alu_b;
            4'd5: alu_result = ~alu_a;
            4'd6: alu_result = alu_a << alu_b[2:0];
            4'd7: alu_result = alu_a >> alu_b[2:0];
            4'd8: begin
                {alu_carry, alu_result} = alu_a + alu_b;
            end
            4'd9:  alu_result = alu_a * alu_b;
            4'd10: alu_result = (alu_a > alu_b) ? alu_a : alu_b;
            4'd11: alu_result = (alu_a < alu_b) ? alu_a : alu_b;
            default: alu_result = 8'd0;
        endcase
        alu_zero = (alu_result == 8'd0);
    end

    // ── FSM ──
    reg [1:0] state, next_state;
    parameter S_IDLE  = 2'd0;
    parameter S_LOAD  = 2'd1;
    parameter S_EXEC  = 2'd2;
    parameter S_STORE = 2'd3;

    always @(posedge clk or posedge rst) begin
        if (rst)
            state <= S_IDLE;
        else
            state <= next_state;
    end

    always @(*) begin
        next_state = state;
        wr_en = 1'b0;
        case (state)
            S_IDLE:  next_state = S_LOAD;
            S_LOAD:  next_state = S_EXEC;
            S_EXEC:  next_state = S_STORE;
            S_STORE: begin
                wr_en = 1'b1;
                next_state = S_IDLE;
            end
        endcase
    end

    // ── Stimulus generation (driven by counters) ──
    always @(posedge clk) begin
        if (!rst) begin
            alu_op <= phase[3:0];
            alu_a <= cycle_count[7:0];
            alu_b <= cycle_count[15:8] ^ phase;
            rd_addr_a <= phase[2:0];
            rd_addr_b <= phase[2:0] + 3'd1;
            wr_addr <= phase[5:3];
            wr_data <= alu_result;
        end
    end

    // ── Continuous assign chain ──
    wire [7:0] sum_ab;
    wire [7:0] diff_ab;
    wire [7:0] combined;
    wire       flag;

    assign sum_ab = rd_data_a + rd_data_b;
    assign diff_ab = rd_data_a - rd_data_b;
    assign combined = (phase[0]) ? sum_ab : diff_ab;
    assign flag = |combined;

    // ── LFSR ──
    reg [15:0] shift_reg;
    always @(posedge clk or posedge rst) begin
        if (rst)
            shift_reg <= 16'd1;
        else
            shift_reg <= {shift_reg[14:0], shift_reg[15] ^ shift_reg[13]};
    end

    // ── Accumulator ──
    reg [15:0] accum;
    always @(posedge clk or posedge rst) begin
        if (rst)
            accum <= 16'd0;
        else if (state == S_STORE)
            accum <= accum + {8'd0, alu_result};
    end

    assign accum_out = accum;

endmodule
"""

DUT_DESCRIPTION = "ALU + RegFile + FSM + Counter + LFSR + Accumulator + Continuous Assigns"


# ──────────────────────────────────────────────────────────────────────────────
# Tool discovery
# ──────────────────────────────────────────────────────────────────────────────

_WINDOWS_SEARCH_DIRS = [
    r"C:\iverilog\bin",
    r"C:\Program Files\Icarus Verilog\bin",
    r"C:\Program Files (x86)\Icarus Verilog\bin",
]

_VERILATOR_WINDOWS_DIRS = [
    r"C:\msys64\mingw64\bin",
    r"C:\verilator\bin",
]


def _find_tool(name: str, extra_dirs: list[str] | None = None) -> str | None:
    env_val = os.environ.get(name.upper())
    if env_val and os.path.isfile(env_val):
        return env_val
    found = shutil.which(name)
    if found:
        return found
    search = extra_dirs or []
    if sys.platform == "win32":
        search = _WINDOWS_SEARCH_DIRS + search
    for d in search:
        for suffix in ("", ".exe"):
            candidate = os.path.join(d, name + suffix)
            if os.path.isfile(candidate):
                return candidate
    return None


IVERILOG = _find_tool("iverilog")
VVP = _find_tool("vvp")


# ──────────────────────────────────────────────────────────────────────────────
# Machine info
# ──────────────────────────────────────────────────────────────────────────────


def _get_cpu_name() -> str:
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    name = platform.processor()
    if name:
        return name
    return platform.machine() or "unknown"


def _get_ram_gb() -> float | None:
    try:
        import psutil  # type: ignore[import]

        return psutil.virtual_memory().total / 1024**3
    except ImportError:
        pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / 1024**2
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / 1024**3
        except Exception:
            pass
    return None


def collect_machine_info() -> dict:
    uname = platform.uname()
    info: dict = {
        "os": f"{uname.system} {uname.release}".strip(),
        "cpu": _get_cpu_name(),
        "cores": os.cpu_count() or 1,
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "iverilog": _iverilog_version(),
        "verilator": _verilator_version(),
    }
    ram = _get_ram_gb()
    if ram is not None:
        info["ram_gb"] = ram
    return info


def _iverilog_version() -> str | None:
    if not IVERILOG:
        return None
    try:
        r = subprocess.run(
            [IVERILOG, "-V"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for line in (r.stdout + r.stderr).splitlines():
            if "Icarus Verilog" in line:
                return line.strip()
    except Exception:
        pass
    return None


def _verilator_version() -> str | None:
    verilator = _find_tool("verilator", extra_dirs=_VERILATOR_WINDOWS_DIRS)
    if not verilator:
        return None
    try:
        r = subprocess.run(
            [verilator, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        line = (r.stdout + r.stderr).splitlines()[0].strip() if (r.stdout + r.stderr) else None
        return line
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Capability detection
# ──────────────────────────────────────────────────────────────────────────────


def _has_vm_cython() -> bool:
    try:
        from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON  # noqa: PLC0415

        return bool(_HAS_CYTHON)
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Testbench source helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_ref_src(sim_time: int) -> str:
    """Append self-contained clock + reset initial blocks to BENCH_DUT."""
    return BENCH_DUT.replace(
        "endmodule",
        f"""\
    initial begin
        clk = 0;
        rst = 1;
        #20 rst = 0;
        #{sim_time};
        $finish;
    end

    initial forever #5 clk = ~clk;

endmodule
""",
    )


def _make_icarus_src(sim_time: int) -> str:
    tb = f"""\
module bench_tb;
    reg clk, rst;
    wire [15:0] accum_out;

    bench dut(.clk(clk), .rst(rst), .accum_out(accum_out));

    initial begin
        clk = 0;
        rst = 1;
        #20 rst = 0;
        #{sim_time};
        $finish;
    end

    always #5 clk = ~clk;
endmodule
"""
    return BENCH_DUT + "\n" + tb


def _make_verilator_cpp(cycles: int) -> str:
    return f"""\
#include "Vbench.h"
#include "verilated.h"
#include <cstdio>
#include <chrono>

int main(int argc, char** argv) {{
    Verilated::commandArgs(argc, argv);
    Vbench* dut = new Vbench;

    dut->rst = 1;
    dut->clk = 0;
    for (int i = 0; i < 4; i++) {{
        dut->clk = !dut->clk;
        dut->eval();
    }}
    dut->rst = 0;

    auto t0 = std::chrono::high_resolution_clock::now();

    for (int i = 0; i < {cycles} * 2; i++) {{
        dut->clk = !dut->clk;
        dut->eval();
    }}

    auto t1 = std::chrono::high_resolution_clock::now();
    double elapsed = std::chrono::duration<double>(t1 - t0).count();

    printf("%.6f\\n", elapsed);

    dut->final();
    delete dut;
    return 0;
}}
"""


def _parse_design():
    p = verilog_parser(start="source_text")
    tree = p.build_tree(BENCH_DUT)
    return tree_to_design(tree)


# ──────────────────────────────────────────────────────────────────────────────
# Engine runners — each returns {"time": float, "throughput": float} or {"error": str}
# ──────────────────────────────────────────────────────────────────────────────


def run_reference(cycles: int, max_time: int, sim_time: int) -> dict:
    ref_src = _make_ref_src(sim_time)
    p = verilog_parser(start="source_text")
    tree = p.build_tree(ref_src)
    design = tree_to_design(tree)
    module = design.modules[0]

    sim = Simulator(module)
    sim._sched.executor.loop_limit = max(100_000, max_time * 4)  # type: ignore[attr-defined]

    t0 = time.perf_counter()
    sim.run(max_time=max_time)
    elapsed = time.perf_counter() - t0
    return {"time": elapsed, "throughput": cycles / elapsed if elapsed > 0 else 0}


def run_vm_python(cycles: int, max_time: int) -> dict:
    """VM engine in pure-Python bytecode mode (no Cython extension)."""
    design = _parse_design()
    module = design.modules[0]

    sim = Simulator(module, engine="vm")  # force_python=True
    clk = sim.signal("clk")
    sim.fork(Clock(clk, period=10))

    def test(s):
        s.drive("rst", Value(1, width=1))
        s._sched.schedule_at(20, ("clock_toggle", "rst", Value(0, width=1)))

    t0 = time.perf_counter()
    sim.run(test, max_time=max_time)
    elapsed = time.perf_counter() - t0
    return {"time": elapsed, "throughput": cycles / elapsed if elapsed > 0 else 0}


def run_vm_cython(cycles: int, max_time: int) -> dict:
    """VM engine with Cython C delta loop."""
    if not _has_vm_cython():
        return {"error": "Cython VM extension not built (run: uv run python setup_cython.py build_ext --inplace)"}

    design = _parse_design()
    module = design.modules[0]

    sim = Simulator(module, engine="vm-fast")  # force_python=False, uses Cython
    clk = sim.signal("clk")
    sim.fork(Clock(clk, period=10))

    def test(s):
        s.drive("rst", Value(1, width=1))
        s._sched.schedule_at(20, ("clock_toggle", "rst", Value(0, width=1)))

    t0 = time.perf_counter()
    sim.run(test, max_time=max_time)
    elapsed = time.perf_counter() - t0
    return {"time": elapsed, "throughput": cycles / elapsed if elapsed > 0 else 0}


def run_compiled_step(cycles: int, max_time: int) -> dict:
    design = _parse_design()
    module = design.modules[0]

    sim = Simulator(module, engine="compiled")
    clk = sim.signal("clk")
    sim.fork(Clock(clk, period=10))

    def test(s):
        s.drive("rst", Value(1, width=1))
        s._sched.schedule_at(20, ("clock_toggle", "rst", Value(0, width=1)))

    t0 = time.perf_counter()
    sim.run(test, max_time=max_time)
    elapsed = time.perf_counter() - t0
    return {"time": elapsed, "throughput": cycles / elapsed if elapsed > 0 else 0}


def run_compiled_batch(cycles: int) -> dict:
    design = _parse_design()
    module = design.modules[0]

    sim = Simulator(module, engine="compiled")
    from typing import Any  # noqa: PLC0415

    csim: Any = sim._sched._sim  # type: ignore[attr-defined]

    sim.drive("rst", Value(1, width=1))
    sim.drive("clk", Value(0, width=1))
    csim.snapshot()
    sim.drive("clk", Value(1, width=1))
    csim.step()
    csim.snapshot()
    sim.drive("clk", Value(0, width=1))
    csim.step()
    sim.drive("rst", Value(0, width=1))
    csim.snapshot()
    sim.drive("clk", Value(1, width=1))
    csim.step()
    csim.snapshot()
    sim.drive("clk", Value(0, width=1))
    csim.step()

    t0 = time.perf_counter()
    sim.batch_run(cycles, "clk", clock_period=10)
    elapsed = time.perf_counter() - t0
    return {"time": elapsed, "throughput": cycles / elapsed if elapsed > 0 else 0}


def run_icarus(cycles: int, sim_time: int) -> dict:
    if not IVERILOG or not VVP:
        return {"error": "iverilog/vvp not found"}

    src = _make_icarus_src(sim_time)
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "bench.v")
        out_path = os.path.join(tmpdir, "bench.out")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(src)

        t0 = time.perf_counter()
        r = subprocess.run(
            [IVERILOG, "-o", out_path, src_path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        compile_time = time.perf_counter() - t0
        if r.returncode != 0:
            return {"error": f"compile failed: {r.stderr.strip()[:200]}"}

        t0 = time.perf_counter()
        subprocess.run(
            [VVP, out_path],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=tmpdir,
            check=False,
        )
        run_time = time.perf_counter() - t0

    return {
        "compile_time": compile_time,
        "time": run_time,
        "throughput": cycles / run_time if run_time > 0 else 0,
    }


def run_verilator(cycles: int) -> dict:
    verilator = _find_tool("verilator", extra_dirs=_VERILATOR_WINDOWS_DIRS)
    if not verilator:
        return {"error": "verilator not found"}

    with tempfile.TemporaryDirectory() as tmpdir:
        dut_path = os.path.join(tmpdir, "bench.v")
        tb_path = os.path.join(tmpdir, "sim_main.cpp")
        with open(dut_path, "w", encoding="utf-8") as f:
            f.write(BENCH_DUT)
        with open(tb_path, "w", encoding="utf-8") as f:
            f.write(_make_verilator_cpp(cycles))

        t0 = time.perf_counter()
        r = subprocess.run(
            [verilator, "--cc", "--exe", "--build", "-Wno-fatal", "-o", "bench_sim", dut_path, tb_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmpdir,
            check=False,
        )
        compile_time = time.perf_counter() - t0
        if r.returncode != 0:
            return {"error": f"verilator build failed: {r.stderr.strip()[:200]}"}

        exe_path = os.path.join(tmpdir, "obj_dir", "bench_sim")
        if not os.path.isfile(exe_path):
            for cand in [
                os.path.join(tmpdir, "bench_sim"),
                os.path.join(tmpdir, "obj_dir", "Vbench"),
            ]:
                if os.path.isfile(cand):
                    exe_path = cand
                    break
            else:
                return {"error": "verilator: compiled binary not found"}

        t0 = time.perf_counter()
        r = subprocess.run(
            [exe_path],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=tmpdir,
            check=False,
        )
        run_time = time.perf_counter() - t0

        # Prefer the elapsed time printed by the binary (avoids subprocess overhead)
        stdout = r.stdout.strip()
        try:
            run_time = float(stdout.splitlines()[-1])
        except (ValueError, IndexError):
            pass

    return {
        "compile_time": compile_time,
        "time": run_time,
        "throughput": cycles / run_time if run_time > 0 else 0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Profiling
# ──────────────────────────────────────────────────────────────────────────────


def profile_reference(_cycles: int, max_time: int, sim_time: int, top_n: int = 30) -> None:
    ref_src = _make_ref_src(sim_time)
    p = verilog_parser(start="source_text")
    tree = p.build_tree(ref_src)
    design = tree_to_design(tree)
    module = design.modules[0]

    sim = Simulator(module)
    sim._sched.executor.loop_limit = max(100_000, max_time * 4)  # type: ignore[attr-defined]

    pr = cProfile.Profile()
    pr.enable()
    sim.run(max_time=max_time)
    pr.disable()

    for sort_key, label in [("cumulative", "cumulative"), ("tottime", "total (self)")]:
        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s)
        ps.sort_stats(sort_key)
        print(f"\n{'=' * 80}")
        print(f"PROFILE — top {top_n} functions by {label} time")
        print("=" * 80)
        ps.print_stats(top_n)
        print(s.getvalue())


# ──────────────────────────────────────────────────────────────────────────────
# Markdown generation
# ──────────────────────────────────────────────────────────────────────────────

_ENGINE_ORDER = [
    "Compiled (batch)",
    "Verilator",
    "Compiled (step)",
    "VM (Cython)",
    "Icarus Verilog",
    "VM (Python)",
    "Reference",
]


def _sort_key(name: str, tp: float) -> tuple:
    try:
        order = _ENGINE_ORDER.index(name)
    except ValueError:
        order = len(_ENGINE_ORDER)
    return (-tp, order)


def _fmt_tp(tp: float) -> str:
    if tp >= 1_000_000:
        return f"{tp / 1_000_000:.2f}M cyc/s"
    if tp >= 1_000:
        return f"{tp / 1_000:.1f}K cyc/s"
    return f"{tp:.0f} cyc/s"


def generate_markdown(
    results: dict[str, dict],
    cycles: int,
    machine_info: dict,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    lines += [
        "# Simulation Benchmarks",
        "",
        "> Auto-generated by `benchmarks/benchmark.py`. Edit the script, not this file.",
        f"> Last updated: {now}",
        "",
        "## How to Update",
        "",
        "```bash",
        "# Quick run (50K cycles, console only)",
        "uv run python benchmarks/benchmark.py",
        "",
        "# Write results to this file",
        "uv run python benchmarks/benchmark.py --update",
        "",
        "# Longer run for more stable numbers",
        "uv run python benchmarks/benchmark.py --cycles 200000 --update",
        "```",
        "",
        "Icarus Verilog and Verilator are optional — the script detects them automatically on both Windows and Linux.",
        "The Cython-accelerated VM and compiled engines require:",
        "",
        "```bash",
        "uv run python setup_cython.py build_ext --inplace",
        "```",
        "",
        "## Benchmark DUT",
        "",
        f"**{DUT_DESCRIPTION}** — a single self-contained Verilog module that exercises:",
        "",
        "- **ALU** — 12 operations (add, sub, and, or, xor, not, shifts, multiply, "
        "min, max, add-with-carry via concatenation LHS)",
        "- **Register file** — 8-entry memory array, synchronous write, async read via `assign`",
        "- **FSM** — 4-state IDLE→LOAD→EXEC→STORE cycle, drives register write-enable",
        "- **Counters** — 16-bit cycle counter + 8-bit phase counter",
        "- **LFSR** — 16-bit XOR-feedback shift register",
        "- **Accumulator** — 16-bit, conditional on FSM STORE state",
        "- **Continuous-assign chain** — 4 assigns with mux and reduction-OR",
        "- **Counter-driven stimulus** — inputs change every cycle with no external driver",
        "",
        "This combination exercises: expression evaluation, sensitivity analysis, "
        "NBA scheduling, delta cycles, `always @(*)` re-triggering, and memory array reads.",
        "",
        "## Engines",
        "",
        "| Engine | Description |",
        "|--------|-------------|",
        "| Reference | Pure-Python tree-walking interpreter — baseline for correctness |",
        "| VM (Python) | Stack-based bytecode interpreter, pure Python dispatch |",
        "| VM (Cython) | Same bytecode, C delta loop via Cython extension (`_interp_fast.pyx`) |",
        "| Compiled (step) | Design-specific Cython `.pyx`, event-driven step mode |",
        "| Compiled (batch) | Same compiled code, `nogil` C loop — no Python per cycle |",
        "| Icarus Verilog | Industry-standard interpreted simulator (external process) |",
        "| Verilator | Compiled C++ simulator (external process) |",
        "",
    ]

    # ── Machine info ─────────────────────────────────────────────────────────
    lines += [
        "## Test Machine",
        "",
        "| | |",
        "|---|---|",
        f"| OS | {machine_info.get('os', 'unknown')} |",
        f"| CPU | {machine_info.get('cpu', 'unknown')} |",
        f"| Cores | {machine_info.get('cores', '?')} |",
    ]
    if "ram_gb" in machine_info:
        lines.append(f"| RAM | {machine_info['ram_gb']:.0f} GB |")
    lines.append(f"| Python | {machine_info.get('python', 'unknown')} |")
    if machine_info.get("iverilog"):
        lines.append(f"| Icarus | {machine_info['iverilog']} |")
    if machine_info.get("verilator"):
        lines.append(f"| Verilator | {machine_info['verilator']} |")
    lines.append("")

    # ── Results table ────────────────────────────────────────────────────────
    lines += [
        "## Results",
        "",
        f"DUT: {DUT_DESCRIPTION} — **{cycles:,} cycles**",
        "",
    ]

    if not results:
        lines.append("*No results — run the benchmark script.*")
        lines.append("")
        return "\n".join(lines)

    icarus_tp = results.get("Icarus Verilog", {}).get("throughput", 0.0)
    ref_tp = results.get("Reference", {}).get("throughput", 0.0)
    baseline_tp = icarus_tp or ref_tp

    sorted_results = sorted(
        results.items(),
        key=lambda kv: _sort_key(kv[0], kv[1].get("throughput", 0.0)),
    )

    lines.append("| Engine | Time | Throughput | vs Icarus |")
    lines.append("|--------|------|-----------|-----------|")

    for name, r in sorted_results:
        if "error" in r and r.get("throughput", 0) == 0:
            lines.append(f"| {name} | — | *skipped* | — |")
            continue

        t = r.get("time", 0.0)
        tp = r.get("throughput", 0.0)
        time_str = f"{t:.3f}s"
        tp_str = _fmt_tp(tp)

        if name == "Icarus Verilog":
            vs = "baseline"
        elif baseline_tp > 0 and tp > 0:
            ratio = tp / baseline_tp
            if ratio >= 1.05:
                vs = f"{ratio:.1f}x faster"
            elif ratio <= 0.95:
                vs = f"{1.0 / ratio:.1f}x slower"
            else:
                vs = "~parity"
        else:
            vs = "—"

        lines.append(f"| {name} | {time_str} | {tp_str} | {vs} |")

    lines.append("")

    # ── Notes on skipped engines ─────────────────────────────────────────────
    skipped = [(name, r["error"]) for name, r in results.items() if "error" in r and r.get("throughput", 0) == 0]
    if skipped:
        lines += ["**Skipped:**", ""]
        for name, err in skipped:
            lines.append(f"- **{name}**: {err}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:  # noqa: PLR0912, PLR0915
    ap = argparse.ArgumentParser(description="Simulation benchmark — all engines and simulators")
    ap.add_argument(
        "--cycles",
        type=int,
        default=50_000,
        help="Clock cycles to simulate (default: 50000)",
    )
    ap.add_argument(
        "--update",
        action="store_true",
        help="Write results to notes/benchmarks.md",
    )
    ap.add_argument(
        "--profile",
        action="store_true",
        help="Run cProfile on reference engine and exit",
    )
    args = ap.parse_args()

    cycles = args.cycles
    sim_time = cycles * 10 + 20
    max_time = sim_time + 20

    machine_info = collect_machine_info()

    print(f"{'=' * 70}")
    print("Simulation Benchmark")
    print(f"  DUT:     {DUT_DESCRIPTION}")
    print(f"  Python:  {machine_info['python']}")
    print(f"  OS:      {machine_info['os']}")
    print(f"  CPU:     {machine_info['cpu']}")
    print(f"  Cores:   {machine_info['cores']}")
    if "ram_gb" in machine_info:
        print(f"  RAM:     {machine_info['ram_gb']:.0f} GB")
    print(f"  Cycles:  {cycles:,}")
    print(f"  iverilog:{' ' + IVERILOG if IVERILOG else ' not found'}")
    verilator = _find_tool("verilator", extra_dirs=_VERILATOR_WINDOWS_DIRS)
    print(f"  verilator:{' ' + verilator if verilator else ' not found'}")
    print(f"  VM Cython: {'yes' if _has_vm_cython() else 'no (run setup_cython.py)'}")
    print(f"{'=' * 70}")

    if args.profile:
        profile_reference(cycles, max_time, sim_time)
        return

    results: dict[str, dict] = {}
    runners = [
        ("Reference", lambda: run_reference(cycles, max_time, sim_time)),
        ("VM (Python)", lambda: run_vm_python(cycles, max_time)),
        ("VM (Cython)", lambda: run_vm_cython(cycles, max_time)),
        ("Compiled (step)", lambda: run_compiled_step(cycles, max_time)),
        ("Compiled (batch)", lambda: run_compiled_batch(cycles)),
        ("Icarus Verilog", lambda: run_icarus(cycles, sim_time)),
        ("Verilator", lambda: run_verilator(cycles)),
    ]
    warmup_runners = [
        ("Reference", lambda: run_reference(200, 2200, 2020)),
        ("VM (Python)", lambda: run_vm_python(200, 2200)),
        ("VM (Cython)", lambda: run_vm_cython(200, 2200)),
        ("Compiled (step)", lambda: run_compiled_step(200, 2200)),
        ("Compiled (batch)", lambda: run_compiled_batch(200)),
    ]
    warmup_set = {name for name, _ in warmup_runners}
    warmup_map = dict(warmup_runners)

    total = len(runners)
    for step, (name, run_fn) in enumerate(runners, 1):
        print(f"\n[{step}/{total}] {name}...")
        try:
            if name in warmup_set:
                print("  warming up...")
                warmup_map[name]()
            result = run_fn()
        except Exception as e:
            print(f"  ERROR: {e}")
            results[name] = {"error": str(e), "throughput": 0.0, "time": 0.0}
            continue

        if "error" in result and result.get("throughput", 0) == 0:
            print(f"  SKIPPED: {result['error']}")
            results[name] = result
            continue

        t = result["time"]
        tp = result["throughput"]
        if result.get("compile_time"):
            print(f"  compile: {result['compile_time']:.3f}s")
        print(f"  run:     {t:.3f}s")
        print(f"  throughput: {_fmt_tp(tp)}")
        results[name] = result

    # ── Summary comparison ────────────────────────────────────────────────
    def _tp(name: str) -> float:
        return results.get(name, {}).get("throughput", 0.0)

    ref_tp = _tp("Reference")
    vm_py_tp = _tp("VM (Python)")
    vm_cy_tp = _tp("VM (Cython)")
    compiled_tp = _tp("Compiled (batch)")
    icarus_tp = _tp("Icarus Verilog")

    print(f"\n{'─' * 60}")
    print("Key ratios:")
    if ref_tp > 0 and vm_py_tp > 0:
        print(f"  VM (Python) vs Reference:   {vm_py_tp / ref_tp:.1f}x")
    if ref_tp > 0 and vm_cy_tp > 0:
        print(f"  VM (Cython) vs Reference:   {vm_cy_tp / ref_tp:.1f}x")
    if vm_py_tp > 0 and vm_cy_tp > 0:
        print(f"  VM (Cython) vs VM (Python): {vm_cy_tp / vm_py_tp:.1f}x")
    if ref_tp > 0 and compiled_tp > 0:
        print(f"  Compiled (batch) vs Reference: {compiled_tp / ref_tp:.0f}x")
    if icarus_tp > 0 and vm_cy_tp > 0:
        ratio = vm_cy_tp / icarus_tp
        label = f"{ratio:.1f}x faster" if ratio > 1 else f"{1 / ratio:.1f}x slower"
        print(f"  VM (Cython) vs Icarus:      {label}")
    if icarus_tp > 0 and compiled_tp > 0:
        print(f"  Compiled (batch) vs Icarus: {compiled_tp / icarus_tp:.1f}x faster")
    print(f"{'─' * 60}")

    # ── Markdown output ───────────────────────────────────────────────────
    md = generate_markdown(results, cycles, machine_info)
    print(f"\n{'=' * 70}")
    print("MARKDOWN (notes/benchmarks.md)")
    print("=" * 70)
    print(md)

    if args.update:
        dest = os.path.join(ROOT, "notes", "benchmarks.md")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Written to {dest}")


if __name__ == "__main__":
    main()

"""Composability showcase: Design-space exploration.

Demonstrates something IMPOSSIBLE in plain Verilog: generating multiple
design variants from a parameter sweep, emitting each one, and producing
a comparison table — all from a single Python script.

In Verilog, you can parameterize a module, but you cannot iterate over
parameter combinations, generate multiple elaborated designs, or compute
comparative metrics.  You'd need external scripting (Tcl, Makefiles).

With the DSL, it's just a Python for-loop.
"""

from veriforge.dsl.lib import mac, pipelined_mult, fir_filter, sync_fifo
from veriforge.codegen import emit_module


# ---------------------------------------------------------------------------
# Helper: count rough resource estimates from emitted Verilog
# ---------------------------------------------------------------------------


def analyze_verilog(verilog_text):
    """Estimate design metrics from emitted Verilog text.

    Returns a dict with rough counts — not a synthesis tool, but useful
    for comparing relative complexity across design variants.
    """
    lines = verilog_text.strip().splitlines()
    regs = sum(1 for ln in lines if ln.strip().startswith("reg ") or "output reg" in ln)
    wires = sum(1 for ln in lines if ln.strip().startswith("wire ") or ln.strip().startswith("assign "))
    always_blocks = sum(1 for ln in lines if "always" in ln)
    return {
        "lines": len(lines),
        "regs": regs,
        "wires": wires,
        "always_blocks": always_blocks,
    }


# ---------------------------------------------------------------------------
# Exploration 1: FIR filter sweep (tap count × data width)
# ---------------------------------------------------------------------------

print("=" * 78)
print("FIR Filter Design-Space Exploration")
print("=" * 78)
print()
print(f"{'Taps':>6} {'DataW':>6} {'CoeffW':>7} | {'Lines':>6} {'Regs':>5} {'Wires':>6} {'Always':>7}")
print("-" * 60)

fir_configs = [
    {"num_taps": 4, "data_width": 8, "coeff_width": 8},
    {"num_taps": 4, "data_width": 16, "coeff_width": 16},
    {"num_taps": 8, "data_width": 16, "coeff_width": 16},
    {"num_taps": 16, "data_width": 16, "coeff_width": 16},
    {"num_taps": 16, "data_width": 16, "coeff_width": 24},
    {"num_taps": 32, "data_width": 16, "coeff_width": 16},
]

for cfg in fir_configs:
    mod = fir_filter(**cfg).build()
    verilog = emit_module(mod)
    stats = analyze_verilog(verilog)
    print(
        f"{cfg['num_taps']:>6} {cfg['data_width']:>6} {cfg['coeff_width']:>7} | "
        f"{stats['lines']:>6} {stats['regs']:>5} {stats['wires']:>6} {stats['always_blocks']:>7}"
    )


# ---------------------------------------------------------------------------
# Exploration 2: FIFO depth scaling
# ---------------------------------------------------------------------------

print()
print("=" * 78)
print("FIFO Depth Scaling")
print("=" * 78)
print()
print(f"{'Depth':>6} {'DataW':>6} | {'Lines':>6} {'Regs':>5} {'Wires':>6} {'Always':>7}")
print("-" * 50)

for depth in [4, 8, 16, 32, 64, 256]:
    mod = sync_fifo(data_width=8, depth=depth).build()
    verilog = emit_module(mod)
    stats = analyze_verilog(verilog)
    print(f"{depth:>6} {8:>6} | {stats['lines']:>6} {stats['regs']:>5} {stats['wires']:>6} {stats['always_blocks']:>7}")


# ---------------------------------------------------------------------------
# Exploration 3: Pipelined multiplier — stages vs latency
# ---------------------------------------------------------------------------

print()
print("=" * 78)
print("Pipelined Multiplier — Pipeline Depth vs Complexity")
print("=" * 78)
print()
print(f"{'AW':>4} {'BW':>4} {'Stages':>7} | {'Lines':>6} {'Regs':>5} {'Always':>7}")
print("-" * 48)

for a_width in [8, 16, 18]:
    for stages in [2, 3, 4, 6]:
        mod = pipelined_mult(a_width=a_width, b_width=a_width, stages=stages).build()
        verilog = emit_module(mod)
        stats = analyze_verilog(verilog)
        print(
            f"{a_width:>4} {a_width:>4} {stages:>7} | "
            f"{stats['lines']:>6} {stats['regs']:>5} {stats['always_blocks']:>7}"
        )


# ---------------------------------------------------------------------------
# Show one variant's full Verilog (smallest FIR for readability)
# ---------------------------------------------------------------------------

print()
print("=" * 78)
print("Full Verilog for 4-tap, 8-bit FIR (smallest variant)")
print("=" * 78)
mod = fir_filter(num_taps=4, data_width=8, coeff_width=8).build()
print(emit_module(mod))

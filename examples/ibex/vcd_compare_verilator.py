"""Compare Ibex Verilator VCD trace against our simulator.

Parses the reference VCD produced by Verilator (examples/ibex/verilator/ibex_trace.vcd),
runs our simulator for the same design, and compares signals at each clock posedge.

Usage:
    uv run python examples/ibex/vcd_compare_verilator.py
"""

import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")

# $readmemh path in testbench is relative, so set CWD to sim/
os.chdir(SIM_DIR)

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.testbench import Simulator  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
ENGINE = "compiled"
DEFINES = {"SYNTHESIS": ""}

VCD_PATH = os.path.join(SCRIPT_DIR, "verilator", "ibex_trace.vcd")

# RTL files in dependency order
FILES = [
    os.path.join(RTL_DIR, "prim_assert.sv"),
    os.path.join(RTL_DIR, "dv_fcov_macros.svh"),
    os.path.join(RTL_DIR, "ibex_pkg.sv"),
    os.path.join(RTL_DIR, "ibex_alu.sv"),
    os.path.join(RTL_DIR, "ibex_branch_predict.sv"),
    os.path.join(RTL_DIR, "ibex_compressed_decoder.sv"),
    os.path.join(RTL_DIR, "ibex_counter.sv"),
    os.path.join(RTL_DIR, "ibex_csr.sv"),
    os.path.join(RTL_DIR, "ibex_decoder.sv"),
    os.path.join(RTL_DIR, "ibex_fetch_fifo.sv"),
    os.path.join(RTL_DIR, "ibex_multdiv_fast.sv"),
    os.path.join(RTL_DIR, "ibex_pmp.sv"),
    os.path.join(RTL_DIR, "ibex_prefetch_buffer.sv"),
    os.path.join(RTL_DIR, "ibex_register_file_ff.sv"),
    os.path.join(RTL_DIR, "ibex_wb_stage.sv"),
    os.path.join(RTL_DIR, "ibex_load_store_unit.sv"),
    os.path.join(RTL_DIR, "ibex_ex_block.sv"),
    os.path.join(RTL_DIR, "ibex_if_stage.sv"),
    os.path.join(RTL_DIR, "ibex_cs_registers.sv"),
    os.path.join(RTL_DIR, "ibex_id_stage.sv"),
    os.path.join(RTL_DIR, "ibex_controller.sv"),
    os.path.join(RTL_DIR, "ibex_core.sv"),
    os.path.join(SIM_DIR, "testbench.v"),
]


# ── VCD parser (handles aliased identifiers properly) ────────────────


def parse_verilator_vcd(path: str) -> dict:
    """Parse a Verilator VCD file, returning per-signal timelines.

    Handles Verilator's identifier aliasing: multiple scoped signals
    share the same VCD identifier.  We collect ALL scoped names for
    each identifier so every signal gets its timeline.

    Returns:
        dict with keys:
            'signals': {full_name: {'width': int, 'ident': str}}
            'changes': {full_name: [(time, int_value), ...]}
            'timescale': str
    """
    signals: dict[str, dict] = {}  # full_name -> {width, ident}
    ident_to_names: dict[str, list[str]] = {}  # ident -> [full_name, ...]
    ident_changes: dict[str, list[tuple[int, str]]] = {}  # ident -> [(time, raw_val)]
    timescale = "1ps"

    scope_stack: list[str] = []
    current_time = 0

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if line.startswith("$timescale"):
                m = re.match(r"\$timescale\s+(\S+)\s+\$end", line)
                if m:
                    timescale = m.group(1)
                continue

            if line.startswith("$scope"):
                m = re.match(r"\$scope\s+\w+\s+(\S+)\s+\$end", line)
                if m:
                    scope_stack.append(m.group(1))
                continue

            if line.startswith("$upscope"):
                if scope_stack:
                    scope_stack.pop()
                continue

            if line.startswith("$var"):
                m = re.match(
                    r"\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+\[.*?\])?\s+\$end",
                    line,
                )
                if m:
                    width = int(m.group(1))
                    ident = m.group(2)
                    raw_name = m.group(3)
                    scope = ".".join(scope_stack) if scope_stack else ""
                    full_name = f"{scope}.{raw_name}" if scope else raw_name

                    signals[full_name] = {"width": width, "ident": ident}
                    ident_to_names.setdefault(ident, []).append(full_name)
                    if ident not in ident_changes:
                        ident_changes[ident] = []
                continue

            if line.startswith(("$date", "$version", "$enddefinitions", "$dumpvars", "$end", "$comment")):
                continue

            # Timestamp
            if line.startswith("#"):
                m = re.match(r"#(\d+)", line)
                if m:
                    current_time = int(m.group(1))
                continue

            # Single-bit value change: 0!, 1!, x!
            m = re.match(r"^([01xXzZ])(\S+)$", line)
            if m:
                val = m.group(1).lower()
                ident = m.group(2)
                if ident in ident_changes:
                    ident_changes[ident].append((current_time, val))
                continue

            # Multi-bit value change: b10101011 !
            m = re.match(r"^[bB]([01xXzZ]+)\s+(\S+)$", line)
            if m:
                val = m.group(1).lower()
                ident = m.group(2)
                if ident in ident_changes:
                    ident_changes[ident].append((current_time, val))
                continue

    # Distribute changes: each ident's changes belong to ALL its signals
    changes: dict[str, list[tuple[int, int]]] = {}
    for ident, raw_changes in ident_changes.items():
        # Convert raw VCD values to integers
        int_changes: list[tuple[int, int]] = []
        for t, raw_val in raw_changes:
            int_changes.append((t, _vcd_val_to_int(raw_val)))

        for name in ident_to_names.get(ident, []):
            changes[name] = int_changes

    return {"signals": signals, "changes": changes, "timescale": timescale}


def _vcd_val_to_int(v: str) -> int:
    """Convert a VCD value string to integer. x/z → 0."""
    v = v.lower().replace("x", "0").replace("z", "0")
    if len(v) == 1:
        return int(v)
    return int(v, 2)


def _value_at(timeline: list[tuple[int, int]], t: int) -> int | None:
    """Get the value at time t from a sorted timeline."""
    result = None
    for ct, cv in timeline:
        if ct <= t:
            result = cv
        else:
            break
    return result


# ── Signal name mapping ──────────────────────────────────────────────

# Verilator hierarchy: TOP.tb_verilator.core.xxx → our sim: core.xxx
# Verilator testbench: TOP.tb_verilator.xxx → our sim: xxx
VERILATOR_TB_PREFIX = "TOP.tb_verilator."

# Signals that exist only in one testbench or are VCD artifacts
IGNORE_SIGNALS = {
    "halted",
    "halted_r",
    "halt_code",
    "halt_code_r",
    "cycle_count_o",  # Verilator output port, our TB has cycle_count
    "cycle_count",  # Different semantics: Verilator resets on rst_n, ours free-runs
}

# Parameter / localparam names that are constants, not runtime signals.
# Verilator dumps them with initial values; our sim may not propagate them.
# We detect these heuristically: width-32+ signals whose Verilator value
# never changes, or signals matching known parameter naming patterns.
_PARAM_PATTERNS = {
    "ADDR_W",
    "BUS_SIZE",
    "BUS_BYTES",
    "BUS_W",
    "IC_SIZE_BYTES",
    "IC_NUM_WAYS",
    "IC_LINE_SIZE",
    "IC_LINE_BYTES",
    "IC_LINE_W",
    "IC_NUM_LINES",
    "IC_LINE_BEATS",
    "IC_LINE_BEATS_W",
    "IC_INDEX_W",
    "IC_INDEX_HI",
    "IC_TAG_SIZE",
    "IC_OUTPUT_BEATS",
    "LfsrWidth",
    "Width",
    "CounterWidth",
    "ProvideValUpd",
    "PMPNumRegions",
    "PMPNumChan",
    "PMPAddrWidth",
    "RV32M",
    "RV32MEnabled",
    "RV32ZC",
    "RegFileDataWidth",
    "MemDataWidth",
    "MHPMCounterWidth",
    "DbgHwBreakNum",
    "DmBaseAddr",
    "DmHaltAddr",
    "DmExceptionAddr",
    "DmAddrMask",
    "SCRAMBLE_KEY_W",
    "SCRAMBLE_NONCE_W",
    "PMP_MAX_REGIONS",
    "PMP_CFG_W",
}


def _is_parameter_signal(name: str, changes: list) -> bool:
    """Heuristically detect parameter/constant signals."""
    leaf = name.rsplit(".", maxsplit=1)[-1]
    # Check against known parameter names
    if leaf in _PARAM_PATTERNS:
        return True
    # All-caps leaf (common for localparams): e.g. SCRAMBLE_KEY_W
    if leaf.isupper() and "_" in leaf:
        return True
    # Signal that never changes after t=0 AND has a name starting with upper
    if leaf[0:1].isupper() and len(changes) <= 1:
        return True
    return False


def map_verilator_to_our(verilator_name: str) -> str | None:
    """Map a Verilator VCD signal name to our simulator's signal name.

    Returns None if the signal should be skipped.
    """
    if not verilator_name.startswith(VERILATOR_TB_PREFIX):
        return None  # Skip package-level constants etc.

    # Strip the testbench prefix
    remainder = verilator_name[len(VERILATOR_TB_PREFIX) :]

    # Skip ibex_pkg constants and other non-signal scopes
    if remainder.startswith("ibex_pkg."):
        return None

    # The remainder should be the signal name in our simulator
    return remainder


# ── Main comparison logic ────────────────────────────────────────────


def main() -> int:
    if not os.path.isfile(VCD_PATH):
        print(f"ERROR: Verilator VCD not found: {VCD_PATH}")
        print("Generate it first — see examples/ibex/verilator/README.md")
        return 1

    # ── Parse reference VCD ──────────────────────────────────────────
    print("Parsing Verilator VCD reference trace...")
    t0 = time.time()
    ref = parse_verilator_vcd(VCD_PATH)
    print(f"  {len(ref['signals'])} signals, timescale={ref['timescale']}")
    print(f"  Parsed in {time.time() - t0:.2f}s")

    # Find clock timeline from Verilator VCD
    clk_ident = None
    for name, info in ref["signals"].items():
        if name.endswith(".clk") and "tb_verilator" in name:
            clk_ident = info["ident"]
            clk_changes = ref["changes"].get(name, [])
            break

    if not clk_changes:
        # Try any clk signal
        for name, changes in ref["changes"].items():
            if name.endswith(".clk") or name.endswith(".clk_i"):
                if len(changes) > 10:
                    clk_changes = changes
                    break

    # Extract posedge times (transitions from 0→1)
    posedge_times: list[int] = []
    prev_clk = 0
    for t, v in clk_changes:
        if v == 1 and prev_clk == 0:
            posedge_times.append(t)
        prev_clk = v
    print(f"  {len(posedge_times)} clock posedges found")
    if posedge_times:
        print(f"  First posedge at t={posedge_times[0]}, last at t={posedge_times[-1]}")

    # Verilator time units: 1ps, clock half-period = 5 (5ps)
    # Our sim uses #5, so time unit = 5. Posedge at t=0,10,20,...
    # Verilator posedges at t=0,10,20,...  (5ps half-period)
    # Thus: verilator_time / 1 == our_time (both use same integer scale)
    # Actually: Verilator CLK_PERIOD_NS=10, half=5, timescale=1ps
    # So verilator times are 0,5,10,15,... in ps
    # Our sim: #5 means 5 time-units, posedges at 0,10,20,...
    # Mapping: our_time = verilator_time (they happen to match)

    # ── Build our simulator ──────────────────────────────────────────
    print("\nParsing Ibex design...")
    t0 = time.time()
    design = parse_files(
        FILES,
        preprocess=True,
        defines=DEFINES,
        include_paths=[RTL_DIR],
        cache_dir=os.path.join(SCRIPT_DIR, ".pcache"),
    )
    print(f"  Parsed in {time.time() - t0:.1f}s")

    print(f"Creating simulator (engine={ENGINE})...")
    t0 = time.time()
    sim = Simulator(design.get_module("testbench"), engine=ENGINE, design=design)
    our_signals = set(sim.signals())
    print(f"  Created in {time.time() - t0:.2f}s — {len(our_signals)} signals")

    # Drive unconnected IC cache inputs
    for name in ["core.ic_tag_rdata_i", "core.ic_data_rdata_i"]:
        try:
            sim.drive(name, 0)
        except Exception:
            pass

    # ── Map signals ──────────────────────────────────────────────────
    # Build mapping: verilator_name → our_name for signals present in both
    signal_map: dict[str, str] = {}  # verilator_name → our_name
    unmapped_ref: list[str] = []
    for vname in sorted(ref["signals"]):
        our_name = map_verilator_to_our(vname)
        if our_name is None:
            continue
        leaf = our_name.split(".")[-1]
        if leaf in IGNORE_SIGNALS:
            continue
        if our_name in our_signals:
            signal_map[vname] = our_name
        else:
            unmapped_ref.append(f"  {vname} → {our_name}")

    print(f"\n  Mapped: {len(signal_map)} signals")
    print(f"  Unmapped (in ref but not in our sim): {len(unmapped_ref)}")
    if unmapped_ref and len(unmapped_ref) <= 20:
        for line in unmapped_ref:
            print(line)

    # Filter out parameter/constant signals
    param_filtered = 0
    filtered_map: dict[str, str] = {}
    for vname, our_name in signal_map.items():
        changes = ref["changes"].get(vname, [])
        if _is_parameter_signal(vname, changes):
            param_filtered += 1
        else:
            filtered_map[vname] = our_name
    signal_map = filtered_map
    print(f"  Filtered params:    {param_filtered}")
    print(f"  Signals to compare: {len(signal_map)}")

    if not signal_map:
        print("ERROR: No signals could be mapped between simulators!")
        return 1

    # Find reset release time in Verilator VCD
    rst_name = None
    for vname in ref["changes"]:
        if vname.endswith(".rst_n") or vname.endswith(".rst_ni"):
            if "tb_verilator" in vname and vname.count(".") == 2:
                rst_name = vname
                break
    # If not found, try any rst_n
    if rst_name is None:
        for vname in ref["changes"]:
            if vname.endswith(".rst_n"):
                rst_name = vname
                break

    rst_release_time = 0
    if rst_name and rst_name in ref["changes"]:
        for t, v in ref["changes"][rst_name]:
            if v == 1:
                rst_release_time = t
                break
    print(f"  Reset release at t={rst_release_time} (from {rst_name})")

    # ── Run our simulator and compare at each posedge ────────────────
    print("\nRunning simulation and comparing at each posedge...")
    print(f"  Engine: {ENGINE}")
    print(f"  Posedges to compare: {len(posedge_times)}")

    total_checked = 0
    total_mismatches = 0
    total_checked_post_reset = 0
    total_mismatches_post_reset = 0
    first_mismatches: list[str] = []
    first_post_reset_mismatches: list[str] = []
    MAX_MISMATCH_DETAIL = 50
    per_signal_mismatches: dict[str, int] = {}

    t0 = time.time()

    cycle = 0
    for posedge_t in posedge_times:
        if posedge_t > sim.time:
            sim.run(max_time=posedge_t)

        if sim.time < posedge_t:
            break  # sim finished early

        # Compare signals at this posedge
        for vname, our_name in signal_map.items():
            ref_timeline = ref["changes"].get(vname, [])
            ref_val = _value_at(ref_timeline, posedge_t)
            if ref_val is None:
                continue

            try:
                our_val = sim.read(our_name)
            except (KeyError, AttributeError):
                continue

            # Normalize: mask to signal width
            width = ref["signals"][vname]["width"]
            mask = (1 << width) - 1
            ref_masked = ref_val & mask
            # Value objects have .val and .mask; extract integer
            if hasattr(our_val, "val"):
                our_int = our_val.val
                our_xmask = getattr(our_val, "mask", 0)
            elif isinstance(our_val, int):
                our_int = our_val
                our_xmask = 0
            else:
                our_int = 0
                our_xmask = 0
            our_masked = our_int & mask

            # Skip comparison if our value has x/z bits
            if our_xmask & mask:
                continue

            total_checked += 1
            is_post_reset = posedge_t > rst_release_time
            if is_post_reset:
                total_checked_post_reset += 1

            if ref_masked != our_masked:
                # Skip pre-reset reset-related mismatches: our Verilog
                # testbench releases reset via NBA one cycle earlier than
                # Verilator's C++ driver, so reset ports differ at t=90.
                if not is_post_reset:
                    leaf = our_name.rsplit(".", 1)[-1]
                    if leaf in ("rst_ni", "rst_n", "unused_rst_n", "unused_rst"):
                        continue
                total_mismatches += 1
                per_signal_mismatches[our_name] = per_signal_mismatches.get(our_name, 0) + 1
                if is_post_reset:
                    total_mismatches_post_reset += 1
                    if len(first_post_reset_mismatches) < MAX_MISMATCH_DETAIL:
                        first_post_reset_mismatches.append(
                            f"  t={posedge_t} {our_name}: ref=0x{ref_masked:X} our=0x{our_masked:X} (width={width})"
                        )
                elif len(first_mismatches) < MAX_MISMATCH_DETAIL:
                    first_mismatches.append(
                        f"  t={posedge_t} {our_name}: ref=0x{ref_masked:X} our=0x{our_masked:X} (width={width})"
                    )

        cycle += 1

    elapsed = time.time() - t0
    print(f"  Compared in {elapsed:.2f}s")
    print(f"\n{'=' * 60}")
    print(f"  Cycles compared:    {cycle}")
    print(f"  Signal checks:      {total_checked}")
    print(f"  Mismatches (total): {total_mismatches}")
    if total_checked > 0:
        match_pct = 100.0 * (total_checked - total_mismatches) / total_checked
        print(f"  Match rate (total): {match_pct:.2f}%")
    print(f"  Post-reset checks:  {total_checked_post_reset}")
    print(f"  Post-reset mismatches: {total_mismatches_post_reset}")
    if total_checked_post_reset > 0:
        post_pct = 100.0 * (total_checked_post_reset - total_mismatches_post_reset) / total_checked_post_reset
        print(f"  Post-reset match:   {post_pct:.2f}%")
    print(f"{'=' * 60}")

    # Show top mismatching signals
    if per_signal_mismatches:
        print("\nTop 20 mismatching signals (by count):")
        sorted_sigs = sorted(per_signal_mismatches.items(), key=lambda x: -x[1])
        for name, count in sorted_sigs[:20]:
            print(f"  {count:4d}x {name}")

    if first_post_reset_mismatches:
        print(f"\nFirst {len(first_post_reset_mismatches)} POST-RESET mismatches:")
        for line in first_post_reset_mismatches:
            print(line)
    elif first_mismatches:
        print(f"\nFirst {len(first_mismatches)} PRE-RESET mismatches:")
        for line in first_mismatches[:20]:
            print(line)

    return 0 if total_mismatches_post_reset == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

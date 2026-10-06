#!/usr/bin/env python3
"""Report how many bytes the compiled engine copies per snapshot, per cached design.

`batch_run` snapshots every signal (`sv`/`sm`), every wide-signal word, and
*every memory element* (value + mask, 8 bytes each regardless of width) at each
snapshot point -- once per cycle before the posedge, plus once more on cycles
where batch_run events were applied, plus once before a reacting negedge.
That cost is independent of how much of the design is active, and for designs
with large memories (e.g. image line buffers) it can dominate: measured at
roughly 40 us per MB copied per cycle on the dev machine.

Reads the elaboration-cache sidecars (`.cycache/_elab_*.json`), so it works on
any design that has been compiled at least once -- no recompile needed.

Usage:
    uv run python benchmarks/snapshot_footprint.py              # ./.cycache
    uv run python benchmarks/snapshot_footprint.py /path/to/.cycache
    uv run python benchmarks/snapshot_footprint.py --top 5      # largest five only
"""

from __future__ import annotations

import argparse
import glob
import json
import os

_US_PER_MB = 40.0  # measured, dev machine; order-of-magnitude guide only


def _footprint(meta: dict) -> dict:
    n_sigs = int(meta.get("n_sigs", 0))
    widths = meta.get("sig_widths") or []
    wide_words = sum((w + 63) // 64 for w in widths if w > 64)
    mems = meta.get("mem_info") or []
    mem_bytes = 0
    for width, depth in mems:
        words = (width + 63) // 64 if width > 64 else 1
        mem_bytes += depth * words * 8 * 2  # val + mask
    scalar_bytes = n_sigs * 8 * 2
    wide_bytes = wide_words * 8 * 2
    largest = sorted(((d * ((w + 63) // 64 if w > 64 else 1) * 16, w, d) for w, d in mems), reverse=True)
    return {
        "n_sigs": n_sigs,
        "n_mems": len(mems),
        "scalar_bytes": scalar_bytes,
        "wide_bytes": wide_bytes,
        "mem_bytes": mem_bytes,
        "total": scalar_bytes + wide_bytes + mem_bytes,
        "largest": largest[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache_dir", nargs="?", default=os.environ.get("VERIFORGE_COMPILE_CACHE", ".cycache"))
    parser.add_argument("--top", type=int, default=10, help="show the N designs with the largest footprint")
    args = parser.parse_args()

    rows = []
    for path in glob.glob(os.path.join(args.cache_dir, "_elab_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        rows.append((meta.get("keyed_name", os.path.basename(path)), _footprint(meta)))
    if not rows:
        print(f"no _elab_*.json in {args.cache_dir!r} -- compile the design once (engine='compiled') first")
        return
    rows.sort(key=lambda r: r[1]["total"], reverse=True)
    for name, fp in rows[: args.top]:
        mb = fp["total"] / 2**20
        print(
            f"{name}: {fp['n_sigs']} sigs, {fp['n_mems']} mems -- per snapshot copies "
            f"{fp['total'] / 1024:.0f} KB (memories {fp['mem_bytes'] / 1024:.0f} KB, "
            f"signals {fp['scalar_bytes'] / 1024:.0f} KB, wide {fp['wide_bytes'] / 1024:.0f} KB) "
            f"~ {mb * _US_PER_MB:.0f} us per snapshot"
        )
        for nbytes, width, depth in fp["largest"]:
            print(f"    memory [{width}-bit x {depth}]: {nbytes / 1024:.0f} KB")


if __name__ == "__main__":
    main()

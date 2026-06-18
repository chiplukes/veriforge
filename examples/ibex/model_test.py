"""Test tree-to-model conversion on Ibex RTL files (single parser instance)."""

import os
import traceback
from veriforge.preprocessor import preprocess_file
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design


def main():
    rtl_dir = os.path.join(os.path.dirname(__file__), "rtl")
    files = sorted(
        f for f in os.listdir(rtl_dir) if f.endswith(".sv") and not f.startswith("prim_") and not f.endswith(".svh")
    )

    # Large files that cause Earley parser to hang in sequential mode
    skip = {
        "ibex_compressed_decoder.sv",
        "ibex_decoder.sv",
        "ibex_controller.sv",
        "ibex_cs_registers.sv",
        "ibex_core.sv",
        "ibex_id_stage.sv",
    }

    p = verilog_parser(start="verilog")
    ok = 0
    fail = 0
    for f in files:
        if f in skip:
            print(f"  SKIP {f}")
            continue
        try:
            src = preprocess_file(os.path.join(rtl_dir, f))
            tree = p.parser.parse(src)
            model = tree_to_design(tree)
            info = ", ".join(
                f"{m.name}: {len(m.ports)}p {len(m.variables)}v {len(m.always_blocks)}a" for m in model.modules
            )
            print(f"  OK   {f}: {info}")
            ok += 1
        except Exception:
            lines = traceback.format_exc().strip().split("\n")
            print(f"  FAIL {f}: {lines[-1]}")
            fail += 1

    print(f"\n{ok}/{ok + fail} passed ({len(skip)} skipped)")


if __name__ == "__main__":
    main()

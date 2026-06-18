"""Test parsing all Ibex RTL files."""

import os
import subprocess
import sys


def main():
    rtl_dir = os.path.join(os.path.dirname(__file__), "rtl")
    files = sorted(
        f for f in os.listdir(rtl_dir) if f.endswith(".sv") and not f.startswith("prim_") and not f.endswith(".svh")
    )

    ok = 0
    fail = 0
    for f in files:
        path = os.path.join(rtl_dir, f).replace("\\", "/")
        script = (
            "from veriforge.preprocessor import preprocess_file\n"
            "from veriforge.verilog_parser import verilog_parser\n"
            'p = verilog_parser(start="verilog")\n'
            f'src = preprocess_file("{path}")\n'
            "tree = p.parser.parse(src)\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                print(f"  OK   {f}")
                ok += 1
            else:
                err = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown"
                print(f"  FAIL {f}: {err}")
                fail += 1
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT {f}")
            fail += 1

    print(f"\n{ok}/{ok + fail} passed")


if __name__ == "__main__":
    main()

"""Example: DSP inference patterns — MAC, pipelined multiplier, FIR filter.

Demonstrates patterns that synthesis tools map to DSP48 slices.
"""

from veriforge.codegen import emit_module
from veriforge.dsl.lib import fir_filter, mac, pipelined_mult


def main():
    # Multiply-accumulate: maps to DSP48 MAC mode
    m = mac(a_width=18, b_width=18)
    print("=== Multiply-Accumulate (18x18) ===")
    print(emit_module(m.build()))

    # Pipelined multiplier: matches DSP48 internal pipeline
    pm = pipelined_mult(a_width=18, b_width=18, stages=3)
    print("\n=== Pipelined Multiplier (3-stage, 18x18) ===")
    print(emit_module(pm.build()))

    # 4-tap FIR: transposed form for DSP48 chaining
    fir = fir_filter(data_width=16, coeff_width=16, num_taps=4)
    print("\n=== 4-Tap FIR Filter (16-bit) ===")
    print(emit_module(fir.build()))

    # 8-tap FIR with wider data
    fir8 = fir_filter(data_width=18, coeff_width=18, num_taps=8, name="fir_8tap")
    print("\n=== 8-Tap FIR Filter (18-bit) ===")
    print(emit_module(fir8.build()))


if __name__ == "__main__":
    main()
